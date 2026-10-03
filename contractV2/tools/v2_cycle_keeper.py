#!/usr/bin/env python3
"""Read-only Cycle preflight: check, wait, and print an executable operator transaction.

No key, signing, or transaction submission is supported. Every RPC is an allowlisted read.
All treasury reads and execute() simulation use one numbered block. A ready result is a
simulation at that block, not a guarantee that a later transaction will execute.
"""
import argparse
import datetime
import http.client
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


# Verified with `cast sig` against these exact Solidity signatures.
EXECUTE = "0x61461954"  # execute()
VIEWS = {
    "pending": "0x84c9853b",  # reentryPending()
    "salePrice": "0xfc7a13ed",  # reentrySalePrice()
    "saleAt": "0xc60b1cd5",  # reentrySaleAt()
    "stockUpdatedAt": "0x2436192c",  # reentryStockUpdatedAt()
    "recoveryDue": "0xd52bccce",  # recoveryDue()
    "lotCount": "0x7ae169e9",  # lotCount()
}
WAIT_ERRORS = {"0x47a2375f": "NotDue", "0xb0782df7": "Cooldown"}
PAUSE_ERRORS = {"0x7db7a074": "Unhealthy"}
ACTIONS = {0: "Stop", 1: "TakeProfit", 2: "BuyDip", 5: "BuyRecovery"}
READ_METHODS = frozenset({"eth_chainId", "eth_getBlockByNumber", "eth_getCode", "eth_call"})
MAX_RESPONSE_BYTES = 128 * 1024
MAX_LOTS = 128


class PreflightError(RuntimeError):
    pass


class RpcError(PreflightError):
    def __init__(self, method, code, message, data=None):
        super().__init__(f"{method}: RPC error {code}: {message[:200]}")
        self.code = code
        self.data = data


def address(value):
    if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
        raise argparse.ArgumentTypeError("expected a 20-byte 0x address")
    if int(value[2:], 16) == 0:
        raise argparse.ArgumentTypeError("address must be nonzero")
    return value.lower()


def rpc_url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        parsed.port  # Reject malformed and out-of-range ports before opening a request.
    except ValueError:
        raise argparse.ArgumentTypeError("invalid RPC URL") from None
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None or parsed.fragment
            or re.search(r"\s", value)):
        raise argparse.ArgumentTypeError("RPC must be an explicit HTTP(S) URL without userinfo or fragment")
    return value


def positive_chain(value):
    try:
        number = int(value, 0) if value.startswith("0x") else int(value)
    except (AttributeError, ValueError):
        raise argparse.ArgumentTypeError("chain id must be a positive integer") from None
    if number <= 0 or number >= 2**256:
        raise argparse.ArgumentTypeError("chain id must be a positive uint256")
    return number


def positive_seconds(value):
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("interval must be a positive number of seconds") from None
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("interval must be a positive number of seconds")
    return seconds


def quantity(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)", value):
        raise PreflightError(f"invalid {name} quantity")
    number = int(value, 16)
    if number >= 2**256:
        raise PreflightError(f"{name} exceeds uint256")
    return number


def hex_bytes(value, name, *, size=None, nonempty=False):
    if not isinstance(value, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})*", value):
        raise PreflightError(f"invalid {name} bytes")
    if size is not None and len(value) != 2 + 2 * size:
        raise PreflightError(f"invalid {name} length")
    if nonempty and len(value) == 2:
        raise PreflightError(f"{name} is empty; treasury has no code")
    return value.lower()


def decode_word(value, name, *, boolean=False):
    number = int(hex_bytes(value, name, size=32), 16)
    if boolean and number not in {0, 1}:
        raise PreflightError(f"invalid {name} boolean")
    return bool(number) if boolean else number


class Rpc:
    def __init__(self, url, timeout=10):
        self.url = rpc_url(url)
        self.timeout = timeout
        self._id = 0

    def call(self, method, params):
        if method not in READ_METHODS:
            raise PreflightError("RPC method is outside the read-only allowlist")
        self._id += 1
        request = urllib.request.Request(self.url, data=json.dumps({
            "jsonrpc": "2.0", "id": self._id, "method": method, "params": params,
        }).encode(), headers={"Content-Type": "application/json", "User-Agent": "HedgeFun-cycle-preflight/1"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
            raise PreflightError(f"{method}: network failure ({type(error).__name__})") from None
        if len(body) > MAX_RESPONSE_BYTES:
            raise PreflightError(f"{method}: oversized RPC response")
        try:
            reply = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            raise PreflightError(f"{method}: invalid RPC JSON") from None
        if (not isinstance(reply, dict) or reply.get("jsonrpc") != "2.0"
                or type(reply.get("id")) is not int or reply["id"] != self._id
                or ("result" in reply) == ("error" in reply)):
            raise PreflightError(f"{method}: invalid RPC envelope")
        if "error" in reply:
            failure = reply["error"]
            if (not isinstance(failure, dict) or type(failure.get("code")) is not int
                    or not isinstance(failure.get("message"), str)):
                raise PreflightError(f"{method}: invalid RPC error")
            raise RpcError(method, failure["code"], failure["message"], failure.get("data"))
        return reply["result"]


def revert_data(error):
    data = error.data
    # Some providers wrap the revert bytes once. Never infer execution status from error text.
    if isinstance(data, dict):
        data = data.get("data")
    try:
        return hex_bytes(data, "revert data")
    except PreflightError:
        return None


def check(rpc, chain_id, treasury, caller):
    treasury, caller = address(treasury), address(caller)
    actual_chain = quantity(rpc.call("eth_chainId", []), "chain id")
    if actual_chain != chain_id:
        raise PreflightError(f"chain mismatch: expected {chain_id}, received {actual_chain}")
    header = rpc.call("eth_getBlockByNumber", ["latest", False])
    if not isinstance(header, dict):
        raise PreflightError("invalid latest block")
    block_number = quantity(header.get("number"), "block number")
    timestamp = quantity(header.get("timestamp"), "block timestamp")
    block_hash = hex_bytes(header.get("hash"), "block hash", size=32)
    block = hex(block_number)
    hex_bytes(rpc.call("eth_getCode", [treasury, block]), "treasury code", nonempty=True)
    state = {}
    for name, selector in VIEWS.items():
        state[name] = decode_word(rpc.call("eth_call", [{"to": treasury, "data": selector}, block]),
                                  name, boolean=name in {"pending", "recoveryDue"})
    if (state["lotCount"] > MAX_LOTS or state["pending"] != (state["saleAt"] != 0)
            or (state["recoveryDue"] and not state["pending"])):
        raise PreflightError("inconsistent Cycle state")
    if state["pending"]:
        if not (state["salePrice"] > 0 and 0 < state["stockUpdatedAt"] <= state["saleAt"] <= timestamp):
            raise PreflightError("invalid Cycle sale reference")
    elif any(state[name] for name in ("salePrice", "saleAt", "stockUpdatedAt")):
        raise PreflightError("cleared Cycle state contains a sale reference")
    result = {"chainId": chain_id, "treasury": treasury, "block": block_number, "state": state}
    try:
        encoded = hex_bytes(rpc.call("eth_call", [{"from": caller, "to": treasury,
                                                   "data": EXECUTE, "value": "0x0"}, block]),
                            "execute return", size=64)
    except RpcError as error:
        data = revert_data(error)
        execution_error = error.code in {3, -32000, -32015}
        if execution_error and data in WAIT_ERRORS:
            result.update(status="waiting", reason=WAIT_ERRORS[data])
        elif execution_error and data in PAUSE_ERRORS:
            result.update(status="paused", reason=PAUSE_ERRORS[data])
        else:
            raise PreflightError(f"execute simulation failed: {error}") from None
    else:
        action, lot = int(encoded[2:66], 16), int(encoded[66:], 16)
        if (action not in ACTIONS or lot >= MAX_LOTS
                or (action == 5 and not state["recoveryDue"])):
            raise PreflightError("invalid Cycle execute action or lot")
        result.update(status="ready", action=ACTIONS[action], lot=lot, transaction={
            "chainId": chain_id, "from": caller, "to": treasury, "data": EXECUTE, "value": "0x0",
        })
    # A reorg between numbered reads must not turn mixed observations into a ready result.
    confirmed = rpc.call("eth_getBlockByNumber", [block, False])
    if (not isinstance(confirmed, dict) or confirmed.get("number") != block
            or hex_bytes(confirmed.get("hash"), "confirmed block hash", size=32) != block_hash):
        raise PreflightError("snapshot block changed during preflight")
    return result


def fingerprint(result):
    return json.dumps({key: value for key, value in result.items() if key not in {"block", "checkedAt"}},
                      sort_keys=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc", required=True, type=rpc_url)
    parser.add_argument("--chain-id", required=True, type=positive_chain)
    parser.add_argument("--treasury", required=True, type=address)
    parser.add_argument("--caller", required=True, type=address, help="public caller address used for simulation")
    parser.add_argument("--once", action="store_true", help="check once and exit")
    parser.add_argument("--interval", type=positive_seconds, default=60, help="poll seconds (default: 60)")
    args = parser.parse_args(argv)
    rpc, previous = Rpc(args.rpc), None
    try:
        while True:
            try:
                result = check(rpc, args.chain_id, args.treasury, args.caller)
            except (PreflightError, argparse.ArgumentTypeError) as error:
                result = {"status": "error", "reason": str(error)}
            current = fingerprint(result)
            if current != previous:
                result["checkedAt"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                print(json.dumps(result, sort_keys=True), flush=True)
            previous = current
            if args.once:
                return 1 if result["status"] == "error" else 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
