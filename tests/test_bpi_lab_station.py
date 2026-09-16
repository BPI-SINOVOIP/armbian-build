"""僅使用純資料、本機暫存檔與假適配器；不接觸任何實體站點。"""

import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bpi_lab_station as lab


def station():
    return {
        "schema": "bpi-lab-station-v1", "station_id": "fixture-station",
        "mode": "simulation", "enabled": True, "hardware_id": "fixture-board",
        "board": "Banana Pi M4 Zero 測試板", "compatible_boards": ["bpi-m4zero"],
        "boot_config_sha256": "b" * 64, "test_version": "fixture-v1",
        "timeout_seconds": 2,
        "resources": {kind: "sim:fixture:" + kind for kind in ("uart", "power", "media")},
        "adapter": {"kind": "simulator-v1"},
    }


def hardware():
    data = station()
    data.update(mode="hardware", hardware_id="HW-FIXTURE",
                resources={"uart": "uart:fixture:serial-1", "power": "power:fixture:port-1",
                           "media": "cid:fixture-media-1"},
                adapter={"kind": "external-v1", "argv": ["/fixture/not-executed"], "sha256": "e" * 64},
                qualification={"status": "qualified", "evidence_sha256": "c" * 64,
                               "evidence_path": "/fixture/not-opened.json"},
                authorization={"userarea_write": True, "hardware_id": "HW-FIXTURE",
                               "media_identity": "cid:fixture-media-1", "backup_sha256": "d" * 64,
                               "record": "本機假資料授權，非實體設備授權"})
    return data


def request(data=None, stage="preflight"):
    data = station() if data is None else data
    return {
        "schema": "bpi-lab-request-v1", "work_key": "fixture-work", "attempt_id": "fixture-attempt",
        "stage": stage, "station_id": data["station_id"], "hardware_id": data["hardware_id"],
        "image_sha256": "a" * 64, "boot_config_sha256": data["boot_config_sha256"],
        "test_version": data["test_version"], "mode": data["mode"],
        "image": {"board": "bpi-m4zero", "sha256": "a" * 64, "path": "fixture.img.xz"},
        "image_root": "/fixture/images",
    }


def report(req, status="passed"):
    result = {"schema": "bpi-lab-stage-v1", **{key: req[key] for key in lab.BINDINGS},
              "status": status, "hardware_validated": req["mode"] == "hardware"}
    if req["mode"] == "hardware":
        result.update(media_identity_verified=True, backup_verified=True,
                      full_readback_verified=True, compressed_sha256_verified=True,
                      customer_kernel_verified=True, checks={"fixture": True}, rescue_verified=True)
    return result


def add_resume(req):
    req["resume"] = {"next_stage": "boot", "previous_reports": [
        {"stage": "deploy", "sha256": "f" * 64, "path": "/fixture/deploy/response.json"}]}
    return req


def qualification_document(data):
    return {"schema": "bpi-lab-qualification-v1",
            **{key: copy.deepcopy(data[key]) for key in
               ("station_id", "hardware_id", "resources", "boot_config_sha256", "test_version")},
            "media": data["resources"]["media"], "adapter_sha256": data["adapter"]["sha256"],
            "hardware_validated": True, "rescue_verified": True, "single_image_cycle_verified": True,
            "source_evidence": [{"path": "/fixture/raw-not-opened.json", "sha256": "f" * 64}]}


class StationValidationTests(unittest.TestCase):
    def test_original_data_digest_and_shared_locks(self):
        data = station()
        original = copy.deepcopy(data)
        self.assertIs(lab.validate_station(data), data)
        self.assertEqual(data, original)
        self.assertEqual(lab.station_digest(data), lab.station_digest(dict(reversed(list(data.items())))))
        other = copy.deepcopy(data)
        other["station_id"] = "other-station"
        self.assertNotEqual(lab.station_digest(data), lab.station_digest(other))
        keys = lab.resource_keys(data)
        self.assertEqual(keys, sorted(keys))
        shared = set(keys) & set(lab.resource_keys(other))
        self.assertIn("hardware:fixture-board", shared)
        self.assertEqual(len(shared), 4)

    def test_core_fields_types_and_unknown_fields(self):
        values = {"schema": ["bad"], "mode": ["bad", True], "enabled": [1, "true"],
                  "station_id": ["", " padded", "bad\n"], "hardware_id": [None, ""],
                  "board": [""], "test_version": [""], "boot_config_sha256": ["a", "A" * 64],
                  "timeout_seconds": [0, -1, True, float("nan"), float("inf"), 86401, 10**500],
                  "compatible_boards": [[], ["other"], ["bpi-m4zero"] * 2, [None]],
                  "resources": [None], "adapter": [{}, {"kind": "unknown"}]}
        for field, alternatives in values.items():
            for value in alternatives:
                with self.subTest(field=field, value=value):
                    data = station()
                    data[field] = value
                    with self.assertRaises(ValueError):
                        lab.validate_station(data)
        data = station()
        data["enabeld"] = True
        with self.assertRaises(ValueError):
            lab.validate_station(data)

    def test_resource_ids(self):
        for kind, value in (("uart", "/dev/ttyUSB0"), ("media", "/dev/mmcblk0"),
                            ("power", "power:unknown"), ("media", "sim:media"),
                            ("uart", "/dev/serial/by-id/../ttyUSB0"), ("media", "pending:x:media")):
            data = hardware()
            data["resources"][kind] = value
            with self.subTest(kind=kind, value=value), self.assertRaises(ValueError):
                lab.validate_station(data)
        data = station()
        data["resources"]["media"] = data["resources"]["uart"]
        with self.assertRaises(ValueError):
            lab.validate_station(data)
        data = hardware()
        data["resources"]["uart"] = "/dev/serial/by-id/usb-fixture-unique"
        data["resources"]["media"] = "/dev/disk/by-id/mmc-fixture-unique"
        data["authorization"]["media_identity"] = data["resources"]["media"]
        self.assertIs(lab.validate_station(data), data)

    def test_incomplete_hardware_qualification_and_authorization(self):
        original = hardware()
        self.assertIs(lab.validate_station(original), original)
        for section in ("qualification", "authorization", "adapter", "resources"):
            for field in original[section]:
                data = copy.deepcopy(original)
                del data[section][field]
                with self.subTest(section=section, field=field), self.assertRaises(ValueError):
                    lab.validate_station(data)
        for section, field, value in (
            ("qualification", "status", "awaiting_hardware"),
            ("qualification", "evidence_sha256", "0" * 64),
            ("qualification", "evidence_path", "relative.json"),
            ("authorization", "userarea_write", False),
            ("authorization", "userarea_write", 1),
            ("authorization", "hardware_id", "other-board"),
            ("authorization", "media_identity", "cid:other-media"),
            ("authorization", "backup_sha256", "0" * 64),
            ("authorization", "record", ""),
            ("adapter", "argv", []), ("adapter", "argv", ["relative"]),
        ):
            data = hardware()
            data[section][field] = value
            with self.subTest(section=section, field=field), self.assertRaises(ValueError):
                lab.validate_station(data)
        data = hardware()
        data["adapter"] = {"kind": "simulator-v1"}
        with self.assertRaises(ValueError):
            lab.validate_station(data)

    def test_disabled_cli_template_and_missing_fields(self):
        data = hardware()
        data.update(enabled=False, station_id="pending-bpi-m4zero", hardware_id="pending-bpi-m4zero",
                    boot_config_sha256="0" * 64,
                    resources={kind: "pending:bpi-m4zero:" + kind for kind in ("uart", "power", "media")},
                    adapter={"kind": "external-v1", "argv": [], "sha256": "0" * 64},
                    qualification={"status": "awaiting_hardware"}, authorization={"userarea_write": False})
        self.assertIs(lab.validate_station(data), data)
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, "停用"):
            lab.run_stage(data, request(data), Path(tmp) / "evidence")
        data["enabled"] = True
        with self.assertRaises(ValueError):
            lab.validate_station(data)
        data["enabled"] = False
        for key in ("hardware_id", "boot_config_sha256", "resources", "adapter", "qualification", "authorization"):
            data.pop(key)
        self.assertIs(lab.validate_station(data), data)

    def test_simulator_setting_rejections(self):
        for adapter in ({"kind": "simulator-v1", "delay_seconds": True},
                        {"kind": "simulator-v1", "delay_seconds": -1},
                        {"kind": "simulator-v1", "outcomes": {"other": "passed"}},
                        {"kind": "simulator-v1", "outcomes": {"boot": "unknown"}}):
            data = station()
            data["adapter"] = adapter
            with self.subTest(adapter=adapter), self.assertRaises(ValueError):
                lab.validate_station(data)


class ReportContractTests(unittest.TestCase):
    def test_every_binding_and_schema(self):
        req = request()
        valid = report(req)
        self.assertIs(lab.validate_report(valid, req), valid)
        for field in (*lab.BINDINGS, "schema"):
            invalid = dict(valid, **{field: "other"})
            with self.subTest(field=field), self.assertRaises(ValueError):
                lab.validate_report(invalid, req)
            del invalid[field]
            with self.assertRaises(ValueError):
                lab.validate_report(invalid, req)

    def test_no_simulation_promotion(self):
        req = request()
        for value in (True, 0, 1, "false", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                lab.validate_report(dict(report(req), hardware_validated=value), req)
        req = request(hardware())
        with self.assertRaises(ValueError):
            lab.validate_report(dict(report(req), synthetic=True), req)
        with self.assertRaises(ValueError):
            lab.validate_report(dict(report(req), hardware_validated=False), req)

    def test_hardware_stage_attestations(self):
        flags = {"preflight": ("media_identity_verified", "backup_verified"),
                 "deploy": ("full_readback_verified", "compressed_sha256_verified"),
                 "boot": ("customer_kernel_verified",), "recovery": ("rescue_verified",)}
        for stage, required in flags.items():
            req = request(hardware(), stage)
            valid = report(req)
            self.assertIs(lab.validate_report(valid, req), valid)
            for flag in required:
                for value in (False, 1, "true", None):
                    with self.subTest(stage=stage, flag=flag, value=value), self.assertRaises(ValueError):
                        lab.validate_report(dict(valid, **{flag: value}), req)
                invalid = dict(valid)
                del invalid[flag]
                with self.assertRaises(ValueError):
                    lab.validate_report(invalid, req)
        req = request(hardware(), "smoke")
        self.assertEqual(lab.validate_report(report(req), req)["status"], "passed")
        for checks in ({}, {"fixture": False}, {"fixture": 1}, {"fixture": True, "other": False},
                       {"": True}, [True], None):
            with self.subTest(checks=checks), self.assertRaises(ValueError):
                lab.validate_report(dict(report(req), checks=checks), req)

    def test_failed_and_blocked_are_valid_results(self):
        for mode in (station(), hardware()):
            req = request(mode)
            for status in ("failed", "blocked"):
                result = {"schema": "bpi-lab-stage-v1", **{key: req[key] for key in lab.BINDINGS},
                          "status": status, "hardware_validated": False}
                self.assertIs(lab.validate_report(result, req), result)

    def test_resume_requires_current_state_and_next_stage(self):
        for data in (station(), hardware()):
            req = add_resume(request(data))
            valid = dict(report(req), resume_state_verified=True, resume_next_stage="boot")
            self.assertIs(lab.validate_report(valid, req), valid)
            for change in ({"resume_state_verified": False}, {"resume_state_verified": 1},
                           {"resume_next_stage": "deploy"}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    lab.validate_report(dict(valid, **change), req)
            with self.assertRaises(ValueError):
                lab.validate_report(report(req), req)
            self.assertEqual(lab.validate_report(report(req, "blocked"), req)["status"], "blocked")
        req["stage"] = "boot"
        with self.assertRaises(ValueError):
            lab.validate_report(report(req), req)

    def test_repeated_resume_receipts_and_initial_preflight(self):
        req = add_resume(request())
        req["resume"]["previous_reports"] = [
            {"stage": "preflight", "sha256": "f" * 64, "path": f"/fixture/guard-{index}.json"}
            for index in range(3)]
        valid = dict(report(req), resume_state_verified=True, resume_next_stage="boot")
        self.assertIs(lab.validate_report(valid, req), valid)
        req["resume"]["previous_reports"].append(req["resume"]["previous_reports"][0])
        with self.assertRaises(ValueError):
            lab.validate_report(valid, req)
        req["resume"] = {"next_stage": "preflight", "previous_reports": []}
        valid["resume_next_stage"] = "preflight"
        self.assertIs(lab.validate_report(valid, req), valid)


_ADAPTER = """import json, sys
req = json.load(sys.stdin)
keys = ('work_key', 'attempt_id', 'stage', 'station_id', 'hardware_id',
        'image_sha256', 'boot_config_sha256', 'test_version', 'mode')
out = dict(schema='bpi-lab-stage-v1', status='passed', hardware_validated=False,
           **{key: req[key] for key in keys})
"""


class StageExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = self.root / "evidence"

    def external(self, body="print(json.dumps(out))\n", prefix=_ADAPTER):
        path = self.root / "adapter.py"
        path.write_text("#!" + str(Path(sys.executable).resolve()) + "\n" + prefix + body, encoding="utf-8")
        path.chmod(0o700)
        data = station()
        data["adapter"] = {"kind": "external-v1", "argv": [str(path)],
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        return data

    def assert_evidence(self, req):
        self.assertEqual({p.name for p in self.evidence.iterdir()},
                         {"request.json", "stdout.log", "stderr.log", "response.json"})
        self.assertEqual(json.loads((self.evidence / "request.json").read_text()), req)
        for name in ("stdout.log", "stderr.log"):
            self.assertLessEqual((self.evidence / name).stat().st_size, lab.MAX_OUTPUT_BYTES)

    def test_simulator_all_stages_and_outcomes(self):
        for stage in lab.STAGES:
            for status in lab.STATUSES:
                with self.subTest(stage=stage, status=status):
                    data = station()
                    data["adapter"]["outcomes"] = {stage: status}
                    req = request(data, stage)
                    evidence = self.root / (stage + "-" + status)
                    with mock.patch.object(lab.subprocess, "Popen", side_effect=AssertionError("不可啟動程序")):
                        result = lab.run_stage(data, req, evidence)
                    self.assertEqual(result["status"], status)
                    self.assertIs(result["hardware_validated"], False)
                    self.assertIs(result["synthetic"], True)
                    self.assertEqual(json.loads((evidence / "response.json").read_text()), result)

    def test_simulator_resume_and_timeout(self):
        data = station()
        req = add_resume(request(data))
        result = lab.run_stage(data, req, self.evidence)
        self.assertTrue(result["resume_state_verified"])
        self.assertEqual(result["resume_next_stage"], "boot")
        data["timeout_seconds"] = 0.03
        data["adapter"]["delay_seconds"] = 10
        started = time.monotonic()
        with self.assertRaisesRegex(ValueError, "逾時"):
            lab.run_stage(data, request(data), self.root / "timeout")
        self.assertLess(time.monotonic() - started, 1)

    def test_external_round_trip_and_literal_image_text(self):
        data = self.external("out['received_image'] = req['image']\nprint(json.dumps(out))\n"
                             "print('fixture-stderr', file=sys.stderr)\n")
        req = request(data)
        req["image"]["path"] = "$(touch NEVER_EXECUTE); fixture.img.xz"
        result = lab.run_stage(data, req, self.evidence)
        self.assertEqual(result["received_image"], req["image"])
        self.assertFalse(result["hardware_validated"])
        self.assert_evidence(req)
        self.assertEqual((self.evidence / "stderr.log").read_bytes(), b"fixture-stderr\n")

    def test_resume_external_unsupported_or_verified(self):
        data = self.external()
        req = add_resume(request(data))
        with self.assertRaisesRegex(ValueError, "續作"):
            lab.run_stage(data, req, self.evidence)
        data = self.external("out['status'] = 'blocked'\nprint(json.dumps(out))\n")
        self.assertEqual(lab.run_stage(data, req, self.root / "blocked")["status"], "blocked")
        data = self.external("out.update(resume_state_verified=True, resume_next_stage=req['resume']['next_stage'])\n"
                             "print(json.dumps(out))\n")
        self.assertTrue(lab.run_stage(data, req, self.root / "verified")["resume_state_verified"])

    def test_disabled_binding_and_board_rejected_before_spawn(self):
        data = self.external()
        for field in ("station_id", "hardware_id", "boot_config_sha256", "test_version", "mode"):
            req = request(data)
            req[field] = "hardware" if field == "mode" else "c" * 64
            with self.subTest(field=field), mock.patch.object(lab, "_external") as adapter:
                with self.assertRaises(ValueError):
                    lab.run_stage(data, req, self.evidence)
                adapter.assert_not_called()
        req = request(data)
        req["image"]["board"] = "bpi-m5"
        with self.assertRaises(ValueError):
            lab.run_stage(data, req, self.evidence)
        req = request(data)
        req["image"]["sha256"] = "c" * 64
        with self.assertRaises(ValueError):
            lab.run_stage(data, req, self.evidence)
        req = request(data)
        req["image"]["expected_sha256"] = "c" * 64
        with self.assertRaises(ValueError):
            lab.run_stage(data, req, self.evidence)
        data["enabled"] = False
        with self.assertRaisesRegex(ValueError, "停用"):
            lab.run_stage(data, request(data), self.evidence)
        self.assertFalse(self.evidence.exists())

    def test_wrong_job_stage_and_simulation_promotion(self):
        for field, value in (("work_key", "other"), ("attempt_id", "other"),
                             ("stage", "boot"), ("mode", "hardware"), ("hardware_validated", True)):
            data = self.external(f"out[{field!r}] = {value!r}\nprint(json.dumps(out))\n")
            target = self.root / field
            with self.subTest(field=field), self.assertRaises(ValueError):
                lab.run_stage(data, request(data), target)
            self.assertEqual(json.loads((target / "response.json").read_text())[field], value)

    def test_nonzero_exit_and_bad_json_preserve_raw(self):
        bodies = ("print(json.dumps(out)); sys.exit(7)\n", "print('invalid-json')\n",
                  "print(json.dumps(out)); print(json.dumps(out))\n",
                  "print('{\"status\":\"passed\",\"status\":\"blocked\"}')\n",
                  "sys.stdout.buffer.write(b'\\xff')\n", "print('NaN')\n",
                  "out['fixture_note'] = '\\ud800'; print(json.dumps(out))\n")
        for index, body in enumerate(bodies):
            data = self.external(body)
            target = self.root / str(index)
            with self.subTest(index=index), self.assertRaises(ValueError):
                lab.run_stage(data, request(data), target)
            self.assertTrue((target / "stdout.log").read_bytes())
            self.assertEqual(json.loads((target / "response.json").read_text())["schema"],
                             "bpi-lab-adapter-error-v1")

    def test_stdout_and_stderr_limits(self):
        for stream in ("stdout", "stderr"):
            data = self.external(f"sys.{stream}.buffer.write(b'x' * (1024 * 1024 + 1))\n"
                                 f"sys.{stream}.flush()\n")
            target = self.root / stream
            started = time.monotonic()
            with self.subTest(stream=stream), self.assertRaisesRegex(ValueError, "1 MiB"):
                lab.run_stage(data, request(data), target)
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual((target / (stream + ".log")).stat().st_size, lab.MAX_OUTPUT_BYTES)

    def test_timeout_terminates_process_group(self):
        marker = self.root / "child-finished"
        pidfile = self.root / "child-pid"
        body = ("import os, signal, time\n"
                "pid = os.fork()\n"
                "if pid == 0:\n"
                "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "    time.sleep(0.8)\n"
                f"    open({str(marker)!r}, 'w').close()\n"
                "    os._exit(0)\n"
                f"open({str(pidfile)!r}, 'w').write(str(pid))\n"
                "print('fixture-started', flush=True)\n"
                "time.sleep(20)\n")
        data = self.external(body, prefix="")
        data["timeout_seconds"] = 0.2
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(ValueError, "逾時"):
                lab.run_stage(data, request(data), self.evidence)
            self.assertLess(time.monotonic() - started, 2)
            self.assertTrue(pidfile.exists())
            self.assertIn(b"fixture-started", (self.evidence / "stdout.log").read_bytes())
            time.sleep(0.9)
            self.assertFalse(marker.exists())
        finally:
            if pidfile.exists():
                try:
                    os.kill(int(pidfile.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_closed_pipes_still_require_bounded_exit(self):
        data = self.external("import os, time\nos.close(1); os.close(2); time.sleep(10)\n", prefix="")
        data["timeout_seconds"] = 0.05
        with self.assertRaisesRegex(ValueError, "逾時"):
            lab.run_stage(data, request(data), self.evidence)

    def test_interrupt_preserves_partial_output_and_propagates(self):
        data = self.external("import sys, time\nprint('fixture-partial', flush=True)\n"
                             "print('fixture-error', file=sys.stderr, flush=True)\ntime.sleep(10)\n",
                             prefix="")
        selector = lab.selectors.DefaultSelector()
        real_select = selector.select

        def interrupt_after_output(timeout):
            paths = [self.evidence / name for name in ("stdout.log", "stderr.log")]
            if all(path.exists() and path.stat().st_size for path in paths):
                raise KeyboardInterrupt()
            return real_select(min(timeout, 0.1))

        with mock.patch.object(lab.selectors, "DefaultSelector", return_value=selector):
            with mock.patch.object(selector, "select", side_effect=interrupt_after_output):
                with self.assertRaises(KeyboardInterrupt):
                    lab.run_stage(data, request(data), self.evidence)
        self.assertEqual((self.evidence / "stdout.log").read_bytes(), b"fixture-partial\n")
        self.assertEqual((self.evidence / "stderr.log").read_bytes(), b"fixture-error\n")
        self.assertEqual(json.loads((self.evidence / "response.json").read_text())["schema"],
                         "bpi-lab-adapter-error-v1")

    def test_executable_hash_permissions_and_symlinks(self):
        for case in ("hash", "nonexecutable", "writable", "symlink", "parent-symlink", "fifo"):
            data = self.external()
            path = Path(data["adapter"]["argv"][0])
            if case == "hash":
                data["adapter"]["sha256"] = "f" * 64
            elif case == "nonexecutable":
                path.chmod(0o600)
            elif case == "writable":
                path.chmod(0o777)
            elif case == "symlink":
                link = self.root / "adapter-link"
                link.symlink_to(path)
                data["adapter"]["argv"][0] = str(link)
            elif case == "parent-symlink":
                link = self.root / "parent-link"
                link.symlink_to(self.root, target_is_directory=True)
                data["adapter"]["argv"][0] = str(link / path.name)
            else:
                path.unlink()
                os.mkfifo(path, 0o700)
            with self.subTest(case=case), mock.patch.object(lab.subprocess, "Popen") as spawn:
                with self.assertRaises(ValueError):
                    lab.run_stage(data, request(data), self.root / case)
                spawn.assert_not_called()

    def test_verified_executable_snapshot_cannot_be_replaced(self):
        data = self.external()
        original = lab.subprocess.Popen

        def replace_then_launch(*args, **kwargs):
            path = Path(data["adapter"]["argv"][0])
            path.write_text("#!/bin/sh\nexit 9\n", encoding="utf-8")
            return original(*args, **kwargs)

        with mock.patch.object(lab.subprocess, "Popen", side_effect=replace_then_launch):
            self.assertEqual(lab.run_stage(data, request(data), self.evidence)["status"], "passed")

    def test_verified_executable_snapshot_is_sealed(self):
        data = self.external("try:\n    open(sys.argv[0], 'wb')\n"
                             "except PermissionError:\n    out['snapshot_sealed'] = True\n"
                             "print(json.dumps(out))\n")
        self.assertTrue(lab.run_stage(data, request(data), self.evidence)["snapshot_sealed"])

    def test_argv_remains_literal_and_large_stdin_does_not_deadlock(self):
        data = self.external("out['fixture_args'] = sys.argv[1:]\nprint(json.dumps(out))\n")
        data["adapter"]["argv"].append("literal;$(touch NEVER_EXECUTE)")
        req = request(data)
        req["image"]["fixture_padding"] = "x" * 200000
        result = lab.run_stage(data, req, self.evidence)
        self.assertEqual(result["fixture_args"], ["literal;$(touch NEVER_EXECUTE)"])
        data = self.external("import time\ntime.sleep(10)\n", prefix="")
        data["timeout_seconds"] = 0.05
        with self.assertRaisesRegex(ValueError, "逾時"):
            lab.run_stage(data, req, self.root / "unread-stdin")

    def test_qualification_hash_checked_before_adapter_without_hardware(self):
        evidence = self.root / "qualification.json"
        data = hardware()
        evidence.write_text(json.dumps(qualification_document(data)), encoding="utf-8")
        data["qualification"]["evidence_path"] = str(evidence)
        req = request(data)
        with mock.patch.object(lab, "_external") as adapter:
            with self.assertRaisesRegex(ValueError, "資格證據 SHA-256"):
                lab.run_stage(data, req, self.evidence)
            adapter.assert_not_called()
        data["qualification"]["evidence_sha256"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
        fake = json.dumps(report(req)).encode()
        with mock.patch.object(lab, "_external", return_value=(fake, b"", None)) as adapter:
            self.assertEqual(lab.run_stage(data, req, self.root / "matched")["status"], "passed")
            adapter.assert_called_once()
        evidence.write_text('{"fixture": false}\n', encoding="utf-8")
        with mock.patch.object(lab, "_external") as adapter:
            with self.assertRaisesRegex(ValueError, "資格證據 SHA-256"):
                lab.run_stage(data, req, self.root / "changed")
            adapter.assert_not_called()

    def test_qualification_cannot_be_reused_or_incomplete(self):
        data = hardware()
        original = qualification_document(data)
        invalid = []
        for field in original:
            document = copy.deepcopy(original)
            del document[field]
            invalid.append(document)
        changes = {"schema": "bpi-lab-stage-v1", "station_id": "other-station",
                   "hardware_id": "other-board", "boot_config_sha256": "9" * 64,
                   "test_version": "other-version", "media": "cid:other-media",
                   "adapter_sha256": "9" * 64,
                   "resources": {**original["resources"], "power": "power:other-pair"}}
        for field, value in changes.items():
            invalid.append({**original, field: value})
        for field in ("hardware_validated", "rescue_verified", "single_image_cycle_verified"):
            for value in (False, 1, "true", None):
                invalid.append({**original, field: value})
        for references in ([], {}, [{}], [{"path": "/fixture/raw"}],
                           [{"path": "relative", "sha256": "f" * 64}],
                           [{"path": "/fixture/raw", "sha256": "invalid"}],
                           [{"path": "/fixture/raw", "sha256": "0" * 64}],
                           original["source_evidence"] * 2):
            invalid.append({**original, "source_evidence": references})
        evidence = self.root / "qualification.json"
        data["qualification"]["evidence_path"] = str(evidence)
        for index, document in enumerate(invalid):
            evidence.write_text(json.dumps(document), encoding="utf-8")
            data["qualification"]["evidence_sha256"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
            with self.subTest(index=index), mock.patch.object(lab, "_external") as adapter:
                with self.assertRaises(ValueError):
                    lab.run_stage(data, request(data), self.root / str(index))
                adapter.assert_not_called()

    def test_qualification_requires_bounded_unique_json_even_with_matching_hash(self):
        data = hardware()
        valid = json.dumps(qualification_document(data)).encode()
        payloads = (b"fixture-not-json", b"[]", b"x" * (lab.MAX_OUTPUT_BYTES + 1),
                    valid[:-1] + b', "station_id": "other-station"}')
        evidence = self.root / "qualification.json"
        data["qualification"]["evidence_path"] = str(evidence)
        for index, payload in enumerate(payloads):
            evidence.write_bytes(payload)
            data["qualification"]["evidence_sha256"] = hashlib.sha256(payload).hexdigest()
            with self.subTest(index=index), mock.patch.object(lab, "_external") as adapter:
                with self.assertRaises(ValueError):
                    lab.run_stage(data, request(data), self.root / str(index))
                adapter.assert_not_called()

    def test_qualification_symlinks_and_device_are_never_opened(self):
        original = self.root / "qualification.json"
        original.write_bytes(b"fixture")
        link = self.root / "qualification-link"
        link.symlink_to(original)
        for index, path in enumerate((link, Path("/dev/null"))):
            data = hardware()
            data["qualification"]["evidence_path"] = str(path)
            real_open = lab.os.open

            def checked_open(name, *args, **kwargs):
                self.assertNotEqual(name, "null")
                self.assertNotEqual(str(name), "/dev/null")
                return real_open(name, *args, **kwargs)

            with mock.patch.object(lab.os, "open", side_effect=checked_open):
                with mock.patch.object(lab, "_external") as adapter, self.assertRaises(ValueError):
                    lab.run_stage(data, request(data), self.root / str(index))
                adapter.assert_not_called()

    def test_evidence_reuse_and_symlinks_never_overwrite(self):
        data = station()
        req = request(data)
        lab.run_stage(data, req, self.evidence)
        before = {path.name: path.read_bytes() for path in self.evidence.iterdir()}
        with self.assertRaisesRegex(ValueError, "不得覆寫"):
            lab.run_stage(data, req, self.evidence)
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.evidence.iterdir()})
        self.assert_evidence(req)
        victim = self.root / "untouched"
        victim.write_bytes(b"fixture")
        for index, name in enumerate(("request.json", "stdout.log", "stderr.log", "response.json")):
            target = self.root / ("link-" + str(index))
            target.mkdir()
            (target / name).symlink_to(victim)
            with self.assertRaises(ValueError):
                lab.run_stage(data, req, target)
            self.assertEqual(victim.read_bytes(), b"fixture")
        directory_link = self.root / "directory-link"
        directory_link.symlink_to(self.evidence, target_is_directory=True)
        with self.assertRaises(ValueError):
            lab.run_stage(data, req, directory_link)

    def test_concurrent_evidence_reservation_has_one_winner(self):
        data = station()
        req = request(data)

        def execute(_):
            try:
                return lab.run_stage(data, req, self.evidence)["status"]
            except ValueError:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(execute, range(2))), ["passed", "rejected"])
        self.assert_evidence(req)


if __name__ == "__main__":
    unittest.main()
