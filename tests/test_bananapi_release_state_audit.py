#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import importlib.util
import lzma
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/audit-bananapi-release-state.py"
SPEC = importlib.util.spec_from_file_location("bananapi_release_state_audit", SCRIPT)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = AUDIT
SPEC.loader.exec_module(AUDIT)


class BananaPiReleaseStateAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.matrix = self.root / "matrix.tsv"
        self.formal = self.root / "formal"
        self.candidate = self.root / "candidate"
        self.state = self.root / "state"
        self.output = self.root / "output"
        self.policy = self.root / "candidate-input-policy.tsv"
        self.formal.mkdir()
        self.candidate.mkdir()
        (self.state / "items").mkdir(parents=True)
        (self.state / "boards").mkdir()
        (self.state / "logs").mkdir()
        (self.state / "raw-items").mkdir()
        (self.state / "raw-images").mkdir()
        self.matrix.write_text(
            "folder\tboard\tbranch\treleases\n"
            "bpi-demo\tbananapidemonstration\tcurrent\ttrixie,bookworm\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_archive(
        self, directory: Path, release: str, profile: str, tag: str = ""
    ) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        suffix = "minimal" if profile == "minimal" else "xfce_desktop"
        archive = directory / (
            "Armbian-test_Bananapidemonstration_"
            f"{release}_current_1.0_{suffix}{tag}.img.xz"
        )
        archive.write_bytes(lzma.compress(f"{release}-{profile}".encode()))
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        Path(f"{archive}.sha").write_text(
            f"{digest}  {archive.name}\n", encoding="utf-8"
        )
        return archive

    def create_candidate_item(
        self,
        release: str,
        profile: str,
        source_commit: str = "a" * 40,
        build_context: str = "b" * 64,
        *,
        staged: bool = False,
    ) -> None:
        directory = (
            self.candidate / ".staging-bpi-demo-source"
            if staged
            else self.candidate / "bpi-demo"
        )
        archive = self.create_archive(directory, release, profile)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        log = self.state / "logs" / f"bpi-demo-{release}-{profile}.log"
        log.write_text("成功建置\n", encoding="utf-8")
        log_digest = hashlib.sha256(log.read_bytes()).hexdigest()
        marker = self.state / "items" / f"bpi-demo-{release}-{profile}.complete"
        marker.write_text(
            f"source_commit={source_commit}\n"
            f"build_context_sha256={build_context}\n"
            "folder=bpi-demo\n"
            "board=bananapidemonstration\n"
            "branch=current\n"
            f"release={release}\n"
            f"profile={profile}\n"
            f"archive={archive.name}\n"
            f"sha256={digest}\n"
            f"log={log}\n"
            f"log_sha256={log_digest}\n",
            encoding="utf-8",
        )

    def create_board_marker(
        self,
        source_commit: str = "a" * 40,
        build_context: str = "b" * 64,
    ) -> None:
        (self.state / "boards" / "bpi-demo.complete").write_text(
            f"source_commit={source_commit}\n"
            f"build_context_sha256={build_context}\n"
            "folder=bpi-demo\n"
            "board=bananapidemonstration\n"
            "branch=current\n"
            "images=4\n"
            "status=complete\n",
            encoding="utf-8",
        )

    def write_policy(
        self, source_commit: str = "a" * 40, build_context: str = "b" * 64
    ) -> None:
        self.policy.write_text(
            "folder\tsource_commit\tbuild_context_sha256\n"
            f"bpi-demo\t{source_commit}\t{build_context}\n",
            encoding="utf-8",
        )

    def create_raw_item(self, release: str, profile: str) -> None:
        raw_dir = self.state / "raw-images" / f"{release}-{profile}"
        raw_dir.mkdir(parents=True)
        raw = raw_dir / f"demo-{release}-{profile}.img"
        raw.write_bytes(f"raw-{release}-{profile}".encode())
        digest = hashlib.sha256(raw.read_bytes()).hexdigest()
        Path(f"{raw}.sha").write_text(f"{digest}  {raw.name}\n", encoding="utf-8")
        log = self.state / "logs" / f"raw-{release}-{profile}.log"
        log.write_text("原始映像建置完成\n", encoding="utf-8")
        log_digest = hashlib.sha256(log.read_bytes()).hexdigest()
        marker = self.state / "raw-items" / f"bpi-demo-{release}-{profile}.ready"
        marker.write_text(
            "source_commit=" + "a" * 40 + "\n"
            "build_context_sha256=" + "b" * 64 + "\n"
            "folder=bpi-demo\n"
            "board=bananapidemonstration\n"
            "branch=current\n"
            f"release={release}\n"
            f"profile={profile}\n"
            f"raw_image={raw}\n"
            f"raw_sha256={digest}\n"
            f"log={log}\n"
            f"log_sha256={log_digest}\n",
            encoding="utf-8",
        )

    def audit_command(self, *extra: str) -> list[str]:
        return [
            "python3",
            str(SCRIPT),
            "--matrix",
            str(self.matrix),
            "--formal-release",
            str(self.formal),
            "--candidate",
            f"測試候選|{self.candidate}|{self.state}",
            "--output-dir",
            str(self.output),
            "--verify-digests",
            "--verify-xz",
            *extra,
        ]

    def run_audit(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.audit_command(*extra),
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )

    def run_audit_in_process(self, *extra: str) -> int:
        with (
            mock.patch.object(sys, "argv", self.audit_command(*extra)[1:]),
            mock.patch("builtins.print"),
        ):
            return AUDIT.main()

    def read_tsv(self, name: str) -> list[dict[str, str]]:
        with (self.output / name).open(encoding="utf-8", newline="") as stream:
            return list(csv.DictReader(stream, delimiter="\t"))

    def test_verification_workers_default_and_limits(self) -> None:
        for extra, expected in (
            ((), 1),
            (("--verification-workers", "1"), 1),
            (("--verification-workers", "2"), 2),
            (("--verification-workers", "4"), 4),
            (("--verification-workers", "16"), 16),
        ):
            with self.subTest(extra=extra):
                with mock.patch.object(sys, "argv", self.audit_command(*extra)[1:]):
                    self.assertEqual(AUDIT.parse_args().verification_workers, expected)

    def test_verification_workers_rejects_invalid_values(self) -> None:
        for value in ("0", "-1", "17", "1.5", "文字", ""):
            with self.subTest(value=value):
                result = self.run_audit("--verification-workers", value)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("驗證工作數必須是 1 到 16 的正整數", result.stderr)
                self.assertNotIn("盤點完成", result.stdout)
                self.assertFalse(self.output.exists())

    def test_default_and_one_worker_do_not_create_a_pool(self) -> None:
        def on_main_thread(finder):
            def checked(*args, **kwargs):
                self.assertIs(threading.current_thread(), threading.main_thread())
                return finder(*args, **kwargs)

            return checked

        for extra in ((), ("--verification-workers", "1")):
            with self.subTest(extra=extra):
                with (
                    mock.patch.object(
                        AUDIT, "ThreadPoolExecutor",
                        side_effect=AssertionError("單工不得建立執行緒池"),
                    ) as pool,
                    mock.patch.object(
                        AUDIT, "find_formal_artifact",
                        side_effect=on_main_thread(AUDIT.find_formal_artifact),
                    ) as formal,
                    mock.patch.object(
                        AUDIT, "find_candidate_artifact",
                        side_effect=on_main_thread(AUDIT.find_candidate_artifact),
                    ) as candidate,
                    mock.patch.object(
                        AUDIT, "find_candidate_raw_artifact",
                        side_effect=on_main_thread(AUDIT.find_candidate_raw_artifact),
                    ) as raw,
                ):
                    self.assertEqual(self.run_audit_in_process(*extra), 0)
                    pool.assert_not_called()
                    self.assertEqual(formal.call_count, 4)
                    self.assertEqual(candidate.call_count, 4)
                    self.assertEqual(raw.call_count, 4)

    def test_two_workers_overlap_and_preserve_all_output_order(self) -> None:
        self.matrix.write_text(
            self.matrix.read_text(encoding="utf-8")
            + "bpi-second\tbananapisecond\tcurrent\tbookworm,trixie\n",
            encoding="utf-8",
        )
        for release in ("trixie", "bookworm"):
            for profile in ("minimal", "xfce"):
                self.create_archive(self.formal / "bpi-demo", release, profile)
        self.create_candidate_item("trixie", "minimal")
        self.create_candidate_item("bookworm", "minimal", build_context="c" * 64)
        self.create_raw_item("trixie", "minimal")
        self.create_raw_item("trixie", "xfce")
        extra = ("--target-build-context", "b" * 64)
        result = self.run_audit(*extra)
        self.assertEqual(result.returncode, 0, result.stderr)
        baseline = {path.name: path.read_bytes() for path in self.output.iterdir()}
        barrier = threading.Barrier(2, timeout=5)
        lock = threading.Lock()
        rows = AUDIT.read_matrix(self.matrix)
        finished = {
            (name, row.folder, release): threading.Event()
            for name in ("formal", "candidate")
            for row in rows
            for release in row.releases
        }
        completed = []
        active = 0
        peak = 0

        def concurrent_finder(name, finder):
            def checked(source, key, **kwargs):
                nonlocal active, peak
                self.assertIsNot(threading.current_thread(), threading.main_thread())
                self.assertEqual(kwargs, {"verify_digests": True, "verify_xz": True})
                with lock:
                    active += 1
                    peak = max(peak, active)
                try:
                    # 同時進入兩項驗證，並強制後提交的項目先完成。
                    barrier.wait()
                    artifact = finder(source, key, **kwargs)
                    event = finished[(name, key.folder, key.release)]
                    if key.profile == "minimal":
                        self.assertTrue(event.wait(5), "並行驗證未如期完成")
                    with lock:
                        completed.append((name, key))
                    if key.profile == "xfce":
                        event.set()
                    return artifact
                finally:
                    with lock:
                        active -= 1

            return checked

        def on_main_thread(function):
            def checked(*args, **kwargs):
                self.assertIs(threading.current_thread(), threading.main_thread())
                return function(*args, **kwargs)

            return checked

        with (
            mock.patch.object(
                AUDIT, "ThreadPoolExecutor", wraps=AUDIT.ThreadPoolExecutor
            ) as pool,
            mock.patch.object(
                AUDIT, "find_formal_artifact",
                side_effect=concurrent_finder("formal", AUDIT.find_formal_artifact),
            ),
            mock.patch.object(
                AUDIT, "find_candidate_artifact",
                side_effect=concurrent_finder("candidate", AUDIT.find_candidate_artifact),
            ),
            mock.patch.object(
                AUDIT, "find_candidate_raw_artifact",
                side_effect=on_main_thread(AUDIT.find_candidate_raw_artifact),
            ) as raw,
            mock.patch.object(
                AUDIT, "candidate_input_matches",
                side_effect=on_main_thread(AUDIT.candidate_input_matches),
            ),
            mock.patch.object(
                AUDIT, "board_marker_status",
                side_effect=on_main_thread(AUDIT.board_marker_status),
            ),
            mock.patch.object(
                AUDIT, "write_tsv", side_effect=on_main_thread(AUDIT.write_tsv),
            ),
        ):
            self.assertEqual(
                self.run_audit_in_process("--verification-workers", "2", *extra), 0
            )
            pool.assert_called_once_with(max_workers=2)
            self.assertEqual(raw.call_count, 8)
        self.assertEqual(peak, 2)
        self.assertEqual(active, 0)
        self.assertEqual(
            completed,
            [
                (name, AUDIT.ItemKey(row.folder, row.board, row.branch, release, profile))
                for row in rows
                for name in ("formal", "candidate")
                for release in row.releases
                for profile in ("xfce", "minimal")
            ],
        )
        self.assertEqual(
            {path.name: path.read_bytes() for path in self.output.iterdir()}, baseline
        )
        for workers in ("2", "4"):
            with self.subTest(workers=workers):
                result = self.run_audit("--verification-workers", workers, *extra)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    {path.name: path.read_bytes() for path in self.output.iterdir()},
                    baseline,
                )

    def test_parallel_worker_exceptions_propagate_before_output(self) -> None:
        for finder in ("find_formal_artifact", "find_candidate_artifact"):
            with self.subTest(finder=finder):
                with (
                    mock.patch.object(
                        AUDIT, finder, side_effect=RuntimeError("驗證工作失敗")
                    ),
                    mock.patch.object(AUDIT, "write_tsv") as write_tsv,
                    self.assertRaisesRegex(RuntimeError, "驗證工作失敗"),
                ):
                    self.run_audit_in_process("--verification-workers", "2")
                write_tsv.assert_not_called()
                self.assertFalse(self.output.exists())

    def test_parallel_sha_and_xz_failures_prevent_success(self) -> None:
        for source in ("formal", "candidate"):
            for verification in ("sha", "xz"):
                with self.subTest(source=source, verification=verification):
                    if source == "formal":
                        archive = self.create_archive(
                            self.formal / "bpi-demo", "trixie", "minimal"
                        )
                    else:
                        self.create_candidate_item("trixie", "minimal")
                        archive = next((self.candidate / "bpi-demo").glob("*.img.xz"))
                    original = archive.read_bytes()
                    original_digest = hashlib.sha256(original).hexdigest()
                    archive.write_bytes("損壞的 XZ 串流".encode("utf-8"))
                    if verification == "xz":
                        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                        Path(f"{archive}.sha").write_text(
                            f"{digest}  {archive.name}\n", encoding="utf-8"
                        )
                        if source == "candidate":
                            marker = self.state / "items" / "bpi-demo-trixie-minimal.complete"
                            marker.write_text(
                                marker.read_text(encoding="utf-8").replace(
                                    f"sha256={original_digest}\n", f"sha256={digest}\n"
                                ),
                                encoding="utf-8",
                            )
                    result = self.run_audit("--verification-workers", "2")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(
                        "映像 SHA-256 驗證失敗" if verification == "sha"
                        else "CalledProcessError",
                        result.stderr,
                    )
                    self.assertNotIn("盤點完成", result.stdout)
                    self.assertFalse(self.output.exists())
                    archive.write_bytes(original)
                    Path(f"{archive}.sha").write_text(
                        f"{original_digest}  {archive.name}\n", encoding="utf-8"
                    )

    def test_parallel_candidate_log_digest_failure_prevents_success(self) -> None:
        self.create_candidate_item("trixie", "minimal")
        log = self.state / "logs" / "bpi-demo-trixie-minimal.log"
        log.write_text("遭修改的建置日誌\n", encoding="utf-8")
        result = self.run_audit("--verification-workers", "2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("候選日誌 SHA-256 驗證失敗", result.stderr)
        self.assertNotIn("盤點完成", result.stdout)
        self.assertFalse(self.output.exists())

    def test_complete_formal_board_wins_over_partial_candidate(self) -> None:
        for release in ("trixie", "bookworm"):
            for profile in ("minimal", "xfce"):
                self.create_archive(self.formal / "bpi-demo", release, profile)
        self.create_candidate_item("trixie", "minimal")
        result = self.run_audit("--reuse-formal")
        self.assertEqual(result.returncode, 0, result.stderr)
        decisions = self.read_tsv("板卡決策.tsv")
        self.assertEqual(decisions[0]["決策"], "沿用既有正式")
        candidates = self.read_tsv("候選處置.tsv")
        self.assertEqual(candidates[0]["處置"], "未採用部分候選")
        self.assertEqual(self.read_tsv("待辦佇列.tsv"), [])

    def test_formal_board_is_only_a_baseline_by_default(self) -> None:
        for release in ("trixie", "bookworm"):
            for profile in ("minimal", "xfce"):
                self.create_archive(self.formal / "bpi-demo", release, profile)
        result = self.run_audit()
        self.assertEqual(result.returncode, 0, result.stderr)
        decisions = self.read_tsv("板卡決策.tsv")
        self.assertEqual(decisions[0]["決策"], "本輪全板待建")
        self.assertEqual(len(self.read_tsv("待辦佇列.tsv")), 4)

    def test_partial_candidate_is_kept_and_only_missing_items_are_queued(self) -> None:
        self.create_candidate_item("trixie", "minimal")
        result = self.run_audit()
        self.assertEqual(result.returncode, 0, result.stderr)
        decisions = self.read_tsv("板卡決策.tsv")
        self.assertEqual(decisions[0]["決策"], "保留部分候選並補缺")
        queue = self.read_tsv("待辦佇列.tsv")
        self.assertEqual(len(queue), 3)
        ledger = self.read_tsv("映像盤點.tsv")
        self.assertEqual(sum(row["狀態"] == "本輪已完成" for row in ledger), 1)

    def test_complete_candidate_requires_only_board_verification(self) -> None:
        for release in ("trixie", "bookworm"):
            for profile in ("minimal", "xfce"):
                self.create_candidate_item(release, profile)
        result = self.run_audit()
        self.assertEqual(result.returncode, 0, result.stderr)
        decisions = self.read_tsv("板卡決策.tsv")
        self.assertEqual(decisions[0]["決策"], "候選只補整板驗證")
        queue = self.read_tsv("待辦佇列.tsv")
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]["動作"], "補整板驗證")

    def test_raw_item_waits_for_compression_without_rebuild(self) -> None:
        self.create_candidate_item("trixie", "minimal")
        self.create_raw_item("trixie", "xfce")
        result = self.run_audit(
            "--target-source-commit",
            "a" * 40,
            "--target-build-context",
            "b" * 64,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        queue = self.read_tsv("待辦佇列.tsv")
        actions = [row["動作"] for row in queue]
        self.assertEqual(actions.count("等待壓縮"), 1)
        self.assertEqual(actions.count("建置缺少項目"), 2)
        ledger = self.read_tsv("映像盤點.tsv")
        raw_row = next(row for row in ledger if row["狀態"] == "待壓縮")
        self.assertEqual(raw_row["處置"], "不得重新編譯；由壓縮工作續作")
        residues = self.read_tsv("候選交易殘留.tsv")
        self.assertEqual(len(residues), 1)
        self.assertEqual(residues[0]["類別"], "原始映像狀態")
        self.assertEqual(residues[0]["處置"], "等待壓縮")

    def test_candidate_input_policy_accepts_only_the_board_identity(self) -> None:
        self.create_candidate_item("trixie", "minimal")
        self.write_policy()
        result = self.run_audit("--candidate-input-policy", str(self.policy))
        self.assertEqual(result.returncode, 0, result.stderr)
        ledger = self.read_tsv("映像盤點.tsv")
        self.assertEqual(sum(row["狀態"] == "本輪已完成" for row in ledger), 1)
        copied_policy = self.read_tsv("候選輸入政策.tsv")
        self.assertEqual(copied_policy[0]["source_commit"], "a" * 40)

    def test_candidate_input_policy_rejects_a_different_context(self) -> None:
        self.create_candidate_item("trixie", "minimal")
        self.write_policy(build_context="c" * 64)
        result = self.run_audit("--candidate-input-policy", str(self.policy))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.read_tsv("待辦佇列.tsv")), 4)

    def test_candidate_input_policy_accepts_matching_board_marker(self) -> None:
        for release in ("trixie", "bookworm"):
            for profile in ("minimal", "xfce"):
                self.create_candidate_item(release, profile)
        self.create_board_marker()
        self.write_policy()
        result = self.run_audit("--candidate-input-policy", str(self.policy))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_tsv("板卡決策.tsv")[0]["決策"], "沿用完整候選")
        self.assertEqual(self.read_tsv("待辦佇列.tsv"), [])

    def test_candidate_input_policy_rejects_mismatched_board_marker(self) -> None:
        for release in ("trixie", "bookworm"):
            for profile in ("minimal", "xfce"):
                self.create_candidate_item(release, profile)
        self.write_policy()
        cases = (
            ("c" * 40, "b" * 64, "板級完成標記來源提交不符逐板政策"),
            ("a" * 40, "c" * 64, "板級完成標記建置內容雜湊不符逐板政策"),
        )
        for source_commit, build_context, reason in cases:
            with self.subTest(reason=reason):
                self.create_board_marker(source_commit, build_context)
                result = self.run_audit(
                    "--candidate-input-policy", str(self.policy)
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    self.read_tsv("板卡決策.tsv")[0]["決策"],
                    "候選只補整板驗證",
                )
                queue = self.read_tsv("待辦佇列.tsv")
                self.assertEqual(len(queue), 1)
                self.assertEqual(queue[0]["動作"], "補整板驗證")
                self.assertEqual(queue[0]["原因"], reason)

    def test_candidate_input_policy_cannot_mix_with_global_identity(self) -> None:
        self.write_policy()
        result = self.run_audit(
            "--candidate-input-policy",
            str(self.policy),
            "--target-source-commit",
            "a" * 40,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不可與全域來源", result.stderr)

    def test_missing_items_are_sorted_and_never_invented(self) -> None:
        result = self.run_audit()
        self.assertEqual(result.returncode, 0, result.stderr)
        queue = self.read_tsv("待辦佇列.tsv")
        self.assertEqual(len(queue), 4)
        self.assertEqual(
            [(row["發行版"], row["類型"]) for row in queue],
            [
                ("trixie", "minimal"),
                ("trixie", "xfce"),
                ("bookworm", "minimal"),
                ("bookworm", "xfce"),
            ],
        )
        self.assertEqual(self.read_tsv("中止產物.tsv"), [])
        self.assertEqual(self.read_tsv("舊暫存目錄.tsv"), [])
        self.assertEqual(self.read_tsv("候選交易殘留.tsv"), [])

    def test_transaction_state_is_visible_and_queued(self) -> None:
        transactions = self.state / "transactions"
        transactions.mkdir()
        transaction = transactions / "bpi-demo.state"
        transaction.write_text(
            "folder=bpi-demo\nphase=prepared\n",
            encoding="utf-8",
        )
        result = self.run_audit()
        self.assertEqual(result.returncode, 0, result.stderr)
        residues = self.read_tsv("候選交易殘留.tsv")
        self.assertEqual(len(residues), 1)
        self.assertEqual(residues[0]["類別"], "交易狀態")
        self.assertEqual(residues[0]["板目錄"], "bpi-demo")
        self.assertEqual(residues[0]["處置"], "等待整板交易收斂")
        queue = self.read_tsv("待辦佇列.tsv")
        self.assertEqual(
            sum(row["動作"] == "等待整板交易收斂" for row in queue), 1
        )

    def test_in_progress_release_directories_are_visible_and_queued(self) -> None:
        kinds = {
            ".staging-bpi-demo-source": "候選暫存目錄",
            ".previous-bpi-demo-source": "候選回復目錄",
            ".failed-bpi-demo-source": "候選失敗目錄",
        }
        for name in kinds:
            directory = self.candidate / name
            directory.mkdir()
            (directory / "狀態.txt").write_text("進行中\n", encoding="utf-8")
        result = self.run_audit()
        self.assertEqual(result.returncode, 0, result.stderr)
        residues = self.read_tsv("候選交易殘留.tsv")
        self.assertEqual({row["類別"] for row in residues}, set(kinds.values()))
        self.assertEqual({row["板目錄"] for row in residues}, {"bpi-demo"})
        queue = self.read_tsv("待辦佇列.tsv")
        self.assertEqual(
            sum(row["動作"] == "等待整板交易收斂" for row in queue), 2
        )
        self.assertEqual(
            sum(row["動作"] == "檢查失敗交易殘留" for row in queue), 1
        )

    def test_staged_candidate_item_is_counted_without_aborting_audit(self) -> None:
        self.create_candidate_item("trixie", "minimal", staged=True)
        result = self.run_audit()
        self.assertEqual(result.returncode, 0, result.stderr)
        ledger = self.read_tsv("映像盤點.tsv")
        self.assertEqual(sum(row["狀態"] == "本輪已完成" for row in ledger), 1)
        residues = self.read_tsv("候選交易殘留.tsv")
        self.assertEqual(len(residues), 1)
        self.assertEqual(residues[0]["類別"], "候選暫存目錄")
        queue = self.read_tsv("待辦佇列.tsv")
        self.assertEqual(
            sum(row["動作"] == "等待整板交易收斂" for row in queue), 1
        )

    def test_valid_board_marker_selects_complete_candidate(self) -> None:
        for release in ("trixie", "bookworm"):
            for profile in ("minimal", "xfce"):
                self.create_candidate_item(release, profile)
        self.create_board_marker()
        result = self.run_audit()
        self.assertEqual(result.returncode, 0, result.stderr)
        decisions = self.read_tsv("板卡決策.tsv")
        self.assertEqual(decisions[0]["決策"], "沿用完整候選")
        self.assertEqual(
            {row["處置"] for row in self.read_tsv("候選處置.tsv")}, {"採用"}
        )
        self.assertEqual(self.read_tsv("待辦佇列.tsv"), [])

    def test_profile_marker_can_precede_board_specific_tag(self) -> None:
        for release in ("trixie", "bookworm"):
            for profile in ("minimal", "xfce"):
                self.create_archive(
                    self.formal / "bpi-demo", release, profile, "_board-tag"
                )
        result = self.run_audit("--reuse-formal")
        self.assertEqual(result.returncode, 0, result.stderr)
        decisions = self.read_tsv("板卡決策.tsv")
        self.assertEqual(decisions[0]["決策"], "沿用既有正式")
        self.assertEqual(len(self.read_tsv("映像盤點.tsv")), 4)


if __name__ == "__main__":
    unittest.main()
