#!/usr/bin/env python3
"""桌面套件相容修補器回歸測試。"""

import importlib.util
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
PATCHER_PATH = ROOT / "lib" / "tools" / "common" / "patch-desktop-package-compatibility.py"


def load_patcher():
    spec = importlib.util.spec_from_file_location("desktop_package_compatibility", PATCHER_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class DesktopPackageCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.patcher = load_patcher()

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.common_yaml = Path(self.temporary_directory.name) / "common.yaml"
        self.document = {
            "tiers": {
                "minimal": {
                    "packages": [
                        "pipewire-libcamera",
                        "gstreamer1.0-libcamera",
                        "v4l-utils",
                    ]
                },
                "mid": {
                    "packages": [
                        "mesa-utils",
                        "glmark2-wayland",
                        "glmark2-es2-wayland",
                        "glmark2-x11",
                        "glmark2-es2-x11",
                    ]
                },
            }
        }
        self.common_yaml.write_text(
            yaml.safe_dump(self.document, sort_keys=False),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_jammy_uses_available_package_names(self) -> None:
        changed = self.patcher.patch_common_yaml(self.common_yaml, "jammy")
        self.assertTrue(changed)

        patched = yaml.safe_load(self.common_yaml.read_text(encoding="utf-8"))
        all_packages = {
            package
            for tier in patched["tiers"].values()
            for package in tier["packages"]
        }
        self.assertTrue(self.patcher.JAMMY_REMOVE.isdisjoint(all_packages))
        self.assertIn("glmark2", patched["tiers"]["mid"]["packages"])
        self.assertIn("glmark2-es2", patched["tiers"]["mid"]["packages"])
        self.assertIn("v4l-utils", patched["tiers"]["minimal"]["packages"])
        self.assertIn("glmark2-wayland", patched["tiers"]["mid"]["packages"])

    def test_patch_is_idempotent(self) -> None:
        self.assertTrue(self.patcher.patch_common_yaml(self.common_yaml, "jammy"))
        first = self.common_yaml.read_bytes()
        self.assertFalse(self.patcher.patch_common_yaml(self.common_yaml, "jammy"))
        self.assertEqual(first, self.common_yaml.read_bytes())

    def test_other_releases_are_not_modified(self) -> None:
        before = self.common_yaml.read_bytes()
        self.assertFalse(self.patcher.patch_common_yaml(self.common_yaml, "noble"))
        self.assertEqual(before, self.common_yaml.read_bytes())


if __name__ == "__main__":
    unittest.main()
