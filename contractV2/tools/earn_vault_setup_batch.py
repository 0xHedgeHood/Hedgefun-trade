#!/usr/bin/env python3
"""Build an OFFLINE Safe Transaction Builder batch for the RHNVDA Earn vault.

This tool uses Foundry's local ``cast`` only to encode calldata and checksums. It
does not query a chain, sign, or broadcast. Addresses supplied here must first be
verified against deployed contracts; Safe signers should simulate the resulting
batch in the Safe UI before executing it.

    python3 tools/earn_vault_setup_batch.py build --addresses deploy/earn-addresses.json
      --call-desk 0x... --vault 0x... --adapter 0x...
      --put-desk 0x... --buyer 0xWINTERMUTE --depositor 0xUSER [--depositor 0xSECOND_USER]
    python3 tools/earn_vault_setup_batch.py decode --addresses deploy/earn-addresses.json deploy/safe/<file>.json

The call desk address must be a NEW strict-physical desk. The legacy
CoveredCallDesk has a NetShare backstop and is intentionally refused. The
operator-supplied addresses file is deliberately not part of this source repo.
"""

import argparse
import datetime
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import earn_safe_codec as build

KNOWN_LEGACY_CALL_DESK = "0xb28Aa6ADE3f4504B5554dc1BDb23EBa2FE1afD87"
METHODS = {
    "list": ("list(address,address,bool)", [("underlying", "address"), ("feed", "address"), ("enabled", "bool")]),
    "setWriter": ("setWriter(address,bool)", [("writer", "address"), ("allowed", "bool")]),
    "setBuyer": ("setBuyer(address,bool)", [("buyer", "address"), ("allowed", "bool")]),
    "setPutDesk": ("setPutDesk(address)", [("newDesk", "address")]),
    "setSwapAdapter": ("setSwapAdapter(address)", [("adapter", "address")]),
    "setEligible": ("setEligible(address,bool)", [("account", "address"), ("allowed", "bool")]),
}


def load_addresses(path):
    """Load a locally maintained, independently verified Robinhood Chain address file."""
    try:
        addresses = json.loads(Path(path).read_text())
        if addresses["chainId"] != 4663:
            raise build.Refuse("addresses file does not describe Robinhood Chain 4663")
        valid_address(addresses["safe"], "Safe")
        valid_address(addresses["stocks"]["NVDA"], "RHNVDA")
        valid_address(addresses["rhNvdaFeed"], "RHNVDA feed")
        valid_address(addresses["legacyCallDesk"], "legacy CoveredCallDesk")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise build.Refuse(f"invalid addresses file {path}: {error}") from error
    return addresses


def valid_address(value, name):
    address = build.checksum_addr(value)
    if address.lower() == build.ZERO:
        raise build.Refuse(f"{name} cannot be the zero address")
    return address


def make_call(to, name, values):
    signature, inputs = METHODS[name]
    if len(values) != len(inputs):
        raise build.Refuse(f"{name}: wrong argument count")
    data = build.cast("calldata", signature, *(build._arg(v) for v in values)).lower()
    if not data.startswith(build.selector(signature)):
        raise build.Refuse(f"{name}: cast returned a foreign selector")
    return {"to": to, "name": name, "values": list(values), "data": data}


def plan(call_desk, vault, adapter, put_desk, buyer, depositors, addresses=None):
    if addresses is None:
        raise build.Refuse("pass a verified --addresses file")
    safe = valid_address(addresses["safe"], "Safe")
    stock = valid_address(addresses["stocks"]["NVDA"], "RHNVDA")
    feed = valid_address(addresses["rhNvdaFeed"], "RHNVDA feed")
    call_desk = valid_address(call_desk, "call desk")
    vault = valid_address(vault, "vault")
    adapter = valid_address(adapter, "adapter")
    put_desk = valid_address(put_desk, "put desk")
    buyer = valid_address(buyer, "buyer")
    depositors = [valid_address(d, "depositor") for d in depositors]
    if not depositors:
        raise build.Refuse("provide at least one --depositor for the initial controlled deposit")
    legacy = valid_address(addresses["legacyCallDesk"], "legacy CoveredCallDesk")
    if call_desk.lower() in {KNOWN_LEGACY_CALL_DESK.lower(), legacy.lower()}:
        raise build.Refuse("the existing CoveredCallDesk can NetShare-settle a Physical call at backstop; deploy a strict-physical desk")
    roles = {"Safe": safe, "RHNVDA": stock, "feed": feed, "call desk": call_desk, "vault": vault,
             "adapter": adapter, "put desk": put_desk, "buyer": buyer}
    if len({a.lower() for a in roles.values()}) != len(roles):
        raise build.Refuse("a contract, Safe, token, feed, or buyer address was reused for another role")
    if len({d.lower() for d in depositors}) != len(depositors):
        raise build.Refuse("duplicate depositor address")
    if any(d.lower() in {call_desk.lower(), vault.lower(), adapter.lower(), put_desk.lower(), stock.lower(), feed.lower()}
           for d in depositors):
        raise build.Refuse("a depositor is one of the protocol contract, token, or feed addresses")

    return [
        make_call(call_desk, "list", [stock, feed, True]),
        make_call(call_desk, "setWriter", [vault, True]),
        make_call(call_desk, "setBuyer", [buyer, True]),
        make_call(put_desk, "list", [stock, feed, True]),
        make_call(put_desk, "setWriter", [vault, True]),
        make_call(put_desk, "setBuyer", [buyer, True]),
        make_call(vault, "setPutDesk", [put_desk]),
        make_call(vault, "setSwapAdapter", [adapter]),
        make_call(vault, "setBuyer", [buyer, True]),
        *(make_call(vault, "setEligible", [d, True]) for d in depositors),
    ]


def transaction(call):
    _, inputs = METHODS[call["name"]]
    method_inputs = [{"internalType": ty, "name": name, "type": ty} for name, ty in inputs]
    displayed_values = {name: build._arg(value) for (name, _), value in zip(inputs, call["values"])}
    return {"to": call["to"], "value": "0", "data": call["data"],
            "contractMethod": {"inputs": method_inputs, "name": call["name"], "payable": False},
            "contractInputsValues": displayed_values}


def bundle(calls, addresses=None):
    if addresses is None:
        raise build.Refuse("pass a verified --addresses file")
    safe = valid_address(addresses["safe"], "Safe")
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    result = {
        "version": "1.0", "chainId": str(addresses["chainId"]), "createdAt": int(time.time() * 1000),
        "meta": {
            "name": f"EARN-VAULT setup {now}",
            "description": "RHNVDA Earn vault initialization. Offline calldata only; verify deployed addresses and simulate in Safe before signing.",
            "txBuilderVersion": "1.16.5", "createdFromSafeAddress": safe, "createdFromOwnerAddress": "",
        },
        "transactions": [transaction(call) for call in calls],
    }
    result["meta"]["checksum"] = build.safe_checksum(result)
    return result


def describe(calls):
    for index, call in enumerate(calls, 1):
        _, inputs = METHODS[call["name"]]
        arguments = ", ".join(f"{name}={build._arg(value)}" for (name, _), value in zip(inputs, call["values"]))
        print(f"  [{index}/{len(calls)}] {call['to']}  {call['name']}({arguments})")


def decode(path, addresses):
    batch = json.loads(Path(path).read_text())
    if str(batch.get("chainId")) != str(addresses["chainId"]):
        raise build.Refuse("batch chain ID differs from Robinhood Chain")
    meta = batch.get("meta") or {}
    if str(meta.get("createdFromSafeAddress", "")).lower() != addresses["safe"].lower():
        raise build.Refuse("batch Safe differs from production Safe")
    if meta.get("checksum") != build.safe_checksum(batch):
        raise build.Refuse("batch checksum does not match contents")
    txs = batch.get("transactions") or []
    if len(txs) < 10:
        raise build.Refuse("setup must contain both desks, vault configuration, and at least one depositor")
    expected_names = ["list", "setWriter", "setBuyer", "list", "setWriter", "setBuyer",
                      "setPutDesk", "setSwapAdapter", "setBuyer"] + ["setEligible"] * (len(txs) - 9)
    calls = []
    for index, (tx, name) in enumerate(zip(txs, expected_names), 1):
        signature, _ = METHODS[name]
        data = str(tx.get("data", "")).lower()
        if not data.startswith(build.selector(signature)) or str(tx.get("value")) != "0":
            raise build.Refuse(f"transaction {index}: unexpected selector or nonzero value")
        decoded = json.loads(build.cast("calldata-decode", "--json", signature, data))
        values = [build._norm(value) for value in decoded]
        call = make_call(valid_address(tx.get("to"), f"transaction {index} target"), name, values)
        if call["data"] != data or transaction(call) != tx:
            raise build.Refuse(f"transaction {index}: calldata or Safe display fields do not round-trip")
        calls.append(call)
    canonical = plan(calls[0]["to"], calls[6]["to"], calls[7]["values"][0], calls[3]["to"],
                     calls[2]["values"][0], [call["values"][0] for call in calls[9:]], addresses)
    if [transaction(call) for call in canonical] != txs:
        raise build.Refuse("batch does not match the required Earn vault setup sequence")
    describe(calls)
    print(f"\ntransactions   {len(txs)}\nmeta.checksum  {meta['checksum']}\nbatch digest   {build.batch_digest(txs)}")
    print("VERDICT        internally consistent; compare every address with independently verified deployments before signing")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("build")
    create.add_argument("--addresses", type=Path, required=True, help="verified local Robinhood Chain addresses JSON")
    for flag in ("call-desk", "vault", "adapter", "put-desk", "buyer"):
        create.add_argument(f"--{flag}", required=True)
    create.add_argument("--depositor", action="append", required=True, help="repeat for each approved initial depositor")
    create.add_argument("--output", type=Path, help="default: timestamped file under deploy/safe/")
    create.add_argument("--check", action="store_true", help="print calls and digest without writing a file")
    inspect = subparsers.add_parser("decode")
    inspect.add_argument("--addresses", type=Path, required=True, help="same verified addresses JSON used to build")
    inspect.add_argument("file", type=Path)
    args = parser.parse_args(argv)
    addresses = load_addresses(args.addresses)
    if args.command == "decode":
        decode(args.file, addresses)
        return 0
    calls = plan(args.call_desk, args.vault, args.adapter, args.put_desk, args.buyer, args.depositor, addresses)
    result = bundle(calls, addresses)
    describe(calls)
    print(f"\ntransactions   {len(calls)}\nmeta.checksum  {result['meta']['checksum']}\nbatch digest   {build.batch_digest(result['transactions'])}")
    if args.check:
        print("CHECK ONLY: no file written; no chain read or transaction sent")
        return 0
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or ROOT / "deploy" / "safe" / f"{timestamp}-earn-vault-setup.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as file:
        file.write(json.dumps(result, indent=2) + "\n")
    print(f"wrote          {output}\nNEXT           decode this file, verify deployed addresses, then simulate in Safe UI; nothing was sent")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except build.Refuse as error:
        sys.exit(f"REFUSED: {error}")
