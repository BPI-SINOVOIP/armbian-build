#!/usr/bin/env python3
"""Banana Pi 全板卡盤點與證據登錄回歸測試。"""

import importlib.util
from pathlib import Path
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
        registered = AUDIT.batch_index(status)
        self.assertEqual(set(registered), set(self.by_id))

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
            "bananapiaim7": "L1",
            "bananapicm2": "L0",
            "bananapicm5pro": "L2",
            "bananapif2p": "L1",
            "bananapif2s": "L1",
            "bananapim1super": "L1",
            "bananapim2c": "L0",
            "bananapim4": "L1",
            "bananapim4super": "L0",
            "bananapim6": "L1",
            "bananapir1": "L2",
            "bananapir2": "L2",
            "bananapir3mini": "L1",
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
        self.assertEqual(self.by_id["bananapim4zero"].level, "L3")
        self.assertEqual(self.by_id["bananapim4berry"].level, "L4")
        self.assertFalse(any(board.level == "L5" for board in self.boards))

    def test_ai2n_supported_fields_are_complete(self) -> None:
        self.assertEqual(AUDIT.field_gaps(self.by_id["bpi-ai2n"]), [])
        self.assertEqual(self.by_id["bpi-ai2n"].level, "L0")

    def test_generated_outputs_cover_every_board(self) -> None:
        report = AUDIT.markdown_text(self.boards, "2026-08-26")
        tsv = AUDIT.tsv_text(self.boards)
        for board_id in self.by_id:
            self.assertIn(f"`{board_id}`", report)
            self.assertIn(f"\n{board_id}\t", "\n" + tsv)


if __name__ == "__main__":
    unittest.main()
