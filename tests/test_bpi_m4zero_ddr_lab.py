#!/usr/bin/env python3
"""BPI-M4 Zero M4ZLAB2 主機工具單元測試。"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "bpi-m4zero-ddr-lab.py"
PATCH_PATH = (
    Path(__file__).resolve().parents[1]
    / "patch/u-boot/v2026.01/board_bananapim4zero/015-sunxi-h616-add-standalone-ddr-lab.patch"
)
SPEC = importlib.util.spec_from_file_location("bpi_m4zero_ddr_lab", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
lab = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lab)


def base_profile(**updates: object) -> dict[str, object]:
    profile: dict[str, object] = {
        "id": 1,
        "clk": 480,
        "dx_odt": 0x07070707,
        "dx_dri": 0x0E0E0E0E,
        "ca_dri": 0x00000E0E,
        "odt_en": 0xAAAAEEEE,
        "tpr0": 0,
        "tpr2": 0,
        "tpr6": 0x44000000,
        "tpr10": 0x402F6663,
        "tpr11": 0x24242624,
        "tpr12": 0x0F0F100F,
        "level": "M2",
        "passes": 3,
        "window": 8,
    }
    profile.update(updates)
    return profile


class ScriptedChannel:
    def __init__(self, lines: list[str | None]):
        self.lines = list(lines)
        self.sent: list[str] = []

    def send_line(self, line: str) -> None:
        self.sent.append(line)

    def read_line(self, timeout: float) -> str | None:
        if not self.lines:
            return None
        return self.lines.pop(0)


def bench(op: str, rate_mib: int, byte_count: int = 1024 * 1024) -> dict[str, object]:
    bytes_per_second = rate_mib * 1024 * 1024
    return {
        "op": op,
        "bytes": byte_count,
        "ticks": byte_count * lab.TIMER_HZ // bytes_per_second,
    }


def ranking_record(
    profile: dict[str, object],
    status: str,
    domain: list[int] | None = None,
    rates: tuple[int, int, int] = (100, 90, 80),
    all_passed: bool = True,
) -> dict[str, object]:
    record: dict[str, object] = {
        "key": lab.profile_key(profile),
        "profile": lab.normalize_profile(profile),
        "status": status,
        "all_passed": all_passed,
        "bench": [
            bench("read", rates[0]),
            bench("write", rates[1]),
            bench("copy", rates[2]),
        ],
    }
    if domain is not None:
        record["scan"] = {
            "fields": {"tpr6": domain},
            "coordinates": {"tpr6": profile["tpr6"]},
        }
    return record


class ProfileTests(unittest.TestCase):
    def test_cli_help_is_traditional_chinese(self) -> None:
        help_text = lab.build_parser().format_help()
        self.assertIn("用法：", help_text)
        self.assertIn("選項：", help_text)
        self.assertIn("執行多欄位笛卡兒掃描", help_text)

    def test_run_command_has_every_field_and_is_short(self) -> None:
        command = lab.build_run_command(base_profile())
        self.assertLess(len(command.encode("ascii")), 256)
        parts = command.rstrip("\n").split()
        self.assertEqual(parts[0], "R")
        self.assertEqual(len(parts[1:]), len(lab.PROFILE_FIELDS))
        self.assertEqual(parts[1], "id=1")
        self.assertEqual(parts[2], "clk=480")
        self.assertEqual(parts[3], "dx_odt=0x07070707")
        self.assertEqual(parts[-3:], ["level=2", "passes=3", "window=8"])

    def test_missing_field_is_rejected(self) -> None:
        profile = base_profile()
        del profile["tpr12"]
        with self.assertRaisesRegex(lab.LabError, "缺少完整欄位"):
            lab.build_run_command(profile)

    def test_json_profile_and_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.json"
            path.write_text(json.dumps(base_profile()), encoding="utf-8")
            loaded = lab.load_profile(path)
            updated = lab.apply_overrides(loaded, ["clk=0x318", "level=m1"])
        self.assertEqual(updated["clk"], 792)
        self.assertEqual(updated["level"], "M1")

    def test_cartesian_matrix_and_inclusive_range(self) -> None:
        fields = lab.parse_scan_fields(["clk=480,528", "tpr6=0x10:0x12"])
        matrix = lab.expand_matrix(base_profile(), fields)
        self.assertEqual(len(matrix), 6)
        coordinates = [item[1] for item in matrix]
        self.assertIn({"clk": 528, "tpr6": 0x12}, coordinates)
        self.assertEqual(matrix[0][0]["id"], 1)

    def test_non_pll_step_clock_is_rejected(self) -> None:
        with self.assertRaisesRegex(lab.LabError, "12 MHz"):
            lab.normalize_profile(base_profile(clk=799))

    def test_repeat_profiles_uses_unique_transaction_ids(self) -> None:
        profiles = lab.repeat_profiles([(base_profile(id=100), {})], 3)
        self.assertEqual([item[0]["id"] for item in profiles], [100, 101, 102])
        self.assertEqual(len({lab.profile_key(item[0]) for item in profiles}), 1)

    def test_command_matches_firmware_field_contract(self) -> None:
        patch_text = PATCH_PATH.read_text(encoding="utf-8")
        command = lab.build_run_command(base_profile()).strip().split()[1:]
        self.assertEqual(
            [token.split("=", 1)[0] for token in command], list(lab.PROFILE_FIELDS)
        )
        for field in lab.PROFILE_FIELDS:
            self.assertIn(f'!strcmp(key, "{field}")', patch_text)
        for event_type in lab.EVENT_TYPES - {"BOOT_ERROR"}:
            self.assertIn(f'"_{event_type}', patch_text)


class ProtocolTests(unittest.TestCase):
    def test_parse_all_uart_event_types(self) -> None:
        for event_type in sorted(lab.EVENT_TYPES):
            with self.subTest(event_type=event_type):
                event = lab.parse_event(f"雜訊 M4ZLAB2_{event_type} id=1 value=0x10")
                self.assertIsNotNone(event)
                assert event is not None
                self.assertEqual(event["type"], event_type)
                self.assertEqual(event["fields"]["id"], "1")
        self.assertIsNone(lab.parse_event("一般 U-Boot 輸出"))

    def test_pass_and_benchmark_conversion(self) -> None:
        channel = ScriptedChannel(
            [
                "M4ZLAB2_START id=1",
                "M4ZLAB2_BENCH id=1 bytes=1048576 timer_hz=24000000 write_ticks=240000 read_ticks=120000 copy_ticks=480000",
                "M4ZLAB2_FINAL id=1 result=pass recovered=pass",
                "M4ZLAB2_READY size_mib=2048 ranks=1 safe_clk=480",
            ]
        )
        result = lab.execute_profile(channel, base_profile(), 1.0)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["all_passed"])
        self.assertEqual(len(result["bench"]), 3)
        self.assertAlmostEqual(
            result["bench"][0]["bytes_per_second"], 100 * 1024 * 1024
        )

    def test_watchdog_reset_after_start(self) -> None:
        channel = ScriptedChannel(
            [
                "M4ZLAB2_START id=1",
                "M4ZLAB2_READY size_mib=2048 ranks=1 safe_clk=480",
            ]
        )
        result = lab.execute_profile(channel, base_profile(), 1.0)
        self.assertEqual(result["status"], "watchdog_reset")
        self.assertFalse(result["all_passed"])

    def test_timeout_without_final(self) -> None:
        channel = ScriptedChannel(["M4ZLAB2_START id=1", None])
        result = lab.execute_profile(channel, base_profile(), 0.01)
        self.assertEqual(result["status"], "timeout")
        self.assertTrue(result["start_seen"])

    def test_external_reset_uses_argument_list_without_shell(self) -> None:
        completed = mock.Mock(returncode=0, stdout="")
        with mock.patch.object(lab.subprocess, "run", return_value=completed) as run:
            lab.run_external_reset("reset-board --port 'USB 1'", 2.0)
        run.assert_called_once()
        arguments, keywords = run.call_args
        self.assertEqual(arguments[0], ["reset-board", "--port", "USB 1"])
        self.assertNotIn("shell", keywords)


class PersistenceAndRankingTests(unittest.TestCase):
    def test_jsonl_incremental_resume(self) -> None:
        profile = base_profile()
        record = lab.make_record(
            profile, {"status": "pass", "all_passed": True, "bench": []}
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "results.jsonl"
            with lab.JsonlWriter(path) as writer:
                writer.append(record)
            self.assertIn(lab.profile_key(profile), lab.completed_profile_keys(path))
            self.assertEqual(len(lab.load_jsonl([path])), 1)

    def test_ranking_rejects_mixed_repeat_results(self) -> None:
        profile = base_profile(id=200)
        records = [
            ranking_record(dict(profile, id=200 + index), status)
            for index, status in enumerate(("pass", "pass", "fail"))
        ]
        ranking = lab.build_ranking(records, min_samples=3)
        self.assertEqual(ranking["m2_all_passed_candidates"], 0)
        self.assertIsNone(ranking["safe_candidate"])

    def test_rank_safe_performance_margin_and_m2_filter(self) -> None:
        domain = [1, 2, 3, 4, 5]
        records: list[dict[str, object]] = []
        for value in domain:
            status = "pass" if value in {2, 3, 4} else "fail"
            profile = base_profile(id=value, clk=792, tpr6=value)
            rates = (180, 170, 160) if value == 3 else (150, 140, 130)
            records.append(ranking_record(profile, status, domain, rates))

        safe_profile = base_profile(id=10, clk=480, tpr6=9)
        records.append(ranking_record(safe_profile, "pass", [9], (80, 70, 60)))
        records.append(
            ranking_record(
                base_profile(id=11, level="M1"), "pass", None, (999, 999, 999)
            )
        )

        ranking = lab.build_ranking(records)
        self.assertEqual(ranking["m2_all_passed_candidates"], 4)
        self.assertEqual(ranking["safe_candidate"]["profile"]["id"], 10)
        self.assertEqual(ranking["best_performance_candidate"]["profile"]["id"], 3)
        self.assertEqual(ranking["maximum_margin_candidate"]["profile"]["id"], 3)
        margin = ranking["maximum_margin_candidate"]["margin"]["fields"]["tpr6"]
        self.assertEqual(margin["radius_steps"], 1)
        self.assertFalse(margin["boundary_truncated"])

    def test_boundary_margin_is_marked_truncated(self) -> None:
        domain = [10, 20, 30]
        records = [
            ranking_record(base_profile(id=value, tpr6=value), "pass", domain)
            for value in domain
        ]
        ranking = lab.build_ranking(records)
        candidate = next(
            item
            for item in (
                ranking["safe_candidate"],
                ranking["best_performance_candidate"],
                ranking["maximum_margin_candidate"],
            )
            if item["profile"]["tpr6"] == 20
        )
        field_margin = candidate["margin"]["fields"]["tpr6"]
        self.assertEqual(field_margin["radius_steps"], 1)
        self.assertTrue(field_margin["boundary_truncated"])

    def test_multidimensional_margin_holds_other_fields_fixed(self) -> None:
        domains = {"tpr6": [1, 2, 3], "tpr11": [10, 20, 30]}
        records: list[dict[str, object]] = []
        for tpr6 in domains["tpr6"]:
            for tpr11 in domains["tpr11"]:
                profile = base_profile(id=tpr6 * 100 + tpr11, tpr6=tpr6, tpr11=tpr11)
                record = ranking_record(profile, "pass", None)
                record["scan"] = {
                    "fields": domains,
                    "coordinates": {"tpr6": tpr6, "tpr11": tpr11},
                }
                records.append(record)
        ranking = lab.build_ranking(records)
        candidate = ranking["maximum_margin_candidate"]
        self.assertEqual(candidate["profile"]["tpr6"], 2)
        self.assertEqual(candidate["profile"]["tpr11"], 20)
        self.assertEqual(candidate["margin"]["minimum_radius_steps"], 1)
        self.assertEqual(set(candidate["margin"]["fields"]), {"tpr6", "tpr11"})

    def test_generic_timer_rate_uses_24_mhz(self) -> None:
        profile = base_profile(id=99)
        record = ranking_record(profile, "pass", None)
        record["bench"] = [
            {"op": op, "bytes": 1024 * 1024, "ticks": 24_000_000}
            for op in ("read", "write", "copy")
        ]
        ranking = lab.build_ranking([record])
        performance = ranking["best_performance_candidate"]["performance"]
        self.assertEqual(performance["timer_hz"], [24_000_000])
        self.assertAlmostEqual(performance["worst_mib_per_second"], 1.0)


if __name__ == "__main__":
    unittest.main()
