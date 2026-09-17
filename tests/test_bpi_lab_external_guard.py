"""固定 SD 保護、目標 runtime 與小型 newc 封裝離線回歸。"""

import copy
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import bpi_lab_external_guard as guard
from tools import bpi_lab_external_linux as linux
from test_bpi_lab_external_linux import RootFixture, UUID


def elf(machine=183):
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    header = ident + struct.pack("<HHIQQQIHHHHHH", 2, machine, 1, 0x400078, 64, 0, 0, 64, 56, 1, 64, 0, 0)
    segment = struct.pack("<IIQQQQQQ", 1, 5, 0, 0x400000, 0x400000, 124, 124, 4096)
    return header + segment + bytes(4)


INIT = b'''#!/bin/sh
mountroot
run_scripts /scripts/init-bottom
exec run-init "${rootmnt}" "${init}" "$@"
'''
FUNCTIONS = b'''run_scripts()
{
    initdir=${1}
    [ ! -d "${initdir}" ] && return
    shift
    . "${initdir}/ORDER"
}
'''


class ArchiveHardlinkTests(unittest.TestCase):
    @staticmethod
    def raw(members):
        output = bytearray()
        for row in [*members, {"name": "TRAILER!!!"}]:
            name = row["name"].encode() + b"\0"
            data = row.get("data", b"")
            values = (row.get("inode", 42), row.get("mode", 0o100755), row.get("uid", 0), 0,
                      row.get("links", 2), 0, len(data), 0, 1, 0, 0, len(name), 0)
            output.extend(b"070701" + b"".join(f"{value:08x}".encode() for value in values) + name)
            output.extend(bytes(-len(output) % 4))
            output.extend(data)
            output.extend(bytes(-len(output) % 4))
        return bytes(output)

    def test_complete_group_preserves_aliases_and_data_in_either_order(self):
        for data_index in (0, 1, None):
            members = [{"name": name, "data": b"payload" if data_index == index else b""}
                       for index, name in enumerate(("bin/a", "bin/b"))]
            entries, _ = guard.parse_archive(self.raw(members))
            self.assertEqual(entries["bin/a"], entries["bin/b"])
            self.assertEqual(entries["bin/a"]["hardlinks"], ("bin/a", "bin/b"))
            self.assertEqual(entries["bin/a"]["data"], b"" if data_index is None else b"payload")
            for compression in ("none", "gzip"):
                repacked = guard.archive(entries, compression)
                self.assertEqual(guard.parse_archive(repacked), (entries, compression))
                self.assertEqual(repacked, guard.archive(entries, compression))

    def test_incomplete_or_contradictory_groups_rejected(self):
        cases = [
            [{"name": "a"}],
            [{"name": "a"}, {"name": "b", "links": 3}],
            [{"name": "a"}, {"name": "b", "links": 1}],
            [{"name": "a"}, {"name": "b", "uid": 1}],
            [{"name": "a", "data": b"x"}, {"name": "b", "data": b"y"}],
            [{"name": "a", "mode": 0o120777, "data": b"b"}, {"name": "b"}],
            [{"name": "a", "links": 0}],
        ]
        for members in cases:
            with self.subTest(members=members), self.assertRaises(ValueError):
                guard.parse_archive(self.raw(members))

    def test_repack_rejects_missing_modified_or_forged_aliases(self):
        base, _ = guard.parse_archive(self.raw([{"name": "a"}, {"name": "b", "data": b"x"}]))
        cases = [copy.deepcopy(base) for _ in range(4)]
        del cases[0]["b"]
        cases[1]["b"]["data"] = b"y"
        cases[2]["a"]["hardlinks"] = ("a", "a")
        cases[3]["a"]["hardlinks"] = ("b", "c")
        for entries in cases:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                guard.archive(entries)

    def test_alias_cannot_be_a_parent_directory(self):
        entries, _ = guard.parse_archive(self.raw([{"name": "a"}, {"name": "b", "data": b"x"}]))
        with self.assertRaises(ValueError):
            guard._put(entries, "a/child", guard.entry(b"x"))

    def test_single_link_and_device_inode_collisions_do_not_merge(self):
        entries, _ = guard.parse_archive(self.raw([{"name": "a", "links": 1, "data": b"a"},
                                                  {"name": "b", "links": 1, "data": b"b"}]))
        self.assertNotIn("hardlinks", entries["a"])
        self.assertNotEqual(entries["a"]["data"], entries["b"]["data"])

    def test_archive_rejects_unknown_compression_and_reserved_name(self):
        with self.assertRaises(ValueError):
            guard.archive({}, "zstd")
        with self.assertRaises(ValueError):
            guard.archive({"TRAILER!!!": guard.entry(b"")})


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = RootFixture(self.root, readonly=False, initramfs=True)
        self.addCleanup(mock.patch.stopall)
        self.native_popen = guard.subprocess.Popen
        mock.patch.object(guard.subprocess, "Popen", side_effect=AssertionError("離線測試禁止真實子程序")).start()
        mock.patch.dict(guard.INIT_PROFILES, {(hashlib.sha256(INIT).hexdigest(), hashlib.sha256(FUNCTIONS).hexdigest()):
                                            "離線最小 initramfs 替身"}).start()

    def protect(self):
        f = self.fixture
        return guard.protect(f.expected, root_path=f.customer, **f.kwargs)

    def test_guard_sets_and_verifies_whole_sd_and_partitions(self):
        result = self.protect()
        self.assertEqual(result["status"], "protected")
        self.assertFalse(result["hardware_validated"])
        self.assertEqual([Path(path).name for path in self.fixture.ops.ro_sets], ["mmcblk0", "mmcblk0p1"])
        self.assertEqual(result["observation"]["sd_readback"]["sha256"], self.fixture.sd["full_sha256"])
        self.assertFalse(self.fixture.ops.fds)

    def test_wrong_root_never_sets_any_device_readonly(self):
        f = self.fixture
        f.ops.root_rdev = f.ops.nodes[str(f.dev / "mmcblk0p1")]["rdev"]
        with self.assertRaises(ValueError):
            self.protect()
        self.assertFalse(f.ops.ro_sets)

    def test_falsey_ops_never_fall_back_to_native(self):
        with mock.patch.object(linux.media, "NativeOps", side_effect=AssertionError("禁止退回原生 I/O")):
            with self.assertRaisesRegex(ValueError, "假值"):
                guard.protect(self.fixture.expected, ops=False)

    def test_fstab_boot_or_swap_on_sd_rejected_before_ioctl(self):
        f = self.fixture
        sources = ("LABEL=rescue /boot ext4 defaults 0 2", "UUID=87654321-4321-4321-abcd-123456789012 none swap sw 0 0",
                   str(f.dev / "mmcblk0p1") + " /boot ext4 defaults 0 2")
        for source in sources:
            f.put(f.customer / "etc/fstab", source + "\n")
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.protect()
            self.assertFalse(f.ops.ro_sets)

    def test_missing_or_unknown_fstab_does_not_handoff(self):
        f = self.fixture
        f.put(f.customer / "etc/fstab", "PARTUUID=unknown /boot ext4 defaults 0 2\n")
        with self.assertRaises(ValueError):
            self.protect()
        (f.customer / "etc/fstab").unlink()
        with self.assertRaises(OSError):
            self.protect()
        self.assertFalse(f.ops.ro_sets)

    def test_failed_blkroset_never_claims_protection(self):
        self.fixture.ops.ro_fail = True
        with self.assertRaisesRegex(ValueError, "BLKROSET"):
            self.protect()
        self.assertFalse(self.fixture.ops.fds)

    def test_hash_failure_retains_readonly_without_handoff(self):
        f = self.fixture
        f.ops.data[str(f.dev / "mmcblk0")][0] = 1
        with self.assertRaisesRegex(ValueError, "摘要"):
            self.protect()
        self.assertEqual([f.ops.read_only[str(f.dev / name)] for name in ("mmcblk0", "mmcblk0p1")], [1, 1])

    def test_customer_fstab_symlink_escape_rejected(self):
        f = self.fixture
        (f.customer / "etc/fstab").unlink()
        outside = self.root / "outside-fstab"
        outside.write_text(f"UUID={UUID} / ext4 defaults 0 1\n")
        (f.customer / "etc/fstab").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "逃出"):
            self.protect()
        self.assertFalse(f.ops.ro_sets)

    def ref(self, name, blob):
        path = self.root / name
        path.write_bytes(blob)
        return {"path": str(path), "sha256": hashlib.sha256(blob).hexdigest()}

    def bundle_fixture(self):
        original = {}
        for name, blob, mode in (("init", INIT, 0o100755), ("scripts/functions", FUNCTIONS, 0o100644),
                                 ("scripts/init-bottom/ORDER", "# 原排程\n".encode(), 0o100644), ("bin/sh", elf(), 0o100755)):
            guard._put(original, name, guard.entry(blob, mode))
        addition = {}
        for name in ("usr/bin/python3", "usr/sbin/blkid", "usr/lib/python3.11/lib-dynload/_fixture.so"):
            guard._put(addition, name, guard.entry(elf(), 0o100755))
        bundle = {"schema": guard.BUNDLE_SCHEMA, "architecture": "arm64",
                  "archive": self.ref("runtime.cpio", guard.archive(addition)),
                  "python": "/usr/bin/python3", "blkid": "/usr/sbin/blkid",
                  "library_dirs": ["/usr/lib"], "stdlib_dirs": ["/usr/lib/python3.11"],
                  "init_profile": {"init_sha256": hashlib.sha256(INIT).hexdigest(), "functions_sha256": hashlib.sha256(FUNCTIONS).hexdigest()}}
        probe = {"schema": "bpi-lab-external-runtime-probe-v1", "hardware_validated": False, "architecture": "arm64",
                 "python": {"python_major": 3, "machine": "aarch64", "imports": "complete"},
                 "blkid_stdout_sha256": "a" * 64, "emulator": None, "runtime_archive_sha256": bundle["archive"]["sha256"]}
        return original, addition, bundle, probe

    def build_fixture(self):
        original, addition, bundle, probe = self.bundle_fixture()
        origin_ref = self.ref("original.initrd", guard.archive(original, "gzip"))
        bundle_ref = self.ref("bundle.json", linux.encoded(bundle))
        with mock.patch.object(guard, "_probe", return_value=probe) as execute:
            result = guard.build(origin_ref, self.fixture.expected, bundle_ref, self.root / "guard")
        execute.assert_called_once()
        return result, origin_ref, bundle_ref

    def test_newc_roundtrip_and_reproducibility(self):
        original, _, _, _ = self.bundle_fixture()
        for compression in ("none", "gzip"):
            blob = guard.archive(original, compression)
            self.assertEqual(guard.parse_archive(blob), (original, compression))
            self.assertEqual(blob, guard.archive(original, compression))

    def test_truncated_archive_or_trailing_archive_rejected(self):
        original, _, _, _ = self.bundle_fixture()
        blob = guard.archive(original)
        for changed in (blob[:-20], blob + blob, b"not-cpio", b"070701" + bytes(104)):
            with self.subTest(size=len(changed)), self.assertRaises(ValueError):
                guard.parse_archive(changed)

    def test_symlink_escape_and_cycle_rejected(self):
        entries = {"escape": guard.entry(b"../host", 0o120777), "cycle": guard.entry(b"cycle", 0o120777)}
        for name in entries:
            with self.subTest(name=name), self.assertRaises(ValueError):
                guard.resolve_entry(entries, name)

    def test_host_x86_or_missing_executable_rejected(self):
        with self.assertRaisesRegex(ValueError, "目標架構"):
            guard.elf_info(elf(62), "arm64")
        with self.assertRaises(ValueError):
            guard.executable_closure({}, ["/usr/bin/python3"], "arm64", ["/usr/lib"])

    def test_unknown_init_flow_rejected_even_when_hashes_rebound(self):
        original, _, bundle, _ = self.bundle_fixture()
        blob = INIT.replace(b"run_scripts /scripts/init-bottom", b"if false; then\nrun_scripts /scripts/init-bottom\nfi")
        original["init"]["data"] = blob
        bundle["init_profile"]["init_sha256"] = hashlib.sha256(blob).hexdigest()
        with self.assertRaisesRegex(ValueError, "未知 initramfs"):
            guard._init_order(original, bundle["init_profile"])

    def test_build_adds_real_order_hook_and_marks_derived(self):
        result, original, _ = self.build_fixture()
        self.assertTrue(result["derived"])
        self.assertFalse(result["hardware_validated"])
        self.assertFalse(result["original_boot_chain_verified"])
        entries, _ = guard.parse_archive(Path(result["derived_initrd"]["path"]).read_bytes())
        self.assertTrue(entries["scripts/init-bottom/ORDER"]["data"].startswith(
            ("if ! LD_LIBRARY_PATH=/usr/lib /" + guard.HOOK_PATH + "; then\n").encode()))
        self.assertIn(b"while :; do :; done", entries[guard.HOOK_PATH]["data"])
        self.assertEqual(hashlib.sha256(Path(original["path"]).read_bytes()).hexdigest(), original["sha256"])
        self.assertEqual(guard.validate_manifest(result, self.fixture.expected), result)

    def test_missing_runtime_or_wrong_bundle_architecture_rejected(self):
        original, _, bundle, _ = self.bundle_fixture()
        bundle["architecture"] = "arm"
        with self.assertRaises(ValueError):
            guard.build(self.ref("original.initrd", guard.archive(original)), self.fixture.expected,
                        self.ref("bundle.json", linux.encoded(bundle)), self.root / "guard")
        self.assertFalse((self.root / "guard").exists())

    def test_runtime_execution_probe_failure_blocks_artifact(self):
        original, _, bundle, _ = self.bundle_fixture()
        with mock.patch.object(guard, "_probe", side_effect=ValueError("目標執行探測失敗")):
            with self.assertRaises(ValueError):
                guard.build(self.ref("original.initrd", guard.archive(original)), self.fixture.expected,
                            self.ref("bundle.json", linux.encoded(bundle)), self.root / "guard")
        self.assertFalse((self.root / "guard").exists())

    def test_probe_uses_staged_target_tools_and_ignores_unreferenced_dynamic_link(self):
        original, _, bundle, _ = self.bundle_fixture()
        bundle["library_dirs"].append("/opt/bpi-libs")
        guard._put(original, "etc/mtab", guard.entry(b"/proc/mounts", 0o120777))
        entries, _, _ = guard._assemble(guard.archive(original), self.fixture.expected, bundle,
                                       Path(bundle["archive"]["path"]).read_bytes())
        emulator = self.ref("qemu-aarch64-static", b"fixture")
        Path(emulator["path"]).chmod(0o755)
        calls = []
        self.assertTrue(entries["scripts/init-bottom/ORDER"]["data"].startswith(
            ("if ! LD_LIBRARY_PATH=/usr/lib:/opt/bpi-libs /" + guard.HOOK_PATH + "; then\n").encode()))

        def execute(argv, **kwargs):
            calls.append(argv)
            self.assertEqual(argv[:2], [emulator["path"], "-L"])
            staged = Path(argv[2])
            self.assertTrue(Path(argv[3]).is_relative_to(staged))
            self.assertEqual(Path(argv[3]).read_bytes(), elf())
            self.assertFalse((staged / "etc/mtab").exists())
            self.assertGreater(kwargs["timeout"], 0)
            if len(calls) == 1:
                self.assertNotIn("LD_LIBRARY_PATH", kwargs["env"])
                self.assertEqual(Path(argv[3]), staged / "bin/sh")
                self.assertEqual(argv[4:], ["-c", "printf '%s\\n' bpi-external-shell-v1"])
                stdout = b"bpi-external-shell-v1\n"
            elif len(calls) == 2:
                self.assertEqual(argv[4:8], ["-I", "-S", "-B", "-c"])
                self.assertIn("import bpi_lab_external_guard", argv[8])
                self.assertIn("sys.version_info>=(3,9)", argv[8])
                self.assertEqual((staged / guard.PREFIX / "bpi_lab_external_guard.py").read_bytes(), Path(guard.__file__).read_bytes())
                stdout = linux.encoded({"python_major": 3, "machine": "aarch64", "imports": "complete"})
            elif len(calls) == 3:
                self.assertEqual(argv[4:], ["--version"])
                stdout = b"blkid fixture\n"
            else:
                self.assertEqual(Path(argv[3]), staged / "bin/sh")
                self.assertEqual(argv[4:], ["-c", "printf '%s\\n' bpi-external-shell-v1"])
                stdout = b"bpi-external-shell-v1\n"
            if len(calls) > 1:
                self.assertEqual(kwargs["env"]["LD_LIBRARY_PATH"], str(staged / "usr/lib") + ":" + str(staged / "opt/bpi-libs"))
            return guard.subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr=b"")

        with mock.patch.object(guard.subprocess, "run", side_effect=execute):
            result = guard._probe(entries, bundle, emulator, 60)
        self.assertEqual(len(calls), 4)
        guard._validate_probe(result, bundle)

    def test_bootstrap_shell_cannot_depend_on_later_library_environment(self):
        original, _, bundle, _ = self.bundle_fixture()
        entries, _, _ = guard._assemble(guard.archive(original), self.fixture.expected, bundle,
                                       Path(bundle["archive"]["path"]).read_bytes())
        emulator = self.ref("qemu-aarch64-static", b"fixture")
        Path(emulator["path"]).chmod(0o755)

        def execute(argv, **kwargs):
            available = "LD_LIBRARY_PATH" in kwargs["env"]
            return guard.subprocess.CompletedProcess(argv, 0 if available else 127,
                                                      stdout=b"bpi-external-shell-v1\n" if available else b"",
                                                      stderr=b"" if available else b"libc.so.6\n")

        with mock.patch.object(guard.subprocess, "run", side_effect=execute) as execute:
            with self.assertRaisesRegex(ValueError, "初始 shell"):
                guard._probe(entries, bundle, emulator, 60)
        execute.assert_called_once()

    def test_explicit_qemu_guest_base_is_bounded_and_pinned(self):
        emulator = self.ref("qemu-aarch64-static", b"fixture")
        Path(emulator["path"]).chmod(0o755)
        ref = {**emulator, "guest_base": 2**32}
        self.assertEqual(guard._emulator_options(ref, "arm64"), [ref["path"], "-B", "0x100000000"])
        for value in (True, 0, -65536, 123, 2**48, "0x100000000"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                guard._emulator_options({**emulator, "guest_base": value}, "arm64")
        with self.assertRaises(ValueError):
            guard._emulator_options({**ref, "args": ["-strace"]}, "arm64")
        with self.assertRaises(ValueError):
            guard._emulator_options({**ref, "sha256": "0" * 64}, "arm64")

    def test_library_path_cannot_inject_order_shell_commands(self):
        _, _, bundle, _ = self.bundle_fixture()
        for path in ("/opt/bpi-libs;true", "/opt/$(true)", "/opt/lib:/host", "/opt/lib extra"):
            changed = copy.deepcopy(bundle)
            changed["library_dirs"] = [path]
            with self.subTest(path=path), self.assertRaises(ValueError):
                guard._bundle(changed)

    def test_order_stops_handoff_when_hook_interpreter_cannot_start(self):
        _, _, bundle, _ = self.bundle_fixture()
        script = guard._order_invocation(bundle).decode()
        script = script.replace("/" + guard.HOOK_PATH, str(self.root / "missing-hook"))
        # 僅把固定停止迴圈換成可判讀退出碼；不執行 init 或開啟媒體。
        script = script.replace("while :; do :; done", "exit 77") + "printf handoff\n"
        with mock.patch.object(guard.subprocess, "Popen", self.native_popen):
            result = guard.subprocess.run(["/bin/sh", "-c", script], stdin=guard.subprocess.DEVNULL,
                                          stdout=guard.subprocess.PIPE, stderr=guard.subprocess.PIPE,
                                          timeout=2, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})
        self.assertEqual(result.returncode, 77)
        self.assertNotIn(b"handoff", result.stdout)

    def test_comment_only_or_late_hook_is_not_execution_evidence(self):
        original, _, bundle, _ = self.bundle_fixture()
        for blob in (INIT.replace(b"run_scripts /scripts/init-bottom", b"# run_scripts /scripts/init-bottom"),
                     INIT.replace(b"mountroot\n", b"") + b"mountroot\n"):
            changed = copy.deepcopy(original)
            changed["init"]["data"] = blob
            profile = {**bundle["init_profile"], "init_sha256": hashlib.sha256(blob).hexdigest()}
            with self.assertRaises(ValueError):
                guard._init_order(changed, profile)

    def test_tampered_hook_or_original_input_cannot_revalidate(self):
        result, original, _ = self.build_fixture()
        path = Path(result["derived_initrd"]["path"])
        entries, compression = guard.parse_archive(path.read_bytes())
        entries[guard.HOOK_PATH]["data"] = b"#!/bin/sh\nexit 0\n"
        blob = guard.archive(entries, compression)
        path.write_bytes(blob)
        result["derived_initrd"].update(bytes=len(blob), sha256=hashlib.sha256(blob).hexdigest())
        with self.assertRaises(ValueError):
            guard.validate_manifest(result, self.fixture.expected)
        with self.assertRaises(ValueError):
            guard.validate_manifest(result, self.fixture.expected, original_initrd={**original, "sha256": "0" * 64})

    def test_rebound_launch_or_removed_dependencies_rejected(self):
        result, _, _ = self.build_fixture()
        missing = copy.deepcopy(result)
        missing["dependencies"] = []
        with self.assertRaises(ValueError):
            guard.validate_manifest(missing, self.fixture.expected)
        probe = copy.deepcopy(result)
        probe["runtime_probe"]["python"] = {"python_major": 3}
        with self.assertRaises(ValueError):
            guard.validate_manifest(probe, self.fixture.expected)
        path = Path(result["derived_initrd"]["path"])
        entries, compression = guard.parse_archive(path.read_bytes())
        entries[guard.PREFIX + "/run.py"]["data"] = b"pass\n"
        blob = guard.archive(entries, compression)
        path.write_bytes(blob)
        result["derived_initrd"].update(bytes=len(blob), sha256=hashlib.sha256(blob).hexdigest())
        with self.assertRaises(ValueError):
            guard.validate_manifest(result, self.fixture.expected)


if __name__ == "__main__":
    unittest.main()
