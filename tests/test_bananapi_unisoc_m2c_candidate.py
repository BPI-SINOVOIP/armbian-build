#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path(
    "/media/pi/SMCI/bpi/unisoc/sync-20260524/source_sync_rls_25c"
)
CONTRACT = REPO_ROOT / "config/validation/bananapi-unisoc-uis7885-m2c-vendor.json"
BOARD = REPO_ROOT / "config/boards/bananapim2c.wip"
FAMILY = REPO_ROOT / "config/sources/families/unisoc-uis7885-bpi.conf"
SOURCE_POLICY = (
    REPO_ROOT
    / "docs/evidence/bananapi-family-optimization/E-unisoc-m2c-source-policy-20260827.md"
)
SOURCE_VERIFIER = REPO_ROOT / "tools/verify-bananapi-unisoc-m2c-sources.sh"
BUILD_TOOL = REPO_ROOT / "tools/build-bananapi-unisoc-m2c-candidate.sh"
ISOLATED_TOOL = (
    REPO_ROOT / "tools/run-bananapi-unisoc-m2c-candidate-isolated-cache.sh"
)
CANDIDATE_VERIFIER = REPO_ROOT / "tools/verify-bananapi-unisoc-m2c-candidate.sh"
SAFE_REMOVAL = REPO_ROOT / "tools/bananapi-safe-removal.sh"
PUBLIC_STAGE_TOOL = REPO_ROOT / "tools/stage-bpi-m2c-unisoc-hybrid-release.sh"

INTERNAL_GATED_TOOLS = (
    "build-bpi-m2c-unisoc-yocto.sh",
    "make-bpi-m2c-unisoc-hybrid-matrix.sh",
    "make-bpi-m2c-unisoc-hybrid-pac.sh",
    "make-bpi-m2c-unisoc-sd-rootfs.sh",
    "make-bpi-m2c-unisoc-sdroot-pac.sh",
)
PUBLIC_GATED_TOOLS = (
    "stage-bpi-m2c-unisoc-hybrid-release.sh",
    "stage-bpi-m2c-unisoc-release.sh",
)
REMOVAL_GATED_TOOLS = (
    "make-bpi-m2c-unisoc-hybrid-matrix.sh",
    "make-bpi-m2c-unisoc-hybrid-pac.sh",
    "make-bpi-m2c-unisoc-sd-rootfs.sh",
    "make-bpi-m2c-unisoc-sdroot-pac.sh",
    "stage-bpi-m2c-unisoc-hybrid-release.sh",
)


class BananaPiUnisocM2CCandidateTests(unittest.TestCase):
    def create_allowlisted_source_fixture(
        self, base: Path
    ) -> tuple[Path, Path, Path]:
        source = base / "source"
        source.mkdir()
        required_content = {
            "layers/meta-unisoc/conf/machine/uis7885-2h10.conf": (
                'KERNEL_BOARD = "uis7885-2h10"\n'
                'UBOOT_BOARD = "uis7885_2h10"\n'
                'SUPPORT_EMMC_UFS_SDBOOT = "yes"\n'
            ),
            "prebuilts/pac_config/uis7885-2h10-uboot22.ini": (
                "SPLLoaderSDBOOT=1@./out/target/product/uis7885-2h10/u-boot-spl-16k-sign.bin\n"
                "BOOT=1@./out/target/product/uis7885-2h10/boot-sign.img\n"
                "DTBO=1@./out/target/product/uis7885-2h10/dtbo-sign.img\n"
            ),
            "layers/meta-unisoc/recipes-bsp/u-boot/u-boot22.bb": (
                "inherit sign_unisoc_binary\n"
                'UNISOC_SIGN_ENABLE ?= "no"\n'
            ),
            "layers/meta-unisoc/recipes-bsp/chipram/chipram.bb": (
                "inherit sign_unisoc_binary deploy\n"
                'UNISOC_SIGN_ENABLE ?= "yes"\n'
            ),
        }
        for relative, content in required_content.items():
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        (source / ".gitignore").write_text(".repo/\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "user.name=測試",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-q",
                "-m",
                "建立測試來源",
            ],
            check=True,
        )
        source_revision = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()

        manifest_repo = source / ".repo" / "manifests"
        manifest_repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(manifest_repo)], check=True)
        (manifest_repo / "default.xml").write_text("<manifest/>\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(manifest_repo), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(manifest_repo),
                "-c",
                "user.name=測試",
                "-c",
                "user.email=test@example.invalid",
                "commit",
                "-q",
                "-m",
                "建立測試 manifest",
            ],
            check=True,
        )
        manifest_revision = subprocess.run(
            ["git", "-C", str(manifest_repo), "rev-parse", "HEAD"],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()

        resolved_manifest = base / "resolved.xml"
        resolved_manifest.write_text(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            f'<manifest><project name="fixture" path="." revision="{source_revision}"/></manifest>\n',
            encoding="utf-8",
        )
        untracked = source / "必要輸入.bin"
        untracked.write_bytes(b"allowlisted-input")
        untracked_sha = hashlib.sha256(untracked.read_bytes()).hexdigest()

        contract = {
            "source": {
                "baseline": "測試基線",
                "manifest_commit": manifest_revision,
                "resolved_manifest": "../resolved.xml",
                "resolved_manifest_sha256": hashlib.sha256(
                    resolved_manifest.read_bytes()
                ).hexdigest(),
                "project_count": 1,
                "expected_local_diffs": {},
                "untracked_inputs": {
                    "policy": "deny-unless-allowlisted",
                    "allowlist_required_fields": [
                        "path",
                        "sha256",
                        "purpose",
                        "license_status",
                    ],
                    "allowlist": [
                        {
                            "path": "./必要輸入.bin",
                            "sha256": untracked_sha,
                            "purpose": "測試必要輸入",
                            "license_status": "local-use-authorized",
                        }
                    ],
                    "known_unclassified": {"blocking": False},
                },
            },
            "required_files": [],
            "external_patch_evidence": [],
        }
        contract_path = base / "contract.json"
        contract_path.write_text(
            json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return source, contract_path, untracked

    def test_board_stays_wip_and_names_contract(self) -> None:
        text = BOARD.read_text(encoding="utf-8")
        self.assertTrue(BOARD.name.endswith(".wip"))
        self.assertIn('BOARDFAMILY="unisoc-uis7885-bpi"', text)
        self.assertIn('KERNEL_TARGET="vendor"', text)
        self.assertIn('UNISOC_UIS7885_BPI_BASELINE="UNC_LINUX_RLS_25C_W26.07.2"', text)
        self.assertIn(
            'UNISOC_UIS7885_BPI_MANIFEST_COMMIT="7ac2b5ae548b9dd9c4d2f0b32476abd5c6fa7058"',
            text,
        )
        self.assertIn("L0 本機來源快照稽核", text)

    def test_family_refuses_normal_armbian_image(self) -> None:
        text = FAMILY.read_text(encoding="utf-8")
        self.assertIn("late_family_config__unisoc_uis7885_bpi_is_vendor_pac_wip", text)
        self.assertIn("不是一般 Armbian raw image 目標", text)
        self.assertIn("新舊工具均不得建立公開映像", text)

    def test_contract_is_l0_snapshot_and_rejects_unclassified_inputs(self) -> None:
        contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        untracked = contract["source"]["untracked_inputs"]
        known = untracked["known_unclassified"]
        self.assertEqual(contract["board"], "bananapim2c")
        self.assertEqual(contract["candidate_scope"], "local-source-snapshot-audit")
        self.assertEqual(contract["current_evidence_level"], "L0")
        self.assertNotIn("target_evidence_level", contract)
        self.assertFalse(contract["public_release_allowed"])
        self.assertFalse(contract["hardware_claims_allowed"])
        self.assertFalse(contract["complete_rootfs_image_allowed"])
        self.assertFalse(contract["component_build_allowed"])
        self.assertEqual(contract["source"]["project_count"], 95)
        self.assertEqual(len(contract["source"]["expected_local_diffs"]), 41)
        self.assertEqual(len(contract["external_patch_evidence"]), 5)
        self.assertEqual(untracked["policy"], "deny-unless-allowlisted")
        self.assertEqual(untracked["allowlist"], [])
        self.assertEqual(
            set(untracked["allowlist_required_fields"]),
            {"path", "sha256", "purpose", "license_status"},
        )
        self.assertTrue(known["blocking"])
        self.assertEqual(known["project_count"], 55)
        self.assertEqual(known["file_count"], 6751)
        self.assertFalse(contract["source"]["remote_access"]["portable_fetch_proven"])
        self.assertGreaterEqual(len(contract["blockers"]), 7)
        self.assertGreaterEqual(len(contract["promotion_prerequisites"]), 7)

    def test_new_candidate_has_no_l2_description(self) -> None:
        paths = (
            BOARD,
            FAMILY,
            CONTRACT,
            SOURCE_POLICY,
            BUILD_TOOL,
            CANDIDATE_VERIFIER,
            SOURCE_VERIFIER,
        )
        for path in paths:
            self.assertNotIn("L2", path.read_text(encoding="utf-8"), path)

    def test_policy_records_all_material_limits(self) -> None:
        text = SOURCE_POLICY.read_text(encoding="utf-8")
        for phrase in (
            "L0 本機來源快照稽核",
            "41 組不可重放差異",
            "未追蹤檔總數為 6,751",
            "deny-unless-allowlisted",
            "Unauthorized",
            "歷史 PAC 證據限制",
            "chipram",
            "沒有查閱、複製或輸出任何私鑰內容",
        ):
            self.assertIn(phrase, text)

    def test_build_tool_refuses_component_mode(self) -> None:
        result = subprocess.run(
            [str(BUILD_TOOL), "--mode", "kernel"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("只允許 audit", result.stderr)

    def test_allowlisted_untracked_input_requires_exact_hash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bananapi-m2c-source-") as temp:
            source, contract, untracked = self.create_allowlisted_source_fixture(
                Path(temp)
            )
            command = [
                str(SOURCE_VERIFIER),
                "--source-root",
                str(source),
                "--contract",
                str(contract),
            ]
            accepted = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            self.assertIn("允許清單檔案數：1", accepted.stdout)
            self.assertIn("未分類未追蹤檔數：0", accepted.stdout)

            untracked.write_bytes(b"changed-input")
            rejected = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("未追蹤輸入 SHA-256 不符", rejected.stdout)

    def test_candidate_verifier_rejects_any_extra_entry(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bananapi-m2c-extra-") as temp:
            candidate = Path(temp)
            for name in (
                "CANDIDATE_STATUS.json",
                "CONTRACT.json",
                "SHA256SUMS",
                "SOURCE_POLICY.md",
                "SOURCE_VERIFICATION.txt",
            ):
                (candidate / name).write_text("", encoding="utf-8")
            (candidate / "未允許內容.bin").write_bytes(b"not-allowed")
            result = subprocess.run(
                [str(CANDIDATE_VERIFIER), "--candidate-dir", str(candidate)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("未允許的額外項目", result.stderr)

    def test_old_build_and_release_tools_use_l0_guard(self) -> None:
        for name in INTERNAL_GATED_TOOLS:
            text = (REPO_ROOT / "tools" / name).read_text(encoding="utf-8")
            self.assertIn("bananapi-m2c-l0-guard.sh", text, name)
            self.assertIn("bananapi_m2c_require_local_source_snapshot", text, name)
        for name in PUBLIC_GATED_TOOLS:
            text = (REPO_ROOT / "tools" / name).read_text(encoding="utf-8")
            self.assertIn("bananapi-m2c-l0-guard.sh", text, name)
            self.assertIn("bananapi_m2c_require_public_release", text, name)

    def test_recursive_removal_sites_use_common_guard(self) -> None:
        for name in REMOVAL_GATED_TOOLS:
            text = (REPO_ROOT / "tools" / name).read_text(encoding="utf-8")
            self.assertIn("bananapi-safe-removal.sh", text, name)
            self.assertIn("bananapi_require_safe_removal_target", text, name)
            for line in text.splitlines():
                if "rm -rf" in line:
                    self.assertIn("--one-file-system --", line, f"{name}: {line}")

    def run_removal_guard(
        self, target: Path, prefix: Path, minimum_depth: int
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                "-c",
                'source "$1"; bananapi_require_safe_removal_target "$2" "$3" "$4"',
                "bash",
                str(SAFE_REMOVAL),
                str(target),
                str(prefix),
                str(minimum_depth),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_safe_removal_guard_accepts_only_deep_regular_subdirectory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bananapi-m2c-path-") as temp:
            prefix = Path(temp) / "輸出根目錄"
            target = prefix / "日期" / "候選"
            target.mkdir(parents=True)
            result = self.run_removal_guard(target, prefix, 2)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(target.is_dir())

    def test_safe_removal_guard_rejects_outside_shallow_and_symlink_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bananapi-m2c-path-") as temp:
            base = Path(temp)
            prefix = base / "輸出根目錄"
            shallow = prefix / "一層"
            outside = base / "外部" / "日期" / "候選"
            real_target = prefix / "日期" / "實體"
            symlink_target = prefix / "日期" / "連結"
            shallow.mkdir(parents=True)
            outside.mkdir(parents=True)
            real_target.mkdir(parents=True)
            symlink_target.symlink_to(real_target, target_is_directory=True)

            self.assertNotEqual(
                self.run_removal_guard(outside, prefix, 2).returncode, 0
            )
            self.assertNotEqual(
                self.run_removal_guard(shallow, prefix, 2).returncode, 0
            )
            self.assertNotEqual(
                self.run_removal_guard(symlink_target, prefix, 2).returncode, 0
            )
            self.assertTrue(outside.is_dir())
            self.assertTrue(shallow.is_dir())
            self.assertTrue(real_target.is_dir())

    def test_safe_removal_guard_rejects_mountpoint(self) -> None:
        if not Path("/dev/shm").is_dir():
            self.skipTest("本機沒有可供唯讀負向測試的 /dev/shm")
        result = self.run_removal_guard(Path("/dev/shm"), Path("/dev"), 1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("掛載點", result.stderr)

    def test_public_stage_refusal_preserves_existing_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bananapi-m2c-stage-") as temp:
            base = Path(temp)
            matrix = base / "matrix"
            target_root = base / "release"
            target = target_root / "禁止發布"
            matrix.mkdir()
            target.mkdir(parents=True)
            (matrix / "matrix-summary.tsv").write_text(
                "release\tflavor\tstatus\twork_dir\tpac\n", encoding="utf-8"
            )
            sentinel = target / "使用者資料"
            sentinel.write_text("保留", encoding="utf-8")
            environment = os.environ.copy()
            environment["TARGET_ROOT"] = str(target_root)
            result = subprocess.run(
                [
                    str(PUBLIC_STAGE_TOOL),
                    "--matrix-dir",
                    str(matrix),
                    "--target-dir",
                    str(target),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("契約禁止公開發布", result.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "保留")

    @unittest.skipUnless(SOURCE_ROOT.is_dir(), "本機沒有指定的 Unisoc 來源證據")
    def test_local_source_is_explicitly_blocked_by_unclassified_inputs(self) -> None:
        environment = os.environ.copy()
        environment["GIT_OPTIONAL_LOCKS"] = "0"
        result = subprocess.run(
            [str(SOURCE_VERIFIER), "--source-root", str(SOURCE_ROOT)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=environment,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("未追蹤檔總數：6751", result.stdout)
        self.assertIn("未分類未追蹤檔數：6751", result.stdout)
        self.assertIn("結果：失敗", result.stdout)

        with tempfile.TemporaryDirectory(prefix="bananapi-m2c-candidate-") as temp:
            candidate = Path(temp) / "candidate"
            build_result = subprocess.run(
                [
                    str(ISOLATED_TOOL),
                    "--source-root",
                    str(SOURCE_ROOT),
                    "--output-dir",
                    str(candidate),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
            )
            self.assertNotEqual(build_result.returncode, 0)
            self.assertFalse(candidate.exists())


if __name__ == "__main__":
    unittest.main()
