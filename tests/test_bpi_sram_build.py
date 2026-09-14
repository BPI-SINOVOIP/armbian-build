#!/usr/bin/env python3
"""SRAM builder 的參數與安全契約測試；不建置假產物，也不寫入測試檔案。"""

from __future__ import annotations

from contextlib import redirect_stderr
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import tarfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools/build_bpi_sram_supervisor.py"
SPEC = importlib.util.spec_from_file_location("bpi_sram_build", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def member(name: str, kind=tarfile.REGTYPE, link: str = "") -> tarfile.TarInfo:
    result = tarfile.TarInfo(name)
    result.type = kind
    result.linkname = link
    return result


class ArgumentsTests(unittest.TestCase):
    def parse(self, *extra):
        return builder.build_parser().parse_args([
            "--source-git", "/cache/.git", "--output", "/build/new", *extra,
        ])

    def test_defaults(self):
        args = self.parse()
        self.assertEqual(args.source_git, Path("/cache/.git"))
        self.assertEqual(args.output, Path("/build/new"))
        self.assertEqual(args.cross_compile, "aarch64-linux-gnu-")
        self.assertEqual(args.jobs, 4)

    def test_limits_and_absolute_cross_prefix(self):
        for count in (1, 16):
            self.assertEqual(self.parse("--jobs", str(count)).jobs, count)
        self.assertEqual(self.parse("--cross-compile", "/opt/bin/aarch64-none-elf-").cross_compile,
                         "/opt/bin/aarch64-none-elf-")

    def test_invalid_jobs_and_prefixes(self):
        cases = [("--jobs", value) for value in ("0", "17", "-1", "1.5", "no", "999999")]
        cases += [("--cross-compile", value) for value in ("", "gcc", "ccache gcc-", "$(cmd)-", "x;y-")]
        for args in cases:
            with self.subTest(args=args), redirect_stderr(io.StringIO()) as output:
                with self.assertRaises(SystemExit) as raised:
                    self.parse(*args)
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("參數錯誤", output.getvalue())

    def test_required_arguments_and_unknown_options(self):
        for args in ([], ["--source-git", "/cache/.git"], ["--output", "/build/new"],
                     ["--source-git", "/cache/.git", "--output", "/build/new", "--dry-run"]):
            with self.subTest(args=args), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    builder.build_parser().parse_args(args)

    def test_chinese_help(self):
        help_text = builder.build_parser().format_help()
        self.assertIn("用法：", help_text)
        self.assertIn("尚", "尚未實板驗證")
        self.assertIn("選項", help_text)
        self.assertIn("上限 16", help_text)
        self.assertNotIn("usage:", help_text)


class ArchiveTests(unittest.TestCase):
    def test_regular_directory_and_internal_symlink(self):
        builder.validate_members([
            member("include", tarfile.DIRTYPE), member("include/header.h"),
            member("arch", tarfile.DIRTYPE), member("arch/arm", tarfile.DIRTYPE),
            member("arch/arm/link.h", tarfile.SYMTYPE, "../../include/header.h"),
        ])

    def test_traversal_absolute_and_git_metadata(self):
        for name in ("../outside", "/absolute", "a/../../bad", "a//bad", "./bad",
                     "a/./bad", ".git/config", "nested/.git/config", "a\\b", ""):
            with self.subTest(name=name), self.assertRaises(builder.BuildError):
                builder.validate_members([member(name)])

    def test_duplicate_and_file_parent(self):
        for entries in ([member("same"), member("same")], [member("a"), member("a/b")],
                        [member("a/", tarfile.DIRTYPE), member("a", tarfile.DIRTYPE)]):
            with self.subTest(entries=entries), self.assertRaises(builder.BuildError):
                builder.validate_members(entries)

    def test_special_files_hardlinks_and_sparse_are_rejected(self):
        for kind in (tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE,
                     tarfile.GNUTYPE_SPARSE):
            with self.subTest(kind=kind), self.assertRaises(builder.BuildError):
                builder.validate_members([member("special", kind, "target")])

    def test_symlink_escape_and_dangling(self):
        for link in ("/outside", "../../outside", "missing", ""):
            with self.subTest(link=link), self.assertRaises(builder.BuildError):
                builder.validate_members([member("d", tarfile.DIRTYPE),
                                          member("d/link", tarfile.SYMTYPE, link)])

    def test_symlink_parent_and_chain_are_rejected(self):
        cases = [
            [member("target", tarfile.DIRTYPE), member("link", tarfile.SYMTYPE, "target"),
             member("link/file")],
            [member("target"), member("first", tarfile.SYMTYPE, "target"),
             member("second", tarfile.SYMTYPE, "first")],
            [member("file"), member("dir", tarfile.DIRTYPE),
             member("alias", tarfile.SYMTYPE, "dir"),
             member("second", tarfile.SYMTYPE, "alias/../file")],
        ]
        for entries in cases:
            with self.subTest(entries=entries), self.assertRaises(builder.BuildError):
                builder.validate_members(entries)

    def test_hash_mismatch_precedes_extraction(self):
        with mock.patch.object(builder, "file_record", return_value={"sha256": "0" * 64}), \
                mock.patch.object(builder.tarfile, "open") as opener, \
                mock.patch.object(Path, "mkdir") as mkdir:
            with self.assertRaisesRegex(builder.BuildError, "SHA-256"):
                builder.extract_archive(Path("archive.tar"), Path("source"))
            opener.assert_not_called()
            mkdir.assert_not_called()


class OutputAndEnvironmentTests(unittest.TestCase):
    def test_bare_and_worktree_git_dirs_use_the_same_fixed_identity(self):
        for bare in ("true", "false"):
            runner = mock.Mock()
            runner.run.side_effect = [bare, builder.COMMIT, builder.TREE]
            self.assertEqual(builder.source_identity(runner, ["git"]),
                             {"commit": builder.COMMIT, "tree": builder.TREE})
        runner = mock.Mock()
        runner.run.side_effect = ["false", "0" * 40, builder.TREE]
        with self.assertRaisesRegex(builder.BuildError, "固定基準"):
            builder.source_identity(runner, ["git"])

    def test_existing_output_is_rejected_without_writing(self):
        with self.assertRaisesRegex(builder.BuildError, "已存在"):
            builder.new_output_path(MODULE_PATH.parent, Path("/cache/.git"))

    def test_symlink_output_and_parent_are_rejected(self):
        output = Path("/safe/new")
        for symlink in (output, output.parent):
            with self.subTest(symlink=symlink), mock.patch.object(
                Path, "is_symlink", autospec=True, side_effect=lambda path: path == symlink
            ), self.assertRaisesRegex(builder.BuildError, "符號連結"):
                builder.new_output_path(output, Path("/cache/.git"))

    def test_output_inside_git_is_rejected(self):
        with mock.patch.object(Path, "is_symlink", return_value=False), \
                mock.patch.object(Path, "exists", return_value=False), \
                mock.patch.object(Path, "is_dir", return_value=True), \
                self.assertRaisesRegex(builder.BuildError, "來源 Git"):
            builder.new_output_path(Path("/cache/.git/new"), Path("/cache/.git"))

    def test_parent_must_exist_and_dotdot_is_rejected(self):
        with mock.patch.object(Path, "is_symlink", return_value=False), \
                mock.patch.object(Path, "exists", return_value=False), \
                mock.patch.object(Path, "is_dir", return_value=False), \
                self.assertRaisesRegex(builder.BuildError, "父目錄"):
            builder.new_output_path(Path("/safe/new"), Path("/cache/.git"))
        with self.assertRaisesRegex(builder.BuildError, "不得含"):
            builder.new_output_path(Path("/safe/../new"), Path("/cache/.git"))

    def test_environment_is_fixed_and_does_not_inherit_git_or_make_overrides(self):
        with mock.patch.dict(os.environ, {"MAKEFLAGS": "-n", "CC": "false", "CFLAGS": "-O0",
                                         "GIT_DIR": "/wrong", "GIT_CONFIG_COUNT": "1",
                                         "SOURCE_DATE_EPOCH": "1"}):
            env = builder.build_environment(Path("/safe/new"))
        for name in ("MAKEFLAGS", "CC", "CFLAGS", "GIT_DIR", "GIT_CONFIG_COUNT"):
            self.assertNotIn(name, env)
        self.assertEqual(env["SOURCE_DATE_EPOCH"], "1789401600")
        self.assertEqual(env["KBUILD_BUILD_USER"], "bpi")
        self.assertEqual(env["KBUILD_BUILD_HOST"], "bpi")
        self.assertEqual(env["LC_ALL"], "C")
        self.assertEqual(env["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(env["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(env["GIT_CEILING_DIRECTORIES"], "/safe/new")

    def test_fixed_input_identity(self):
        self.assertEqual(builder.COMMIT, "25049ad560826f7dc1c4740883b0016014a59789")
        self.assertEqual(builder.TREE, "2ccf5ff0294135c081c77f6fd9e1a6e697cd527b")
        self.assertEqual(builder.ARCHIVE_SHA256,
                         "d4c25dae69c1d796f5dd20d2e3d8a41198483f389f5453f7dfbc68ba897f8226")
        self.assertEqual(len(builder.INPUT_FILES), 10)
        self.assertEqual(builder.file_record(MODULE_PATH)["sha256"],
                         hashlib.sha256(MODULE_PATH.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
