#!/usr/bin/env python3
"""跨平台唯讀預檢的離線回歸；不啟動 SSH、不讀取真實板子或區塊媒體。"""

import ast
from contextlib import redirect_stdout
import copy
import errno
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location("bpi_lab_linux", ROOT / "tools/bpi_lab_linux.py")
linux = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(linux)
NONCE = "ab" * 32
CONTROLLER = "/sys/devices/platform/fixture-soc/12000000.mmc"
CARD = CONTROLLER + "/mmc_host/mmc7/mmc7:0001"
MEDIA = CARD + "/block/mmcblk7"
PARTITION = MEDIA + "/mmcblk7p2"
UUID = "11111111-2222-3333-4444-555555555555"
CID = "1234567890abcdef" * 2
SECTORS = 268435456
DT = ["fixture,cross-platform-board", "fixture,soc"]


def ok(value):
    return {"status": "ok", "value": value}


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def expected(architecture="arm64"):
    return {"schema": linux.EXPECTED_SCHEMA, "architecture": architecture, "kernel_release": "6.12.9-fixture",
            "dt_compatible": list(DT), "root": {"uuid": UUID, "cid": CID, "controller": CONTROLLER,
                                                "bytes": SECTORS * 512, "media_type": "MMC"}}


def media():
    return {"name": "mmcblk7", "sysfs_path": MEDIA, "device_path": CARD, "major_minor": "179:224",
            "cid": CID, "type": "MMC", "controller": CONTROLLER, "sectors": SECTORS,
            "bytes": SECTORS * 512, "slaves": [], "is_partition": False}


def mount():
    return {"mount_id": 31, "parent_id": 22, "major_minor": "179:226", "root": "/", "mount_point": "/",
            "options": ["rw", "relatime"], "optional_fields": [], "fs_type": "ext4",
            "source": "/dev/root", "super_options": ["rw"]}


def root_identity():
    return {"mount_id": 31, "major_minor": "179:226", "stat_major_minor": "179:226",
            "sysfs_path": PARTITION, "sysfs_major_minor": "179:226", "parent_path": MEDIA,
            "parent_major_minor": "179:224", "partition_number": 2, "bytes": 4096 * 512,
            "uuid": UUID, "uuid_source": "/dev/disk/by-uuid", "uuid_major_minor": "179:226",
            "uuid_sysfs_path": PARTITION}


def fixture(machine="aarch64"):
    observation = {
        "schema": linux.OBSERVATION_SCHEMA, "nonce": NONCE, "sampling_finished": True,
        "uname": ok({"sysname": "Linux", "nodename": "fixture", "release": "6.12.9-fixture",
                     "version": "#1", "machine": machine}),
        "cpuinfo": ok({"raw": "processor\t: 0\n", "records": [[{"key": "processor", "value": "0"}]]}),
        "dt_model": ok("跨平台離線板型"), "dt_compatible": ok(list(DT)),
        "mounts": ok([mount()]), "root": ok(root_identity()), "media": ok([media()]),
        "root_after": ok(root_identity()), "media_after": ok([media()]), "swaps": ok([]),
        "meminfo": ok({"MemTotal": {"value": 1024000, "unit": "kB"},
                       "MemAvailable": {"value": 512000, "unit": "kB"}}),
        "failed_services": ok({"manager": "systemd", "status": "ok", "units": []}),
        "cpu_test": {"status": "not_tested", "reason": "本工具不執行 CPU 負載測試"},
        "memory_test": {"status": "not_tested", "reason": "本工具不執行記憶體負載測試"},
    }
    return {"schema": linux.COLLECTION_SCHEMA, "status": "collected", **linux.SCOPE,
            "source": "ssh", "alias": "fixture", "nonce": NONCE, "ssh_exitcode": 0,
            "collector_sha256": hashlib.sha256(linux.REMOTE_SCRIPT.encode("utf-8")).hexdigest(),
            "observation": observation}


class ValidationTests(unittest.TestCase):
    def validate(self, data=None, want=None):
        with mock.patch.object(linux.subprocess, "Popen", side_effect=AssertionError("離線不得啟動程序")):
            return linux.validate(data if data is not None else fixture(), want if want is not None else expected())

    def test_supported_aliases_and_no_stress_claim(self):
        for alias, architecture in linux.ARCHITECTURES.items():
            with self.subTest(alias=alias):
                result = self.validate(fixture(alias), expected(architecture))
                self.assertEqual(result["status"], "passed", result)
                self.assertTrue(result["ok"])
                self.assertEqual({key: result[key] for key in linux.SCOPE}, linux.SCOPE)

    def test_large_media_and_sd_are_supported_without_fixed_board(self):
        data, want = fixture(), expected()
        for key in ("media", "media_after"):
            data["observation"][key]["value"][0]["type"] = "SD"
        want["root"]["media_type"] = "SD"
        self.assertGreater(want["root"]["bytes"], 64 * 1024**3)
        self.assertEqual(self.validate(data, want)["status"], "passed")

    def test_controller_supports_platform_unit_addresses(self):
        controller = "/sys/devices/platform/soc@0/fixture,mmc@12000000"
        data = json.loads(json.dumps(fixture()).replace(CONTROLLER, controller))
        want = expected()
        want["root"]["controller"] = controller
        self.assertEqual(self.validate(data, want)["status"], "passed")

    def test_multiple_mismatches_all_reported(self):
        want = expected("arm32")
        want["kernel_release"] = "6.1-other"
        want["dt_compatible"] = ["fixture,other"]
        want["root"].update(uuid="other-uuid", cid="aa" * 16, controller=CONTROLLER + "-other", bytes=512,
                            media_type="SD")
        result = self.validate(want=want)
        failures = {item["check"] for item in result["checks"] if item["status"] == "failed"}
        self.assertEqual(result["status"], "failed")
        self.assertTrue({"architecture", "kernel_release", "dt_compatible", "root_uuid", "root_cid",
                         "root_controller", "root_bytes", "root_media_type"} <= failures)

    def test_expected_cid_on_other_device_cannot_pass(self):
        data = fixture()
        other = media()
        other.update(name="mmcblk8", sysfs_path=MEDIA.replace("mmcblk7", "mmcblk8"),
                     major_minor="179:240")
        for key in ("media", "media_after"):
            data["observation"][key]["value"][0]["cid"] = "ef" * 16
            data["observation"][key]["value"].append(copy.deepcopy(other))
        result = self.validate(data)
        self.assertEqual(result["status"], "failed")
        self.assertIn({"check": "root_cid", "status": "failed", "reason": "根媒體欄位須符合預期配置"}, result["checks"])

    def test_wrong_parent_device_uuid_and_partition_chains(self):
        changes = (("major_minor", "179:5"), ("stat_major_minor", "179:5"),
                   ("sysfs_major_minor", "179:5"), ("parent_major_minor", "179:5"),
                   ("uuid_major_minor", "179:5"), ("uuid_sysfs_path", MEDIA),
                   ("sysfs_path", PARTITION + "-forged"), ("parent_path", MEDIA + "-other"),
                   ("partition_number", True), ("partition_number", 3), ("bytes", SECTORS * 1024),
                   ("mount_id", 30), ("uuid_source", "/proc/cmdline"))
        for key, value in changes:
            with self.subTest(key=key):
                data = fixture()
                for name in ("root", "root_after"):
                    data["observation"][name]["value"][key] = value
                self.assertNotEqual(self.validate(data)["status"], "passed")

    def test_whole_media_root_and_partition_devnum_consistency(self):
        data = fixture()
        data["observation"]["mounts"]["value"][0]["major_minor"] = "179:224"
        for name in ("root", "root_after"):
            data["observation"][name]["value"].update(
                major_minor="179:224", stat_major_minor="179:224", sysfs_major_minor="179:224",
                uuid_major_minor="179:224", uuid_sysfs_path=MEDIA, sysfs_path=MEDIA, partition_number=None)
        self.assertEqual(self.validate(data)["status"], "passed")
        for name in ("root", "root_after"):
            data["observation"][name]["value"]["parent_major_minor"] = "179:5"
        self.assertNotEqual(self.validate(data)["status"], "passed")
        data = fixture()
        for name in ("root", "root_after"):
            data["observation"][name]["value"]["parent_major_minor"] = "179:226"
        for name in ("media", "media_after"):
            data["observation"][name]["value"][0]["major_minor"] = "179:226"
        self.assertNotEqual(self.validate(data)["status"], "passed")

    def test_controller_prefix_trick_and_media_malformations(self):
        for key, value in (("controller", CONTROLLER + "-fake"), ("device_path", CONTROLLER + "0/mmc_host/mmc7/mmc7:0001"),
                           ("is_partition", True), ("slaves", ["dm-0"]), ("bytes", True),
                           ("sectors", 1), ("sysfs_path", "/sys/devices/virtual/block/mmcblk7"),
                           ("cid", "x" * 32), ("type", "unknown")):
            with self.subTest(key=key):
                data = fixture()
                for name in ("media", "media_after"):
                    data["observation"][name]["value"][0][key] = value
                self.assertNotEqual(self.validate(data)["status"], "passed")

    def test_unsupported_root_mounts_and_ambiguous_mounts(self):
        for fs_type in ("overlay", "nfs", "nfs4", "btrfs", "tmpfs", "unknown"):
            data = fixture()
            data["observation"]["mounts"]["value"][0]["fs_type"] = fs_type
            self.assertNotEqual(self.validate(data)["status"], "passed")
        for change in ("duplicate", "bind", "none", "duplicate_id"):
            data = fixture()
            table = data["observation"]["mounts"]["value"]
            if change in ("duplicate", "duplicate_id"):
                other = copy.deepcopy(table[0])
                other["mount_id" if change == "duplicate" else "mount_point"] = 32 if change == "duplicate" else "/tmp"
                table.append(other)
            elif change == "bind":
                table[0]["root"] = "/subdirectory"
            else:
                table.clear()
            self.assertNotEqual(self.validate(data)["status"], "passed")

    def test_virtual_or_stacked_root_cannot_claim_mmc(self):
        data = fixture()
        for key in ("root", "root_after"):
            data["observation"][key]["value"].update(sysfs_path="/sys/devices/virtual/block/dm-0",
                parent_path="/sys/devices/virtual/block/dm-0", uuid_sysfs_path="/sys/devices/virtual/block/dm-0",
                partition_number=None)
        self.assertNotEqual(self.validate(data)["status"], "passed")

    def test_duplicate_media_and_identity_mutation_rejected(self):
        data = fixture()
        for name in ("media", "media_after"):
            data["observation"][name]["value"] *= 2
        self.assertNotEqual(self.validate(data)["status"], "passed")
        for name in ("root_after", "media_after"):
            data = fixture()
            data["observation"][name] = ok({} if name == "root_after" else [])
            self.assertNotEqual(self.validate(data)["status"], "passed")

    def test_missing_required_fields_never_pass(self):
        for field in fixture()["observation"]:
            data = fixture()
            del data["observation"][field]
            with self.subTest(field=field):
                self.assertNotEqual(self.validate(data)["status"], "passed")

    def test_unavailable_error_and_wrong_value_types_never_pass(self):
        for name in ("uname", "cpuinfo", "dt_model", "dt_compatible", "mounts", "root", "media",
                     "swaps", "meminfo", "failed_services", "root_after", "media_after"):
            for value in ({"status": "unavailable", "reason": "離線缺少資料"},
                          {"status": "error", "reason": "離線失敗"}, ok(None), ok(True)):
                data = fixture()
                data["observation"][name] = value
                with self.subTest(field=name, value=value):
                    self.assertNotEqual(self.validate(data)["status"], "passed")

    def test_required_content_malformed_is_blocked_not_exception(self):
        for name, value in (("uname", {}), ("cpuinfo", {"raw": "", "records": []}),
                            ("dt_model", ""), ("dt_compatible", []), ("swaps", [{}]),
                            ("meminfo", {"MemTotal": None}), ("meminfo", {}), ("media", [None]),
                            ("root", {}), ("mounts", [None]), ("failed_services", {})):
            data = fixture()
            data["observation"][name] = ok(value)
            with self.subTest(name=name):
                self.assertNotEqual(self.validate(data)["status"], "passed")

    def test_failed_services_and_non_systemd_explicit_skip(self):
        data = fixture()
        data["observation"]["failed_services"] = ok({"manager": "systemd", "status": "ok",
            "units": [{"unit": "fixture.service", "load": "loaded", "active": "failed", "sub": "failed",
                       "description": "離線失敗服務"}]})
        self.assertEqual(self.validate(data)["status"], "failed")
        data["observation"]["failed_services"] = ok({"manager": "init", "status": "skipped", "reason": "非 systemd"})
        result = self.validate(data)
        self.assertEqual(result["status"], "passed")
        self.assertIn({"check": "failed_services", "status": "skipped", "reason": "非 systemd"}, result["checks"])
        data["observation"]["failed_services"]["value"]["manager"] = "systemd"
        self.assertNotEqual(self.validate(data)["status"], "passed")

    def test_memory_available_must_fit_total(self):
        data = fixture()
        data["observation"]["meminfo"]["value"]["MemAvailable"]["value"] = 2048000
        self.assertNotEqual(self.validate(data)["status"], "passed")

    def test_memory_total_available_arbitrary_types_never_traceback(self):
        for name in ("MemTotal", "MemAvailable"):
            for value in (None, [], [1], 0, 1, True, "1024", {}, {"value": [], "unit": "kB"}):
                data = fixture()
                data["observation"]["meminfo"]["value"][name] = value
                with self.subTest(name=name, value=value):
                    self.assertNotEqual(self.validate(data)["status"], "passed")

    def test_every_json_node_accepts_no_uncaught_type_errors(self):
        original = fixture()
        def paths(value, prefix=()):
            yield prefix
            if type(value) is dict:
                for key, item in value.items():
                    yield from paths(item, prefix + (key,))
            elif type(value) is list:
                for index, item in enumerate(value):
                    yield from paths(item, prefix + (index,))
        for path in paths(original):
            for replacement in (None, True, False, 1, -1, 1.5, "", "wrong", [], [None], {}, {"wrong": 1}):
                changed = copy.deepcopy(original)
                if path:
                    parent = changed
                    for part in path[:-1]:
                        parent = parent[part]
                    parent[path[-1]] = replacement
                else:
                    changed = replacement
                with self.subTest(path=path, replacement=replacement):
                    result = linux.validate(changed, expected())
                    self.assertIn(result["status"], ("passed", "failed", "blocked"))

    def test_expected_arbitrary_json_types_only_raise_contract_errors(self):
        for section, keys in ((None, tuple(expected())), ("root", tuple(expected()["root"]))):
            for key in keys:
                for value in (None, [], {}, True, 1, 1.5, ""):
                    want = expected()
                    target = want if section is None else want[section]
                    target[key] = value
                    with self.subTest(section=section, key=key, value=value), self.assertRaises(linux.LinuxError):
                        linux.validate_expected(want)

    def test_scope_source_nonce_and_collector_version_tampering(self):
        for key, value in (("read_only", False), ("hardware_validation", True), ("smoke_tested", True),
                           ("stress_tested", True), ("source", "simulation"), ("ssh_exitcode", True),
                           ("ssh_exitcode", 1), ("nonce", "cd" * 32), ("collector_sha256", "ef" * 32),
                           ("status", "passed"), ("observation", [])):
            data = fixture()
            data[key] = value
            self.assertNotEqual(self.validate(data)["status"], "passed")
        for name in ("cpu_test", "memory_test"):
            data = fixture()
            data["observation"][name]["status"] = "passed"
            self.assertNotEqual(self.validate(data)["status"], "passed")
        for value in (None, [], {}, True):
            self.assertNotEqual(linux.validate(value, expected())["status"], "passed")

    def test_configuration_requires_all_fields_and_valid_types(self):
        for key in expected():
            want = expected()
            del want[key]
            with self.assertRaises(linux.LinuxError):
                self.validate(want=want)
        for key in expected()["root"]:
            want = expected()
            del want["root"][key]
            with self.assertRaises(linux.LinuxError):
                self.validate(want=want)
        for key, value in (("bytes", True), ("bytes", 1), ("cid", "bad"), ("uuid", ""),
                           ("controller", "/sys/devices/../dev"), ("media_type", "eMMC")):
            want = expected()
            want["root"][key] = value
            with self.assertRaises(linux.LinuxError):
                self.validate(want=want)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.namespace = {"__name__": "_offline_collector"}
        exec(compile(linux.REMOTE_SCRIPT, "<離線收集程式>", "exec"), self.namespace)
        self.files = {
            "/proc/self/mountinfo": b"31 22 179:226 / / rw,relatime - ext4 /dev/root rw\n",
            "/proc/cpuinfo": b"processor\t: 0\n", "/proc/1/comm": b"systemd\n",
            "/proc/meminfo": b"MemTotal: 1024000 kB\nMemAvailable: 512000 kB\n",
            "/proc/swaps": b"Filename\tType\tSize\tUsed\tPriority\n",
            "/sys/firmware/devicetree/base/model": "跨平台離線板型\0".encode(),
            "/sys/firmware/devicetree/base/compatible": ("\0".join(DT) + "\0").encode(),
            MEDIA + "/device/cid": (CID + "\n").encode(), MEDIA + "/device/type": b"MMC\n",
            MEDIA + "/size": (str(SECTORS) + "\n").encode(), MEDIA + "/dev": b"179:224\n",
            PARTITION + "/partition": b"2\n", PARTITION + "/dev": b"179:226\n",
            PARTITION + "/size": b"4096\n",
        }
        self.directories = {"/sys/class/block": ["mmcblk7", "mmcblk7p2", "mmcblk7boot0"],
                            "/dev/disk/by-uuid": [UUID], MEDIA + "/slaves": []}
        self.paths = {"/sys/class/block/mmcblk7": MEDIA, MEDIA + "/device": CARD,
                      "/sys/dev/block/179:226": PARTITION, "/sys/dev/block/179:224": MEDIA}
        self.reads = []
        self.root_dev = (179, 226)
        self.uuid_dev = (179, 226)
        self.uuid_mode = stat.S_IFBLK

    def run_collector(self, operation="collect", *arguments):
        def read(path, mode):
            self.assertEqual(mode, "rb", "遠端只能開啟唯讀系統欄位")
            path = str(path)
            self.reads.append(path)
            value = self.files.get(path, FileNotFoundError(errno.ENOENT, "離線檔案不存在"))
            if isinstance(value, BaseException):
                raise value
            return io.BytesIO(value)
        def scan(path):
            path = str(path)
            if path not in self.directories:
                raise FileNotFoundError(errno.ENOENT, "離線目錄不存在")
            manager = mock.MagicMock()
            manager.__enter__.return_value = iter(SimpleNamespace(name=name) for name in self.directories[path])
            return manager
        def resolve(path, strict):
            self.assertTrue(strict)
            value = self.paths.get(str(path))
            if value is None:
                raise FileNotFoundError(errno.ENOENT, "離線路徑不存在")
            return Path(value)
        def info(path):
            if str(path) == "/":
                return SimpleNamespace(st_dev=os.makedev(*self.root_dev))
            if str(path) == "/dev/disk/by-uuid/" + UUID:
                return SimpleNamespace(st_mode=self.uuid_mode, st_rdev=os.makedev(*self.uuid_dev))
            raise FileNotFoundError(errno.ENOENT, "離線裝置不存在")
        with mock.patch("builtins.open", side_effect=read), mock.patch("os.scandir", side_effect=scan), \
                mock.patch.object(Path, "resolve", autospec=True, side_effect=resolve), \
                mock.patch("os.stat", side_effect=info), \
                mock.patch("os.path.exists", side_effect=lambda path: str(path) in self.files), \
                mock.patch("os.path.isdir", return_value=False), mock.patch("os.path.isfile", return_value=True), \
                mock.patch("os.access", return_value=True), mock.patch("os.uname", return_value=SimpleNamespace(
                    sysname="Linux", nodename="fixture", release="6.12.9-fixture", version="#1", machine="aarch64")), \
                mock.patch.dict(self.namespace, service_command=mock.Mock(return_value="")):
            return self.namespace[operation](*(arguments or ((NONCE,) if operation == "collect" else ())))

    def test_full_collector_output_is_valid_without_live_reads(self):
        actual = self.run_collector()
        self.assertEqual(actual, fixture()["observation"])
        data = fixture()
        data["observation"] = actual
        self.assertEqual(linux.validate(data, expected())["status"], "passed")
        self.assertFalse(any(path.startswith("/dev/") for path in self.reads))
        self.assertTrue(all(path.startswith(("/proc/", "/sys/")) for path in self.reads))

    def test_mountinfo_optional_fields_and_escapes(self):
        self.files["/proc/self/mountinfo"] = (
            b"31 22 179:226 / / rw shared:4 master:1 - ext4 /dev/root rw\n"
            b"32 31 0:9 / /path\\040with\\134slash rw - tmpfs tmpfs rw\n")
        table = self.run_collector("mounts")
        self.assertEqual(table[0]["optional_fields"], ["shared:4", "master:1"])
        self.assertEqual(table[1]["mount_point"], "/path with\\slash")

    def test_mountinfo_malformed_duplicate_and_escape_rejected(self):
        for raw in (b"", b"31 22 malformed\n", b"31 22 179:226 / /bad\\041 rw - ext4 /dev/root rw\n",
                    self.files["/proc/self/mountinfo"] * 2, b"x" * 131073):
            self.files["/proc/self/mountinfo"] = raw
            self.assertNotEqual(self.run_collector()["mounts"]["status"], "ok")

    def test_overlay_nfs_btrfs_rejected_even_with_expected_cid(self):
        for fs_type in ("overlay", "nfs", "btrfs"):
            self.files["/proc/self/mountinfo"] = f"31 22 0:44 / / rw - {fs_type} fixture rw\n".encode()
            actual = self.run_collector()
            self.assertEqual(actual["media"]["value"][0]["cid"], CID)
            self.assertEqual(actual["root"]["status"], "unavailable")

    def test_lvm_and_stacked_devices_rejected(self):
        self.paths["/sys/dev/block/179:226"] = "/sys/devices/virtual/block/dm-0"
        self.assertEqual(self.run_collector()["root"]["status"], "unavailable")
        self.paths["/sys/dev/block/179:226"] = PARTITION
        self.directories[MEDIA + "/slaves"] = ["dm-0"]
        self.assertNotEqual(self.run_collector()["root"]["status"], "ok")

    def test_partition_without_slaves_directory_is_normal(self):
        self.assertNotIn(PARTITION + "/slaves", self.directories)
        self.assertEqual(self.run_collector()["root"]["status"], "ok")
        del self.directories[MEDIA + "/slaves"]
        self.assertNotEqual(self.run_collector()["root"]["status"], "ok")

    def test_uuid_unavailable_never_inferred_from_mount_source(self):
        self.directories["/dev/disk/by-uuid"] = []
        actual = self.run_collector()
        self.assertEqual(actual["root"]["status"], "unavailable")
        self.assertIn("UUID", actual["root"]["reason"])

    def test_uuid_target_is_checked_as_block_device(self):
        for mode, device in ((stat.S_IFREG, (179, 226)), (stat.S_IFBLK, (179, 224))):
            self.uuid_mode, self.uuid_dev = mode, device
            self.assertEqual(self.run_collector()["root"]["status"], "unavailable")

    def test_collect_whole_media_root(self):
        self.files["/proc/self/mountinfo"] = b"31 22 179:224 / / rw - ext4 /dev/root rw\n"
        self.root_dev = self.uuid_dev = (179, 224)
        observation = self.run_collector()
        root = observation["root"]["value"]
        self.assertEqual(root["sysfs_path"], root["parent_path"])
        self.assertIsNone(root["partition_number"])
        data = fixture()
        data["observation"] = observation
        self.assertEqual(linux.validate(data, expected())["status"], "passed")

    def test_root_stat_and_sysfs_must_match_mount_number(self):
        self.root_dev = (179, 225)
        self.assertEqual(self.run_collector()["root"]["status"], "error")
        self.root_dev = (179, 226)
        self.files[PARTITION + "/dev"] = b"179:225\n"
        self.assertEqual(self.run_collector()["root"]["status"], "error")

    def test_non_systemd_skipped_without_command(self):
        self.files["/proc/1/comm"] = b"init\n"
        services = self.run_collector()["failed_services"]["value"]
        self.assertEqual(services["status"], "skipped")
        self.assertEqual(services["manager"], "init")

    def test_missing_init_does_not_become_non_systemd_skip(self):
        del self.files["/proc/1/comm"]
        self.assertEqual(self.run_collector()["failed_services"]["status"], "unavailable")

    def test_systemd_query_uses_fixed_argv_and_parses_failure(self):
        command = mock.Mock(return_value="fixture.service loaded failed failed 離線失敗\n")
        with mock.patch.dict(self.namespace, read=mock.Mock(return_value="systemd\n"), service_command=command), \
                mock.patch("os.path.isfile", return_value=True), mock.patch("os.access", return_value=True):
            result = self.namespace["failed_services"]()
        command.assert_called_once_with(["/usr/bin/systemctl", "--failed", "--type=service",
                                         "--no-legend", "--plain", "--no-pager"])
        self.assertEqual(result["units"][0]["unit"], "fixture.service")
        with mock.patch.dict(self.namespace, read=mock.Mock(return_value="systemd\n")), \
                mock.patch("os.path.isfile", return_value=False):
            self.assertEqual(self.namespace["sample"](self.namespace["failed_services"])["status"], "unavailable")

    def test_non_systemd_conflicting_runtime_is_not_skipped(self):
        command = mock.Mock(side_effect=AssertionError("非 systemd 不執行查詢"))
        with mock.patch.dict(self.namespace, read=mock.Mock(return_value="init\n"), service_command=command), \
                mock.patch("os.path.isdir", return_value=True):
            result = self.namespace["sample"](self.namespace["failed_services"])
        self.assertEqual(result["status"], "unavailable")
        command.assert_not_called()

    def test_dt_nul_validation_and_cpuinfo_read_limit(self):
        for raw in (b"", b"unterminated", b"one\0two\0", b"\0"):
            self.files["/sys/firmware/devicetree/base/model"] = raw
            self.assertEqual(self.run_collector()["dt_model"]["status"], "error")
        self.files["/proc/cpuinfo"] = b"x" * 131073
        self.assertEqual(self.run_collector()["cpuinfo"]["status"], "error")

    def test_directory_bounds_and_capacity_relation(self):
        self.directories["/sys/class/block"] = ["fixture"] * 257
        self.assertEqual(self.run_collector()["media"]["status"], "error")
        self.directories["/sys/class/block"] = ["mmcblk7"]
        self.files[MEDIA + "/size"] = b"0\n"
        self.assertEqual(self.run_collector()["media"]["status"], "error")

    def test_no_permission_and_global_timeout_are_not_ignored(self):
        self.files["/proc/cpuinfo"] = PermissionError(errno.EACCES, "離線拒絕")
        self.assertEqual(self.run_collector()["cpuinfo"]["status"], "error")
        self.files["/proc/cpuinfo"] = TimeoutError("離線總期限")
        with self.assertRaises(TimeoutError):
            self.run_collector()

    def test_remote_script_standard_library_and_no_writes(self):
        tree = ast.parse(linux.REMOTE_SCRIPT)
        modules = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        self.assertEqual(modules, {"errno", "json", "os", "re", "selectors", "signal", "stat", "subprocess", "sys", "time"})
        forbidden = {"os.system", "os.open", "os.mkdir", "os.unlink", "os.remove", "os.rename", "os.replace",
                     "subprocess.run", "subprocess.call", "subprocess.check_output"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                self.assertNotIn(ast.unparse(node.func), forbidden)
                if isinstance(node.func, ast.Name) and node.func.id == "open":
                    self.assertEqual(ast.literal_eval(node.args[1]), "rb")
                if ast.unparse(node.func) == "subprocess.Popen":
                    self.assertIn("shell=False", ast.unparse(node))


class TransportTests(unittest.TestCase):
    def run_fake(self, program, *, payload=b"fixture", timeout=2, remote=False):
        real_popen = subprocess.Popen
        children = []
        def launch(argv, **kwargs):
            self.assertIs(kwargs["shell"], False)
            process = real_popen([sys.executable, "-I", "-B", "-c", program], **kwargs)
            children.append(process)
            return process
        try:
            with mock.patch.object(linux.subprocess, "Popen", side_effect=launch):
                if remote:
                    namespace = {"__name__": "_offline_collector"}
                    exec(compile(linux.REMOTE_SCRIPT, "<離線服務查詢>", "exec"), namespace)
                    return namespace["service_command"](["/usr/bin/systemctl", "--failed"])
                return linux._fetch(["/fixture/never-ssh"], payload, timeout)
        finally:
            for process in children:
                self.assertIsNotNone(process.poll(), "假程序必須被回收")

    def test_strict_argv_absolute_commands_and_no_script_on_command_line(self):
        argv = linux.ssh_argv("/tmp/config", "fixture", "/tmp/known_hosts", NONCE, 19)
        self.assertEqual(argv[0], "/usr/bin/ssh")
        self.assertEqual(argv[-7:], ["fixture", "/usr/bin/python3", "-I", "-B", "-", NONCE, "19"])
        for option in ("StrictHostKeyChecking=yes", "BatchMode=yes", "UpdateHostKeys=no", "ControlPath=none",
                       "ControlMaster=no", "ControlPersist=no", "ForwardAgent=no", "ClearAllForwardings=yes",
                       "PasswordAuthentication=no", "KbdInteractiveAuthentication=no", "PermitLocalCommand=no",
                       "UserKnownHostsFile=/tmp/known_hosts", "GlobalKnownHostsFile=/dev/null",
                       "KnownHostsCommand=none", "VerifyHostKeyDNS=no", "ProxyCommand=none", "ProxyJump=none"):
            self.assertIn(option, argv)
        self.assertNotIn(linux.REMOTE_SCRIPT, argv)
        self.assertEqual(argv[argv.index("-F") + 1], "/tmp/config")

    def test_alias_path_nonce_and_deadline_injection_rejected(self):
        for alias in ("-bad", "fixture;id", "fixture\nid", "fixture arg", "$(id)"):
            with self.assertRaises(ValueError):
                linux.ssh_argv("/tmp/config", alias, "/tmp/known_hosts", NONCE, 19)
        for hosts in ("/tmp/%h", "/tmp/${HOME}", "/tmp/../hosts", "/tmp/two files", "/tmp/x\nid"):
            with self.assertRaises(ValueError):
                linux.ssh_argv("/tmp/config", "fixture", hosts, NONCE, 19)
        for nonce, timeout in ((";id", 1), (NONCE, True), (NONCE, float("nan")), (NONCE, 56)):
            with self.assertRaises(ValueError):
                linux.ssh_argv("/tmp/config", "fixture", "/tmp/known_hosts", nonce, timeout)

    def test_stdin_script_delivered_exactly_and_stderr_not_json(self):
        payload = linux.REMOTE_SCRIPT.encode()
        program = ("import sys\n"
                   f"assert sys.stdin.buffer.read() == {payload!r}\n"
                   "sys.stderr.buffer.write(b'diagnostic')\n"
                   "sys.stdout.buffer.write(b'{\"fixture\":true}')\n")
        self.assertEqual(self.run_fake(program, payload=payload), b'{"fixture":true}')

    def test_stdout_stderr_combined_bound(self):
        for out, err in ((1025, 0), (0, 1025), (512, 513)):
            program = f"import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'x'*{out}); sys.stderr.buffer.write(b'x'*{err})"
            with mock.patch.object(linux, "MAX_OUTPUT_BYTES", 1024), self.assertRaisesRegex(linux.LinuxError, "上限"):
                self.run_fake(program)

    def test_timeout_blocked_stdin_and_closed_outputs(self):
        for prefix in ("", "import os; os.close(1); os.close(2); "):
            started = time.monotonic()
            with self.assertRaisesRegex(linux.LinuxError, "逾時"):
                self.run_fake(prefix + "import time; time.sleep(10)", payload=b"x" * 200000, timeout=0.15)
            self.assertLess(time.monotonic() - started, 3)

    def test_exit_code_and_early_stdin_close_fail(self):
        with self.assertRaisesRegex(linux.LinuxError, "非零"):
            self.run_fake("import sys; sys.stdin.buffer.read(); sys.exit(7)")
        with self.assertRaises(linux.LinuxError):
            self.run_fake("import os; os.close(0)", payload=b"x" * 200000)

    def test_service_query_output_exit_and_timeout_bounds(self):
        self.assertEqual(self.run_fake("print('fixture.service loaded failed failed 離線失敗')", remote=True),
                         "fixture.service loaded failed failed 離線失敗\n")
        for program in ("print('x'*32769)", "import sys; sys.exit(2)",
                        "import sys; sys.stderr.write('fixture')"):
            with self.assertRaises(ValueError):
                self.run_fake(program, remote=True)
        with self.assertRaisesRegex(ValueError, "逾時"):
            self.run_fake("import os,time; os.close(1); os.close(2); time.sleep(10)", remote=True)

    def test_collect_actual_transport_is_used_with_fixed_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            config, hosts = Path(directory) / "config", Path(directory) / "known_hosts"
            config.write_text("Host fixture\n HostName 192.0.2.10\n", encoding="utf-8")
            hosts.write_text("fixture ssh-ed25519 AAAA\n", encoding="utf-8")
            with mock.patch.object(linux, "_fetch", return_value=encoded(fixture()["observation"])) as fetch, \
                    mock.patch.object(linux.secrets, "token_hex", return_value=NONCE):
                result = linux.collect(ssh_config=config, alias="fixture", known_hosts=hosts)
            self.assertEqual(result["status"], "collected")
            self.assertEqual(linux.validate(result, expected())["status"], "passed")
            argv, payload, timeout = fetch.call_args.args
            self.assertEqual(argv[0], "/usr/bin/ssh")
            self.assertEqual(payload, linux.REMOTE_SCRIPT.encode("utf-8"))
            self.assertEqual(timeout, 20)

    def test_collect_configuration_mutation_and_remote_nonce_mismatch(self):
        digest = {"bytes": 1, "sha256": "ab" * 32}
        with mock.patch.object(linux, "config_fingerprint", side_effect=[digest, digest, {"bytes": 2}]), \
                mock.patch.object(linux, "_fetch", return_value=encoded(fixture()["observation"])), \
                self.assertRaisesRegex(linux.LinuxError, "改變"):
            linux.collect(ssh_config="/tmp/config", alias="fixture", known_hosts="/tmp/known_hosts")
        with mock.patch.object(linux, "config_fingerprint", return_value=digest), \
                mock.patch.object(linux, "_fetch", return_value=encoded(fixture()["observation"])), \
                mock.patch.object(linux.secrets, "token_hex", return_value="cd" * 32), \
                self.assertRaisesRegex(linux.LinuxError, "識別碼"):
            linux.collect(ssh_config="/tmp/config", alias="fixture", known_hosts="/tmp/known_hosts")

    def test_invalid_timeout_precedes_files_and_process(self):
        for timeout in (0, 1, 61, True, "20", float("inf"), float("nan")):
            with mock.patch.object(linux, "config_fingerprint") as fingerprint, \
                    mock.patch.object(linux.subprocess, "Popen") as popen, self.assertRaises(linux.LinuxError):
                linux.collect(ssh_config="/tmp/config", alias="fixture", known_hosts="/tmp/known_hosts", timeout=timeout)
            fingerprint.assert_not_called()
            popen.assert_not_called()


class CliTests(unittest.TestCase):
    def test_strict_json_rejects_duplicate_nan_truncated_and_oversized(self):
        for raw in (b"", b"{", b"{}{}", b'\xff', b'{"key":1,"key":2}', b'{"value":NaN}',
                    b'{"value":Infinity}', b'"\\ud800"', b"[" * 2000 + b"]" * 2000,
                    b"x" * (linux.MAX_OUTPUT_BYTES + 1)):
            with self.assertRaises(linux.LinuxError):
                linux.parse_json(raw)

    def test_offline_cli_and_multiple_failure_exit_status(self):
        with tempfile.TemporaryDirectory() as directory:
            report_path, expected_path = Path(directory) / "report.json", Path(directory) / "expected.json"
            report_path.write_bytes(encoded(fixture()))
            expected_path.write_bytes(encoded(expected()))
            for kernel, code in (("6.12.9-fixture", 0), ("6.0-mismatch", 1)):
                want = expected()
                want["kernel_release"] = kernel
                expected_path.write_bytes(encoded(want))
                output = io.StringIO()
                with mock.patch.object(linux.subprocess, "Popen", side_effect=AssertionError("離線不得建立程序")), \
                        redirect_stdout(output):
                    result = linux.main(["validate", "--report", str(report_path), "--expected", str(expected_path)])
                self.assertEqual(result, code)
                self.assertEqual(json.loads(output.getvalue())["ok"], code == 0)

    def test_cli_collect_calls_collect_instead_of_offline_validation(self):
        output = io.StringIO()
        with mock.patch.object(linux, "collect", return_value=fixture()) as collect, redirect_stdout(output):
            code = linux.main(["collect", "--ssh-config", "/tmp/config", "--alias", "fixture", "--known-hosts", "/tmp/hosts"])
        self.assertEqual(code, 0)
        collect.assert_called_once_with(ssh_config="/tmp/config", alias="fixture", known_hosts="/tmp/hosts", timeout=20)

    def test_invalid_cli_is_json_and_cannot_launch_ssh(self):
        for arguments in ([], ["collect"], ["validate"], ["validate", "--command", "id"],
                          ["collect", "--ssh-config", "/tmp/config", "--alias", "fixture", "--known-hosts", "/tmp/missing"]):
            output = io.StringIO()
            with mock.patch.object(linux.subprocess, "Popen") as popen, redirect_stdout(output):
                result = linux.main(arguments)
            self.assertEqual(result, 2)
            self.assertEqual(json.loads(output.getvalue())["status"], "error")
            popen.assert_not_called()

    def test_cli_ssh_failures_are_specific_json_without_raw_log_claim(self):
        for message in ("SSH 結束碼非零：255", "SSH 唯讀收集逾時", "SSH stdout 與 stderr 合計超過上限"):
            output = io.StringIO()
            with mock.patch.object(linux, "collect", side_effect=linux.LinuxError(message)), redirect_stdout(output):
                code = linux.main(["collect", "--ssh-config", "/tmp/config", "--alias", "fixture", "--known-hosts", "/tmp/hosts"])
            result = json.loads(output.getvalue())
            self.assertEqual(code, 2)
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["error"], message)
            self.assertIs(result["raw_streams_saved"], False)

    def test_help_is_traditional_chinese(self):
        for command in ([], ["collect"], ["validate"]):
            output = io.StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
                linux.main(command + ["--help"])
            self.assertEqual(raised.exception.code, 0)
            self.assertIn("用法：", output.getvalue())
            self.assertNotIn("usage:", output.getvalue())
            self.assertNotIn("options:", output.getvalue())

    def test_nonregular_json_sources_rejected_without_opening_devices(self):
        with tempfile.TemporaryDirectory() as directory:
            target, link, fifo = (Path(directory) / name for name in ("report", "link", "fifo"))
            target.write_bytes(encoded(fixture()))
            link.symlink_to(target)
            os.mkfifo(fifo)
            for path in (link, fifo, Path(directory)):
                with self.assertRaises(linux.LinuxError):
                    linux.load_json(path)


if __name__ == "__main__":
    unittest.main()
