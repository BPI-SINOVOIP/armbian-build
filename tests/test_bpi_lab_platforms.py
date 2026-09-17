#!/usr/bin/env python3
"""僅以小型本機來源與臨時副本驗證；不接觸映像、網路或硬體。"""

from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bpi_lab_platforms as platforms


COUNTS = {
    "allwinner32": 12, "allwinner64": 1, "allwinner-h618": 3, "amlogic": 4,
    "rockchip32": 1, "rockchip64": 8, "mediatek32": 1, "mediatek64": 6,
    "spacemit-k1": 2, "spacemit-k3": 1, "sunplus": 2, "renesas": 1,
    "realtek": 2, "synaptics": 1,
}


class PlatformTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads((platforms.ROOT / platforms.PLATFORMS).read_text(encoding="utf-8"))
        paths = [platforms.REGISTRY, platforms.PLATFORMS]
        paths += [source["path"] for source in cls.document["sources"]
                  if source["path"] not in platforms.DEPENDENCIES]
        cls.blobs = {path: (platforms.ROOT / path).read_bytes() for path in paths}
        # 共用工具正由其他代理修改；僅在臨時樣本內建立引用片段，不改正式 pins。
        for path, entry in cls.document["shared_tools"].items():
            lines = [ref["token"] for ref in entry["evidence"]]
            blob = ("\n".join(lines) + "\n").encode()
            cls.blobs[path] = blob
            source = next(item for item in cls.document["sources"] if item["path"] == path)
            source["sha256"] = hashlib.sha256(blob).hexdigest()
        cls.blobs[platforms.PLATFORMS] = json.dumps(cls.document, ensure_ascii=False).encode()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, blob in self.blobs.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
        self.manifest = copy.deepcopy(self.document)

    def save(self):
        (self.root / platforms.PLATFORMS).write_text(
            json.dumps(self.manifest, ensure_ascii=False), encoding="utf-8")

    def validate(self):
        self.save()
        return platforms.validate(self.root)

    def row(self, name):
        return next(row for row in self.manifest["boards"] if row["board"] == name)

    def rejects(self, code):
        with self.assertRaises(platforms.PlatformError) as caught:
            self.validate()
        self.assertEqual(caught.exception.code, code)

    def cli(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = platforms.main([*args, "--root", str(self.root)])
        return result, stdout.getvalue(), stderr.getvalue()

    def test_actual_45_boards_and_14_groups_match_registry(self):
        result = self.validate()
        registry = json.loads(self.blobs[platforms.REGISTRY])
        self.assertEqual({row["board"] for row in result["boards"]},
                         {row["board"] for row in registry["boards"]})
        self.assertEqual(dict(platforms.Counter(row["group"] for row in result["boards"])), COUNTS)
        self.assertEqual(dict(platforms.Counter(row["architecture"] for row in result["boards"])),
                         {"arm32": 16, "arm64": 26, "riscv64": 3})
        self.assertEqual(len(result["sources"]), 98)

    def test_each_source_sha_and_line_reference_is_checked(self):
        self.assertEqual(self.validate(), self.document)
        for source in self.document["sources"]:
            self.assertEqual(source["sha256"], hashlib.sha256(self.blobs[source["path"]]).hexdigest())

    def test_every_hardware_and_whole_backend_qualification_stays_false(self):
        for row in [self.manifest, *self.manifest["boards"]]:
            for field in ("hardware_qualified", "execution_ready", "backend_complete"):
                self.assertIs(row[field], False)
        for row in self.manifest["boards"]:
            self.assertEqual(row["software"], platforms.SOFTWARE)
            self.assertIsNone(row["special_boot"]["secure_boot_enabled"])

    def test_shared_implementation_is_separate_from_board_integration(self):
        shared = self.validate()["shared_tools"]
        self.assertEqual(set(shared), set(platforms.DEPENDENCIES))
        for tool in shared.values():
            self.assertEqual(tool["availability"], "implemented")
            self.assertEqual(tool["validation"], "source_reviewed")
            self.assertEqual(tool["board_integration"], "pending")
            self.assertIs(tool["whole_adapter_ready"], False)
        uboot = shared["tools/bpi_lab_uboot.py"]
        self.assertIn("arm32:zImage:bootz", uboot["supported"])
        self.assertIn("FIT", uboot["unsupported"])
        self.assertIn("NVMe-root", shared["tools/bpi_lab_linux.py"]["unsupported"])

    def test_h618_stages_only_apply_to_0845_and_not_whole_adapter(self):
        h618 = self.validate()["shared_tools"]["tools/bpi_lab_h618.py"]
        self.assertEqual(h618["hardware_ids"], ["bpi-m4zero-0845"])
        self.assertEqual(h618["supported"], ["preflight", "deploy", "smoke"])
        self.assertEqual(h618["unsupported"], ["boot", "recovery", "resume"])
        for name in ("bpi-m4z", "bpi-m4b", "bpi-m4z-emac"):
            self.assertFalse(self.row(name)["execution_ready"])

    def test_h618_uses_next_script_and_realtek_m4_is_not_h618(self):
        self.assertEqual(self.row("bpi-m4z")["boot_profile"], "sunxi64")
        profile = self.manifest["boot_profiles"]["sunxi64"]
        self.assertEqual(profile["command"], "booti")
        self.assertEqual(profile["evidence"][0]["path"], "config/bootscripts/boot-sun50i-next.cmd")
        self.assertEqual(self.row("bpi-m4")["group"], "realtek")
        self.assertIsNone(self.manifest["boot_profiles"]["realtek-vendor"]["command"])
        self.assertEqual(self.manifest["boot_profiles"]["realtek-vendor"]["kernel_format"], "unknown")

    def test_extlinux_and_k3_do_not_inherit_generic_booti_support(self):
        self.assertEqual(self.row("bpi-r2pro")["boot_profile"], "r2pro-extlinux")
        self.assertIsNone(self.manifest["boot_profiles"]["r2pro-extlinux"]["command"])
        self.assertNotIn("config/bootscripts/boot-rockchip64.cmd", self.row("bpi-r2pro")["sources"])
        for name in ("bpi-r3", "bpi-r3mini", "bpi-r4", "bpi-r4lite", "bpi-r4pro", "bpi-r64"):
            self.assertEqual(self.row(name)["boot_profile"], "filogic-extlinux")
        self.assertEqual(self.row("bpi-f3")["boot_profile"], "k1-extlinux")
        self.assertEqual(self.row("bpi-cm6")["boot_profile"], "cm6-extlinux")
        self.assertEqual(self.manifest["boot_profiles"]["k3-vendor"]["status"], "vendor_review_required")

    def test_media_differences_and_existing_registry_issues_survive(self):
        self.assertEqual(self.row("bpi-f2p")["storage"]["emmc"]["status"], "candidate_excluded")
        self.assertEqual(self.row("bpi-f2s")["storage"]["emmc"]["status"], "board_declared")
        self.assertEqual(self.row("bpi-r3mini")["storage"]["emmc"]["status"], "board_declared")
        self.assertEqual(self.row("bpi-r4pro")["storage"]["emmc"]["status"], "candidate_excluded")
        self.assertTrue(any("unknown_kernel" in item for item in self.row("bpi-f2p")["blockers"]))
        self.assertEqual((self.root / platforms.REGISTRY).read_bytes(), self.blobs[platforms.REGISTRY])

    def test_missing_board_is_rejected(self):
        self.manifest["boards"].pop()
        self.rejects("coverage")

    def test_extra_board_is_rejected(self):
        self.manifest["boards"].append(copy.deepcopy(self.manifest["boards"][0]))
        self.rejects("coverage")

    def test_duplicate_board_is_rejected(self):
        self.manifest["boards"][1] = copy.deepcopy(self.manifest["boards"][0])
        self.rejects("duplicate")

    def test_unknown_board_is_rejected(self):
        self.manifest["boards"][0]["board"] = "bpi-unknown"
        self.rejects("coverage")

    def test_architecture_and_family_mismatch_are_rejected(self):
        for field, value in (("architecture", "arm64"), ("family", "sun50iw9-bpi"),
                             ("config_path", "config/boards/bananapim4zero.conf")):
            with self.subTest(field=field):
                self.manifest = copy.deepcopy(self.document)
                self.row("bpi-m1")[field] = value
                self.rejects("board_mismatch")

    def test_group_mismatch_is_rejected(self):
        self.row("bpi-m1")["group"] = "allwinner-h618"
        self.rejects("group_mismatch")

    def test_profile_cannot_be_replaced_by_architecture_lookalike(self):
        for name in ("bpi-m1", "bpi-m4", "bpi-f3", "bpi-sm10", "bpi-r3", "bpi-m6"):
            with self.subTest(board=name):
                self.manifest = copy.deepcopy(self.document)
                self.row(name)["boot_profile"] = "sunxi64"
                self.rejects("profile_mismatch")

    def test_qualification_promotion_and_non_boolean_false_are_rejected(self):
        for value in (True, 0, None, "false"):
            for field in ("hardware_qualified", "execution_ready", "backend_complete"):
                with self.subTest(value=value, field=field):
                    self.manifest = copy.deepcopy(self.document)
                    self.row("bpi-m4z")[field] = value
                    self.rejects("qualification")

    def test_top_level_and_secure_qualification_promotion_are_rejected(self):
        self.manifest["hardware_qualified"] = True
        self.rejects("qualification")
        self.manifest = copy.deepcopy(self.document)
        self.row("bpi-m1")["special_boot"]["secure_boot_enabled"] = False
        self.rejects("qualification")

    def test_tool_existence_does_not_promote_board_software(self):
        self.row("bpi-m1")["software"]["uboot_tool"] = "ready"
        self.rejects("software")

    def test_shared_tool_scope_and_h618_hardware_id_are_enforced(self):
        for field, value in (("hardware_ids", ["bpi-m4berry"]), ("whole_adapter_ready", True),
                             ("supported", ["preflight", "deploy", "smoke", "boot"])):
            with self.subTest(field=field):
                self.manifest = copy.deepcopy(self.document)
                self.manifest["shared_tools"]["tools/bpi_lab_h618.py"][field] = value
                self.rejects("software")

    def test_source_drift_in_board_family_script_and_shared_tool_is_rejected(self):
        for path in ("config/boards/bananapi.conf", "config/sources/families/include/sunxi_common.inc",
                     "config/bootscripts/boot-sunxi.cmd", "tools/bpi_lab_uboot.py"):
            with self.subTest(path=path):
                target = self.root / path
                target.write_bytes(self.blobs[path] + b"\n")
                self.rejects("source_changed")
                target.write_bytes(self.blobs[path])

    def test_registry_drift_is_rejected(self):
        target = self.root / platforms.REGISTRY
        target.write_bytes(target.read_bytes() + b"\n")
        self.rejects("source_changed")

    def test_config_hash_cannot_be_silently_rebaselined(self):
        path = "config/boards/bananapi.conf"
        (self.root / path).write_bytes(b"BOARDFAMILY=sun50iw9-bpi\n")
        source = next(item for item in self.manifest["sources"] if item["path"] == path)
        source["sha256"] = hashlib.sha256((self.root / path).read_bytes()).hexdigest()
        self.rejects("registry")

    def test_missing_source_file_and_omitted_source_record_are_rejected(self):
        path = self.manifest["sources"][0]["path"]
        (self.root / path).unlink()
        self.rejects("io_error")
        (self.root / path).write_bytes(self.blobs[path])
        self.manifest["sources"].pop(0)
        self.rejects("source_missing")

    def test_duplicate_source_and_missing_board_source_are_rejected(self):
        self.manifest["sources"].append(copy.deepcopy(self.manifest["sources"][0]))
        self.rejects("duplicate")
        self.manifest = copy.deepcopy(self.document)
        self.row("bpi-m1")["sources"].pop()
        self.rejects("source_missing")

    def test_out_of_scope_device_image_absolute_and_parent_paths_are_rejected(self):
        for path in ("/dev/null", "../config/boards/bananapi.conf", "output/large.img.xz",
                     "config/boards/../../private.conf"):
            with self.subTest(path=path):
                self.manifest = copy.deepcopy(self.document)
                self.manifest["sources"][0]["path"] = path
                self.rejects("source_scope")

    def test_source_symlink_fifo_directory_and_oversize_are_rejected(self):
        path = self.root / "config/bootscripts/boot-sunxi.cmd"
        path.unlink()
        path.symlink_to(self.root / "config/bootscripts/boot-sun50i-next.cmd")
        self.rejects("not_regular")
        path.unlink()
        os.mkfifo(path)
        self.rejects("not_regular")
        path.unlink()
        path.mkdir()
        self.rejects("not_regular")
        path.rmdir()
        path.write_bytes(b"x" * (platforms.MAX_SOURCE_BYTES + 1))
        self.rejects("metadata_too_large")

    def test_directory_symlink_is_rejected(self):
        directory = self.root / "config/bootscripts"
        directory.rename(self.root / "saved-scripts")
        directory.symlink_to(self.root / "saved-scripts", target_is_directory=True)
        self.rejects("io_error")

    def test_sha_encoding_unknown_fields_and_invalid_types_are_rejected(self):
        for change in (lambda: self.manifest["sources"][0].update(sha256="bad"),
                       lambda: self.manifest.update(extra=True),
                       lambda: self.row("bpi-m1").update(sources="bad"),
                       lambda: self.manifest.update(board_count=True)):
            self.manifest = copy.deepcopy(self.document)
            change()
            with self.assertRaises(platforms.PlatformError):
                self.validate()

    def test_json_duplicate_keys_nan_and_broken_json_are_rejected(self):
        path = self.root / platforms.PLATFORMS
        for raw, code in (("{", "invalid_json"), ('{"schema":1,"schema":2}', "duplicate_key"),
                          ('{"value":NaN}', "schema")):
            with self.subTest(raw=raw):
                path.write_text(raw, encoding="utf-8")
                with self.assertRaises(platforms.PlatformError) as caught:
                    platforms.validate(self.root)
                self.assertEqual(caught.exception.code, code)

    def test_evidence_line_token_and_soc_mismatch_are_rejected(self):
        for field, value in (("line", 0), ("line", True), ("token", "不存在的來源片段"),
                             ("path", "tools/unlisted.py")):
            with self.subTest(field=field):
                self.manifest = copy.deepcopy(self.document)
                self.manifest["boot_profiles"]["sunxi32"]["evidence"][0][field] = value
                self.rejects("evidence")
        self.manifest = copy.deepcopy(self.document)
        self.row("bpi-m1")["soc"] = "H618"
        self.rejects("evidence")

    def test_format_filename_status_and_artifact_invention_are_rejected(self):
        for field, value, code in (("kernel_format", "FIT", "profiles"),
                                  ("kernel_file", "uImage", "profiles"),
                                  ("status", "ready", "qualification")):
            with self.subTest(field=field):
                self.manifest = copy.deepcopy(self.document)
                self.manifest["boot_profiles"]["sunxi64"][field] = value
                self.rejects(code)
        self.manifest = copy.deepcopy(self.document)
        self.row("bpi-m1")["boot_chain"]["artifacts"].append("unknown-container.bin")
        self.rejects("evidence")

    def test_sd_only_limit_cannot_be_removed(self):
        self.row("bpi-f2p")["storage"]["emmc"] = {
            "status": "not_declared", "evidence": [], "note": "未知。"}
        self.rejects("storage")

    def test_validation_never_executes_shell_or_opens_images(self):
        self.save()
        original = os.open

        def guarded(path, flags, *args, **kwargs):
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            self.assertFalse(str(path).endswith((".img", ".xz", ".bin")))
            return original(path, flags, *args, **kwargs)

        with mock.patch.object(platforms.catalog.os, "open", side_effect=guarded), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("不得啟動子程序")), \
                mock.patch("os.system", side_effect=AssertionError("不得執行 shell")):
            platforms.validate(self.root)

    def test_even_reviewed_shell_text_is_not_executed(self):
        path = "config/bootscripts/boot-sunxi.cmd"
        marker = self.root / "must-not-exist"
        blob = self.blobs[path] + f"\n$(touch {marker})\nsource /dev/null\n".encode()
        (self.root / path).write_bytes(blob)
        source = next(item for item in self.manifest["sources"] if item["path"] == path)
        source["sha256"] = hashlib.sha256(blob).hexdigest()
        with mock.patch("subprocess.Popen", side_effect=AssertionError("不得執行來源")):
            self.validate()
        self.assertFalse(marker.exists())

    def test_source_identity_change_at_final_check_is_rejected(self):
        original = platforms.catalog._unchanged

        def changed(root_fd, name, expected):
            if name == platforms.REGISTRY:
                count[0] += 1
                if count[0] == 2:
                    raise platforms.catalog.CatalogError("identity_changed", "來源讀取後遭替換")
            return original(root_fd, name, expected)

        count = [0]
        with mock.patch.object(platforms.catalog, "_unchanged", side_effect=changed):
            self.rejects("identity_changed")

    def test_cli_validate_and_list_are_deterministic_and_read_only(self):
        first = self.cli("validate", "--json")
        self.assertEqual(first, self.cli("validate", "--json"))
        self.assertEqual(first[0], 0)
        summary = json.loads(first[1])
        self.assertEqual(summary["group_counts"], COUNTS)
        self.assertTrue(summary["local_sources_verified"])
        self.assertFalse(summary["hardware_qualified"])
        result, output, error = self.cli("list", "--json")
        self.assertEqual((result, error), (0, ""))
        self.assertEqual(len(json.loads(output)["boards"]), 45)
        self.assertEqual((self.root / platforms.PLATFORMS).read_bytes(), self.blobs[platforms.PLATFORMS])

    def test_cli_filter_still_checks_unselected_sources(self):
        result, output, _ = self.cli("list", "--group", "allwinner32", "--json")
        self.assertEqual(result, 0)
        self.assertEqual(len(json.loads(output)["boards"]), 12)
        (self.root / "config/boards/bananapism10.wip").write_bytes(b"changed\n")
        result, _, error = self.cli("list", "--board", "bpi-m1")
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(error)["code"], "source_changed")

    def test_cli_invalid_selection_and_arguments_fail_closed(self):
        for args in (("list", "--board", "unknown"), ("validate", "--board", "bpi-m1"),
                     ("execute",), ("list", "--unknown")):
            with self.subTest(args=args):
                result, output, error = self.cli(*args)
                self.assertEqual((result, output), (2, ""))
                self.assertEqual(json.loads(error)["status"], "invalid")

    def test_cli_help_and_human_output_are_chinese(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            platforms.main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("用法：", output.getvalue())
        self.assertNotIn("options:", output.getvalue())
        result, output, _ = self.cli("list", "--board", "bpi-m4")
        self.assertEqual(result, 0)
        self.assertIn("待核定", output)
        self.assertIn("工具待整合", output)


class LiveSourceTests(unittest.TestCase):
    def test_checked_in_manifest_matches_live_sources(self):
        """真實來源漂移必須失敗；不得由單元樣本或更新摘要掩蓋。"""
        self.assertEqual(platforms.validate()["board_count"], 45)


if __name__ == "__main__":
    unittest.main()
