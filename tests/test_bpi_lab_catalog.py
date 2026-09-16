#!/usr/bin/env python3
"""跨板離線回歸；旁檔不是實際內容核對，只對臨時小檔做完整 SHA 驗證。"""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock


MODULE = Path(__file__).resolve().parents[1] / "tools" / "bpi_lab_catalog.py"
SPEC = importlib.util.spec_from_file_location("bpi_lab_catalog", MODULE)
catalog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(catalog)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "images"
        self.build = self.base / "build"
        self.root.mkdir()
        self.build.mkdir()
        self.config("bananapi", "sun7i", "armhf")
        self.image = self.make_image()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return path

    def config(self, profile, family, arch):
        self.write(self.build / f"config/boards/{profile}.conf", f'BOARDFAMILY="{family}"\n')
        self.write(self.build / f"config/sources/families/{family}.conf", f'declare -g ARCH="{arch}"\n')

    def make_image(self, board="bpi-m1", profile="Bananapi", release="bookworm",
                   variant="minimal", branch="current", kernel="6.18.49", data=b"\x00\x01\x02\x03"):
        suffix = "_" + variant if variant else ""
        path = self.root / board / f"Armbian-unofficial_26.11.0-trunk_{profile}_{release}_{branch}_{kernel}{suffix}.img.xz"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.write(Path(str(path) + ".sha"), hashlib.sha256(data).hexdigest() + "  " + path.name + "\n")
        return path

    @property
    def sha(self):
        return Path(str(self.image) + ".sha")

    def scan(self):
        return catalog.scan(self.root, self.build)

    def entry(self):
        return self.scan()["entries"][0]

    def codes(self, value):
        return {issue["code"] for issue in value["issues"]}

    def test_schema_and_hardware_status_are_never_promoted(self):
        report = self.scan()
        self.assertEqual(report["schema"], "bpi-lab-catalog-v1")
        self.assertEqual(report["root"], str(self.root))
        self.assertTrue(report["metadata_only"])
        self.assertFalse(report["hardware_validated"])
        self.assertEqual(report["issues"], [])
        row = report["boards"][0]
        self.assertEqual(row["hardware_status"], "awaiting_hardware")
        self.assertEqual(row["backend_status"], "unqualified")
        self.assertEqual(row["architecture"], "arm32")
        self.assertEqual(row["config_path"], "config/boards/bananapi.conf")
        self.assertEqual(row["config_sha256"], hashlib.sha256((self.build / row["config_path"]).read_bytes()).hexdigest())
        entry = report["entries"][0]
        self.assertEqual(set(entry["identity"]), set(catalog.IDENTITY_FIELDS))
        self.assertEqual(entry["compressed_bytes"], 4)
        self.assertEqual(entry["artifact_board"], "bananapi")
        self.assertEqual(entry["family"], "sun7i")
        self.assertFalse(entry["source_verified"])
        json.dumps(report, ensure_ascii=False)

    def test_sidecar_is_a_claim_not_content_verification(self):
        self.sha.write_text("a" * 64 + "  " + self.image.name + "\n")
        entry = self.entry()
        self.assertEqual(entry["expected_sha256"], "a" * 64)
        self.assertFalse(entry["source_verified"])
        report = catalog.verify_entry(self.root, entry)
        self.assertFalse(report["source_verified"])
        self.assertIn("sha256_mismatch", self.codes(report))

    def test_scan_never_opens_image_content_or_runs_shell(self):
        real_open = os.open

        def guard(path, flags, *args, **kwargs):
            self.assertFalse(str(path).endswith(".img.xz"), "盤點不得開啟映像內容")
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            return real_open(path, flags, *args, **kwargs)

        with mock.patch.object(catalog.os, "open", side_effect=guard), \
                mock.patch("subprocess.Popen", side_effect=AssertionError("不得啟動子程序")):
            self.assertEqual(len(self.scan()["entries"]), 1)

    def test_identity_and_id_are_stable_and_originals_unchanged(self):
        before = {path: catalog._identity(path.stat()) for path in (self.image, self.sha)}
        first, second = self.scan(), self.scan()
        self.assertEqual(first, second)
        self.assertEqual(before, {path: catalog._identity(path.stat()) for path in before})
        os.utime(self.image, ns=(self.image.stat().st_atime_ns, self.image.stat().st_mtime_ns + 10))
        self.assertNotEqual(first["entries"][0]["image_id"], self.entry()["image_id"])

    def test_metadata_id_changes_with_sidecar_claim(self):
        old = self.entry()["image_id"]
        self.sha.write_text("b" * 64 + "  " + self.image.name + "\n")
        self.assertNotEqual(old, self.entry()["image_id"])

    def test_sort_architecture_release_board_variant(self):
        self.config("bananapim4zero", "sun50iw9-bpi", "arm64")
        self.config("bananapif3", "spacemit", "riscv64")
        self.make_image("bpi-f3", "Bananapif3", "jammy")
        self.make_image("bpi-m4z", "Bananapim4zero", "bookworm")
        self.make_image(release="noble")
        self.make_image(variant="xfce_desktop")
        self.make_image("bpi-future", "Future", "bookworm")
        entries = self.scan()["entries"]
        self.assertEqual([entry["architecture"] for entry in entries],
                         ["arm32", "arm32", "arm32", "arm64", "riscv64", "unknown"])
        self.assertEqual([(entry["release"], entry["variant"]) for entry in entries[:3]],
                         [("bookworm", "minimal"), ("bookworm", "xfce_desktop"), ("noble", "minimal")])

    def test_actual_aliases_have_45_explicit_profiles(self):
        self.assertEqual(len(catalog.BOARD_PROFILES), 45)
        self.assertEqual(catalog.BOARD_PROFILES["bpi-m1"], "bananapi")
        self.assertEqual(catalog.BOARD_PROFILES["bpi-m4z-emac"], "bananapim4zeroemac")
        self.assertEqual(catalog.BOARD_PROFILES["bpi-ai2n"], "bpi-ai2n")

    def test_registry_preserves_inventory_gaps_without_hardware_pass(self):
        path = MODULE.parents[1] / "docs/evidence/bpi-multiboard-lab-20260917/board-registry.json"
        registry = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(registry["board_count"], 45)
        self.assertEqual(registry["image_count"], 444)
        self.assertEqual(registry["architecture_board_counts"],
                         {"arm32": 16, "arm64": 26, "riscv64": 3, "unknown": 0})
        self.assertEqual(sum(row["image_count"] for row in registry["boards"]), 444)
        self.assertEqual(sum(len(row["missing_release_variants"]) for row in registry["boards"]), 6)
        self.assertFalse(registry["hardware_validated"])
        self.assertFalse(registry["source_verified"])
        for row in registry["boards"]:
            self.assertEqual(catalog.BOARD_PROFILES[row["board"]], row["artifact_board"])
            self.assertEqual(row["hardware_status"], "awaiting_hardware")
            self.assertEqual(row["backend_status"], "unqualified")
            self.assertRegex(row["config_sha256"], r"^[0-9a-f]{64}$")

    def test_unknown_profile_and_board_are_not_guessed(self):
        self.make_image("bpi-new", "Bananapim1")
        report = self.scan()
        self.assertIn("unknown_board", self.codes(report))
        self.assertIn("unknown_profile", self.codes(report))
        unknown = next(entry for entry in report["entries"] if entry["board"] == "bpi-new")
        self.assertEqual(unknown["architecture"], "unknown")

    def test_directory_profile_mismatch_is_preserved(self):
        self.make_image("bpi-m4z", "Bananapi")
        self.assertIn("board_profile_mismatch", self.codes(self.scan()))

    def test_unknown_role_release_branch_and_kernel_zero(self):
        for variant in ("server", "", "gnome_desktop"):
            self.make_image(variant=variant, release="future", branch="custom", kernel="0")
        report = self.scan()
        for code in ("unknown_variant", "unknown_release", "unknown_branch", "unknown_kernel"):
            self.assertIn(code, self.codes(report))

    def test_unknown_filename_is_retained(self):
        (self.image.parent / "mystery.img.xz").write_bytes(b"x")
        report = self.scan()
        self.assertEqual(len(report["entries"]), 2)
        self.assertIn("unknown_filename", self.codes(report))

    def test_alternative_distribution_prefix(self):
        path = self.image.with_name(self.image.name.replace("Armbian-unofficial", "Bananapi-Armbian"))
        self.image.rename(path)
        self.sha.unlink()
        self.write(Path(str(path) + ".sha"), hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n")
        self.assertEqual(self.scan()["issues"], [])

    def test_missing_sha_is_not_silently_omitted(self):
        self.sha.unlink()
        report = self.scan()
        self.assertEqual(len(report["entries"]), 1)
        self.assertIsNone(report["entries"][0]["expected_sha256"])
        self.assertIn("io_error", self.codes(report))

    def test_sidecar_rejects_wrong_path_duplicate_or_malformed_records(self):
        digest = "a" * 64
        good = digest + "  " + self.image.name + "\n"
        cases = [(digest + "  other.img.xz\n", "sha_filename_mismatch"),
                 (digest + "  ../" + self.image.name, "sha_filename_mismatch"),
                 (digest + "  " + str(self.image), "sha_filename_mismatch"),
                 (digest + "  ./" + self.image.name, "sha_filename_mismatch"),
                 (good * 2, "sha_record_count"), ("", "sha_record_count"),
                 (digest, "sha_format"), ("z" * 64 + "  " + self.image.name, "sha_format")]
        for content, code in cases:
            with self.subTest(code=code, content=content[:80]):
                self.sha.write_text(content)
                entry = self.entry()
                self.assertIsNone(entry["expected_sha256"])
                self.assertIn(code, self.codes(entry))

    def test_binary_sha_marker_uppercase_and_crlf(self):
        self.sha.write_bytes(("A" * 64 + " *" + self.image.name + "\r\n").encode())
        self.assertEqual(self.entry()["expected_sha256"], "a" * 64)

    def test_sidecar_size_limit_and_encoding(self):
        for content, code in ((b"x" * (catalog.MAX_SHA_BYTES + 1), "metadata_too_large"),
                              (b"\xff", "invalid_encoding")):
            self.sha.write_bytes(content)
            self.assertIn(code, self.codes(self.entry()))

    def test_orphan_sha_is_reported(self):
        self.image.unlink()
        report = self.scan()
        self.assertIn("orphan_sha", self.codes(report))
        self.assertIn("empty_board", self.codes(report))
        self.assertEqual(len(report["boards"]), 1)

    def test_empty_image_is_retained(self):
        self.image.write_bytes(b"")
        self.assertIn("empty_image", self.codes(self.entry()))

    def test_image_symlink_fifo_and_directory_are_retained_without_io(self):
        self.image.unlink()
        for kind in ("symlink", "fifo", "directory"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    self.image.symlink_to(self.sha)
                elif kind == "fifo":
                    os.mkfifo(self.image)
                else:
                    self.image.mkdir()
                entry = self.entry()
                self.assertIn("not_regular", self.codes(entry))
                if kind == "directory":
                    self.image.rmdir()
                else:
                    self.image.unlink()

    def test_sidecar_symlink_and_fifo_are_rejected(self):
        self.sha.unlink()
        for kind in ("symlink", "fifo"):
            if kind == "symlink":
                self.sha.symlink_to(self.image)
            else:
                os.mkfifo(self.sha)
            self.assertIn("not_regular", self.codes(self.entry()))
            self.sha.unlink()

    def test_root_and_ancestor_symlinks_are_rejected(self):
        link = self.base / "link"
        link.symlink_to(self.base, target_is_directory=True)
        for root in (link, link / "images", self.root / ".." / "images"):
            report = catalog.scan(root, self.build)
            self.assertTrue(report["issues"])
            self.assertEqual(report["entries"], [])

    def test_directory_symlink_is_not_followed(self):
        (self.root / "bpi-external").symlink_to(self.build, target_is_directory=True)
        report = self.scan()
        self.assertEqual(len(report["entries"]), 1)
        self.assertIn("not_regular", self.codes(report))

    def test_unsafe_image_name_remains_an_issue_and_entry(self):
        (self.image.parent / "bad name.img.xz").write_bytes(b"x")
        report = self.scan()
        self.assertEqual(len(report["entries"]), 2)
        self.assertIn("unsafe_path", self.codes(report))

    def test_hidden_regular_auxiliary_file_is_not_an_error(self):
        (self.root / ".latest-rebuild.lock").write_bytes(b"")
        self.assertEqual(self.scan()["issues"], [])

    def test_static_family_and_board_includes_are_hashed(self):
        self.write(self.build / "config/boards/bananapi.conf",
                   'source "${SRC}/config/boards/include/base.inc"\n')
        self.write(self.build / "config/boards/include/base.inc", "BOARDFAMILY='sun7i'\n")
        self.write(self.build / "config/sources/families/sun7i.conf",
                   'source "${BASH_SOURCE%/*}/include/arch.inc"\n')
        self.write(self.build / "config/sources/families/include/arch.inc", "export ARCH=armhf # 架構\n")
        row = self.scan()["boards"][0]
        self.assertEqual(row["architecture"], "arm32")
        self.assertEqual(len(row["config_sources"]), 4)
        self.assertEqual(row["issues"], [])

    def test_identical_inherited_family_is_not_conflicting(self):
        self.write(self.build / "config/boards/include/base.inc", 'BOARDFAMILY="sun7i"\n')
        self.write(self.build / "config/boards/bananapi.conf",
                   'source "${SRC}/config/boards/include/base.inc"\nBOARDFAMILY="sun7i"\n')
        self.assertEqual(self.entry()["architecture"], "arm32")

    def test_dynamic_conditional_function_and_heredoc_arch_are_not_guessed(self):
        values = ['ARCH="$(touch /tmp/never-run)"\n', 'ARCH="${ARCH:-arm64}"\n',
                  'if true; then\nARCH=arm64\nfi\n', 'f() {\nARCH=arm64\n}\n',
                  'cat <<EOF\nARCH=arm64\nEOF\n', 'ARCH=arm64\nARCH=armhf\n']
        for value in values:
            with self.subTest(value=value):
                self.write(self.build / "config/sources/families/sun7i.conf", value)
                entry = self.entry()
                self.assertEqual(entry["architecture"], "unknown")
                self.assertIn("unknown_architecture", self.codes(entry))

    def test_includes_cannot_escape_config_or_form_cycles(self):
        values = ['source "${SRC}/../outside"\n', 'source /etc/passwd\n',
                  'source "${SRC}/tools/outside"\n', 'source "${UNKNOWN}/config"\n',
                  'source "${SRC}/config/boards/bananapi.conf"\n']
        for value in values:
            with self.subTest(value=value):
                self.write(self.build / "config/boards/bananapi.conf", value)
                entry = self.entry()
                self.assertEqual(entry["architecture"], "unknown")
                self.assertTrue(entry["issues"])

    def test_duplicate_config_is_not_arbitrarily_selected(self):
        self.write(self.build / "config/boards/bananapi.csc", 'BOARDFAMILY="sun7i"\n')
        entry = self.entry()
        self.assertIn("duplicate_config", self.codes(entry))
        self.assertEqual(entry["architecture"], "unknown")

    def test_config_symlink_is_rejected(self):
        path = self.build / "config/boards/bananapi.conf"
        path.unlink()
        path.symlink_to(self.sha)
        self.assertIn("not_regular", self.codes(self.entry()))

    def test_missing_family_and_unsupported_architecture(self):
        path = self.build / "config/sources/families/sun7i.conf"
        path.unlink()
        self.assertIn("unknown_architecture", self.codes(self.entry()))
        self.write(path, "ARCH=amd64\n")
        self.assertIn("unknown_architecture", self.codes(self.entry()))

    def test_board_architecture_conflict_is_not_ignored(self):
        self.write(self.build / "config/boards/bananapi.conf", 'BOARDFAMILY="sun7i"\nARCH=arm64\n')
        self.assertIn("unknown_architecture", self.codes(self.entry()))

    def test_conditional_include_cannot_silently_override_architecture(self):
        self.write(self.build / "config/sources/families/sun7i.conf",
                   'ARCH=armhf\nif true; then\nsource "${SRC}/config/boards/other.inc"\nfi\n')
        entry = self.entry()
        self.assertEqual(entry["architecture"], "unknown")
        self.assertIn("dynamic_include", self.codes(entry))

    def test_config_size_limit_and_invalid_encoding(self):
        path = self.build / "config/boards/bananapi.conf"
        for blob, code in ((b"x" * (catalog.MAX_CONFIG_BYTES + 1), "metadata_too_large"),
                           (b"\xff", "invalid_encoding")):
            path.write_bytes(blob)
            self.assertIn(code, self.codes(self.entry()))

    def test_nested_images_remain_under_the_top_level_board(self):
        directory = self.image.parent / "nested"
        directory.mkdir()
        self.image.rename(directory / self.image.name)
        self.sha.rename(directory / self.sha.name)
        entry = self.entry()
        self.assertEqual(entry["board"], "bpi-m1")
        self.assertEqual(entry["relative_path"].split("/")[1], "nested")
        self.assertEqual(entry["issues"], [])

    def test_depth_limit_is_an_explicit_issue(self):
        (self.image.parent / "nested").mkdir()
        with mock.patch.object(catalog, "MAX_DEPTH", 1):
            self.assertIn("depth_limit", self.codes(self.scan()))

    def test_config_mutation_after_parsing_invalidates_architecture(self):
        original = catalog._ConfigReader.board

        def changed(reader, profile, index):
            result = original(reader, profile, index)
            path = self.build / "config/sources/families/sun7i.conf"
            info = path.stat()
            os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1))
            return result

        with mock.patch.object(catalog._ConfigReader, "board", changed):
            entry = self.entry()
            self.assertEqual(entry["architecture"], "unknown")
            self.assertIn("identity_changed", self.codes(entry))

    def test_image_mutation_during_config_parsing_is_recorded(self):
        original = catalog._ConfigReader.board

        def changed(reader, profile, index):
            result = original(reader, profile, index)
            info = self.image.stat()
            os.utime(self.image, ns=(info.st_atime_ns, info.st_mtime_ns + 1))
            return result

        with mock.patch.object(catalog._ConfigReader, "board", changed):
            self.assertIn("identity_changed", self.codes(self.entry()))

    def test_scan_reports_directory_replacement_without_following_link(self):
        original = catalog._sha

        def replaced(*args):
            result = original(*args)
            self.image.parent.rename(self.root / "old")
            (self.root / "bpi-m1").symlink_to(self.root / "old", target_is_directory=True)
            return result

        with mock.patch.object(catalog, "_sha", side_effect=replaced):
            report = self.scan()
            self.assertEqual(len(report["entries"]), 1)
            self.assertTrue(report["entries"][0]["issues"])

    def test_small_read_detects_growth_and_shrink(self):
        for blob in (b"x", b"x" * (catalog.MAX_SHA_BYTES + 1)):
            with self.sha.open("rb") as stream:
                fake = mock.Mock()
                fake.read.return_value = blob
                with mock.patch.object(catalog, "_regular") as opening:
                    opening.return_value.__enter__.return_value = (fake, catalog._identity(os.fstat(stream.fileno())))
                    with catalog._root(self.root) as root_fd, self.assertRaises(catalog.CatalogError):
                        catalog._read_small(root_fd, self.sha.relative_to(self.root).as_posix(), catalog.MAX_SHA_BYTES)

    def test_missing_build_root_does_not_omit_images(self):
        report = catalog.scan(self.root, self.base / "missing")
        self.assertEqual(len(report["entries"]), 1)
        self.assertIn("config_unavailable", self.codes(report))

    def test_mtime_mutation_during_scan_is_recorded(self):
        original = catalog._sha

        def changing(*args):
            result = original(*args)
            info = self.image.stat()
            os.utime(self.image, ns=(info.st_atime_ns, info.st_mtime_ns + 1))
            return result

        with mock.patch.object(catalog, "_sha", side_effect=changing):
            self.assertIn("identity_changed", self.codes(self.entry()))

    def test_sidecar_replacement_during_scan_is_recorded(self):
        original = catalog._sha

        def replacing(*args):
            result = original(*args)
            content = self.sha.read_bytes()
            self.sha.rename(self.sha.with_suffix(".saved"))
            self.sha.write_bytes(content)
            return result

        with mock.patch.object(catalog, "_sha", side_effect=replacing):
            self.assertIn("identity_changed", self.codes(self.entry()))

    def test_small_fixture_full_verification_does_not_mutate_entry(self):
        entry = self.entry()
        before = copy.deepcopy(entry)
        report = catalog.verify_entry(self.root, entry)
        self.assertTrue(report["source_verified"])
        self.assertEqual(report["verified_bytes"], 4)
        self.assertFalse(report["hardware_validated"])
        self.assertFalse(report["metadata_only"])
        self.assertEqual(entry, before)

    def test_verify_rejects_traversal_and_invalid_identity(self):
        original = self.entry()
        for name in ("../outside", str(self.image), "a/../outside", "a//b", "./a", "", None):
            entry = copy.deepcopy(original)
            entry["relative_path"] = name
            self.assertIn("unsafe_path", self.codes(catalog.verify_entry(self.root, entry)))
        for identity in (None, {}, {**original["identity"], "st_size": True}):
            entry = copy.deepcopy(original)
            entry["identity"] = identity
            self.assertIn("invalid_identity", self.codes(catalog.verify_entry(self.root, entry)))

    def test_verify_rejects_missing_sha_and_metadata_issues(self):
        entry = self.entry()
        entry["expected_sha256"] = None
        self.assertIn("missing_sha256", self.codes(catalog.verify_entry(self.root, entry)))
        entry = self.entry()
        entry["issues"].append({"code": "unknown_variant"})
        self.assertIn("entry_has_issues", self.codes(catalog.verify_entry(self.root, entry)))

    def test_verify_rejects_changed_file_and_symlink(self):
        entry = self.entry()
        self.image.write_bytes(b"changed")
        self.assertIn("identity_changed", self.codes(catalog.verify_entry(self.root, entry)))
        self.image.unlink()
        self.image.symlink_to(self.sha)
        self.assertIn("not_regular", self.codes(catalog.verify_entry(self.root, entry)))

    def test_verify_rejects_directory_symlink(self):
        entry = self.entry()
        self.image.parent.rename(self.root / "saved")
        (self.root / "bpi-m1").symlink_to(self.root / "saved", target_is_directory=True)
        self.assertFalse(catalog.verify_entry(self.root, entry)["source_verified"])

    def test_verify_rejects_tampered_image_id(self):
        entry = self.entry()
        entry["image_id"] = "0" * 64
        self.assertIn("image_id_mismatch", self.codes(catalog.verify_entry(self.root, entry)))

    def test_verify_fifo_and_directory_do_not_block(self):
        entry = self.entry()
        self.image.unlink()
        os.mkfifo(self.image)
        self.assertIn("not_regular", self.codes(catalog.verify_entry(self.root, entry)))
        self.image.unlink()
        self.image.mkdir()
        self.assertIn("not_regular", self.codes(catalog.verify_entry(self.root, entry)))

    def test_verify_rejects_root_replacement_after_read(self):
        entry = self.entry()
        original = catalog._unchanged

        def replaced(*args):
            original(*args)
            self.root.rename(self.base / "old-root")
            self.root.mkdir()

        with mock.patch.object(catalog, "_unchanged", side_effect=replaced):
            self.assertIn("identity_changed", self.codes(catalog.verify_entry(self.root, entry)))

    def test_regular_rejects_device_type_before_content_open(self):
        with catalog._root(self.root) as root_fd:
            with mock.patch.object(catalog.os, "open", wraps=os.open) as opening, \
                    mock.patch.object(catalog.os, "fstat", return_value=mock.Mock(st_mode=stat.S_IFCHR)):
                with self.assertRaises(catalog.CatalogError):
                    with catalog._regular(root_fd, self.image.relative_to(self.root).as_posix()):
                        self.fail("不得開啟裝置")
                self.assertFalse(any(str(call.args[0]).startswith("/proc/") for call in opening.call_args_list))

    def test_verify_detects_mtime_ctime_and_inode_change_after_read(self):
        entry = self.entry()
        original = os.fstat
        for field in ("st_mtime_ns", "st_ctime_ns", "st_ino", "st_size"):
            calls = 0

            def changed(fd):
                nonlocal calls
                info = original(fd)
                if stat.S_ISREG(info.st_mode):
                    calls += 1
                    if calls == 3:
                        result = mock.Mock(wraps=info)
                        setattr(result, field, getattr(info, field) + 1)
                        return result
                return info

            with self.subTest(field=field), mock.patch.object(catalog.os, "fstat", side_effect=changed):
                self.assertIn("identity_changed", self.codes(catalog.verify_entry(self.root, entry)))

    def test_verify_bounds_reads_and_rejects_growth_or_shrink(self):
        entry = self.entry()
        for chunks in ([b""], [b"\x00\x01\x02\x03", b"x"]):
            stream = mock.Mock()
            stream.read.side_effect = chunks
            with mock.patch.object(catalog, "_regular") as opening:
                opening.return_value.__enter__.return_value = (stream, entry["identity"])
                report = catalog.verify_entry(self.root, entry)
                self.assertIn("identity_changed", self.codes(report))
                self.assertTrue(all(call.args[0] <= entry["compressed_bytes"] for call in stream.read.call_args_list))

    def test_verify_detects_path_replacement_while_descriptor_is_pinned(self):
        entry = self.entry()
        original = catalog._unchanged

        def replaced(root_fd, name, identity):
            self.image.rename(self.image.with_suffix(".saved"))
            self.image.write_bytes(b"\x00\x01\x02\x03")
            return original(root_fd, name, identity)

        with mock.patch.object(catalog, "_unchanged", side_effect=replaced):
            self.assertIn("identity_changed", self.codes(catalog.verify_entry(self.root, entry)))


if __name__ == "__main__":
    unittest.main()
