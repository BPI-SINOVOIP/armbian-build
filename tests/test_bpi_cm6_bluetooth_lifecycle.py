#!/usr/bin/env python3
"""隔離驗證服務生命週期；使用真實 Debian helper，systemctl 完全由替身接管。"""

import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "cm6_lifecycle_package", REPO / "tools/package_bpi_cm6_bluetooth.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
UNIT = "bpi-cm6-bluetooth.service"


STUB = r'''import json
import os
from pathlib import Path
import subprocess
import sys

base = Path(os.environ["CM6_LIFECYCLE_BASE"])
root = base / "root"
command = Path(sys.argv[0]).name
args = sys.argv[1:]
with (base / "commands.jsonl").open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"command": command, "args": args}) + "\n")
if command in ("deb-systemd-helper", "deb-systemd-invoke"):
    env = dict(os.environ, DPKG_ROOT=str(root))
    env_key = "CM6_LIFECYCLE_HELPER" if command == "deb-systemd-helper" else "CM6_LIFECYCLE_INVOKE"
    raise SystemExit(subprocess.run([env[env_key], *args], env=env, timeout=5).returncode)
if command != "systemctl":
    raise SystemExit("不允許的測試命令")
arguments = [arg for arg in args if not arg.startswith("--")]
action = arguments[0]
unit = "bpi-cm6-bluetooth.service"
state_path = base / "service.json"
state = json.loads(state_path.read_text())
enabled = root / "etc/systemd/system/multi-user.target.wants" / unit
mask = root / "etc/systemd/system" / unit
masked = mask.is_symlink() and os.readlink(mask) == "/dev/null"
if action == "is-enabled":
    print("masked" if masked else "enabled" if enabled.is_symlink() else "disabled")
    raise SystemExit(0 if enabled.is_symlink() and not masked else 1)
if action == "is-active":
    raise SystemExit(0 if state["active"] else 3)
if action == "daemon-reload":
    raise SystemExit(0)
if action not in ("start", "restart", "stop") or unit not in arguments:
    raise SystemExit("尚未定義的 systemctl 測試呼叫")
if action in ("start", "restart") and masked:
    raise SystemExit(1)
state["active"] = action != "stop"
if action in ("start", "restart"):
    state["generation"] += 1
state_path.write_text(json.dumps(state))
raise SystemExit(0)
'''


@unittest.skipUnless(shutil.which("deb-systemd-helper") and shutil.which("deb-systemd-invoke"),
                     "本機缺少 Debian 服務維護工具，無法進行隔離生命週期驗證")
class BluetoothLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cm6-lifecycle-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "root"
        self.bin = self.base / "stubs"
        self.bin.mkdir()
        self.fake_systemd = self.base / "fake-systemd"
        self.fake_systemd.mkdir()
        self.unit = self.root / "usr/lib/systemd/system" / UNIT
        self.install_unit()
        (self.base / "service.json").write_text(json.dumps({"active": False, "generation": 0}))
        for name in ("systemctl", "deb-systemd-helper", "deb-systemd-invoke"):
            target = self.bin / name
            target.write_text("#!" + sys.executable + "\n" + STUB)
            target.chmod(0o755)
        self.env = {
            "PATH": str(self.bin) + os.pathsep + os.defpath,
            "LC_ALL": "C", "LANG": "C", "PYTHONDONTWRITEBYTECODE": "1",
            "DPKG_MAINTSCRIPT_PACKAGE": "bpi-cm6-bluetooth",
            "CM6_LIFECYCLE_BASE": str(self.base),
            "CM6_LIFECYCLE_HELPER": shutil.which("deb-systemd-helper"),
            "CM6_LIFECYCLE_INVOKE": shutil.which("deb-systemd-invoke"),
        }

    def install_unit(self):
        self.unit.parent.mkdir(parents=True, exist_ok=True)
        self.unit.write_bytes((REPO / "config/spacemit-k1-connectivity" / UNIT).read_bytes())

    @property
    def enabled_link(self):
        return self.root / "etc/systemd/system/multi-user.target.wants" / UNIT

    def state(self):
        return json.loads((self.base / "service.json").read_text())

    def calls(self):
        path = self.base / "commands.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def actions(self):
        actions = []
        for call in self.calls():
            if call["command"] == "systemctl":
                args = [arg for arg in call["args"] if not arg.startswith("--")]
                if args[0] in ("start", "restart", "stop"):
                    actions.append(args[0])
        return actions

    def maintain(self, content, *args, offline=False):
        # 只替換 systemd 存在性檢查；維護腳本本體與 Debian helper 判斷維持原樣。
        self.assertIn("/run/systemd/system", content)
        script = content.replace("/run/systemd/system", shlex.quote(str(self.fake_systemd)))
        env = dict(self.env)
        if offline:
            env["DPKG_ROOT"] = str(self.root)
        result = subprocess.run(["/bin/sh", "-s", "--", *args], input=script,
                                text=True, capture_output=True, env=env, timeout=15)
        self.assertEqual(result.returncode, 0, "維護腳本失敗：" + result.stderr)
        return result

    def test_fresh_install_enables_and_starts_service(self):
        self.maintain(MOD.POSTINST, "configure", "")
        self.assertTrue(self.enabled_link.is_symlink(), "首次安裝應建立自動啟動連結")
        self.assertTrue(self.state()["active"], "首次安裝應啟動服務")
        self.assertEqual(self.actions(), ["start"])

    def test_upgrade_replaces_old_process_with_new_generation(self):
        self.maintain(MOD.POSTINST, "configure", "")
        generation = self.state()["generation"]
        self.maintain(MOD.PRERM, "upgrade", "0.2")
        self.maintain(MOD.POSTRM, "upgrade", "0.2")
        self.install_unit()
        self.maintain(MOD.POSTINST, "configure", "0.1")
        self.assertTrue(self.enabled_link.is_symlink())
        self.assertTrue(self.state()["active"])
        self.assertGreater(self.state()["generation"], generation)
        self.assertEqual(self.actions()[-1], "restart", "升級完成後必須真正重新啟動程序")

    def test_remove_and_reinstall_restores_enabled_service(self):
        self.maintain(MOD.POSTINST, "configure", "")
        self.maintain(MOD.PRERM, "remove")
        self.unit.unlink()
        self.maintain(MOD.POSTRM, "remove")
        self.assertFalse(self.state()["active"])
        self.install_unit()
        self.maintain(MOD.POSTINST, "configure", "0.1")
        self.assertTrue(self.enabled_link.is_symlink(), "重裝不得遺失原本啟用狀態")
        self.assertTrue(self.state()["active"])
        self.assertEqual(self.actions(), ["start", "stop", "restart"])

    def disable_as_user(self):
        self.enabled_link.unlink()
        current = self.state()
        current["active"] = False
        (self.base / "service.json").write_text(json.dumps(current))

    def test_upgrade_preserves_user_disabled_and_stopped_service(self):
        self.maintain(MOD.POSTINST, "configure", "")
        self.disable_as_user()
        generation = self.state()["generation"]
        self.maintain(MOD.PRERM, "upgrade", "0.2")
        self.install_unit()
        self.maintain(MOD.POSTINST, "configure", "0.1")
        self.assertFalse(self.enabled_link.is_symlink(), "不得擅自恢復使用者停用的自動啟動連結")
        self.assertFalse(self.state()["active"], "不得啟動使用者已停用並停止的服務")
        self.assertEqual(self.state()["generation"], generation)

    def test_upgrade_preserves_running_service_with_autostart_disabled(self):
        self.maintain(MOD.POSTINST, "configure", "")
        # systemctl disable 只移除自動啟動連結，沒有 --now 時不停止現有程序。
        self.enabled_link.unlink()
        generation = self.state()["generation"]
        self.maintain(MOD.PRERM, "upgrade", "0.2")
        self.install_unit()
        self.maintain(MOD.POSTINST, "configure", "0.1")
        self.assertFalse(self.enabled_link.is_symlink(), "升級不得重新啟用自動啟動")
        self.assertTrue(self.state()["active"], "升級應更新原本運行中的服務，不得把停用自動啟動誤作停止要求")
        self.assertGreater(self.state()["generation"], generation, "運行中的服務必須換用更新後的程序")

    def test_remove_reinstall_preserves_user_disabled_service(self):
        self.maintain(MOD.POSTINST, "configure", "")
        self.disable_as_user()
        self.maintain(MOD.PRERM, "remove")
        self.unit.unlink()
        self.maintain(MOD.POSTRM, "remove")
        self.install_unit()
        self.maintain(MOD.POSTINST, "configure", "0.1")
        self.assertFalse(self.enabled_link.is_symlink())
        self.assertFalse(self.state()["active"])

    def test_upgrade_preserves_user_mask(self):
        self.maintain(MOD.POSTINST, "configure", "")
        self.disable_as_user()
        mask = self.root / "etc/systemd/system" / UNIT
        mask.symlink_to("/dev/null")
        self.maintain(MOD.PRERM, "upgrade", "0.2")
        self.maintain(MOD.POSTINST, "configure", "0.1")
        self.assertEqual(os.readlink(mask), "/dev/null", "不得解除使用者自行建立的遮罩")
        self.assertFalse(self.state()["active"])

    def test_purge_clears_helper_state_and_fresh_reinstall_enables(self):
        self.maintain(MOD.POSTINST, "configure", "")
        self.maintain(MOD.PRERM, "remove")
        self.unit.unlink()
        self.maintain(MOD.POSTRM, "remove")
        self.maintain(MOD.POSTRM, "purge")
        self.assertFalse(self.enabled_link.is_symlink())
        helper_state = self.root / "var/lib/systemd/deb-systemd-helper-enabled" / (UNIT + ".dsh-also")
        self.assertFalse(helper_state.exists(), "清除套件後應清除 helper 狀態")
        self.install_unit()
        self.maintain(MOD.POSTINST, "configure", "")
        self.assertTrue(self.enabled_link.is_symlink())
        self.assertTrue(self.state()["active"])

    def test_offline_dpkg_root_never_dispatches_live_service_commands(self):
        self.maintain(MOD.POSTINST, "configure", "", offline=True)
        self.maintain(MOD.PRERM, "remove", offline=True)
        self.maintain(MOD.POSTRM, "remove", offline=True)
        live = [call for call in self.calls()
                if call["command"] in ("systemctl", "deb-systemd-invoke")]
        self.assertEqual(live, [], "DPKG_ROOT 離線安裝不得對主機 systemd 派送命令")


if __name__ == "__main__":
    unittest.main()
