"""以替身命令驗證 CM6 蒐證安全邊界；不蒐集主機或真板狀態。"""

import csv
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


TOOL = Path(__file__).resolve().parents[1] / "tools/collect_cm6_rc3_validation.sh"


class Rc3CollectorTests(unittest.TestCase):
    def invoke(self, *args, env=None):
        return subprocess.run(
            ["/bin/bash", str(TOOL), *args], capture_output=True, text=True,
            env=env, timeout=15, check=False,
        )

    def test_dry_run_does_not_create_or_execute(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "證據"
            result = self.invoke("--output", str(output), "--dry-run", env={"PATH": "/不存在"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(output.exists())
            self.assertIn("storage-lsblk", result.stdout)
            self.assertIn("bluetooth-show", result.stdout)
            self.assertEqual(len(result.stdout.splitlines()), 28)

    def test_existing_output_and_symlink_are_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / "既有"
            existing.mkdir()
            sentinel = existing / "保留.txt"
            sentinel.write_text("不得覆寫", encoding="utf-8")
            link = root / "連結"
            link.symlink_to(existing)
            for output in (existing, link):
                result = self.invoke("--output", str(output))
                self.assertEqual(result.returncode, 73, result.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "不得覆寫")
            self.assertEqual(list(existing.iterdir()), [sentinel])

    def test_pseudo_device_output_and_alias_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            alias = Path(tmp) / "裝置別名"
            alias.symlink_to("/dev")
            for output in ("/dev/cm6-collector-must-not-create", str(alias / "cm6-must-not-create"),
                           "/proc/cm6-must-not-create", "/sys/cm6-must-not-create"):
                result = self.invoke("--output", output)
                self.assertEqual(result.returncode, 73, result.stderr)
                self.assertFalse(Path(output).exists())

    def test_missing_parent_and_bad_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.invoke("--output", str(Path(tmp) / "缺少" / "證據"))
            self.assertEqual(result.returncode, 73, result.stderr)
        for args in ((), ("--output",), ("--output", ""), ("--bad",),
                     ("--output", "a", "--output", "b")):
            self.assertEqual(self.invoke(*args).returncode, 64)

    def test_mocked_capture_statuses_permissions_hashes_and_timeouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            commands = root / "命令替身"
            commands.mkdir()
            # 只有建立證據目錄、取時間、雜湊與正規化路徑採用系統工具。
            for name in ("realpath", "mkdir", "date", "sha256sum"):
                (commands / name).symlink_to(Path("/usr/bin") / name)
            executable = commands / "替身"
            executable.write_text(
                "#!/usr/bin/python3\n"
                "import os, pathlib, sys\n"
                "name=pathlib.Path(sys.argv[0]).name\n"
                "if name == 'timeout':\n"
                "    assert sys.argv[1:4] == ['--signal=TERM', '--kill-after=2s', '12s']\n"
                "    if sys.argv[4] == 'bluetoothctl':\n"
                "        print('逾時前保留的輸出'); sys.exit(124)\n"
                "    os.execvpe(sys.argv[4], sys.argv[4:], os.environ)\n"
                "print('替身證據：'+name)\n"
                "if name == 'ip': sys.exit(42)\n",
                encoding="utf-8",
            )
            executable.chmod(0o700)
            # nmcli 刻意缺少；所有會讀主機狀態的程式均以替身取代。
            for name in ("timeout", "uname", "cat", "bash", "lsblk", "findmnt", "df", "ip",
                         "rfkill", "bluetoothctl", "dmesg", "systemctl", "lsusb", "lspci",
                         "aplay", "arecord", "v4l2-ctl"):
                (commands / name).symlink_to(executable)
            output = root / "證據"
            result = self.invoke("--output", str(output), env={"PATH": str(commands)})
            self.assertEqual(result.returncode, 2, result.stderr)
            with (output / "command-status.tsv").open(encoding="utf-8") as stream:
                rows = {row["command_id"]: row for row in csv.DictReader(stream, delimiter="\t")}
            self.assertEqual(len(rows), 28)
            self.assertEqual(rows["identity-uname"]["collection_status"], "COLLECTED")
            self.assertEqual(rows["network-nmcli"]["collection_status"], "BLOCKED")
            self.assertEqual(rows["network-nmcli"]["exit_code"], "127")
            self.assertEqual(rows["network-link"]["collection_status"], "FAIL")
            self.assertEqual(rows["network-link"]["exit_code"], "42")
            self.assertEqual(rows["bluetooth-show"]["collection_status"], "BLOCKED")
            self.assertEqual(rows["bluetooth-show"]["exit_code"], "124")
            self.assertIn("逾時前保留的輸出", (output / "bluetooth-show.stdout.txt").read_text())
            self.assertEqual(output.stat().st_mode & 0o777, 0o700)
            for file in output.iterdir():
                self.assertEqual(file.stat().st_mode & 0o777, 0o600, file)
            hashed = set()
            for line in (output / "SHA256SUMS").read_text().splitlines():
                digest, name = line.split("  ", 1)
                file = output / name
                self.assertEqual(hashlib.sha256(file.read_bytes()).hexdigest(), digest)
                hashed.add(file.name)
            self.assertEqual(hashed, {p.name for p in output.iterdir()} - {"SHA256SUMS"})
            self.assertEqual(len(hashed), 58)


if __name__ == "__main__":
    unittest.main()
