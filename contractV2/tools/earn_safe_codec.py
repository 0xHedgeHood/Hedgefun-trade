"""Offline Safe batch encoding helpers for the RHNVDA Earn setup tool.

ABI encoding and keccak use Foundry's local ``cast``. No chain request, signing,
or broadcast operation is available here.
"""

import json
import re
import subprocess

ZERO = "0x" + "00" * 20
ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class Refuse(Exception):
    """A reason to refuse producing or accepting a Safe batch."""


def cast(*args, stdin=None):
    try:
        result = subprocess.run(["cast", *args], capture_output=True, text=True, input=stdin)
    except FileNotFoundError as error:
        raise Refuse("Foundry's cast is required for offline ABI encoding") from error
    if result.returncode != 0:
        raise Refuse(f"cast {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout.strip()


_SELECTORS = {}


def selector(signature):
    if signature not in _SELECTORS:
        _SELECTORS[signature] = cast("sig", signature).lower()
    return _SELECTORS[signature]


def checksum_addr(value):
    if not isinstance(value, str) or not ADDR_RE.fullmatch(value):
        raise Refuse(f"not an address: {value!r}")
    return cast("to-check-sum-address", value)


def _arg(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "(" + ",".join(_arg(item) for item in value) + ")"
    return str(value)


def _norm(value):
    if isinstance(value, list):
        return [_norm(item) for item in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and ADDR_RE.fullmatch(value):
        return checksum_addr(value)
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def _keccak_text(value):
    # Explicit UTF-8 hex avoids cast interpreting text that begins with 0x as bytes.
    digest = cast("keccak", stdin="0x" + value.encode("utf-8").hex()).lower()
    if value == "abc" and digest != "0x4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45":
        raise Refuse("cast keccak is not keccak256")
    return digest


def _serialize(value):
    """Safe Transaction Builder's serializeJSONObject format."""
    if isinstance(value, list):
        return "[" + ",".join(_serialize(item) for item in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value.keys())
        return "{" + json.dumps(keys, separators=(",", ":"), ensure_ascii=False) + "".join(
            _serialize(value[key]) + "," for key in keys
        ) + "}"
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def safe_checksum(batch):
    meta = {key: value for key, value in batch["meta"].items() if key != "checksum"}
    meta["name"] = None
    return _keccak_text(_serialize({**batch, "meta": meta}))


def batch_digest(transactions):
    return _keccak_text("|".join(
        f"{tx['to'].lower()}:{tx['data'].lower()}" for tx in transactions
    ))
