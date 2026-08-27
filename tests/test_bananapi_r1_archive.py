#!/usr/bin/env python3
"""Banana Pi R1 歷史映像證據回歸測試。"""

from __future__ import annotations

import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "config/validation/bananapi-sunxi-a20-r1-archive.json"
VERIFIER = ROOT / "tools/verify-bananapi-r1-archive.py"
EVIDENCE = (
    ROOT
    / "docs/evidence/bananapi-family-optimization"
    / "E-sunxi-a20-r1-archive-L2-20260827.md"
)

SPEC = importlib.util.spec_from_file_location("bananapi_r1_archive", VERIFIER)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class BananaPiR1ArchiveTests(unittest.TestCase):
    """防止歷史證據被誤升級為現行支援或發布聲明。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    def test_contract_is_archival_l2_and_keeps_all_claims_closed(self) -> None:
        self.assertEqual(self.contract["schema_version"], 1)
        self.assertEqual(self.contract["evidence_class"], "L2（歷史／封存）")
        self.assertEqual(set(self.contract["claims"].values()), {False})

    def test_matrix_has_five_releases_and_two_profiles(self) -> None:
        matrix = {
            (record["release"], record["profile"])
            for record in self.contract["archives"]
        }
        self.assertEqual(
            matrix,
            {
                (release, profile)
                for release in ("bookworm", "jammy", "noble", "resolute", "trixie")
                for profile in ("cli", "xfce")
            },
        )
        self.assertEqual(len(self.contract["archives"]), 10)

    def test_every_evidence_file_is_size_and_hash_locked(self) -> None:
        digest_pattern = re.compile(r"^[0-9a-f]{64}$")
        names: set[str] = set()
        logs: set[str] = set()
        for record in self.contract["archives"]:
            for prefix in ("image", "sidecar", "metadata"):
                key = prefix if prefix != "metadata" else "metadata_file"
                name = record[key]
                self.assertNotIn(name, names)
                names.add(name)
                self.assertGreater(record[f"{prefix}_size"], 0)
                self.assertRegex(record[f"{prefix}_sha256"], digest_pattern)
            self.assertNotIn(record["log"], logs)
            logs.add(record["log"])
            self.assertGreater(record["log_size"], 0)
            self.assertRegex(record["log_sha256"], digest_pattern)
            self.assertGreater(record["uncompressed_size"], record["image_size"])
        self.assertEqual(len(names), 30)
        self.assertEqual(len(logs), 10)

    def test_current_board_stays_eos_and_matches_legacy_hardware_fields(self) -> None:
        board = self.contract["board"]
        self.assertEqual(board["current_file"], "config/boards/bananapir1.eos")
        self.assertEqual(board["legacy_file"], "config/boards/lamobo-r1.eos")
        self.assertEqual(
            board["legacy_commit"],
            "556a14dde79770826d825ef845c430c754d55f9f",
        )
        values = MODULE.verify_board_equivalence(self.contract, ROOT)
        self.assertEqual(values["BOARDFAMILY"], "sun7i")
        self.assertEqual(values["CONFIG_DRAM_CLK"], "384")
        self.assertTrue((ROOT / board["current_file"]).is_file())

    def test_sidecar_parser_accepts_a_path_but_requires_the_image_basename(self) -> None:
        digest = "a" * 64
        with tempfile.TemporaryDirectory() as temporary:
            sidecar = Path(temporary) / "sample.img.xz.sha"
            sidecar.write_text(
                f"{digest}  output/images/sample.img.xz\n", encoding="utf-8"
            )
            MODULE.verify_sidecar(sidecar, "sample.img.xz", digest)
            with self.assertRaises(MODULE.VerificationError):
                MODULE.verify_sidecar(sidecar, "another.img.xz", digest)

    def test_log_contract_requires_completion_and_profile_markers(self) -> None:
        cli = next(
            record
            for record in self.contract["archives"]
            if record["release"] == "trixie" and record["profile"] == "cli"
        )
        xfce = next(
            record
            for record in self.contract["archives"]
            if record["release"] == "trixie" and record["profile"] == "xfce"
        )
        cli_markers = MODULE.required_log_markers(cli)
        xfce_markers = MODULE.required_log_markers(xfce)
        for marker in (
            "SHA256 calculating",
            "Done building image",
            "BUILD_DESKTOP=no",
            "Docker run finished",
            "successful",
        ):
            self.assertIn(marker, cli_markers)
        self.assertIn("BUILD_DESKTOP=yes", xfce_markers)
        self.assertIn("DESKTOP_ENVIRONMENT=xfce", xfce_markers)

    def test_representative_policy_is_trixie_cli_and_eos_aware(self) -> None:
        policy = self.contract["representative_content_check"]
        self.assertEqual((policy["release"], policy["profile"]), ("trixie", "cli"))
        self.assertEqual(policy["partition_table"], "dos")
        self.assertEqual(policy["filesystem"], "ext4")
        self.assertEqual(policy["filesystem_label"], "armbi_root")
        self.assertIn(
            "BOARD_TYPE=eos",
            policy["required_file_lines"]["etc/armbian-release"],
        )
        self.assertIn(
            "boot/dtb-6.18.32-current-sunxi/sun7i-a20-lamobo-r1.dtb",
            policy["required_globs"],
        )

    def test_verifier_uses_read_only_loop_and_mount(self) -> None:
        text = VERIFIER.read_text(encoding="utf-8")
        for required in (
            '"xz", "-t"',
            '"xz", "-dc"',
            '"--read-only"',
            '"ro,noload"',
            '"BOARD_TYPE=eos"',
            "st_mtime_ns",
            "public_release_allowed",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text + CONTRACT.read_text(encoding="utf-8"))
        self.assertNotIn("mount -o rw", text)
        self.assertLess(
            text.index("if cleanup_errors:"),
            text.index("if primary_error:\n            raise primary_error"),
        )

    def test_evidence_document_states_archival_limits(self) -> None:
        text = EVIDENCE.read_text(encoding="utf-8")
        for required in (
            "L2（歷史／封存）",
            "不是本次新建置",
            "不代表目前仍受支援",
            "沒有實機驗證",
            "不得作為公開發布依據",
            "bananapir1.eos",
            "556a14dde79770826d825ef845c430c754d55f9f",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)


if __name__ == "__main__":
    unittest.main()
