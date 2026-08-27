#!/usr/bin/env python3
"""Banana Pi 全板卡盤點與證據登錄回歸測試。"""

import copy
import importlib.util
from pathlib import Path
import subprocess
import sys
import unittest


REPO_DIR = Path(__file__).resolve().parents[1]
SCRIPT = REPO_DIR / "tools" / "bananapi-board-audit.py"
SPEC = importlib.util.spec_from_file_location("bananapi_board_audit", SCRIPT)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


class BananaPiBoardAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.boards, cls.errors = AUDIT.collect_boards()
        cls.by_id = {board.board_id: board for board in cls.boards}

    def test_inventory_has_all_48_board_definitions(self) -> None:
        self.assertEqual(self.errors, [])
        self.assertEqual(len(self.boards), 48)
        self.assertEqual(
            {status: sum(board.status == status for board in self.boards) for status in AUDIT.BOARD_SUFFIXES},
            {"conf": 12, "csc": 14, "wip": 21, "eos": 1},
        )

    def test_status_registry_covers_each_board_once(self) -> None:
        status = AUDIT.load_status()
        self.assertEqual(status["schema_version"], 2)
        registered = AUDIT.batch_index(status)
        self.assertEqual(set(registered), set(self.by_id))
        for section in ("evidence", "next_gates", "open_findings"):
            with self.subTest(section=section):
                self.assertEqual(set(status[section]), set(self.by_id))

    def test_registry_rejects_missing_or_extra_per_board_records(self) -> None:
        original = AUDIT.load_status()
        for section in ("evidence", "next_gates", "open_findings"):
            missing = copy.deepcopy(original)
            missing[section].pop("bananapi")
            self.assertTrue(
                any(section in error and "缺少" in error for error in AUDIT.registry_errors(missing))
            )
            extra = copy.deepcopy(original)
            extra[section]["bananapi-not-real"] = {} if section == "evidence" else []
            self.assertTrue(
                any(section in error and "未登錄" in error for error in AUDIT.registry_errors(extra))
            )

    def test_inherited_w3_fields_are_resolved_without_sourcing_shell(self) -> None:
        board = self.by_id["bananapiw3"]
        self.assertEqual(board.family, "rockchip-rk3588")
        self.assertEqual(board.architecture, "arm64")
        self.assertEqual(board.targets, ("vendor",))

    def test_spacemit_k3_is_riscv64(self) -> None:
        self.assertEqual(self.by_id["bananapism10"].architecture, "riscv64")
        self.assertEqual(self.by_id["bananapism10"].level, "L1")

    def test_recent_candidates_are_backed_by_the_status_registry(self) -> None:
        expected = {
            "bananapiaim7": "L2",
            "bananapicm2": "L0",
            "bananapicm5pro": "L2",
            "bananapif2p": "L2",
            "bananapif2s": "L2",
            "bananapim1super": "L2",
            "bananapim2c": "L0",
            "bananapim4": "L1",
            "bananapim4super": "L0",
            "bananapim6": "L1",
            "bananapir1": "L2",
            "bananapir2": "L2",
            "bananapir3mini": "L2",
            "bananapir4pro": "L2",
            "bananapiw3": "L2",
            "bananapiw2": "L1",
        }
        for board_id, level in expected.items():
            with self.subTest(board_id=board_id):
                self.assertEqual(self.by_id[board_id].level, level)

    def test_forge1_uses_rockchip_armhf_family(self) -> None:
        self.assertEqual(self.by_id["bananapiforge1"].architecture, "armhf")

    def test_rk3568_uart_console_names_are_valid(self) -> None:
        for board_id in ("bananapir2pro", "bananapicm2"):
            cmdline = self.by_id[board_id].fields["SRC_CMDLINE"]
            self.assertIn("console=ttyS2,1500000", cmdline)
            self.assertNotIn("ttyS02", cmdline)

    def test_reference_evidence_does_not_claim_full_release(self) -> None:
        for board_id in (
            "bananapi",
            "bananapipro",
            "bananapim2plus",
            "bananapim7",
            "bananapim2zero",
            "bananapip2zero",
            "bananapip2pro",
            "bananapim64",
            "bananapim5",
            "bananapim2pro",
            "bananapicm4io",
            "bananapim2s",
        ):
            self.assertEqual(self.by_id[board_id].level, "L2")
        self.assertEqual(self.by_id["bananapim4zero"].level, "L2")
        self.assertEqual(self.by_id["bananapim4berry"].level, "L2")
        self.assertFalse(any(board.level in {"L3", "L4", "L5"} for board in self.boards))

    def test_ai2n_supported_fields_are_complete(self) -> None:
        self.assertEqual(AUDIT.field_gaps(self.by_id["bpi-ai2n"]), [])
        self.assertEqual(self.by_id["bpi-ai2n"].level, "L2")

    def test_generated_outputs_cover_every_board(self) -> None:
        report = AUDIT.markdown_text(self.boards, "2026-08-26")
        tsv = AUDIT.tsv_text(self.boards)
        for board_id in self.by_id:
            self.assertIn(f"`{board_id}`", report)
            self.assertIn(f"\n{board_id}\t", "\n" + tsv)

    def test_each_board_uses_its_registered_next_gate(self) -> None:
        status = AUDIT.load_status()
        for board in self.boards:
            with self.subTest(board_id=board.board_id):
                self.assertEqual(board.next_gate, status["next_gates"][board.board_id])

    def test_check_mode_is_read_only_and_compares_both_reports(self) -> None:
        paths = (AUDIT.MARKDOWN_REPORT, AUDIT.TSV_REPORT)
        before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--check"],
            cwd=REPO_DIR,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("狀態與兩份報告一致", result.stderr)
        self.assertEqual(before, {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths})

    def test_check_mode_rejects_write_options(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--check", "--markdown", "/tmp/不應寫入.md"],
            cwd=REPO_DIR,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不得與", result.stderr)


if __name__ == "__main__":
    unittest.main()
