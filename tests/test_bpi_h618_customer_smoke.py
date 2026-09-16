"""純離線短測守門；SSH、系統資訊及 /var/tmp 皆由替身提供，不碰真裝置。"""

from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import stat
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bpi_h618_customer_smoke as smoke


def components():
    return {"schema": "bpi-h618-customer-components-v1", "board": "bananapim4zeroemac",
            "root_uuid": "62cd58de-498d-4325-8528-96a15da7d902",
            "kernel_release": "6.18.49-current-sunxi64", "os": "bookworm",
            "preflight": {"source_verified": True}}


def request():
    return {**components(), "expected": copy.deepcopy(smoke.EXPECTED), "nonce": "c" * 64,
            "duration": 60, "file_mib": 16}


def identity(req):
    controller = req["expected"]["controller"]
    parent = controller + "/mmc_host/mmc1/mmc1:0001/block/mmcblk1"
    return {"devices": [{"name": "mmcblk1", "type": "MMC", "cid": req["expected"]["cid"],
                         "bytes": req["expected"]["bytes"], "device_path": controller + "/mmc_host/mmc1/mmc1:0001",
                         "path": parent}], "sd_status": "disabled", "kernel_release": req["kernel_release"],
            "root": {"target": "/", "source": "/dev/mmcblk1p1", "maj:min": "179:1", "fstype": "ext4",
                     "uuid": req["root_uuid"], "parent_path": parent, "sys_path": parent + "/mmcblk1p1"}}


def success(core, req):
    digest = hashlib.sha256()
    for _ in range(req["file_mib"]):
        digest.update(core["BLOCK"])
    return {"schema": "bpi-h618-customer-smoke-v1", "nonce": req["nonce"], "ok": True,
            "identity_verified": True, "identity_before": identity(req), "identity_after": identity(req),
            "cpu": {"ok": True, "workers": [{"worker": n, "iterations": 1, "sha256": core["BLOCK_SHA"],
                                              "ok": True} for n in range(2)]},
            "file": {"ok": True, "cleaned": True, "bytes_written": req["file_mib"] * 1024**2,
                     "bytes_read": req["file_mib"] * 1024**2, "sha256": digest.hexdigest(),
                     "cache_method": "fsync+POSIX_FADV_DONTNEED"},
            "commands": {name: {"rc": 0, "ok": True, "stdout": "", "stderr": ""}
                         for name in (*core["COMMANDS"], "dmesg_after", "findmnt_before_file", "findmnt_after_file")},
            "failed_services": {"ok": True, "units": []}, "failures": [], "system_verified": False,
            "original_boot_chain_verified": False, "emac": {"status": "not_tested"},
            "wifi": {"status": "not_tested"}, "peripherals": {"status": "not_tested"}}


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.core = smoke.remote_namespace()
        self.config = self.root / "ssh-config"
        self.config.write_text("Host smoke-test\n StrictHostKeyChecking yes\n")
        self.components = self.root / "components.json"
        self.components.write_text(json.dumps(components()))
        self.args = {"ssh_config": self.config, "alias": "smoke-test", "components": self.components,
                     "components_sha256": hashlib.sha256(self.components.read_bytes()).hexdigest(), "output": self.root / "new"}
        self.called = []
        self.mutate = None
        self.rc = 0
        self.popen = patch.object(smoke.backup.subprocess, "Popen", side_effect=AssertionError("禁止真 SSH 或命令"))
        self.popen.start()
        self.addCleanup(self.popen.stop)

    def transport(self, argv, deadline, monotonic):
        self.called.append(argv)
        command = shlex.split(argv[-1])
        self.assertEqual(command[:3], ["python3", "-B", "-c"])
        self.assertEqual(command[3], smoke.REMOTE_SCRIPT)
        req = json.loads(command[4])
        report = success(self.core, req)
        report["commands"]["dmesg"]["stdout"] = "資料：$(不可執行)\n"
        if self.mutate:
            self.mutate(report)
        yield "stdout", json.dumps(report).encode()
        yield "stderr", b""
        yield "exit", self.rc

    def run_smoke(self, **kwargs):
        return smoke.smoke(**(self.args | {"transport": self.transport} | kwargs))

    def test_success_strict_ssh_private_json_rc_and_dmesg(self):
        report = self.run_smoke()
        self.assertTrue(report["ok"])
        self.assertFalse(report["system_verified"])
        self.assertEqual(report["request"]["file_mib"], 16)
        self.assertEqual(report["request"]["duration"], 60)
        self.assertEqual(report["ssh_exitcode"], 0)
        for option in ("StrictHostKeyChecking=yes", "BatchMode=yes", "PasswordAuthentication=no",
                       "ControlPath=none", "PermitLocalCommand=no", "RequestTTY=no"):
            self.assertIn(option, self.called[0])
        self.assertEqual(stat.S_IMODE(self.args["output"].stat().st_mode), 0o700)
        for path in self.args["output"].iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        saved = json.loads((self.args["output"] / "dmesg.json").read_text())
        self.assertEqual(saved["dmesg"]["stdout"], "資料：$(不可執行)\n")

    def test_failed_service_still_saves_full_evidence(self):
        def mutate(report):
            report.update(ok=False, failed_services={"ok": False, "units": ["console-setup.service loaded failed failed"]})
        self.mutate = mutate
        self.rc = 1
        result = self.run_smoke()
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "collected")
        self.assertTrue(result["remote"]["file"]["ok"])
        self.assertTrue(result["remote"]["cpu"]["ok"])
        self.assertTrue((self.args["output"] / "dmesg.json").exists())

    def test_false_success_missing_sections_is_rejected(self):
        self.mutate = lambda r: r.pop("cpu")
        self.assertFalse(self.run_smoke()["ok"])

    def test_wrong_nonce_is_rejected(self):
        self.mutate = lambda r: r.update(nonce="f" * 64)
        self.assertFalse(self.run_smoke()["ok"])

    def test_nonzero_ssh_rc_cannot_succeed(self):
        self.rc = 255
        self.assertFalse(self.run_smoke()["ok"])

    def test_missing_exit_cannot_succeed(self):
        def transport(*args):
            yield from list(self.transport(*args))[:-1]
        self.assertFalse(self.run_smoke(transport=transport)["ok"])

    def test_duration_and_file_bounds_reject_before_transport(self):
        for key, values in (("duration", (0, 301, True, 1.5)), ("file_mib", (0, 129, True, 0.5))):
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.run_smoke(**{key: value})
        self.assertEqual(self.called, [])
        self.assertFalse(self.args["output"].exists())

    def test_trust_and_alias_reject_before_output(self):
        for values in ({"components_sha256": "0" * 64}, {"alias": "host; reboot"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.run_smoke(**values)
        self.assertEqual(self.called, [])

    def test_symlink_input_and_config_are_refused(self):
        for path in (self.components, self.config):
            original = path.with_suffix(".original")
            path.rename(original)
            path.symlink_to(original)
            with self.assertRaises((ValueError, OSError)):
                self.run_smoke()
            path.unlink()
            original.rename(path)
        self.assertEqual(self.called, [])

    def test_existing_or_symlink_output_is_refused(self):
        self.args["output"].mkdir()
        with self.assertRaises(OSError):
            self.run_smoke()
        self.args["output"].rmdir()
        self.args["output"].symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            self.run_smoke()
        self.assertEqual(self.called, [])

    def test_timeout_keeps_partial(self):
        clock = Mock(side_effect=[0, 61, 61])
        result = self.run_smoke(monotonic=clock)
        self.assertFalse(result["ok"])
        self.assertTrue((self.args["output"] / "report.json.partial").exists())

    def test_truncated_json_and_oversized_output_fail(self):
        def transport(*_):
            yield "stdout", b'{"schema":'
            yield "exit", 1
        self.assertFalse(self.run_smoke(transport=transport)["ok"])
        def oversized(*_):
            yield "stderr", b"x" * (1024**2 + 1)
        self.assertFalse(self.run_smoke(transport=oversized, output=self.root / "oversized")["ok"])

    def test_cli_help_and_missing_arguments_are_offline(self):
        with redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as exitcode:
            smoke.main(["--help"])
        self.assertEqual(exitcode.exception.code, 0)
        self.assertIn("--file-mib", output.getvalue())
        with redirect_stderr(io.StringIO()):
            self.assertEqual(smoke.main([]), 1)


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.core = smoke.remote_namespace()
        self.req = request()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.popen = patch.object(self.core["subprocess"], "Popen", side_effect=AssertionError("禁止真命令"))
        self.popen.start()
        self.addCleanup(self.popen.stop)

    def test_fixed_sha_and_two_bounded_cpu_workers(self):
        self.assertEqual(hashlib.sha256(self.core["BLOCK"]).hexdigest(), self.core["BLOCK_SHA"])
        started = time.monotonic()
        result = self.core["cpu_test"](started + 2, 0.05)
        self.assertTrue(result["ok"])
        self.assertEqual([w["worker"] for w in result["workers"]], [0, 1])
        self.assertTrue(all(w["iterations"] > 0 for w in result["workers"]))
        self.assertLess(time.monotonic() - started, 2)

    def test_cpu_digest_mismatch_is_failure(self):
        with patch.dict(self.core, BLOCK_SHA="0" * 64), self.assertRaises(ValueError):
            self.core["cpu_test"](time.monotonic() + 1, 0.02)

    def test_identity_exact_cid_uuid_controller_type_sd_and_kernel(self):
        self.core["validate_identity"](self.req, identity(self.req))
        for mutate in (lambda d: d["devices"][0].update(cid="0" * 32),
                       lambda d: d["devices"][0].update(bytes=1),
                       lambda d: d["devices"][0].update(device_path="/sys/devices/other"),
                       lambda d: d["devices"][0].update(type="SD"),
                       lambda d: d["devices"].append(copy.deepcopy(d["devices"][0])),
                       lambda d: d.update(sd_status="okay"),
                       lambda d: d["root"].update(uuid="wrong"),
                       lambda d: d["root"].update(parent_path="/sys/wrong"),
                       lambda d: d.update(kernel_release="wrong")):
            data = identity(self.req)
            mutate(data)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.core["validate_identity"](self.req, data)

    def test_os_release_is_parsed_not_executed(self):
        parsed = self.core["os_release"]('NAME="Debian GNU/Linux"\nVERSION_CODENAME=bookworm\nPRETTY_NAME="$(任意內容)"\n')
        self.assertEqual(parsed["PRETTY_NAME"], "$(任意內容)")
        with self.assertRaises(ValueError):
            self.core["os_release"]("NAME=x\nNAME=y\n")

    @contextmanager
    def fake_file_system(self, available_mib=69, *, read_mode=None, short_write=False, cache_error=False, fsync_error=False):
        var = self.root / "var"
        tmp = var / "tmp"
        tmp.mkdir(parents=True, mode=0o700)
        actual_open, actual_fstat = os.open, os.fstat
        actual_write, actual_read, actual_fsync = os.write, os.read, os.fsync
        handles, events = {}, []
        def opened(path, flags, *args, **kwargs):
            self.assertFalse(str(path).startswith("/dev/"))
            fd = actual_open(var if path == "/var" else path, flags, *args, **kwargs)
            if path in ("tmp", "payload"):
                handles[path] = fd
            return fd
        def info(fd):
            value = actual_fstat(fd)
            if fd == handles.get("tmp"):
                return SimpleNamespace(st_dev=value.st_dev, st_ino=value.st_ino, st_uid=0, st_mode=value.st_mode)
            return value
        def write(fd, data):
            return actual_write(fd, data[:77777] if short_write else data)
        def read(fd, size):
            if fd == handles.get("payload"):
                if read_mode == "truncated":
                    return b""
                if read_mode == "corrupt":
                    return b"x" * len(actual_read(fd, size))
            return actual_read(fd, size)
        def fsync(fd):
            events.append(("fsync", fd))
            if fsync_error and fd == handles.get("payload"):
                raise OSError("注入同步失敗")
            return actual_fsync(fd)
        def fadvise(fd, offset, length, advice):
            events.append(("cache", fd))
            self.assertIn(("fsync", fd), events)
            self.assertEqual(advice, os.POSIX_FADV_DONTNEED)
            if cache_error:
                raise OSError("注入單檔快取丟棄失敗")
        with ExitStack() as stack:
            for name, replacement in (("open", opened), ("fstat", info), ("write", write), ("read", read),
                                      ("fsync", fsync), ("posix_fadvise", fadvise),
                                      ("fstatvfs", lambda _: SimpleNamespace(f_bavail=available_mib, f_frsize=1024**2))):
                stack.enter_context(patch.object(self.core["os"], name, replacement))
            dev = tmp.stat().st_dev
            yield tmp, f"{os.major(dev)}:{os.minor(dev)}", events

    def test_default_16_mib_fits_69_mib_and_cleans_only_owned_temp(self):
        state = {}
        with self.fake_file_system() as (tmp, devnum, events):
            other = tmp / "preserve"
            other.write_text("保留既有檔案")
            self.core["file_test"](devnum, self.req["nonce"], time.monotonic() + 5, state)
            self.assertTrue(state["ok"])
            self.assertEqual(state["bytes_written"], 16 * 1024**2)
            self.assertTrue(state["cleaned"])
            self.assertFalse(state["physical_read_verified"])
            self.assertEqual(list(tmp.iterdir()), [other])
            self.assertEqual(other.read_text(), "保留既有檔案")
            self.assertTrue(any(e[0] == "cache" for e in events))

    def test_32_mib_reserve_rejects_before_creating_temp(self):
        state = {}
        with self.fake_file_system(available_mib=47) as (tmp, devnum, _):
            with self.assertRaisesRegex(ValueError, "32 MiB"):
                self.core["file_test"](devnum, self.req["nonce"], time.monotonic() + 5, state)
            self.assertEqual(list(tmp.iterdir()), [])
            self.assertEqual(state["bytes_written"], 0)

    def test_exact_space_boundary_and_short_writes(self):
        state = {}
        with self.fake_file_system(available_mib=33, short_write=True) as (tmp, devnum, _):
            self.core["file_test"](devnum, self.req["nonce"], time.monotonic() + 5, state, 1)
            self.assertTrue(state["ok"])
            self.assertEqual(state["bytes_read"], 1024**2)
            self.assertEqual(list(tmp.iterdir()), [])

    def test_truncated_read_cleans_temp_but_fails(self):
        state = {}
        with self.fake_file_system(read_mode="truncated") as (tmp, devnum, _), self.assertRaisesRegex(ValueError, "截斷"):
            try:
                self.core["file_test"](devnum, self.req["nonce"], time.monotonic() + 5, state, 1)
            finally:
                self.assertEqual(list(tmp.iterdir()), [])
                self.assertFalse(state["ok"])
                self.assertEqual(state["bytes_written"], 1024**2)

    def test_corrupt_read_is_not_success(self):
        with self.fake_file_system(read_mode="corrupt") as (tmp, devnum, _), self.assertRaisesRegex(ValueError, "SHA-256"):
            self.core["file_test"](devnum, self.req["nonce"], time.monotonic() + 5, {}, 1)

    def test_cache_failure_cleans_and_refuses_read_success(self):
        state = {}
        with self.fake_file_system(cache_error=True) as (tmp, devnum, _):
            with self.assertRaises(OSError):
                self.core["file_test"](devnum, self.req["nonce"], time.monotonic() + 5, state, 1)
            self.assertEqual(list(tmp.iterdir()), [])
            self.assertEqual(state["bytes_read"], 0)
            self.assertFalse(state["ok"])

    def test_file_fsync_failure_still_cleans(self):
        with self.fake_file_system(fsync_error=True) as (tmp, devnum, _):
            with self.assertRaises(OSError):
                self.core["file_test"](devnum, self.req["nonce"], time.monotonic() + 5, {}, 1)
            self.assertEqual(list(tmp.iterdir()), [])

    def test_wrong_var_tmp_device_refuses_before_write(self):
        with self.fake_file_system() as (tmp, _, _), self.assertRaises(ValueError):
            self.core["file_test"]("0:0", self.req["nonce"], time.monotonic() + 5, {}, 1)
        self.assertEqual(list(tmp.iterdir()), [])

    def test_existing_nonce_directory_is_not_deleted(self):
        with self.fake_file_system() as (tmp, devnum, _):
            old = tmp / ("bpi-customer-smoke-" + self.req["nonce"])
            old.mkdir()
            (old / "preserve").write_text("不可刪除")
            with self.assertRaises(FileExistsError):
                self.core["file_test"](devnum, self.req["nonce"], time.monotonic() + 5, {}, 1)
            self.assertTrue((old / "preserve").exists())

    def fake_execute(self, failed_services=True, bad_identity=False):
        called = []
        def command(argv, deadline, limit=1024**2):
            called.append(argv)
            stdout = ""
            if argv[0] == "systemctl" and failed_services:
                stdout = "console-setup.service loaded failed failed 控制台設定\n"
            if argv[0] == "dmesg":
                stdout = "end0: Link is Up - 100Mbps/Full\n$(不得執行)\n"
            if argv == ["iw", "dev"]:
                stdout = "phy#0\n\tInterface wlan0\n\tInterface bad;reset\n"
            if argv == ["iw", "dev", "wlan0", "link"]:
                stdout = "Not connected.\n"
            return {"argv": argv, "ok": True, "rc": 0, "stdout": stdout, "stderr": ""}
        def text_file(path, limit):
            return "VERSION_CODENAME=bookworm\n" if path == "/etc/os-release" else "資料"
        proof = success(self.core, self.req)
        cpu = Mock(return_value=proof["cpu"])
        def file_test(devnum, nonce, deadline, state, file_mib):
            state.update(proof["file"])
            self.assertEqual(file_mib, 16)
        file = Mock(side_effect=file_test)
        def read_identity(*_):
            if bad_identity:
                raise ValueError("根 CID 不符")
            return identity(self.req)
        with patch.dict(self.core, command=command, text_file=text_file, identity=read_identity,
                        cpu_test=cpu, file_test=file), patch.object(self.core["os"], "geteuid", return_value=0):
            return self.core["execute"](self.req), called, cpu, file

    def test_failed_services_do_not_block_cpu_file_or_final_dmesg(self):
        report, called, cpu, file = self.fake_execute()
        self.assertFalse(report["ok"])
        self.assertFalse(report["failed_services"]["ok"])
        self.assertTrue(report["cpu"]["ok"])
        self.assertTrue(report["file"]["ok"])
        cpu.assert_called_once()
        file.assert_called_once()
        self.assertEqual(sum(c[0] == "dmesg" for c in called), 2)
        self.assertEqual(report["wifi"]["status"], "not_tested")
        self.assertEqual(report["emac"]["status"], "not_tested")
        self.assertTrue(all("bad;reset" not in c for c in called))

    def test_identity_failure_prevents_any_file_test_but_keeps_dmesg(self):
        report, _, cpu, file = self.fake_execute(bad_identity=True)
        cpu.assert_not_called()
        file.assert_not_called()
        self.assertFalse(report["ok"])
        self.assertIn("dmesg_after", report["commands"])

    def test_success_is_only_short_smoke_not_wifi_emac_or_system(self):
        report, _, _, _ = self.fake_execute(failed_services=False)
        self.assertTrue(report["ok"])
        self.assertFalse(report["system_verified"])
        smoke.validate_result(report, self.req)

    def test_fake_success_wrong_hash_or_wifi_claim_is_rejected(self):
        for mutate in (lambda r: r["file"].update(sha256="0" * 64),
                       lambda r: r["cpu"]["workers"][0].update(iterations=0),
                       lambda r: r["wifi"].update(status="passed"),
                       lambda r: r["failed_services"].update(units=["console-setup.service"]),
                       lambda r: r["identity_after"]["devices"][0].update(cid="0" * 32)):
            report = success(self.core, self.req)
            mutate(report)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                smoke.validate_result(report, self.req)

    def test_missing_program_returns_structured_failure(self):
        with patch.object(self.core["subprocess"], "Popen", side_effect=FileNotFoundError):
            result = self.core["command"](["iw", "dev"], time.monotonic() + 2)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["rc"])


if __name__ == "__main__":
    unittest.main()
