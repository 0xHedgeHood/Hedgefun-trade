import importlib.util
from pathlib import Path
import tempfile
import shutil
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("native_testnet_config", ROOT / "tools/native_testnet_config.py")
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


class NativeConfigTest(unittest.TestCase):
    def test_only_permissions_change_and_source_never_changes(self):
        source = (ROOT / "foundry.toml").read_text()
        target = TOOL.render(source)
        parsed = tomllib.loads(target)["profile"]["default"]
        self.assertEqual(parsed["fs_permissions"], [dict(access=access, path=path) for access, path in TOOL.PERMISSIONS])
        self.assertEqual(len(parsed["fs_permissions"]), 9)
        self.assertNotIn(dict(access="read-write", path="./deploy/testnet-v2-fees.json"), parsed["fs_permissions"])
        self.assertEqual((ROOT / "foundry.toml").read_text(), source)
        self.assertEqual(parsed["optimizer_runs"], 1)

    def test_malformed_or_changed_compiler_configuration_is_rejected(self):
        source = (ROOT / "foundry.toml").read_text()
        with self.assertRaises(ValueError):
            TOOL.render(source.replace('optimizer_runs = 1', 'optimizer_runs = 2'))
        with self.assertRaises(ValueError):
            TOOL.render(source.replace('fs_permissions = [', 'permissions = ['))

    def test_temporary_output_contains_no_directory_write_or_wallet_access(self):
        source = (ROOT / "foundry.toml").read_text()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "foundry.toml").write_text(source)
            target = TOOL.create(root)
            try:
                self.assertNotEqual(target.parent, root)
                self.assertEqual(target.name, "foundry.toml")
                for name in ("src", "test", "script", "lib", "deploy", "data", "out", "cache", "broadcast"):
                    self.assertTrue((target.parent / name).is_symlink())
                    self.assertEqual((target.parent / name).readlink(), root.resolve() / name)
                permissions = tomllib.loads(target.read_text())["profile"]["default"]["fs_permissions"]
                self.assertTrue(all(row["path"].startswith("./deploy/testnet-v2-") or row["path"] == "./lib/v4-core/test/bin/v3Factory.bytecode" for row in permissions))
                self.assertEqual((root / "foundry.toml").read_text(), source)
            finally:
                shutil.rmtree(target.parent)


if __name__ == "__main__":
    unittest.main()
