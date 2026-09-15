#!/usr/bin/env python3
"""H618 唯讀採樣的離線回歸；所有遠端資料及 SSH 程序均為模擬。"""

import ast
from contextlib import redirect_stderr, redirect_stdout
import errno
import importlib.util
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bpi_h618_observe", ROOT / "tools/bpi_h618_observe.py")
observe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observe)
TARGET = "pi@192.0.2.10"
PROFILE = "bananapim4berry"
DT_ROOT = "/sys/firmware/devicetree/base/"


def ok(value):
    return {"status": "ok", "value": value}


def fixture(profile=PROFILE, with_lo=False):
    data = {
        "schema": 1, "sampling_finished": True,
        "physical_identity_verified": False, "hardware_validation": False,
        "model": ok("BananaPi M4 Berry"), "compatible": ok(list(observe.PROFILES[profile])),
        "kernel": ok({"sysname": "Linux", "release": "6.18.1", "version": "#1", "machine": "aarch64"}),
        "armbian_release": ok({"BOARD": profile}),
        "mmc": ok([{"device": "mmcblk0", "type": ok("SD\n"), "name": ok("SD32G\n"),
                    "size": ok("62521344\n"), "controlpath": ok("/sys/devices/platform/mmc_host/mmc0")}]),
        "net": ok([{"interface": "eth0", "operstate": ok("up\n"),
                    "speed": ok("1000\n"), "driver": ok("dwmac-sun8i")}]),
        "mounts": ok("/dev/mmcblk0p1 / ext4 rw 0 0\n"),
        "swaps": ok("Filename\tType\tSize\tUsed\tPriority\n"),
    }
    if with_lo:
        data["net"]["value"].append({
            "interface": "lo", "operstate": ok("unknown\n"),
            "speed": {"status": "unavailable", "reason": "迴路介面不適用"},
            "driver": {"status": "unavailable", "reason": "迴路介面不適用"},
        })
    return data


def encoded(data):
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


class ObservationTests(unittest.TestCase):
    def report(self, data, expected=PROFILE):
        with mock.patch.object(observe, "fetch", return_value=encoded(data)) as fetch:
            result = observe.observe(TARGET, expected)
        fetch.assert_called_once_with(TARGET)
        return result

    def test_three_profiles_match_repository_root_compatible(self):
        names = {"bananapim4zero": "zero", "bananapim4zeroemac": "zero-emac", PROFILE: "berry"}
        for profile, suffix in names.items():
            with self.subTest(profile=profile):
                path = ROOT / "patch/kernel/archive/sunxi-6.18/dt_64" / (
                    "sun50i-h618-bananapi-m4-" + suffix + ".dts")
                source = path.read_text(encoding="utf-8")
                values = re.search(r'compatible\s*=\s*([^;]+);', source).group(1)
                self.assertEqual(tuple(re.findall(r'"([^"]+)"', values)), observe.PROFILES[profile])
                data = fixture(profile)
                data["model"] = ok(re.search(r'model\s*=\s*"([^"]+)"', source).group(1))
                result = self.report(data, profile)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["software_profile"], profile)
                self.assertIs(result["physical_identity_verified"], False)
                self.assertIs(result["hardware_validation"], False)

    def test_unknown_conflicting_and_mismatched_profiles_rejected(self):
        for compatible in ([], ["allwinner,sun50i-h618"], ["sinovoip,bpi-m4berry"],
                           list(observe.PROFILES[PROFILE]) + ["sinovoip,bpi-m4-zero"],
                           list(observe.PROFILES["bananapim4zero"])):
            with self.subTest(compatible=compatible):
                data = fixture()
                data["compatible"] = ok(compatible)
                result = self.report(data)
                self.assertEqual(result["status"], "rejected")
                self.assertIs(result["physical_identity_verified"], False)

    def test_emac_requires_explicit_compatible_not_model_or_network(self):
        data = fixture("bananapim4zero")
        data["model"] = ok("BananaPi BPI-M4-Zero EMAC")
        self.assertEqual(self.report(data, "bananapim4zeroemac")["status"], "rejected")
        data["compatible"] = ok(list(observe.PROFILES["bananapim4zeroemac"]))
        data["armbian_release"] = ok({"BOARD": "bananapim4zeroemac"})
        data["model"] = ok("BananaPi BPI-M4-Zero")
        self.assertEqual(self.report(data, "bananapim4zeroemac")["status"], "ok")
        self.assertEqual(self.report(data, "bananapim4zero")["status"], "rejected")

    def test_unavailable_and_error_remain_incomplete(self):
        for status in ("unavailable", "error"):
            data = fixture()
            data["net"]["value"][0]["speed"] = {"status": status, "reason": "模擬無法讀取"}
            result = self.report(data)
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(result["incomplete_fields"], ["net.eth0.speed"])
            self.assertEqual(result["observation"], data)
        data["compatible"] = {"status": "unavailable", "reason": "缺少 DT"}
        self.assertEqual(self.report(data)["status"], "rejected")

    def test_invalid_expected_profile_does_not_launch(self):
        with mock.patch.object(observe, "fetch") as fetch:
            with self.assertRaises(observe.ObservationError):
                observe.observe(TARGET, "unknown")
            fetch.assert_not_called()

    def test_missing_release_board_is_incomplete(self):
        data = fixture()
        data["armbian_release"] = ok({"VERSION": "26.02.0"})
        report = self.report(data)
        self.assertEqual(report["status"], "incomplete")
        self.assertIn("armbian_release.BOARD", report["incomplete_fields"])

    def test_blank_required_text_is_rejected(self):
        for name in ("model", "mounts", "swaps"):
            data = fixture()
            data[name] = ok(" \n\t")
            with self.subTest(name=name), self.assertRaises(observe.ObservationError):
                observe.parse_observation(encoded(data))

    def test_nested_ok_values_must_not_be_empty(self):
        for group, keys in (("mmc", ("type", "name", "size", "controlpath")),
                            ("net", ("operstate", "speed", "driver"))):
            for key in keys:
                for value in ("", " \t\n"):
                    data = fixture()
                    data[group]["value"][0][key] = ok(value)
                    with self.subTest(group=group, key=key, value=value), self.assertRaises(
                            observe.ObservationError):
                        self.report(data)

    def test_mmc_size_requires_positive_decimal_integer(self):
        for value in ("0", "-1", "+1", "1.0", "1e3", "0x10", "01", "１２", "1\n2"):
            data = fixture()
            data["mmc"]["value"][0]["size"] = ok(value)
            with self.subTest(value=value), self.assertRaises(observe.ObservationError):
                self.report(data)
        for value in ("1", "62521344\n"):
            data = fixture()
            data["mmc"]["value"][0]["size"] = ok(value)
            self.assertEqual(self.report(data)["status"], "ok")

    def test_loopback_unavailable_fields_are_not_applicable(self):
        data = fixture(with_lo=True)
        result = self.report(data)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["incomplete_fields"], [])
        self.assertEqual(result["not_applicable_fields"], ["net.lo.driver", "net.lo.speed"])
        self.assertEqual(result["observation"], data)
        self.assertIs(result["physical_identity_verified"], False)
        self.assertIs(result["hardware_validation"], False)

    def test_loopback_errors_and_missing_operstate_remain_incomplete(self):
        for key, status in (("driver", "error"), ("speed", "error"), ("operstate", "unavailable")):
            data = fixture(with_lo=True)
            data["net"]["value"][1][key] = {"status": status, "reason": "模擬讀取失敗"}
            result = self.report(data)
            self.assertEqual(result["status"], "incomplete")
            self.assertIn("net.lo." + key, result["incomplete_fields"])
            self.assertNotIn("net.lo." + key, result["not_applicable_fields"])
        for key in ("driver", "speed"):
            data = fixture(with_lo=True)
            data["net"]["value"][1][key] = ok("")
            with self.assertRaises(observe.ObservationError):
                self.report(data)

    def test_software_declaration_conflicts_are_rejected(self):
        for group, key, value in (("armbian_release", "BOARD", "bananapim2ultra"),
                                  ("armbian_release", "BOARD", "bananapim4zero"),
                                  ("armbian_release", "BOARD", ""),
                                  ("kernel", "machine", "armv7l"),
                                  ("kernel", "machine", "x86_64"),
                                  ("kernel", "sysname", "FreeBSD")):
            data = fixture(with_lo=True)
            data[group]["value"][key] = value
            with self.subTest(group=group, key=key, value=value):
                result = self.report(data)
                self.assertEqual(result["status"], "rejected")
                self.assertIn("軟體宣告衝突", result["error"])
                self.assertIs(result["physical_identity_verified"], False)
                self.assertIs(result["hardware_validation"], False)

    def test_malformed_json_rejected(self):
        for raw in (b"", b"{", b"[]", b"null", b"{}", b"\xff", b'{"schema":1,"schema":1}',
                    encoded(fixture()) + b"{}", b"[" * 2000 + b"]" * 2000,
                    b"x" * (observe.MAX_OUTPUT_BYTES + 1)):
            with self.subTest(size=len(raw)), self.assertRaises(observe.ObservationError):
                observe.parse_observation(raw)

    def test_missing_wrong_or_extra_fields_rejected(self):
        changes = (("schema", True), ("sampling_finished", False), ("model", ok("")),
                   ("compatible", ok([{}])), ("kernel", ok({})), ("net", ok([{}])),
                   ("mmc", ok([{}] * 33)), ("hardware_validation", True),
                   ("physical_identity_verified", True), ("serial", "禁止採集"),
                   ("armbian_release", ok({"SECRET": "禁止採集"})),
                   ("model", ok("\ud800")), ("mounts", ok("")), ("swaps", ok("")),
                   ("armbian_release", ok({})),
                   ("mounts", {"status": "ok"}), ("swaps", {"status": "unknown"}))
        for key, value in changes:
            data = fixture()
            data[key] = value
            with self.subTest(key=key), self.assertRaises(observe.ObservationError):
                observe.parse_observation(json.dumps(data, ensure_ascii=True).encode("utf-8"))
        for key in fixture():
            data = fixture()
            del data[key]
            with self.subTest(missing=key), self.assertRaises(observe.ObservationError):
                observe.parse_observation(encoded(data))

    def test_duplicate_device_rejected(self):
        data = fixture()
        data["mmc"]["value"] *= 2
        with self.assertRaises(observe.ObservationError):
            observe.parse_observation(encoded(data))

    def test_cli_json_exit_status_and_flags(self):
        incomplete = {**fixture(), "mounts": {"status": "error", "reason": "模擬讀取失敗"}}
        for data, expected_code in ((fixture(), 0), ({**fixture(), "compatible": ok([])}, 1), (incomplete, 1)):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(observe, "fetch", return_value=encoded(data)), redirect_stdout(out), redirect_stderr(err):
                code = observe.main(["--target", TARGET, "--expected-profile", PROFILE])
            self.assertEqual(code, expected_code)
            self.assertEqual(err.getvalue(), "")
            self.assertIs(json.loads(out.getvalue())["hardware_validation"], False)

    def test_cli_transport_and_parse_errors_are_json(self):
        for effect in (observe.ObservationError("SSH 採樣逾時"), b"{", b"x" * (observe.MAX_OUTPUT_BYTES + 1)):
            out, err = io.StringIO(), io.StringIO()
            kwargs = {"side_effect": effect} if isinstance(effect, Exception) else {"return_value": effect}
            with mock.patch.object(observe, "fetch", **kwargs), redirect_stdout(out), redirect_stderr(err):
                code = observe.main(["--target", TARGET, "--expected-profile", PROFILE])
            self.assertNotEqual(code, 0)
            self.assertEqual(err.getvalue(), "")
            result = json.loads(out.getvalue())
            self.assertEqual(result["status"], "error")
            self.assertIs(result["physical_identity_verified"], False)
            self.assertIs(result["hardware_validation"], False)

    def test_cli_failures_are_json_without_ssh(self):
        for arguments in ([], ["--target", TARGET], ["--target", "pi@bad", "--expected-profile", PROFILE],
                          ["--target", TARGET, "--expected-profile", "unknown"],
                          ["--target", TARGET, "--expected-profile", PROFILE, "--command", "id"]):
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(observe.subprocess, "Popen") as popen, redirect_stdout(out), redirect_stderr(err):
                code = observe.main(arguments)
            self.assertNotEqual(code, 0)
            self.assertEqual(err.getvalue(), "")
            self.assertEqual(json.loads(out.getvalue())["status"], "error")
            popen.assert_not_called()

    def test_help_is_traditional_chinese(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit) as raised:
            observe.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertIn("用法：", out.getvalue())
        self.assertNotIn("usage:", out.getvalue())
        self.assertNotIn("options:", out.getvalue())


class TransportTests(unittest.TestCase):
    def run_local(self, program, timeout=2):
        """替換 Popen 的執行對象，只啟動不寫檔的本機 Python 假程序。"""
        real_popen = subprocess.Popen
        children = []

        def launch(command, **kwargs):
            self.assertEqual(command, observe.ssh_command(TARGET))
            self.assertIs(kwargs["shell"], False)
            process = real_popen([sys.executable, "-B", "-c", program], **kwargs)
            children.append(process)
            return process

        try:
            with mock.patch.object(observe.subprocess, "Popen", side_effect=launch):
                return observe.fetch(TARGET, timeout=timeout)
        finally:
            for process in children:
                self.assertIsNotNone(process.poll(), "假程序必須已被回收")

    def test_exact_fixed_ssh_options(self):
        self.assertEqual(observe.ssh_command(TARGET), [
            "ssh", "-F", "/dev/null", "-T", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ClearAllForwardings=yes", "-o", "RequestTTY=no", "-o", "ControlMaster=no",
            "-o", "ControlPath=none", "-o", "PermitLocalCommand=no", "-o", "ConnectTimeout=5",
            "-o", "ConnectionAttempts=1", "-o", "UpdateHostKeys=no", TARGET, "python3", "-",
        ])

    def test_account_ipv4_and_injection_rejected(self):
        for target in ("pi@example.com", "pi@::1", "pi@192.0.2.999", "pi@192.000.2.1",
                       "-oProxyCommand=id@192.0.2.1", "pi;id@192.0.2.1", "pi@192.0.2.1;id",
                       "pi@192.0.2.1\nid", "pi@192.0.2.1 -p22", "pi@@192.0.2.1",
                       "pi@192.0.2.1/24", "pi@192.0.2.1:22", "@192.0.2.1", "a" * 33 + "@192.0.2.1"):
            with self.subTest(target=target), mock.patch.object(observe.subprocess, "Popen") as popen:
                with self.assertRaises(observe.ObservationError):
                    observe.fetch(target)
                popen.assert_not_called()

    def test_fixed_stdin_program_and_separate_stderr(self):
        payload = encoded(fixture())
        program = ("import sys\n"
                   f"assert sys.stdin.buffer.read() == {observe.REMOTE_SCRIPT.encode('utf-8')!r}\n"
                   "sys.stderr.buffer.write(b'\\xff')\n"
                   f"sys.stdout.buffer.write({payload!r})\n")
        self.assertEqual(self.run_local(program), payload)

    def test_timeout_including_blocked_stdin_and_closed_output(self):
        for prefix in ("", "import os; os.close(0); os.close(1); os.close(2); "):
            started = time.monotonic()
            with mock.patch.object(observe, "REMOTE_SCRIPT", "x" * 100000), self.assertRaisesRegex(
                    observe.ObservationError, "逾時"):
                self.run_local(prefix + "import time; time.sleep(10)", timeout=0.2)
            self.assertLess(time.monotonic() - started, 3)

    def test_stdout_stderr_and_combined_limit(self):
        for stdout_size, stderr_size in ((1025, 0), (0, 1025), (512, 513)):
            program = ("import sys\n"
                       "sys.stdin.buffer.read()\n"
                       f"sys.stdout.buffer.write(b'x' * {stdout_size})\n"
                       f"sys.stderr.buffer.write(b'x' * {stderr_size})\n")
            with mock.patch.object(observe, "MAX_OUTPUT_BYTES", 1024), self.assertRaisesRegex(
                    observe.ObservationError, "位元組上限"):
                self.run_local(program)
        with mock.patch.object(observe, "MAX_OUTPUT_BYTES", 1024):
            result = self.run_local("import sys; sys.stdin.buffer.read(); sys.stdout.buffer.write(b'x' * 1024)")
        self.assertEqual(len(result), 1024)

    def test_nonzero_return_and_missing_ssh_fail(self):
        with self.assertRaisesRegex(observe.ObservationError, "非零|不為零"):
            self.run_local("import sys; sys.stdin.buffer.read(); sys.stdout.write('{}'); sys.exit(7)")
        with mock.patch.object(observe.subprocess, "Popen", side_effect=FileNotFoundError), self.assertRaises(
                observe.ObservationError):
            observe.fetch(TARGET)

    def test_timeout_parameter_rejected_before_launch(self):
        for timeout in (0, -1, float("inf"), float("nan"), 61, "1"):
            with self.subTest(timeout=timeout), mock.patch.object(observe.subprocess, "Popen") as popen:
                with self.assertRaises(observe.ObservationError):
                    observe.fetch(TARGET, timeout)
                popen.assert_not_called()


class RemoteScriptTests(unittest.TestCase):
    def setUp(self):
        self.files = {
            DT_ROOT + "model": b"BananaPi M4 Berry\0",
            DT_ROOT + "compatible": b"BiPai,bananapi-m4berry\0allwinner,sun50i-h616\0",
            "/etc/armbian-release": b'BOARD=bananapim4berry\nBOARD_NAME="BananaPi M4 Berry"\nSECRET=omitted\n',
            "/sys/class/block/mmcblk0/device/type": b"SD\n",
            "/sys/class/block/mmcblk0/device/name": b"SD32G\n",
            "/sys/class/block/mmcblk0/size": b"62521344\n",
            "/sys/class/net/eth0/operstate": b"up\n",
            "/sys/class/net/eth0/speed": b"1000\n",
            "/proc/mounts": b"/dev/mmcblk0p1 / ext4 rw 0 0\n",
            "/proc/swaps": b"Filename\tType\tSize\tUsed\tPriority\n",
        }
        self.directories = {"/sys/class/block": ["mmcblk0", "mmcblk0p1", "mmcblk0boot0"],
                            "/sys/class/net": ["eth0"]}
        self.reads = []

    def sample(self):
        def read(path, mode):
            self.assertEqual(mode, "rb", "遠端只能開啟唯讀檔案")
            self.reads.append(path)
            value = self.files.get(path, FileNotFoundError(errno.ENOENT, "模擬檔案不存在"))
            if isinstance(value, Exception):
                raise value
            return io.BytesIO(value)

        def scan(path):
            entries = [SimpleNamespace(name=name) for name in self.directories[path]]
            context = mock.MagicMock()
            context.__enter__.return_value = iter(entries)
            return context

        def resolve(path, strict):
            self.assertTrue(strict)
            paths = {"/sys/class/block/mmcblk0/device": "/sys/devices/platform/mmc_host/mmc0/mmc0:0001",
                     "/sys/class/net/eth0/device/driver": "/sys/bus/platform/drivers/dwmac-sun8i"}
            return Path(paths[str(path)])

        output = io.StringIO()
        namespace = {}
        with mock.patch("builtins.open", side_effect=read), mock.patch("os.scandir", side_effect=scan), \
                mock.patch.object(Path, "resolve", autospec=True, side_effect=resolve), \
                mock.patch("os.uname", return_value=SimpleNamespace(
                    sysname="Linux", release="6.18.1", version="#1", machine="aarch64")), redirect_stdout(output):
            exec(compile(observe.REMOTE_SCRIPT, "<固定遠端採樣>", "exec"), namespace)
        return json.loads(output.getvalue())

    def test_remote_output_schema_and_allowlisted_paths(self):
        result = self.sample()
        self.assertEqual(result, {**fixture(), "armbian_release": ok({"BOARD": PROFILE, "BOARD_NAME": "BananaPi M4 Berry"})})
        self.assertEqual(set(self.reads), set(self.files))
        self.assertEqual(observe.parse_observation(encoded(result))[1], [])
        self.assertFalse(any(word in path for path in self.reads for word in ("cid", "serial", "address")))

    def test_absent_error_and_read_bounds_are_distinct(self):
        for value, expected in ((FileNotFoundError(errno.ENOENT, "模擬不存在"), "unavailable"),
                                (OSError(errno.EINVAL, "模擬不支援"), "unavailable"),
                                (PermissionError(errno.EACCES, "模擬拒絕"), "error"),
                                (b"x" * 4097, "error"), (b"\xff", "error")):
            with self.subTest(expected=expected):
                self.files["/sys/class/net/eth0/speed"] = value
                self.assertEqual(self.sample()["net"]["value"][0]["speed"]["status"], expected)
        self.files["/proc/mounts"] = b"x" * 65536
        self.assertEqual(len(self.sample()["mounts"]["value"]), 65536)
        self.files["/proc/mounts"] += b"x"
        self.assertEqual(self.sample()["mounts"]["status"], "error")

    def test_unterminated_and_multiple_model_strings_rejected(self):
        for value in (b"", b"BananaPi M4 Berry", b"BananaPi\0M4 Berry\0", b"\0"):
            self.files[DT_ROOT + "model"] = value
            self.assertEqual(self.sample()["model"]["status"], "error")
        self.files[DT_ROOT + "compatible"] = b"BiPai,bananapi-m4berry"
        self.assertEqual(self.sample()["compatible"]["status"], "error")

    def test_release_is_parsed_without_evaluation(self):
        self.files["/etc/armbian-release"] = b'BOARD="$(id)"\nSECRET="$(id)"\n'
        self.assertEqual(self.sample()["armbian_release"], ok({"BOARD": "$(id)"}))
        for value in (b"BOARD=a\nBOARD=b\n", b'BOARD="', b"BOARD=a b", b"x" * 16385, b"SECRET=a\n", b""):
            self.files["/etc/armbian-release"] = value
            self.assertEqual(self.sample()["armbian_release"]["status"], "error")

    def test_device_iteration_and_item_count_bounded(self):
        self.directories["/sys/class/block"] = ["sda"] * 129
        self.assertEqual(self.sample()["mmc"]["status"], "error")
        self.directories["/sys/class/net"] = ["eth0"] * 33
        self.assertEqual(self.sample()["net"]["status"], "error")

    def test_remote_script_has_no_write_or_command_operations(self):
        tree = ast.parse(observe.REMOTE_SCRIPT)
        imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        self.assertEqual(imports, {"errno", "json", "os", "re", "shlex"})
        self.assertEqual([(node.module, [alias.name for alias in node.names])
                          for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)], [("pathlib", ["Path"])])
        allowed = {"operation", "read", "sample", "text_field", "dt_strings", "release", "devices", "kernel",
                   "open", "len", "ValueError", "all", "enumerate", "str", "Path", "sorted", "getattr", "print",
                   "stream.read", "raw.decode", "raw.endswith", "line.partition", "key.strip", "shlex.split",
                   "os.scandir", "re.fullmatch", "os.path.dirname", "os.path.basename", "result.append",
                   "os.uname", "json.dumps"}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, (ast.Call, ast.Subscript)):
                self.assertIn(node.func.attr, {"split", "splitlines", "resolve"})
            else:
                self.assertIn(ast.unparse(node.func), allowed)
            if isinstance(node.func, ast.Name) and node.func.id == "open":
                self.assertEqual(ast.literal_eval(node.args[1]), "rb")


if __name__ == "__main__":
    unittest.main()
