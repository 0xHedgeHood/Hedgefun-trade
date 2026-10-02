#!/usr/bin/env python3
"""Create a temporary Foundry config with exact, narrow native venue file permissions.

The tracked foundry.toml and every compiler setting remain unchanged. The installed
Foundry release ignores FOUNDRY_FS_PERMISSIONS, so callers pass this file explicitly.
"""
from pathlib import Path
import re
import tempfile
import tomllib

PERMISSIONS = [
    ("read", "./deploy/testnet-v2-fees.json"),
    ("read", "./deploy/testnet-v2-creator.json"),
    ("read", "./lib/v4-core/test/bin/v3Factory.bytecode"),
    *[("read-write", f"./deploy/testnet-v2-native-market.{phase}.{kind}.json")
      for phase in ("init", "poke", "activate") for kind in ("candidate", "dryrun")],
]


def render(source: str) -> str:
    pattern = r"(?m)^fs_permissions\s*=\s*\[\s*\n.*?^\]\s*$"
    matches = list(re.finditer(pattern, source, re.DOTALL))
    if len(matches) != 1:
        raise ValueError("Expected exactly one standalone fs_permissions array")
    rows = "\n".join(f'  {{ access = "{access}", path = "{path}" }},' for access, path in PERMISSIONS)
    replacement = "fs_permissions = [\n" + rows + "\n]"
    match = matches[0]
    result = source[:match.start()] + replacement + source[match.end():]
    before, after = tomllib.loads(source), tomllib.loads(result)
    old_profile = before["profile"]["default"]
    new_profile = after["profile"]["default"]
    wanted = {"solc_version": "0.8.26", "optimizer": True, "optimizer_runs": 1,
              "bytecode_hash": "none", "evm_version": "cancun"}
    if any(old_profile.get(key) != value for key, value in wanted.items()):
        raise ValueError("Unreviewed compiler settings")
    old_profile.pop("fs_permissions")
    new_profile.pop("fs_permissions")
    if before != after:
        raise ValueError("Native config changed a setting other than fs_permissions")
    return result


def create(root: Path) -> Path:
    root = root.resolve()
    source = (root / "foundry.toml").read_text()
    text = render(source)
    # Foundry requires the basename foundry.toml and resolves paths from its
    # parent. Symlink project folders instead of rewriting compiler source-unit
    # names, remappings or library settings. File permissions remain exact.
    directory = Path(tempfile.mkdtemp(prefix="hedgefun-native-config-"))
    for name in ("src", "test", "script", "lib", "deploy", "data", "out", "cache", "broadcast"):
        if name in ("out", "cache", "broadcast"):
            (root / name).mkdir(exist_ok=True)
        (directory / name).symlink_to(root / name, target_is_directory=True)
    target = directory / "foundry.toml"
    target.write_text(text)
    target.chmod(0o600)
    return target


if __name__ == "__main__":
    print(create(Path(__file__).resolve().parents[1]))
