#!/usr/bin/env python3
"""MediaTek 七板、MT7623 特有 rootfs 與 DTB 路徑。"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_bpi_lab_extlinux import FixtureCase, mediatek, template, tree


class MediaTekTests(FixtureCase):
    def test_all_seven_boards(self):
        self.assertEqual(len(mediatek.POLICIES), 7)
        for board in mediatek.POLICIES:
            with self.subTest(board=board):
                self.select(board)
                m = self.prepare()
                self.assertEqual(m["status"], "prepared", m["blockers"])
                mediatek.validate_template(m, template=template(m), artifact_root=self.output)

    def test_r2_actual_script_dtb_path(self):
        self.select("bpi-r2")
        m = self.prepare()
        self.assertEqual(m["files"]["dtb"]["path"], "/boot/dtb/mt7623n-bananapi-bpi-r2.dtb")
        self.assertIn("console=ttyS2,115200n1", m["bootargs_template"])
        self.assertEqual(m["runtime_requirements"]["filesystem"], "ext4")

    def test_r2_does_not_ignore_rootfstype(self):
        self.select("bpi-r2")
        self.files["/boot/armbianEnv.txt"] += b"rootfstype=btrfs\n"
        self.blocked("unused_env")

    def test_r2_environment_dtb_path_is_not_rewritten(self):
        for name in ("mediatek/mt7623n-bananapi-bpi-r2", "mediatek/mt7623n-bananapi-bpi-r2.dtb"):
            with self.subTest(name=name):
                self.select("bpi-r2")
                self.files["/boot/armbianEnv.txt"] = self.files["/boot/armbianEnv.txt"].replace(
                    b"fdtfile=mt7623n-bananapi-bpi-r2.dtb", ("fdtfile=" + name).encode())
                self.files["/boot/dtb/" + name] = self.files.pop("/boot/dtb/" + self.policy["dtb"])
                m = self.prepare()
                self.assertEqual(m["status"], "prepared", m["blockers"])
                self.assertEqual(m["files"]["dtb"]["path"], "/boot/dtb/" + name)
                self.assertEqual(m["runtime_requirements"]["fdtfile"], name)
                self.assertFalse(m["checks"]["dtb_selection"]["rewritten"])

    def test_r2_missing_selected_dtb_does_not_use_script_default(self):
        self.select("bpi-r2")
        self.files["/boot/armbianEnv.txt"] = self.files["/boot/armbianEnv.txt"].replace(
            b"fdtfile=mt7623n-bananapi-bpi-r2.dtb", b"fdtfile=mediatek/mt7623n-bananapi-bpi-r2")
        m = self.blocked("missing_file")
        self.assertNotIn("dtb", m["files"])
        self.assertFalse(any(r["path"] == "/boot/dtb/" + self.policy["dtb"] for r in m["reads"]))

    def test_r2_alias_still_requires_board_identity(self):
        self.select("bpi-r2")
        name = "mediatek/mt7623n-bananapi-bpi-r2"
        self.files["/boot/armbianEnv.txt"] = self.files["/boot/armbianEnv.txt"].replace(
            b"fdtfile=mt7623n-bananapi-bpi-r2.dtb", ("fdtfile=" + name).encode())
        self.files["/boot/dtb/" + name] = tree({**self.policy, "model": "BPI"})
        self.blocked("dtb_identity")

    def test_r2_unsafe_and_unrelated_dtb_names(self):
        for name, code in (("../mt7623n-bananapi-bpi-r2.dtb", "image_path"),
                           ("mediatek/mt7986a-bananapi-bpi-r3.dtb", "dtb_name")):
            with self.subTest(name=name):
                self.select("bpi-r2")
                self.files["/boot/armbianEnv.txt"] = self.files["/boot/armbianEnv.txt"].replace(
                    b"fdtfile=mt7623n-bananapi-bpi-r2.dtb", ("fdtfile=" + name).encode())
                self.blocked(code)

    def test_r2_default_only_without_environment_override(self):
        self.select("bpi-r2")
        self.files["/boot/armbianEnv.txt"] = self.files["/boot/armbianEnv.txt"].replace(
            b"fdtfile=mt7623n-bananapi-bpi-r2.dtb\n", b"")
        m = self.prepare()
        self.assertEqual(m["status"], "prepared", m["blockers"])
        self.assertEqual(m["checks"]["dtb_selection"]["source"], "boot.cmd")

    def test_r2_does_not_apply_overlays_or_extraargs(self):
        for value in (b"overlays=spi\n", b"extraargs=audit=1\n"):
            with self.subTest(value=value):
                self.select("bpi-r2")
                self.files["/boot/armbianEnv.txt"] += value
                self.blocked("unsupported_env")

    def test_filogic_never_asks_for_nonexistent_bootscript(self):
        m = self.prepare()
        self.assertEqual(m["entry"]["kind"], "extlinux")
        self.assertNotIn("config/bootscripts/boot-filogic.cmd", m["sources"])


if __name__ == "__main__":
    unittest.main()
