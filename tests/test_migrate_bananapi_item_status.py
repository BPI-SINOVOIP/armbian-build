#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import tempfile
import unittest
import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/migrate-bananapi-item-status.py"
SOURCE = "a" * 40
CONTEXT = "b" * 64
BSP = "d" * 40
USERPATCHES = "e" * 64


class MigrateBananaPiItemStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.matrix = self.root / "matrix.tsv"
        self.formal = self.root / "formal"
        self.release = self.root / "candidate"
        self.state = self.root / "state"
        self.output = self.state / "migrations" / "result"
        self.formal.mkdir()
        (self.release / "bpi-test").mkdir(parents=True)
        for name in (
            "boards",
            "items",
            "raw-items",
            "transactions",
            "logs",
            "framework-logs",
        ):
            (self.state / name).mkdir(parents=True)
        self.matrix.write_text(
            "folder\tboard\tbranch\treleases\n"
            "bpi-test\tbananapitest\tcurrent\ttrixie\n",
            encoding="utf-8",
        )
        matrix_digest = hashlib.sha256(self.matrix.read_bytes()).hexdigest()
        (self.state / "boards" / "bpi-test.complete").write_text(
            f"source_commit={SOURCE}\n"
            f"bsp_base_commit={BSP}\n"
            f"matrix_sha256={matrix_digest}\n"
            f"userpatches_sha256={USERPATCHES}\n"
            f"build_context_sha256={CONTEXT}\n"
            "folder=bpi-test\n"
            "board=bananapitest\n"
            "branch=current\n"
            "images=2\n"
            "status=complete\n",
            encoding="utf-8",
        )
        for profile in ("minimal", "xfce"):
            log = self.state / "logs" / f"{profile}.log"
            log.write_text("測試日誌\n", encoding="utf-8")
            log_digest = hashlib.sha256(log.read_bytes()).hexdigest()
            archive = self.release / "bpi-test" / f"{profile}.img.xz"
            archive.write_text("測試映像\n", encoding="utf-8")
            archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            Path(f"{archive}.sha").write_text(
                f"{archive_digest}  {archive.name}\n",
                encoding="utf-8",
            )
            marker = self.state / "items" / f"bpi-test-trixie-{profile}.complete"
            marker.write_text(
                f"source_commit={SOURCE}\n"
                f"bsp_base_commit={BSP}\n"
                f"matrix_sha256={matrix_digest}\n"
                f"userpatches_sha256={USERPATCHES}\n"
                f"build_context_sha256={CONTEXT}\n"
                "folder=bpi-test\n"
                "board=bananapitest\n"
                "branch=current\n"
                "release=trixie\n"
                f"profile={profile}\n"
                f"archive={profile}.img.xz\n"
                f"sha256={archive_digest}\n"
                f"log={log}\n"
                f"log_sha256={log_digest}\n",
                encoding="utf-8",
            )
        self.archive_digest = archive_digest
        self.policy = self.root / "policy.py"
        self.policy.write_text(
            """#!/usr/bin/env python3
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--matrix')
parser.add_argument('--candidate-state', type=Path)
parser.add_argument('--output', type=Path)
parser.add_argument('--allow-legacy-item-status', action='store_true')
args = parser.parse_args()
missing = []
for marker in (args.candidate_state / 'items').glob('*.complete'):
    if 'status=complete\\n' not in marker.read_text(encoding='utf-8'):
        missing.append(marker)
if missing and not args.allow_legacy_item_status:
    raise SystemExit(9)
args.output.write_text(
    'folder\\tsource_commit\\tbuild_context_sha256\\n'
    + 'bpi-test\\t' + 'a' * 40 + '\\t' + 'b' * 64 + '\\n',
    encoding='utf-8',
)
""",
            encoding="utf-8",
        )
        self.audit = self.root / "audit.py"
        self.write_audit()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_audit(
        self, fail_full: bool = False, fail_structure: bool = False
    ) -> None:
        self.audit.write_text(
            f"""#!/usr/bin/env python3
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--matrix')
parser.add_argument('--formal-release')
parser.add_argument('--candidate')
parser.add_argument('--candidate-input-policy')
parser.add_argument('--output-dir', type=Path)
parser.add_argument('--verify-digests', action='store_true')
parser.add_argument('--verify-xz', action='store_true')
args = parser.parse_args()
args.output_dir.mkdir()
source = 'a' * 40
context = 'b' * 64
digest = {self.archive_digest!r}
minimal = 'bpi-test/bananapitest/current/trixie/minimal'
xfce = 'bpi-test/bananapitest/current/trixie/xfce'
(args.output_dir / '待辦佇列.tsv').write_text(
    '板目錄\\t板卡\\t分支\\t發行版\\t類型\\t動作\\t原因\\n', encoding='utf-8'
)
(args.output_dir / '候選處置.tsv').write_text(
    '唯一鍵\\t候選來源\\t處置\\t映像\\tSHA256\\t來源提交\\t建置內容雜湊\\n'
    + minimal + '\\t整併候選\\t採用\\tminimal.img.xz\\t' + digest + '\\t' + source + '\\t' + context + '\\n'
    + xfce + '\\t整併候選\\t採用\\txfce.img.xz\\t' + digest + '\\t' + source + '\\t' + context + '\\n',
    encoding='utf-8'
)
(args.output_dir / '映像盤點.tsv').write_text(
    '唯一鍵\\t板目錄\\t板卡\\t分支\\t發行版\\t類型\\t狀態\\t選用來源\\t映像\\tSHA256\\t來源提交\\t建置內容雜湊\\t處置\\n'
    + minimal + '\\tbpi-test\\tbananapitest\\tcurrent\\ttrixie\\tminimal\\t已驗證候選\\t整併候選\\tminimal.img.xz\\t' + digest + '\\t' + source + '\\t' + context + '\\t不再建置\\n'
    + xfce + '\\tbpi-test\\tbananapitest\\tcurrent\\ttrixie\\txfce\\t已驗證候選\\t整併候選\\txfce.img.xz\\t' + digest + '\\t' + source + '\\t' + context + '\\t不再建置\\n',
    encoding='utf-8'
)
(args.output_dir / '板卡決策.tsv').write_text(
    '板目錄\\t板卡\\t分支\\t預期映像數\\t決策\\t選用來源\\n'
    'bpi-test\\tbananapitest\\tcurrent\\t2\\t沿用完整候選\\t整併候選\\n', encoding='utf-8'
)
(args.output_dir / '候選輸入政策.tsv').write_text(
    'folder\\tsource_commit\\tbuild_context_sha256\\n'
    + 'bpi-test\\t' + source + '\\t' + context + '\\n', encoding='utf-8'
)
(args.output_dir / '中止產物.tsv').write_text(
    '候選來源\\t類別\\t大小bytes\\t處置\\t路徑\\n', encoding='utf-8'
)
(args.output_dir / '候選交易殘留.tsv').write_text(
    '候選來源\\t類別\\t板目錄\\t項目\\t檔案數\\t大小bytes\\t處置\\t路徑\\n',
    encoding='utf-8',
)
(args.output_dir / '模式.txt').write_text(
    '完整' if args.verify_digests and args.verify_xz else '結構',
    encoding='utf-8',
)
if {fail_full!r} and args.verify_xz:
    raise SystemExit(7)
if {fail_structure!r} and not args.verify_xz:
    raise SystemExit(8)
""",
            encoding="utf-8",
        )

    def run_migration(
        self, allow_test_tools: bool = True
    ) -> subprocess.CompletedProcess[str]:
        command = [
                "python3",
                str(SCRIPT),
                "--matrix",
                str(self.matrix),
                "--formal-release",
                str(self.formal),
                "--candidate-release",
                str(self.release),
                "--candidate-state",
                str(self.state),
                "--output-dir",
                str(self.output),
                "--expected-boards",
                "1",
                "--expected-items",
                "2",
                "--policy-tool",
                str(self.policy),
                "--audit-tool",
                str(self.audit),
            ]
        if allow_test_tools:
            command.append("--allow-test-tools")
        return subprocess.run(
            command,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_formal_mode_rejects_alternate_validation_tools(self) -> None:
        result = self.run_migration(allow_test_tools=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("只允許使用同版受控政策與稽核工具", result.stderr)

    def test_verified_migration_preserves_backups_and_adds_status(self) -> None:
        result = self.run_migration()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output / "完整性稽核" / "模式.txt").read_text(), "完整")
        self.assertEqual(
            (self.output / "遷移後結構稽核" / "模式.txt").read_text(), "結構"
        )
        self.assertIn("狀態\t成功", (self.output / "執行狀態.tsv").read_text())
        snapshots = self.output / "執行輸入快照"
        self.assertEqual(
            {path.name for path in snapshots.iterdir()},
            {
                "發布矩陣.tsv",
                "狀態遷移工具.py",
                "候選輸入政策工具.py",
                "發布狀態稽核工具.py",
            },
        )
        for profile in ("minimal", "xfce"):
            name = f"bpi-test-trixie-{profile}.complete"
            migrated = (self.state / "items" / name).read_text(encoding="utf-8")
            original = (self.output / "原始標記" / name).read_text(encoding="utf-8")
            self.assertIn("status=complete\n", migrated)
            self.assertIn("status_migration_started_utc=", migrated)
            self.assertIn("status_migration=legacy-item-v1\n", migrated)
            self.assertNotIn("status=complete\n", original)

        repeated = self.run_migration()
        self.assertNotEqual(repeated.returncode, 0)
        self.assertIn("拒絕未重新稽核即回報成功", repeated.stderr)

    def test_failed_full_audit_does_not_modify_markers(self) -> None:
        before = {
            path: path.read_text(encoding="utf-8")
            for path in (self.state / "items").glob("*.complete")
        }
        self.write_audit(fail_full=True)

        result = self.run_migration()

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())
        for path, content in before.items():
            self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_failed_post_migration_audit_rolls_back_all_markers(self) -> None:
        before = {
            path: path.read_text(encoding="utf-8")
            for path in (self.state / "items").glob("*.complete")
        }
        self.write_audit(fail_structure=True)

        result = self.run_migration()

        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.output.is_dir())
        self.assertIn("狀態\t已回滾", (self.output / "執行狀態.tsv").read_text())
        for path, content in before.items():
            self.assertEqual(path.read_text(encoding="utf-8"), content)

        self.write_audit()
        recovered = self.run_migration()

        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertIn("狀態\t成功", (self.output / "執行狀態.tsv").read_text())
        self.assertEqual(
            len(list(self.output.parent.glob("result.recovered-*"))),
            1,
        )

    def test_recovery_refuses_to_overwrite_unknown_newer_marker(self) -> None:
        self.write_audit(fail_structure=True)
        failed = self.run_migration()
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("狀態\t已回滾", (self.output / "執行狀態.tsv").read_text())

        marker = self.state / "items" / "bpi-test-trixie-minimal.complete"
        newer = marker.read_text(encoding="utf-8") + "new_generation=1\n"
        marker.write_text(newer, encoding="utf-8")

        recovered = self.run_migration()

        self.assertNotEqual(recovered.returncode, 0)
        self.assertIn("不是可安全回滾的已知版本", recovered.stderr)
        self.assertEqual(marker.read_text(encoding="utf-8"), newer)

    def test_recovery_rejects_truncated_ledger_before_touching_markers(self) -> None:
        self.write_audit(fail_structure=True)
        failed = self.run_migration()
        self.assertNotEqual(failed.returncode, 0)

        ledger = self.output / "遷移清冊.tsv"
        rows = ledger.read_text(encoding="utf-8").splitlines()
        ledger.write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")
        before = {
            path: path.read_text(encoding="utf-8")
            for path in (self.state / "items").glob("*.complete")
        }

        recovered = self.run_migration()

        self.assertNotEqual(recovered.returncode, 0)
        self.assertIn("遷移清冊數量不符", recovered.stderr)
        for path, content in before.items():
            self.assertEqual(path.read_text(encoding="utf-8"), content)


if __name__ == "__main__":
    unittest.main()
