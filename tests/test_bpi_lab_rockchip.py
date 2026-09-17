#!/usr/bin/env python3
"""Rockchip 原配腳本差異、九板路由及失敗條件。"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_bpi_lab_extlinux import FixtureCase, RELEASE, core, rockchip, legacy, overlay, template


class RockchipTests(FixtureCase):
    def test_all_nine_boards(self):
        self.assertEqual(len(rockchip.POLICIES), 9)
        for board in rockchip.POLICIES:
            with self.subTest(board=board):
                self.select(board)
                m = self.prepare()
                self.assertEqual(m["status"], "prepared", m["blockers"])
                self.assertFalse(m["hardware_validated"])
                rockchip.validate_template(m, template=template(m), artifact_root=self.output)

    def test_console_and_forge1_ramdisk(self):
        for board, tty in (("bpi-m5pro", "ttyS0,1500000"), ("bpi-m7", "ttyS2,1500000"), ("bpi-forge1", "ttyFIQ0,1500000n8")):
            with self.subTest(board=board):
                self.select(board)
                m = self.prepare()
                self.assertIn("console=" + tty, m["bootargs_template"])
                if board == "bpi-forge1":
                    self.assertEqual(m["runtime_requirements"]["ramdisk_addr_r"], "0x02800000")
                    self.assertIn("earlyprintk", m["bootargs_template"])
                else:
                    self.assertEqual(m["runtime_requirements"]["kaslrseed"], "original-script")

    def test_vendor_overlay_fallback(self):
        self.select("bpi-m5pro")
        self.files["/boot/armbianEnv.txt"] += b"overlays=lab\n"
        self.files["/boot/dtb/rockchip/overlay/lab.dtbo"] = overlay()
        m = self.prepare()
        self.assertEqual(m["status"], "prepared", m["blockers"])
        self.assertEqual(m["files"]["overlay_00"]["path"], "/boot/dtb/rockchip/overlay/lab.dtbo")

    def test_m7_current_script_is_not_vendor_script(self):
        self.select("bpi-m7")
        cmd = (core.ROOT / "config/bootscripts/boot-rockchip64.cmd").read_bytes()
        self.files["/boot/boot.cmd"] = cmd
        self.files["/boot/boot.scr"] = legacy(cmd, script=True)
        self.files["/boot/armbianEnv.txt"] += b"extraargs=cma=256M\n"
        m = self.prepare()
        self.assertEqual(m["status"], "prepared", m["blockers"])
        self.assertEqual(m["script_profile"], "rockchip64")
        self.assertIn("cma=256M", m["bootargs_template"])

    def test_rockchip64_does_not_have_unprefixed_fallback(self):
        self.select("bpi-p2pro")
        self.files["/boot/armbianEnv.txt"] += b"overlays=lab\n"
        self.files["/boot/dtb/rockchip/overlay/lab.dtbo"] = overlay()
        self.blocked("overlay_missing")

    def test_script_mismatch_and_custom_script(self):
        self.select("bpi-m7")
        self.files["/boot/boot.cmd"] += "\n# 測試\n".encode()
        self.blocked("boot_script")
        self.files["/boot/boot.scr"] = legacy(self.files["/boot/boot.cmd"], script=True)
        self.blocked("boot_script")

    def test_fixup_and_unknown_environment_block(self):
        self.select("bpi-m7")
        self.files["/boot/fixup.scr"] = b""
        self.blocked("unsupported_fixup")
        del self.files["/boot/fixup.scr"]
        self.files["/boot/armbianEnv.txt"] += b"param_test=1\n"
        self.blocked("unsupported_env")

    def test_no_allwinner_board_policy(self):
        with self.assertRaises(core.Error):
            rockchip.prepare(self.read, board="bpi-m1", kernel_release=RELEASE, output=self.root / "wrong")
        self.assertFalse((self.root / "wrong").exists())


if __name__ == "__main__":
    unittest.main()
