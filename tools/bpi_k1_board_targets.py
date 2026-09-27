#!/usr/bin/env python3
"""共用 K1 官方格式板名與實體板、儲存媒體的精確對照。"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "config/spacemit-k1-profiles/board-targets.json"
BASE_BOARDS = {"bananapicm6": "bpi-cm6", "bananapif3": "bpi-f3"}


def load_registry() -> dict:
    registry = json.loads((ROOT / REGISTRY).read_text())
    if registry.get("schema_version") != 1:
        raise ValueError("官方格式板名設定版本不符")
    targets = registry["targets"]
    for name, target in targets.items():
        if (target["base_board"] not in BASE_BOARDS
                or BASE_BOARDS[target["base_board"]] != target["board"]
                or target["storage"] not in ("sd", "emmc")
                or name != target["base_board"] + (
                    "-vendor-sd" if target["storage"] == "sd" else "-titan-emmc")):
            raise ValueError("官方格式板名與實體板、媒體對照不符")
    return registry


def target_for(board: str) -> dict | None:
    return load_registry()["targets"].get(board)


def resolve_board(board: str) -> str:
    target = target_for(board)
    return target["base_board"] if target else board


def expected_board(board: str, physical: str, storage: str | None = None) -> bool:
    target = target_for(board)
    if target:
        return target["board"] == physical and (storage is None or target["storage"] == storage)
    return BASE_BOARDS.get(board) == physical
