#!/usr/bin/env python3
"""K1 官方 GPT 擴容的拒絕條件與分區保留回歸；不存取真實媒體。"""

import copy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location("bpi_k1_grow_rootfs", Path(__file__).resolve().parents[1] / "tools/bpi_k1_grow_rootfs.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class GrowRootfsTests(unittest.TestCase):
    def setUp(self):
        self.marker = {"board": "bpi-cm6", "storage": "emmc", "layout_version": "bianbu-v2.3",
                       "root_uuid": "11111111-1111-4111-8111-111111111111",
                       "boot_uuid": "22222222-2222-4222-8222-222222222222"}
        self.mounts = {
            "/": {"target": "/", "source": "/dev/mmcblk0p6", "fstype": "ext4", "uuid": self.marker["root_uuid"]},
            "/boot": {"target": "/boot", "source": "/dev/mmcblk0p5", "fstype": "ext4", "uuid": self.marker["boot_uuid"]},
        }
        self.table = {"label": "gpt", "device": "/dev/mmcblk0", "unit": "sectors", "sectorsize": 512,
                      "firstlba": 34, "lastlba": 999999, "id": "fixed-guid",
                      "partitions": [{"node": f"/dev/mmcblk0p{n}", "name": name, "start": start, "size": size}
                                     for n, (name, start, size) in enumerate(MOD.PREFIX, 1)]}
        self.table["partitions"].append({"node": "/dev/mmcblk0p6", "name": "rootfs", "start": 532480, "size": 10000,
                                         "uuid": "33333333-3333-4333-8333-333333333333"})

    def validate(self):
        return MOD.validate_layout(self.marker, self.mounts, self.table)

    def test_exact_official_layout_is_accepted(self):
        self.assertEqual(self.validate()["root_bytes"], 5120000)

    def test_wrong_root_uuid_is_rejected_before_commands(self):
        self.mounts["/"]["uuid"] = self.marker["boot_uuid"]
        with mock.patch.object(MOD, "run") as command, self.assertRaises(ValueError):
            MOD.grow(self.marker, self.mounts, self.table)
        command.assert_not_called()

    def test_wrong_boot_uuid_is_rejected(self):
        self.mounts["/boot"]["uuid"] = self.marker["root_uuid"]
        with self.assertRaises(ValueError): self.validate()

    def test_mbr_is_rejected(self):
        self.table["label"] = "dos"
        with self.assertRaises(ValueError): self.validate()

    def test_other_root_partition_is_rejected(self):
        for source in ("/dev/mmcblk0p1", "/dev/sda6", "/dev/mapper/root", "overlay"):
            with self.subTest(source=source):
                self.mounts["/"]["source"] = source
                with self.assertRaises(ValueError): self.validate()

    def test_boot_on_other_media_is_rejected(self):
        self.mounts["/boot"]["source"] = "/dev/mmcblk1p5"
        with self.assertRaises(ValueError): self.validate()

    def test_missing_boot_mount_is_rejected(self):
        del self.mounts["/boot"]
        with self.assertRaises(ValueError): self.validate()

    def test_wrong_root_name_and_start_are_rejected(self):
        self.table["partitions"][5]["name"] = "other"
        with self.assertRaises(ValueError): self.validate()
        self.table["partitions"][5]["name"] = "rootfs"
        self.table["partitions"][5]["start"] += 1
        with self.assertRaises(ValueError): self.validate()

    def test_shifted_bootfs_is_rejected(self):
        self.table["partitions"][4]["start"] += 1
        with self.assertRaises(ValueError): self.validate()

    def test_extra_partition_is_rejected(self):
        self.table["partitions"].append({"name": "data"})
        with self.assertRaises(ValueError): self.validate()

    def test_wrong_sector_size_is_rejected(self):
        self.table["sectorsize"] = 4096
        with self.assertRaises(ValueError): self.validate()

    def test_dry_run_never_executes_mutating_commands(self):
        with mock.patch.object(MOD, "run") as command:
            result = MOD.grow(self.marker, self.mounts, self.table, dry_run=True)
        command.assert_not_called()
        self.assertEqual(result["status"], "checked_without_changes")

    def test_growth_cannot_change_partition_identity(self):
        after = copy.deepcopy(self.table)
        after["partitions"][5]["size"] += 1000
        MOD.validate_growth(self.table, after)
        after["partitions"][5]["uuid"] = "changed"
        with self.assertRaises(ValueError): MOD.validate_growth(self.table, after)

    def test_growth_cannot_move_boot_or_shrink_root(self):
        after = copy.deepcopy(self.table)
        after["partitions"][4]["start"] += 1
        with self.assertRaises(ValueError): MOD.validate_growth(self.table, after)
        after = copy.deepcopy(self.table)
        after["partitions"][5]["size"] -= 1
        with self.assertRaises(ValueError): MOD.validate_growth(self.table, after)

    def test_nochange_still_finishes_filesystem_growth(self):
        result = subprocess.CompletedProcess([], 1, "NOCHANGE: partition 6", "")
        with mock.patch.object(MOD.Path, "stat", return_value=mock.Mock(st_mode=stat.S_IFBLK)), \
             mock.patch.object(MOD.Path, "open", side_effect=lambda *a, **kw: io.BytesIO(bytes(80))), \
             mock.patch.object(MOD, "run", return_value=result), \
             mock.patch.object(MOD, "checked", side_effect=[json.dumps({"partitiontable": self.table}), "5120000", ""]) as checked:
            report = MOD.grow(self.marker, self.mounts, self.table)
        self.assertFalse(report["partition_changed"])
        self.assertEqual(checked.call_args_list[-1].args[0], ["resize2fs", "/dev/mmcblk0p6"])
        self.assertFalse(any(call.args[0][0] == "partx" for call in checked.call_args_list))

    def test_stale_kernel_geometry_uses_partx_before_resize(self):
        after = copy.deepcopy(self.table)
        after["partitions"][5]["size"] += 1000
        result = subprocess.CompletedProcess([], 0, "CHANGED: partition 6", "")
        with mock.patch.object(MOD.Path, "stat", return_value=mock.Mock(st_mode=stat.S_IFBLK)), \
             mock.patch.object(MOD.Path, "open", side_effect=lambda *a, **kw: io.BytesIO(bytes(80))), \
             mock.patch.object(MOD, "run", return_value=result), \
             mock.patch.object(MOD, "checked", side_effect=[json.dumps({"partitiontable": after}), "5120000", "", "5632000", ""]) as checked:
            MOD.grow(self.marker, self.mounts, self.table)
        self.assertEqual(checked.call_args_list[2].args[0], ["partx", "--update", "--nr", "6", "/dev/mmcblk0"])

    def test_growpart_error_is_not_accepted_as_nochange(self):
        result = subprocess.CompletedProcess([], 1, "", "FAILED")
        with mock.patch.object(MOD.Path, "stat", return_value=mock.Mock(st_mode=stat.S_IFBLK)), \
             mock.patch.object(MOD.Path, "open", side_effect=lambda *a, **kw: io.BytesIO(bytes(80))), \
             mock.patch.object(MOD, "run", return_value=result), \
             mock.patch.object(MOD, "checked") as checked:
            with self.assertRaises(ValueError): MOD.grow(self.marker, self.mounts, self.table)
        checked.assert_not_called()

    @unittest.skipUnless(all(shutil.which(name) for name in ("growpart", "sgdisk", "sfdisk")), "缺少稀疏檔案 GPT 驗證工具")
    def test_real_growpart_preserves_bootinfo_and_first_five_partitions(self):
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "disk.img"
            with image.open("wb") as stream: stream.truncate(512 * 1024 ** 2)
            argv = ["sgdisk", "--clear", "--set-alignment=1"]
            for number, (name, start, size) in enumerate(MOD.PREFIX, 1):
                argv += [f"--new={number}:{start}:{start + size - 1}", f"--change-name={number}:{name}"]
            argv += ["--new=6:532480:598015", "--change-name=6:rootfs", str(image)]
            subprocess.run(argv, check=True, capture_output=True)
            sentinel = bytes(range(80))
            with image.open("r+b") as stream: stream.write(sentinel)
            before = json.loads(subprocess.check_output(["sfdisk", "--json", str(image)]))["partitiontable"]
            # 模擬小映像寫到較大媒體，次要 GPT 仍停在舊磁碟尾端。
            with image.open("r+b") as stream: stream.truncate(1024 * 1024 ** 2)
            subprocess.run(["growpart", str(image), "6"], check=True, capture_output=True)
            after = json.loads(subprocess.check_output(["sfdisk", "--json", str(image)]))["partitiontable"]
            MOD.validate_growth(before, after)
            self.assertGreater(after["lastlba"], before["lastlba"])
            self.assertGreater(after["partitions"][5]["size"], before["partitions"][5]["size"])
            with image.open("rb") as stream: self.assertEqual(stream.read(80), sentinel)


if __name__ == "__main__":
    unittest.main()
