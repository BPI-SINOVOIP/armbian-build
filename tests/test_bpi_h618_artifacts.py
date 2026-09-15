#!/usr/bin/env python3
"""離線清單回歸；只操作臨時一般檔案，不接觸板卡或真實媒體。"""

from contextlib import redirect_stdout
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock


MODULE = Path(__file__).resolve().parents[1] / "tools" / "bpi_h618_artifacts.py"
SPEC = importlib.util.spec_from_file_location("artifacts", MODULE)
artifacts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(artifacts)
COMMIT = "a" * 40
PROFILE = "bananapim4berry"


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "Image").write_bytes(b"\x00\x01\x02\x03")
        self.entries = ["kernel=Image"]

    def make(self, profile=PROFILE):
        report = artifacts.index(self.root, profile, COMMIT, self.entries, "manifest.json")
        self.digest = report["manifest"]["sha256"]
        return report

    def verify(self, profile=PROFILE):
        return artifacts.verify(self.root, "manifest.json", profile, self.digest)

    def mutate(self, change):
        manifest = self.root / "manifest.json"
        data = json.loads(manifest.read_text())
        change(data)
        blob = json.dumps(data).encode()
        manifest.write_bytes(blob)
        self.digest = hashlib.sha256(blob).hexdigest()

    def test_index_verify_and_never_authorize_hardware(self):
        for report in (self.make(), self.verify()):
            self.assertTrue(report["ok"])
            self.assertEqual(report["artifact_count"], 1)
            for key in ("hardware_validated", "bootable_verified", "write_authorized", "source_authenticated"):
                self.assertIs(report[key], False)

    def test_three_declared_profiles(self):
        for profile in artifacts.PROFILES:
            with self.subTest(profile=profile):
                name = profile + ".json"
                made = artifacts.index(self.root, profile, COMMIT, self.entries, name)
                self.assertTrue(artifacts.verify(self.root, name, profile, made["manifest"]["sha256"])["ok"])

    def test_create_does_not_overwrite(self):
        self.make()
        old = (self.root / "manifest.json").read_bytes()
        with self.assertRaises(FileExistsError):
            self.make()
        self.assertEqual((self.root / "manifest.json").read_bytes(), old)

    def test_content_mutation_is_rejected(self):
        self.make()
        (self.root / "Image").write_bytes(b"XXXX")
        with self.assertRaisesRegex(artifacts.ArtifactError, "不符"):
            self.verify()

    def test_size_mutation_is_rejected(self):
        self.make()
        (self.root / "Image").write_bytes(b"XXXXX")
        with self.assertRaises(artifacts.ArtifactError):
            self.verify()

    def test_untrusted_manifest_change_is_rejected(self):
        self.make()
        with (self.root / "manifest.json").open("ab") as stream:
            stream.write(b"\n")
        with self.assertRaisesRegex(artifacts.ArtifactError, "清單 SHA-256"):
            self.verify()

    def test_unknown_and_mismatched_profiles(self):
        self.make()
        for profile in ("unknown", "bananapim4zero", None, []):
            with self.subTest(profile=profile), self.assertRaises(artifacts.ArtifactError):
                self.verify(profile)

    def test_schema_rejects_missing_extra_and_bad_types(self):
        self.make()
        original = json.loads((self.root / "manifest.json").read_text())
        for field in original:
            data = copy.deepcopy(original)
            del data[field]
            with self.subTest(field=field), self.assertRaises(artifacts.ArtifactError):
                artifacts.validate_manifest(data, PROFILE)
        for field, value in (("schema", True), ("schema", 2), ("artifacts", []),
                             ("source_commit", "bad"), ("extra", "欄位")):
            data = copy.deepcopy(original)
            data[field] = value
            with self.subTest(field=field), self.assertRaises(artifacts.ArtifactError):
                artifacts.validate_manifest(data, PROFILE)

    def test_rejects_bad_entry_values(self):
        self.make()
        original = json.loads((self.root / "manifest.json").read_text())
        for field, value in (("role", "command"), ("role", []), ("bytes", True),
                             ("bytes", 0), ("bytes", -1), ("sha256", "0" * 63),
                             ("path", "../Image"), ("path", None), ("extra", 1)):
            data = copy.deepcopy(original)
            data["artifacts"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(artifacts.ArtifactError):
                artifacts.validate_manifest(data, PROFILE)

    def test_paths_are_bounded_and_not_shell_input(self):
        for path in ("/Image", "../Image", "a/../Image", "a//Image", "./Image", "a\\b",
                     "-x", "x\ny", "x;command", "x" * 193, "", "a/", "a b"):
            with self.subTest(path=path), self.assertRaises(artifacts.ArtifactError):
                artifacts.relative_parts(path)

    def test_duplicate_path_or_role_is_rejected_before_read(self):
        for values in (["kernel=Image", "dtb=Image"], ["kernel=Image", "kernel=Missing"]):
            with self.subTest(values=values), self.assertRaises(artifacts.ArtifactError):
                artifacts.index(self.root, PROFILE, COMMIT, values, "manifest.json")
        self.assertFalse((self.root / "manifest.json").exists())

    def test_json_duplicate_keys_and_malformed_data(self):
        for blob in (b'{"schema":1,"schema":1}', b'{"x":{"a":1,"a":2}}', b"{", b"\xff",
                     b"[" * 2000 + b"]" * 2000, b" " * (artifacts.MAX_MANIFEST_BYTES + 1)):
            with self.subTest(blob=blob[:32]), self.assertRaises(artifacts.ArtifactError):
                artifacts.parse_manifest(blob)

    def test_empty_or_missing_artifact(self):
        (self.root / "Image").write_bytes(b"")
        with self.assertRaises(artifacts.ArtifactError):
            self.make()
        (self.root / "Image").unlink()
        with self.assertRaises(FileNotFoundError):
            self.make()

    def test_file_symlink_is_rejected(self):
        self.make()
        (self.root / "Link").symlink_to("Image")
        self.mutate(lambda data: data["artifacts"][0].update(path="Link"))
        with self.assertRaises((OSError, artifacts.ArtifactError)):
            self.verify()

    def test_directory_symlink_is_rejected(self):
        (self.root / "Link").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            artifacts.index(self.root, PROFILE, COMMIT, ["kernel=Link/Image"], "manifest.json")

    def test_root_and_ancestor_symlinks_are_rejected(self):
        (self.root / "Link").symlink_to(self.root, target_is_directory=True)
        (self.root / "Inner").mkdir()
        for root in (self.root / "Link", self.root / "Link" / "Inner"):
            with self.subTest(root=root), self.assertRaises(OSError):
                with artifacts.open_root(root):
                    self.fail("不得接受符號連結")

    def test_manifest_symlink_is_rejected(self):
        self.make()
        (self.root / "Link").symlink_to("manifest.json")
        with self.assertRaises((OSError, artifacts.ArtifactError)):
            artifacts.verify(self.root, "Link", PROFILE, self.digest)

    def test_fifo_and_directory_do_not_block(self):
        os.mkfifo(self.root / "Fifo")
        (self.root / "Directory").mkdir()
        for path in ("Fifo", "Directory"):
            with self.subTest(path=path), self.assertRaises((OSError, artifacts.ArtifactError)):
                artifacts.index(self.root, PROFILE, COMMIT, [f"kernel={path}"], "manifest.json")

    def test_self_reference_and_entry_limit(self):
        for entries in (["kernel=manifest.json"], ["kernel=Image"] * 33):
            with self.subTest(entries=entries), self.assertRaises(artifacts.ArtifactError):
                artifacts.index(self.root, PROFILE, COMMIT, entries, "manifest.json")

    def test_initial_oversize_is_rejected(self):
        with artifacts.open_root(self.root) as directory:
            with self.assertRaises(artifacts.ArtifactError):
                artifacts.fingerprint(directory, "Image", limit=3)

    def test_stream_growth_is_bounded_after_initial_valid_size(self):
        with artifacts.open_root(self.root) as directory, (self.root / "Image").open("rb") as original:
            with mock.patch.object(artifacts, "open_file") as opening:
                stream = opening.return_value.__enter__.return_value
                stream.fileno.return_value = original.fileno()
                stream.read.side_effect = [b"1234", b"5"]
                with self.assertRaisesRegex(artifacts.ArtifactError, "讀取期間"):
                    artifacts.fingerprint(directory, "Image", limit=4)
                self.assertEqual(stream.read.call_count, 2)

    def test_device_type_rejected_before_io_open(self):
        with artifacts.open_root(self.root) as directory:
            with mock.patch.object(artifacts.os, "open", wraps=os.open) as opening:
                with mock.patch.object(artifacts.os, "fstat", return_value=mock.Mock(st_mode=stat.S_IFCHR)):
                    with self.assertRaisesRegex(artifacts.ArtifactError, "一般檔案"):
                        with artifacts.open_file(directory, "Image"):
                            self.fail("不得開啟裝置")
                self.assertEqual(opening.call_count, 1)
                self.assertTrue(opening.call_args.args[1] & os.O_PATH)

    def test_oversized_json_integer_parser_and_cli(self):
        blob = b'{"schema":' + b"9" * 5000 + b'}'
        with self.assertRaises(artifacts.ArtifactError):
            artifacts.parse_manifest(blob)
        (self.root / "manifest.json").write_bytes(blob)
        out = io.StringIO()
        with redirect_stdout(out):
            code = artifacts.main(["verify", "--root", str(self.root), "--expected-profile", PROFILE,
                                   "--manifest-sha256", hashlib.sha256(blob).hexdigest()])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out.getvalue())["ok"])

    def test_changing_stat_during_read_is_rejected(self):
        original = os.fstat
        calls = 0

        def changed(fd):
            nonlocal calls
            calls += 1
            current = original(fd)
            if calls == 3:
                replacement = mock.Mock(wraps=current)
                replacement.st_size = current.st_size + 1
                replacement.st_mtime_ns = current.st_mtime_ns
                replacement.st_ctime_ns = current.st_ctime_ns
                return replacement
            return current

        with artifacts.open_root(self.root) as directory, mock.patch.object(artifacts.os, "fstat", changed):
            with self.assertRaisesRegex(artifacts.ArtifactError, "有變動"):
                artifacts.fingerprint(directory, "Image")

    def test_verify_does_not_modify_files(self):
        self.make()
        before = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.root.iterdir()}
        self.verify()
        after = {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in self.root.iterdir()}
        self.assertEqual(before, after)

    def test_cli_failure_is_nonzero_json_without_execution_authority(self):
        self.make()
        out = io.StringIO()
        with redirect_stdout(out):
            code = artifacts.main(["verify", "--root", str(self.root), "--expected-profile", PROFILE,
                                   "--manifest-sha256", "0" * 64])
        self.assertEqual(code, 1)
        report = json.loads(out.getvalue())
        self.assertFalse(report["ok"])
        self.assertFalse(report["write_authorized"])

    def test_cli_argument_failures_are_json(self):
        for args in ([], ["unknown"], ["verify"], ["index", "--profile", "unknown"]):
            out = io.StringIO()
            with self.subTest(args=args), redirect_stdout(out):
                code = artifacts.main(args)
            self.assertEqual(code, 1)
            self.assertFalse(json.loads(out.getvalue())["ok"])

    def test_help_is_traditional_chinese(self):
        for args in (["--help"], ["index", "--help"], ["verify", "--help"]):
            out = io.StringIO()
            with self.subTest(args=args), redirect_stdout(out), self.assertRaises(SystemExit) as raised:
                artifacts.main(args)
            self.assertEqual(raised.exception.code, 0)
            self.assertIn("用法：", out.getvalue())
            self.assertNotIn("show this help", out.getvalue())


if __name__ == "__main__":
    unittest.main()
