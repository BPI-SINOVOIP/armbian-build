"""CPU 短測的替身回歸；禁止在測試主機啟動實際 CPU 負載。"""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


TOOL = Path(__file__).resolve().parents[1] / "tools/cm6_rc3_short_stress.py"
SPEC = importlib.util.spec_from_file_location("cm6_short_stress", TOOL)
stress = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stress)


def healthy():
    return {
        "temperatures": {"thermal_zone0": {"type": "cpu", "celsius": 50.0}},
        "cooling": {"cooling_device0": {"type": "cpufreq", "cur_state": 0, "max_state": 5}},
        "frequencies": {"policy0": {"scaling_cur_freq": 400000, "scaling_max_freq": 1600000,
                                    "cpuinfo_max_freq": 1600000}},
        "errors": [],
    }


class Worker:
    def __init__(self, *, target, args, daemon):
        self.target, self.args, self.daemon = target, args, daemon
        self.pid = None
        self.exitcode = None
        self.stopped = False

    def start(self):
        self.pid = 12345

    def is_alive(self):
        return self.pid is not None and self.exitcode is None

    def terminate(self):
        self.stopped = True
        self.exitcode = -15

    def kill(self):
        self.stopped = True
        self.exitcode = -9

    def join(self, timeout):
        pass


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, duration):
        self.now += duration


class ShortStressTests(unittest.TestCase):
    def mocked_run(self, output, snapshots, worker_class=Worker):
        clock = Clock()
        workers = []

        def new_worker(**kwargs):
            worker = worker_class(**kwargs)
            workers.append(worker)
            return worker

        with mock.patch.object(stress, "read_snapshot", side_effect=snapshots), \
                mock.patch.object(stress.multiprocessing, "get_context") as context, \
                mock.patch.object(stress.time, "monotonic", clock.monotonic), \
                mock.patch.object(stress.time, "sleep", clock.sleep):
            context.return_value.Process.side_effect = new_worker
            result = stress.run_test(output, seconds=1, workers=2)
        return result, workers

    def test_hot_missing_sensor_and_initial_throttling_never_start_workers(self):
        cases = []
        hot = healthy()
        hot["temperatures"]["thermal_zone0"]["celsius"] = 75.0
        cases.append(hot)
        missing = healthy()
        missing["temperatures"] = {}
        cases.append(missing)
        cooling = healthy()
        cooling["cooling"]["cooling_device0"]["cur_state"] = 1
        cases.append(cooling)
        frequency = healthy()
        frequency["frequencies"]["policy0"]["scaling_max_freq"] = 1228800
        cases.append(frequency)
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as tmp:
                result, workers = self.mocked_run(Path(tmp) / "證據", [case])
                self.assertEqual(result["status"], "BLOCKED")
                self.assertFalse(workers)

    def test_healthy_short_run_low_current_frequency_is_not_throttling(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "證據"
            result, workers = self.mocked_run(output, [healthy()] * 4)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(len(workers), 2)
            self.assertTrue(all(worker.stopped for worker in workers))
            self.assertEqual(result["elapsed_seconds"], 1)
            self.assertEqual([item["exit_code"] for item in result["worker_exit_codes"]], [-15, -15])
            saved = json.loads((output / "summary.json").read_text())
            self.assertEqual(saved, result)
            self.assertEqual(len((output / "temperature.csv").read_text().splitlines()), 5)
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            for path in output.iterdir():
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_temperature_frequency_and_cooling_stop_own_workers(self):
        conditions = []
        hot = healthy()
        hot["temperatures"]["thermal_zone0"]["celsius"] = 90
        conditions.append(hot)
        low = healthy()
        low["frequencies"]["policy0"]["scaling_max_freq"] = 1200000
        conditions.append(low)
        changed_baseline = healthy()
        changed_baseline["frequencies"]["policy0"].update(scaling_max_freq=1200000, cpuinfo_max_freq=1200000)
        conditions.append(changed_baseline)
        cooling = healthy()
        cooling["cooling"]["cooling_device0"]["cur_state"] = 2
        conditions.append(cooling)
        for condition in conditions:
            with self.subTest(condition=condition), tempfile.TemporaryDirectory() as tmp:
                result, workers = self.mocked_run(Path(tmp) / "證據", [healthy(), condition])
                self.assertEqual(result["status"], "FAIL")
                self.assertTrue(all(worker.stopped for worker in workers))
                self.assertEqual(len(result["samples"]), 2)

    def test_exception_and_crashed_worker_leave_no_running_worker(self):
        class CrashedWorker(Worker):
            def start(self):
                super().start()
                self.exitcode = 3

        for snapshots, kind in (([healthy(), OSError("測試讀取異常")], Worker),
                                ([healthy(), KeyboardInterrupt()], Worker),
                                ([healthy(), healthy()], CrashedWorker)):
            with tempfile.TemporaryDirectory() as tmp:
                result, workers = self.mocked_run(Path(tmp) / "證據", snapshots, kind)
                self.assertEqual(result["status"], "FAIL")
                self.assertTrue(all(not worker.is_alive() for worker in workers))

    def test_existing_paths_device_alias_and_invalid_limits_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "既有"
            target.mkdir()
            link = root / "既有連結"
            link.symlink_to(target)
            alias = root / "裝置連結"
            alias.symlink_to("/dev")
            with mock.patch.object(stress, "read_snapshot") as read:
                for output in (target, link, alias / "不可建立", Path("/proc/不可建立")):
                    with self.assertRaises(ValueError):
                        stress.run_test(output, 1, 1)
                for seconds, workers in ((0, 1), (61, 1), (1, 0), (1, 3)):
                    with self.assertRaises(ValueError):
                        stress.run_test(root / "未建立", seconds, workers)
                read.assert_not_called()

    def test_sysfs_fixture_and_missing_data_are_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {
                "class/thermal/thermal_zone0/type": "cpu", "class/thermal/thermal_zone0/temp": "50000",
                "class/thermal/cooling_device0/type": "cpufreq", "class/thermal/cooling_device0/cur_state": "0",
                "class/thermal/cooling_device0/max_state": "5",
                "devices/system/cpu/cpufreq/policy0/scaling_cur_freq": "400000",
                "devices/system/cpu/cpufreq/policy0/scaling_max_freq": "1600000",
                "devices/system/cpu/cpufreq/policy0/cpuinfo_max_freq": "1600000",
            }
            for name, value in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value)
            self.assertEqual(stress.read_snapshot(root), healthy())
            (root / "class/thermal/thermal_zone0/temp").write_text("不可解析")
            malformed = stress.read_snapshot(root)
            self.assertTrue(malformed["errors"])
            self.assertTrue(stress.snapshot_issues(malformed))


if __name__ == "__main__":
    unittest.main()
