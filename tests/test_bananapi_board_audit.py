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
            {"conf": 12, "csc": 13, "wip": 22, "eos": 1},
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

    def test_reference_evidence_does_not_claim_full_release(self) -> None:
        self.assertEqual(self.by_id["bananapim4zero"].level, "L3")
        self.assertEqual(self.by_id["bananapim4berry"].level, "L4")
        self.assertFalse(any(board.level == "L5" for board in self.boards))

    def test_ai2n_supported_fields_are_complete(self) -> None:
        self.assertEqual(AUDIT.field_gaps(self.by_id["bpi-ai2n"]), [])

    def test_generated_outputs_cover_every_board(self) -> None:
        report = AUDIT.markdown_text(self.boards, "2026-08-26")
        tsv = AUDIT.tsv_text(self.boards)
        for board_id in self.by_id:
            self.assertIn(f"`{board_id}`", report)
            self.assertIn(f"\n{board_id}\t", "\n" + tsv)


if __name__ == "__main__":
    unittest.main()
