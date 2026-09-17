"""外部根與固定 SD 的全媒體、同次身分及固定 SSH 離線回歸。"""

import base64
import copy
import hashlib
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import bpi_lab_external_linux as linux
import test_bpi_lab_media as data

NONCE = "a" * 64
UUID = "12345678-1234-5678-90ab-123456789012"
BOOT_ID = "11111111-2222-3333-4444-555555555555"
KEY = "ssh-ed25519 " + base64.b64encode(b"\0\0\0\x0bssh-ed25519\0\0\0\x20" + bytes(range(32))).decode()


class RootOps(data.FakeOps):
    def read(self, path, maximum=65536):
        if str(path).startswith(("/etc/", "/run/")):
            path = self.customer / str(path).lstrip("/")
        return super().read(path, maximum)

    def exists(self, path):
        if str(path).startswith(("/etc/", "/run/")):
            path = self.customer / str(path).lstrip("/")
        return super().exists(path)

    def stat(self, path, *, follow_symlinks=True):
        if str(path) == self.root_mount:
            return SimpleNamespace(st_dev=self.root_rdev, st_ino=2, st_mode=stat.S_IFDIR | 0o755)
        return super().stat(path, follow_symlinks=follow_symlinks)

    def uname(self):
        return SimpleNamespace(machine="aarch64", release="6.18.49-fixture")

    def probe(self, fd, deadline):
        self.probes.append(self.fds[fd][0])
        return copy.deepcopy(self.identities.get(self.fds[fd][0], {"uuid": None, "label": None, "fs_type": None}))

    def ioctl(self, fd, command, buffer=b""):
        path = self.fds[fd][0]
        if command == linux.BLKROGET:
            return struct.pack("=i", self.read_only[path])
        if command == linux.BLKROSET:
            self.ro_sets.append(path)
            if not self.ro_fail:
                self.read_only[path] = struct.unpack("=i", buffer)[0]
                (self.sysroot / "class/block" / Path(path).name / "ro").write_text(str(self.read_only[path]))
            return 0
        return super().ioctl(fd, command, buffer)


class RootFixture(data.Fixture):
    def __init__(self, root, kind="usb", readonly=True, initramfs=False):
        super().__init__(root, kind)
        ops = RootOps(root)
        ops.__dict__.update(self.ops.__dict__)
        self.ops = ops
        self.customer = self.root / "customer"
        self.customer.mkdir()
        self.mountpoint = str(self.customer) if initramfs else "/"
        self.target_name = "sda" if kind == "usb" else "nvme2n1"
        self.root_name = "sda1" if kind == "usb" else "nvme2n1p1"
        number = "8:1" if kind == "usb" else "259:1"
        partition = self.partition(self.target_name, self.root_name, number)
        (partition / "slaves").mkdir()
        sd_partition = self.partition("mmcblk0", "mmcblk0p1", "179:1")
        (sd_partition / "slaves").mkdir()
        self.put(sd_partition / "size", "8")
        ops.nodes[str(self.dev / "mmcblk0p1")]["bytes"] = 4096
        ops.data[str(self.dev / "mmcblk0p1")] = bytearray(4096)
        self.put(self.proc / "self/mountinfo", f"1 0 {number} / {self.mountpoint} rw - ext4 /dev/{self.root_name} rw\n")
        self.put(self.proc / "sys/kernel/random/boot_id", BOOT_ID)
        self.put(self.proc / "sys/kernel/osrelease", "6.18.49-fixture")
        self.put(self.sys / "firmware/devicetree/base/compatible", "fixture,board\0fixture,soc\0")
        self.put(self.customer / "etc/ssh/ssh_host_ed25519_key.pub", KEY + " fixture\n")
        self.put(self.customer / "etc/fstab", f"UUID={UUID} / ext4 defaults 0 1\n")
        ops.root_mount, ops.root_rdev = self.mountpoint, os.makedev(*map(int, number.split(":")))
        ops.customer = self.customer
        ops.identities = {str(self.dev / self.root_name): {"uuid": UUID, "label": "customer", "fs_type": "ext4"},
                          str(self.dev / "mmcblk0p1"): {"uuid": "87654321-4321-4321-abcd-123456789012", "label": "rescue", "fs_type": "ext4"}}
        ops.sysroot, ops.probes, ops.ro_sets, ops.ro_fail = self.sys, [], [], False
        ops.read_only = {path: int(readonly and Path(path).name.startswith("mmc")) for path in ops.nodes}
        for path, value in ops.read_only.items():
            self.put(self.sys / "class/block" / Path(path).name / "ro", value)
        self.kwargs["ops"] = ops
        self.expected = {"schema": linux.EXPECTED_SCHEMA, "architecture": "arm64", "kernel_release": "6.18.49-fixture",
                         "dt_compatible": ["fixture,board", "fixture,soc"], "root_label": "customer", "root_fs_type": "ext4",
                         "root": {"uuid": UUID, "partition_index": 1, "start_lba": 8, "sectors": 32},
                         "target": self.target, "protected_sd": self.sd}
        if readonly and not initramfs:
            self.guard_receipt()

    def guard_receipt(self):
        # 明示離線前置收據；真 guard 的 ioctl 與交接流程另由 guard 測試執行。
        prior = linux.observe(self.expected, linux.digest(self.expected), root_path=self.mountpoint,
                              require_host_key=False, **self.kwargs)
        for name in ("root", "root_after"):
            prior[name]["mount"]["mount_point"] = "/root"
        rows = [row for row in prior["inventory"] if row["parent"] == prior["protected_sd"]["sysfs_path"]]
        receipt = {"schema": "bpi-lab-external-guard-result-v1", "status": "protected", "hardware_validated": False,
                   "expected_sha256": linux.digest(self.expected), "observation": prior,
                   "guard_source_sha256": linux.guard_source_digest(), "fstab_sha256": "a" * 64, "fstab": [],
                   "read_only_changes": [{"devnum": row["devnum"], "bytes": row["bytes"], "before": 0, "after": 1} for row in rows]}
        self.put(self.customer / "run/bpi-external-guard.json", linux.encoded(receipt).decode())

    def observe(self):
        return linux.observe(self.expected, NONCE, root_path=self.mountpoint, **self.kwargs)


class ExternalLinuxTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = RootFixture(self.root)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(linux.subprocess, "Popen", side_effect=AssertionError("離線測試禁止建立真實程序")).start()

    def test_usb_complete_observation_and_validation(self):
        f = self.fixture
        result = f.observe()
        checked = linux.validate_observation(result, f.expected, NONCE)
        self.assertTrue(checked["ok"])
        self.assertFalse(checked["hardware_validated"])
        self.assertEqual(result["sd_readback"]["sha256"], f.sd["full_sha256"])
        self.assertEqual(len(result["inventory"]), 4)
        self.assertTrue(all(not writable for _, writable in f.ops.opens))
        self.assertFalse(f.ops.fds)
        self.assertFalse(f.ops.ro_sets)

    def test_nvme_complete_observation(self):
        with tempfile.TemporaryDirectory() as root:
            f = RootFixture(root, "nvme")
            value = f.observe()
            self.assertEqual(value["target"]["identity"], f.target["identity"])
            self.assertNotIn("cid", value["target"])

    def test_explicit_growth_after_guard_keeps_original_uuid_and_sd(self):
        f = self.fixture
        f.expected["root_growth"] = {"policy": "last-primary-to-media-end",
                                    "partitions": [{"index": 1, "start_lba": 8, "sectors": 32}]}
        f.guard_receipt()
        before = f.observe()
        node = str(f.dev / f.root_name)
        f.put(f.sys / "class/block" / f.root_name / "size", "64")
        f.ops.nodes[node]["bytes"] = 64 * 512
        f.ops.data[node] = bytearray(64 * 512)
        after = f.observe()
        self.assertEqual(after["root"]["block"]["bytes"], 64 * 512)
        linux.validate_media_transition(before, after, f.expected)
        with self.assertRaises(ValueError):
            linux.validate_media_transition(after, before, f.expected)
        with self.assertRaisesRegex(ValueError, "UART"):
            linux.validate_observation(after, f.expected, NONCE, uart_observation=before)
        del f.expected["root_growth"]
        with self.assertRaisesRegex(ValueError, "未授權"):
            f.observe()

    def test_growth_rejects_other_partition_changes_or_root_not_last(self):
        expected = copy.deepcopy(self.fixture.expected)
        expected["root"]["partition_index"] = 2
        baseline = [{"index": 1, "start_lba": 1, "sectors": 4}, {"index": 2, "start_lba": 8, "sectors": 32}]
        expected["root_growth"] = {"policy": "last-primary-to-media-end", "partitions": baseline}
        linux.validate_expected(expected)
        target = {"partitions": copy.deepcopy(baseline)}
        target["partitions"][1]["sectors"] = 64
        self.assertEqual(linux.validate_layout(target, expected), 64)
        for parts in ([target["partitions"][1]], baseline + [{"index": 3, "start_lba": 80, "sectors": 2}],
                      [{**baseline[0], "sectors": 5}, target["partitions"][1]],
                      [baseline[0], {**baseline[1], "start_lba": 9}],
                      [baseline[0], {**baseline[1], "sectors": 31}],
                      [baseline[0], {**baseline[1], "sectors": expected["target"]["bytes"] // 512}]):
            with self.subTest(parts=parts), self.assertRaises(ValueError):
                linux.validate_layout({"partitions": parts}, expected)
        expected["root_growth"]["partitions"].append({"index": 3, "start_lba": 80, "sectors": 2})
        with self.assertRaisesRegex(ValueError, "最後"):
            linux.validate_expected(expected)

    def test_target_contract_cannot_be_fake_mmc(self):
        expected = copy.deepcopy(self.fixture.expected)
        expected["target"]["kind"] = "mmc"
        expected["target"]["identity"] = {"cid": "1" * 32}
        with self.assertRaises(ValueError):
            linux.validate_expected(expected)

    def test_native_blkid_unknown_empty_or_io_error_is_not_empty_media(self):
        cases = ((2, b"", b""), (2, b"", b"I/O error\n"), (0, b"", b""),
                 (0, b"DEVNAME=/proc/self/fd/12\n", b""), (0, b"TYPE=ext4\n", b"I/O error\n"),
                 (8, b"TYPE=ext4\n", b""))
        for code, stdout, stderr in cases:
            result = linux.subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)
            with self.subTest(code=code, stdout=stdout, stderr=stderr), mock.patch.object(linux.subprocess, "run", return_value=result):
                with self.assertRaises(ValueError):
                    linux.NativeOps().probe(12, linux.time.monotonic() + 5)

    def test_native_blkid_preserves_identified_filesystem_or_partition_table(self):
        cases = ((b"TYPE=ext4\nUUID=" + UUID.encode() + b"\nLABEL=customer\n",
                  {"uuid": UUID, "label": "customer", "fs_type": "ext4"}),
                 (b"DEVNAME=/proc/self/fd/12\nPTTYPE=dos\n", {"uuid": None, "label": None, "fs_type": "dos"}))
        for stdout, expected in cases:
            result = linux.subprocess.CompletedProcess([], 0, stdout=stdout, stderr=b"")
            with self.subTest(stdout=stdout), mock.patch.object(linux.subprocess, "run", return_value=result) as execute:
                self.assertEqual(linux.NativeOps().probe(12, linux.time.monotonic() + 5), expected)
            self.assertIn("PTTYPE", execute.call_args.args[0])
            self.assertEqual(execute.call_args.kwargs["pass_fds"], (12,))

    def test_unreadable_visible_media_blocks_observation_and_closes_descriptors(self):
        f = self.fixture
        result = linux.subprocess.CompletedProcess([], 2, stdout=b"", stderr=b"I/O error\n")
        with mock.patch.object(f.ops, "probe", side_effect=linux.NativeOps().probe), \
                mock.patch.object(linux.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(ValueError, "未知"):
                f.observe()
        self.assertFalse(f.ops.fds)

    def test_falsey_ops_never_fall_back_to_native(self):
        with mock.patch.object(linux.media, "NativeOps", side_effect=AssertionError("禁止退回原生 I/O")):
            with self.assertRaisesRegex(ValueError, "假值"):
                linux.observe(self.fixture.expected, NONCE, ops=False)

    def test_guard_fstab_evidence_cannot_be_missing_or_point_to_sd(self):
        raw = self.fixture.observe()
        for key, value in (("fstab_sha256", None), ("fstab", None),
                           ("fstab", [{"source": "LABEL=rescue", "mountpoint": "/boot", "fs_type": "ext4", "devnum": "179:1"}])):
            changed = copy.deepcopy(raw)
            changed["guard"][key] = value
            with self.subTest(field=key), self.assertRaises(ValueError):
                linux.validate_observation(changed, self.fixture.expected, NONCE)

    def test_all_visible_media_uuid_and_label_ambiguity_rejected(self):
        f = self.fixture
        f.usb("sdb", "8:16", "3", "naa.5000000000000002")
        path = str(f.dev / "sdb")
        f.ops.read_only[path] = 0
        f.put(f.sys / "class/block/sdb/ro", 0)
        for identity in ({"uuid": UUID, "label": None, "fs_type": "ext4"},
                         {"uuid": None, "label": "customer", "fs_type": "ext4"}):
            f.ops.identities[path] = identity
            with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, "唯一"):
                f.observe()
            self.assertIn(path, f.ops.probes)

    def test_sd_whole_and_each_partition_must_be_readonly(self):
        f = self.fixture
        for name in ("mmcblk0", "mmcblk0p1"):
            path = str(f.dev / name)
            f.ops.read_only[path] = 0
            f.put(f.sys / "class/block" / name / "ro", 0)
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "唯讀"):
                f.observe()
            f.ops.read_only[path] = 1
            f.put(f.sys / "class/block" / name / "ro", 1)

    def test_sd_hash_is_measured_not_copied_from_contract(self):
        self.fixture.ops.data[str(self.fixture.dev / "mmcblk0")][0] = 1
        with self.assertRaisesRegex(ValueError, "整碟讀回摘要"):
            self.fixture.observe()

    def test_stat_root_parent_and_partition_range_are_bound(self):
        f = self.fixture
        original = f.observe()
        for section, key, value in (("stat", "devnum", "179:1"), ("block", "parent", original["protected_sd"]["sysfs_path"]),
                                    ("mount", "fs_type", "overlay")):
            changed = copy.deepcopy(original)
            changed["root"][section][key] = value
            changed["root_after"] = copy.deepcopy(changed["root"])
            with self.subTest(section=section), self.assertRaises(ValueError):
                linux.validate_observation(changed, f.expected, NONCE)
        expected = copy.deepcopy(f.expected)
        expected["root"]["start_lba"] = 9
        with self.assertRaisesRegex(ValueError, "分割範圍"):
            linux.validate_observation(original, expected, NONCE)

    def test_uart_and_ssh_full_identity_and_time_order(self):
        f = self.fixture
        uart, ssh = f.observe(), f.observe()
        self.assertTrue(linux.validate_observation(ssh, f.expected, NONCE, uart_observation=uart)["ok"])
        for key, value in (("boot_id", "99999999-2222-3333-4444-555555555555"), ("kernel", "wrong"),
                           ("architecture", "arm"), ("nonce", "b" * 64), ("sampling_started_ns", 0)):
            changed = copy.deepcopy(ssh)
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                linux.validate_observation(changed, f.expected, NONCE, uart_observation=uart)

    def test_missing_identity_or_sampling_evidence_rejected(self):
        value = self.fixture.observe()
        for key in value:
            changed = copy.deepcopy(value)
            changed.pop(key)
            with self.subTest(key=key), self.assertRaises(ValueError):
                linux.validate_observation(changed, self.fixture.expected, NONCE)

    def test_nested_raw_identity_fields_cannot_be_omitted(self):
        raw = self.fixture.observe()
        for category in ("target", "protected_sd"):
            for key in raw[category]:
                changed = copy.deepcopy(raw)
                del changed[category][key]
                changed[category + "_after"] = copy.deepcopy(changed[category])
                with self.subTest(category=category, field=key), self.assertRaises(ValueError):
                    linux.validate_observation(changed, self.fixture.expected, NONCE)
        changed = copy.deepcopy(raw)
        changed["mounts"] = []
        with self.assertRaisesRegex(ValueError, "mountinfo"):
            linux.validate_observation(changed, self.fixture.expected, NONCE)

    def test_guard_receipt_must_be_complete_and_from_same_boot(self):
        f = self.fixture
        original = f.observe()
        for mutate in (lambda value: value.update(guard=None),
                       lambda value: value["guard"].update(read_only_changes=[]),
                       lambda value: value["guard"].update(guard_source_sha256="0" * 64),
                       lambda value: value["guard"]["observation"].update(boot_id="99999999-2222-3333-4444-555555555555")):
            value = copy.deepcopy(original)
            mutate(value)
            with self.assertRaises(ValueError):
                linux.validate_observation(value, f.expected, NONCE)

    def test_hotplug_and_ioctl_drift_rejected(self):
        f = self.fixture
        f.ops.event = True
        with self.assertRaisesRegex(ValueError, "事件"):
            f.observe()
        f.ops.event = False
        f.ops.ioctl_fault = 0x80081272
        with self.assertRaises(ValueError):
            f.observe()
        self.assertFalse(f.ops.fds)

    def test_fixed_program_is_deterministic_and_compilable(self):
        text = linux.program(self.fixture.expected, NONCE)
        self.assertEqual(text, linux.program(self.fixture.expected, NONCE))
        compile(text, "<固定遠端收集器>", "exec")
        with self.assertRaises(ValueError):
            linux.program(self.fixture.expected, "';exit;")

    def reference(self, path):
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def ssh_fixture(self):
        identity, hosts = self.root / "identity", self.root / "known_hosts"
        identity.write_bytes(b"fixture-key")
        hosts.write_text("192.0.2.1 " + KEY + "\n")
        return {"host": "192.0.2.1", "port": 22, "user": "root", "identity": self.reference(identity), "known_hosts": self.reference(hosts)}

    def test_collect_uses_fixed_ssh_and_preserves_raw_evidence(self):
        f = self.fixture
        uart, observation = f.observe(), f.observe()
        def transport(argv, chunks, deadline, clock):
            self.assertEqual(argv[-1], "/usr/bin/python3 -I -B -")
            payload = b"".join(chunks)
            self.assertEqual(payload, linux.program(f.expected, NONCE).encode())
            yield "sent", len(payload)
            yield "stdout", linux.encoded(observation)
            yield "exit", 0
        result = linux.collect(f.expected, self.ssh_fixture(), self.root / "collection", nonce=NONCE,
                               uart_observation=uart, transport=transport)
        self.assertEqual(result["observation"], observation)
        self.assertFalse(result["hardware_validated"])
        self.assertTrue((self.root / "collection/collection.json").is_file())

    def test_collect_wrong_uart_hostkey_never_connects(self):
        ssh = self.ssh_fixture()
        Path(ssh["known_hosts"]["path"]).write_text("192.0.2.2 " + KEY + "\n")
        ssh["known_hosts"] = self.reference(Path(ssh["known_hosts"]["path"]))
        transport = mock.Mock()
        with self.assertRaisesRegex(ValueError, "本次 UART"):
            linux.collect(self.fixture.expected, ssh, self.root / "collection", nonce=NONCE,
                          uart_observation=self.fixture.observe(), transport=transport)
        transport.assert_not_called()

    def test_collect_nonzero_or_truncated_transport_has_no_result(self):
        uart = self.fixture.observe()
        def transport(argv, chunks, deadline, clock):
            yield "sent", len(b"".join(chunks))
            yield "stderr", b"fixture-secret-diagnostic"
            yield "exit", 1
        with self.assertRaises(ValueError) as error:
            linux.collect(self.fixture.expected, self.ssh_fixture(), self.root / "collection", nonce=NONCE,
                          uart_observation=uart, transport=transport)
        self.assertNotIn("fixture-secret", str(error.exception))
        self.assertFalse((self.root / "collection/collection.json").exists())


if __name__ == "__main__":
    unittest.main()
