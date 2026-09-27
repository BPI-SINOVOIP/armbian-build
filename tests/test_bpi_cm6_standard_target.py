"""目標建置相依移除的套件身分回歸；不執行 APT 或硬體。"""
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "cm6_standard_target", Path(__file__).resolve().parents[1] / "tools/bpi_cm6_standard_target.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class RemovalPlanTests(unittest.TestCase):
    def setUp(self):
        self.packages = {
            "libcrypt-dev:riscv64": {"architecture": "riscv64", "version": "1"},
            "build-essential": {"architecture": "riscv64", "version": "1"},
            "libcrypt1:riscv64": {"architecture": "riscv64", "version": "1"},
            "python3-bpi-cm6-gpio": {"architecture": "riscv64", "version": "1"},
        }
        self.added = {"libcrypt-dev:riscv64", "build-essential"}

    def test_unqualified_apt_name_maps_to_unique_dpkg_identity(self):
        text = "Purg libcrypt-dev [1]\nRemv build-essential [1]\n"
        result = MOD.removal_plan(text, self.packages, self.added)
        self.assertEqual(result["plan"], text)
        self.assertEqual(result["resolved_packages"], {
            "libcrypt-dev": "libcrypt-dev:riscv64", "build-essential": "build-essential"})
        self.assertEqual(set(result["removed_build_packages"]), self.added)

    def test_explicit_architecture_can_resolve_unqualified_dpkg_name(self):
        result = MOD.removal_plan("Purg build-essential:riscv64 [1]\n", self.packages, self.added)
        self.assertEqual(result["removed_build_packages"], ["build-essential"])

    def test_original_runtime_and_new_product_are_protected(self):
        for name in ("libcrypt1", "libcrypt1:riscv64", "python3-bpi-cm6-gpio"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "原套件或執行期"):
                MOD.removal_plan("Purg " + name + " [1]\n", self.packages, self.added)

    def test_unknown_package_or_wrong_architecture_is_rejected(self):
        for name in ("unknown-package", "libcrypt-dev:amd64"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "未知或架構不唯一"):
                MOD.removal_plan("Purg " + name + " [1]\n", self.packages, self.added)

    def test_ambiguous_unqualified_multiarch_name_is_rejected(self):
        self.packages["libcrypt-dev:amd64"] = {"architecture": "amd64", "version": "1"}
        with self.assertRaisesRegex(ValueError, "未知或架構不唯一"):
            MOD.removal_plan("Purg libcrypt-dev [1]\n", self.packages, self.added)
        result = MOD.removal_plan("Purg libcrypt-dev:riscv64 [1]\n", self.packages, self.added)
        self.assertEqual(result["removed_build_packages"], ["libcrypt-dev:riscv64"])

    def test_install_or_upgrade_is_rejected(self):
        for text in ("Inst libcrypt1 (2 repository [riscv64])\n", "Inst libcrypt1 [1] (2 repository [riscv64])\n"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "不可安裝或升降級"):
                MOD.removal_plan(text, self.packages, self.added)

    def test_empty_or_invalid_removal_name_is_rejected(self):
        for text in ("Purg \n", "Remv libcrypt-dev:any:invalid [1]\n"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                MOD.removal_plan(text, self.packages, self.added)


if __name__ == "__main__":
    unittest.main()
