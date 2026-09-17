"""跨家族準備入口的來源與根媒體綁定回歸。"""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bpi_lab_prepare as prepare


class Reader:
    filesystem_uuid = "12345678-1234-1234-1234-123456789012"

    def __init__(self, source, sha256, output, **kwargs):
        self.output = output
        self.report = {"source_digest": {"bytes": 123, "sha256": sha256},
                       "raw": {"bytes": 4096, "sha256": "b" * 64},
                       "partition": {"partuuid": "12345678-01"}}

    def __enter__(self):
        self.output.mkdir()
        return self

    def __exit__(self, *args):
        prepare.image.save_json(self.output, "extraction.json", self.report)

    def read_file(self, path):
        raise FileNotFoundError(path)


class PrepareTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.output = self.root / "result"
        self.manifest = {"status": "prepared", "hardware_validated": False,
                         "root_uuid": Reader.filesystem_uuid, "blockers": []}
        patcher = mock.patch.object(prepare.image, "ImageReader", Reader)
        patcher.start()
        self.addCleanup(patcher.stop)
        modules = mock.patch.object(prepare, "family_module", return_value=mock.Mock(POLICIES={"bpi-m1": "policy"}))
        modules.start()
        self.addCleanup(modules.stop)

    def run_prepare(self, manifest=None, **kwargs):
        values = {"family": "allwinner", "board": "bpi-m1", "kernel_release": "6.18.49-current-sunxi",
                  "output": self.output, **kwargs}
        with mock.patch.object(prepare, "prepare_family", return_value=manifest or self.manifest):
            return prepare.prepare(self.root / "image.img.xz", "a" * 64, **values)

    def test_ready_is_not_hardware(self):
        report = self.run_prepare()
        self.assertEqual(report["status"], "prepared")
        self.assertTrue(report["root_uuid_verified"])
        for key in ("hardware_validated", "whole_backend_ready", "media_written", "boot_executed"):
            self.assertFalse(report[key])
        self.assertEqual(json.loads((self.output / "preparation.json").read_bytes()), report)
        self.assertEqual(report["components"]["sha256"],
                         prepare.image.digest((self.output / "family-result.json").read_bytes())["sha256"])

    def test_family_block_preserved(self):
        self.manifest.update(status="blocked", blockers=["需要專用 fixup"])
        result = self.run_prepare()
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["blockers"], ["需要專用 fixup"])

    def test_superblock_mismatch(self):
        self.manifest["root_uuid"] = "00000000-0000-0000-0000-000000000000"
        result = self.run_prepare()
        self.assertEqual(result["status"], "blocked")
        self.assertIn("超級區塊", result["error"])

    def test_no_uuid(self):
        del self.manifest["root_uuid"]
        self.assertEqual(self.run_prepare()["status"], "blocked")

    def test_root_label_requires_verified_superblock(self):
        self.manifest.update(root_uuid=None, root_label="BPI-ROOT", root_target="LABEL=BPI-ROOT",
                             root_fstab_target="UUID=" + Reader.filesystem_uuid)
        class LabelReader(Reader):
            filesystem_label = "BPI-ROOT"

            def __enter__(self):
                self.report["filesystem_label_unique"] = True
                self.report["filesystem_labels_complete"] = True
                return super().__enter__()
        with mock.patch.object(prepare.disk, "DiskReader", LabelReader):
            result = self.run_prepare(layout="disk")
        self.assertEqual(result["status"], "prepared")
        self.assertTrue(result["root_identity_verified"])
        self.assertFalse(result["root_uuid_verified"])
        self.assertFalse(result["root_binding"]["unique_on_hardware"])

    def test_root_label_unknown_or_duplicate_rejected(self):
        self.manifest.update(root_uuid=None, root_label="BPI-ROOT", root_target="LABEL=BPI-ROOT",
                             root_fstab_target="UUID=" + Reader.filesystem_uuid)
        for label, unique in ((None, True), ("OTHER", True), ("BPI-ROOT", False), ("BPI-ROOT", None)):
            reader = mock.Mock(filesystem_uuid=Reader.filesystem_uuid, filesystem_label=label,
                               report={"filesystem_label_unique": unique, "filesystem_labels_complete": True})
            with self.subTest(label=label, unique=unique), self.assertRaises(ValueError):
                prepare.root_binding(self.manifest, reader)

    def test_root_label_fstab_mismatch_rejected(self):
        self.manifest.update(root_uuid=None, root_label="BPI-ROOT", root_target="LABEL=BPI-ROOT",
                             root_fstab_target="UUID=other")
        reader = mock.Mock(filesystem_uuid=Reader.filesystem_uuid, filesystem_label="BPI-ROOT",
                           report={"filesystem_label_unique": True, "filesystem_labels_complete": True})
        with self.assertRaisesRegex(ValueError, "fstab"):
            prepare.root_binding(self.manifest, reader)

    def test_old_label_evidence_without_full_partition_coverage_rejected(self):
        self.manifest.update(root_uuid=None, root_label="BPI-ROOT", root_target="LABEL=BPI-ROOT",
                             root_fstab_target="UUID=" + Reader.filesystem_uuid)
        for coverage in (None, False):
            reader = mock.Mock(filesystem_uuid=Reader.filesystem_uuid, filesystem_label="BPI-ROOT",
                               report={"filesystem_label_unique": True, "filesystem_labels_complete": coverage})
            with self.subTest(coverage=coverage), self.assertRaises(ValueError):
                prepare.root_binding(self.manifest, reader)

    def test_hardware_claim_rejected(self):
        self.manifest["hardware_validated"] = True
        result = self.run_prepare()
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["hardware_validated"])

    def test_unknown_status(self):
        self.manifest["status"] = "passed"
        self.assertEqual(self.run_prepare()["status"], "blocked")

    def test_invalid_family_no_output(self):
        with self.assertRaises(ValueError):
            self.run_prepare(family="unknown")
        self.assertFalse(self.output.exists())

    def test_existing_evidence_untouched(self):
        self.output.mkdir()
        (self.output / "preparation.json").write_bytes(b"keep")
        with self.assertRaises(FileExistsError):
            self.run_prepare()
        self.assertEqual((self.output / "preparation.json").read_bytes(), b"keep")

    def test_family_exception_saved(self):
        with mock.patch.object(prepare, "prepare_family", side_effect=ValueError("資料不完整")):
            result = prepare.prepare(self.root / "image", "a" * 64, family="amlogic", board="bpi-m5",
                                     kernel_release="6.18.49-current-meson64", output=self.output)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["error"], "資料不完整")
        self.assertTrue((self.output / "preparation.json").is_file())

    def test_missing_module_before_decompression(self):
        with mock.patch.object(prepare, "family_module", side_effect=ImportError("家族模組無法載入")):
            result = self.run_prepare()
        self.assertEqual(result["status"], "blocked")
        self.assertFalse((self.output / "extraction").exists())
        self.assertIn("無法載入", result["error"])

    def test_parser_bug_retains_diagnostic(self):
        with mock.patch.object(prepare, "prepare_family", side_effect=TypeError("家族資料不能序列化")):
            result = prepare.prepare(self.root / "image", "a" * 64, family="amlogic", board="bpi-m5",
                                     kernel_release="6.18.49-current-meson64", output=self.output)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["error_type"], "TypeError")
        self.assertEqual(result["error"], "家族資料不能序列化")

    def test_wrong_family_board_before_decompression(self):
        result = self.run_prepare(family="amlogic", board="bpi-m1")
        self.assertEqual(result["status"], "blocked")
        self.assertFalse((self.output / "extraction").exists())

    def test_amlogic_explicit_board_alias(self):
        module = mock.Mock()
        module.prepare.return_value = self.manifest
        with mock.patch.object(prepare, "family_module", return_value=module):
            prepare.prepare_family("amlogic", lambda path: b"", board="bpi-m5",
                                   kernel_release="6.18.49-current-meson64", output=self.output)
        self.assertEqual(module.prepare.call_args.kwargs["board"], "bananapim5")

    def test_disk_layout_reader(self):
        with mock.patch.object(prepare.disk, "DiskReader", Reader), mock.patch.object(prepare.image, "ImageReader") as old:
            result = self.run_prepare(layout="disk")
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["layout"], "disk")
        old.assert_not_called()

    def test_invalid_layout(self):
        with self.assertRaises(ValueError):
            self.run_prepare(layout="auto-ignore-errors")
        self.assertFalse(self.output.exists())

    def test_new_family_mapping_before_read(self):
        for family, boards in prepare.BOARD_FAMILIES.items():
            for board in boards:
                with self.subTest(family=family, board=board):
                    result = self.run_prepare(family=family, board=board, output=self.root / board)
                    self.assertEqual(result["status"], "prepared")

    def test_family_mismatch_no_decompression(self):
        for family in prepare.BOARD_FAMILIES:
            with self.subTest(family=family):
                result = self.run_prepare(family=family, board="bpi-m1", output=self.root / family)
                self.assertEqual(result["status"], "blocked")
                self.assertFalse((self.root / family / "extraction").exists())


if __name__ == "__main__":
    unittest.main()
