import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
BSP_COMMON = ROOT / "packages" / "bsp" / "common" / "usr" / "lib" / "sysctl.d"
BSP_BUILDER = ROOT / "lib" / "functions" / "bsp" / "armbian-bsp-cli-deb.sh"


class BspSysctlPackageTests(unittest.TestCase):
    def test_uses_armbian_specific_filename(self):
        self.assertFalse(
            (BSP_COMMON / "50-default.conf").exists(),
            "BSP 不得覆寫由 Jammy systemd 擁有的 50-default.conf",
        )
        self.assertTrue(
            (BSP_COMMON / "60-armbian-defaults.conf").is_file(),
            "BSP 必須使用不與發行版套件衝突的 Armbian 專屬檔名",
        )

    def test_keeps_required_sysctl_defaults(self):
        content = (BSP_COMMON / "60-armbian-defaults.conf").read_text()
        required = (
            "kernel.sysrq = 438",
            "-net.ipv4.ping_group_range = 0 2147483647",
            "-net.core.default_qdisc = fq_codel",
            "fs.protected_regular = 2",
            "vm.max_map_count = 1048576",
        )
        for setting in required:
            with self.subTest(setting=setting):
                self.assertIn(setting, content)

    def test_keeps_virtual_package_contract(self):
        builder = BSP_BUILDER.read_text()
        self.assertIn("Conflicts: linux-sysctl-defaults", builder)
        self.assertIn("Provides: armbian-bsp-cli, linux-sysctl-defaults", builder)


if __name__ == "__main__":
    unittest.main()
