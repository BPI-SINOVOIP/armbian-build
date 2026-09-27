#!/usr/bin/env python3
"""執行真實首次登入來源的函式與決策；僅用合成帳號、暫存標記及隔離命令。

直接從本倉來源取得受測程式，核對正常設定與輸入錯誤的處理。
不操作主機帳號、真實標記、串口或板子；PTY 只驗本機終端語義。
"""

import os
from pathlib import Path
import pty
import select
import signal
import subprocess
import tempfile
import termios
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "packages/bsp/common/usr/lib/armbian/armbian-firstlogin").read_text()


def section(start, end):
    beginning = SOURCE.index(start)
    return SOURCE[beginning:SOURCE.index(end, beginning)]


READER = section('read_password() {', '\nset_shell() {')
CAN_COMPLETE = section('_firstlogin_can_complete() {', '\n_firstlogin_cleanup() {')
CLEANUP = section('_firstlogin_cleanup() {', '\ntrap _firstlogin_cleanup EXIT')
ABORT = section('check_abort() {', '\nmask2cidr() {')
INTERRUPTED = section('_firstlogin_interrupted() {', '\ntrap _firstlogin_interrupted')
ADD_USER = section('add_user() {', '\nset_user_icon() {')
GATE_VALUES = section('FIRSTLOGIN_SUCCESS=0\n', '_firstlogin_can_complete() {')
ROOT_DECISION = section('\t\tif [[ -z "$first_input" || -z "$second_input" ]]; then', '\tdone\n\ttrap - INT')

STUBS = r'''
event() { printf '%s\n' "$1" >> "$CASE_ROOT/events"; }
passwd() {
    if [[ "$1" == -d ]]; then
        event passwd_delete
    else
        event passwd_set
        local first second
        IFS= read -r first || :
        IFS= read -r second || :
        [[ "$first" == "$MOCK_FIRST" && "$second" == "$MOCK_SECOND" ]] && event input_preserved
    fi
    return "$MOCK_STATUS"
}
read_password() {
    READ_COUNT=$(( ${READ_COUNT:-0} + 1 ))
    event password_read
    [[ "$READ_COUNT" == "${READ_FAIL_AT:-0}" ]] && return 1
    if (( READ_COUNT % 2 )); then password="$MOCK_FIRST"; else password="$MOCK_SECOND"; fi
    return 0
}
awk() {
    if [[ "$*" == *nobody* ]]; then
        [[ -f "$CASE_ROOT/user-created" ]] && printf 'fixtureuser\n'
    else
        printf '測試顯示名稱\n'
    fi
    return 0
}
function cracklib-check() { printf '合成資料: OK\n'; }
grep() { return 0; }
tr() { local text; IFS= read -r text || :; printf '%s\n' "$text"; }
id() { [[ -f "$CASE_ROOT/user-created" ]]; }
useradd() {
    event useradd
    : > "$CASE_ROOT/user-created"
    /bin/mkdir -p "$CASE_ROOT/home/fixtureuser"
}
getent() { return 0; }
usermod() { event usermod; }
set_user_icon() { event user_icon; }
touch() { return 0; }
chown() { return 0; }
cut() { local text; IFS= read -r text || :; printf '%s\n' "$text"; }
sync() { return 0; }
rm() {
    [[ "$*" == "-f $CASE_ROOT/marker" ]] || return 97
    event marker_removed
    /bin/rm -- "$CASE_ROOT/marker"
}
readonly -f passwd read_password awk cracklib-check grep tr id useradd getent usermod set_user_icon touch chown cut sync rm
'''


class LocalCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cm6-firstlogin-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        (self.path / 'marker').touch(mode=0o600)
        (self.path / 'events').touch(mode=0o600)
        (self.path / 'etc/sudoers.d').mkdir(parents=True)
        (self.path / 'etc/passwd').write_text('fixtureuser:x:1000:1000:合成帳號:/fixture:/bin/bash\n')

    def execute(self, body, environment=None):
        env = {'PATH': '/nonexistent', 'LANG': 'C.UTF-8', 'CASE_ROOT': str(self.path),
               'MOCK_FIRST': 'fixture-only', 'MOCK_SECOND': 'fixture-only', 'MOCK_STATUS': '0',
               'PRESET_ROOT_PASSWORD': '', 'PRESET_USER_PASSWORD': '', 'PRESET_USER_NAME': 'fixtureuser',
               'PRESET_USER_KEY': '', 'PRESET_DEFAULT_REALNAME': '測試顯示名稱', 'SHELL_PATH': '/bin/bash'}
        env.update(environment or {})
        cleanup = CLEANUP.replace('/root/.not_logged_in_yet', str(self.path / 'marker'))
        script = STUBS + GATE_VALUES + CAN_COMPLETE + '\n' + cleanup
        script += "\ntrap _firstlogin_cleanup EXIT\n"
        script += body
        return subprocess.run(['/bin/bash', '--noprofile', '--norc', '-c', script], env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=4)

    def events(self):
        return (self.path / 'events').read_text().splitlines()

    def user(self, environment=None, fail_late=False):
        user = ADD_USER.replace('/root/.not_logged_in_yet', str(self.path / 'marker'))
        user = user.replace('/home/', str(self.path / 'home') + '/')
        user = user.replace('/etc/', str(self.path / 'etc') + '/')
        extra = 'psd() { return 0; }; atomic_write() { return 1; };\n' if fail_late else ''
        return self.execute(extra + user + r'''
ROOT_PASSWORD_READY=1
add_user
status=$?
printf 'user_status=%s\nuser_complete=%s\n' "$status" "$USER_SETUP_COMPLETE" >> "$CASE_ROOT/events"
if [[ "$status" == 0 ]] && _firstlogin_can_complete; then FIRSTLOGIN_SUCCESS=1; fi
exit "$status"
''', environment)


class UserTests(LocalCase):
    def test_nonempty_password_failure_does_not_grant_groups_or_claim_completion(self):
        result = self.user({'MOCK_STATUS': '10'})
        self.assertEqual(result.returncode, 1)
        self.assertIn('useradd', self.events())
        self.assertIn('passwd_set', self.events())
        self.assertNotIn('usermod', self.events())
        self.assertNotIn('user_complete=1', self.events())
        self.assertNotIn('密碼設定已完成', result.stdout)
        self.assertTrue((self.path / 'marker').exists())

    def test_failed_created_account_is_reused_next_login_without_duplicate_useradd(self):
        self.assertEqual(self.user({'MOCK_STATUS': '10'}).returncode, 1)
        self.assertTrue((self.path / 'marker').exists())
        result = self.user()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(self.events().count('useradd'), 1)
        self.assertEqual(self.events().count('passwd_set'), 2)
        self.assertIn('user_complete=1', self.events())
        self.assertFalse((self.path / 'marker').exists())

    def test_explicit_empty_password_policy_still_deletes_password_on_success(self):
        result = self.user({'MOCK_FIRST': '', 'MOCK_SECOND': ''})
        self.assertEqual(result.returncode, 0)
        self.assertIn('passwd_delete', self.events())
        self.assertNotIn('passwd_set', self.events())
        self.assertIn('user_complete=1', self.events())
        self.assertFalse((self.path / 'marker').exists())

    def test_empty_password_command_failure_keeps_incomplete_account_and_marker(self):
        result = self.user({'MOCK_FIRST': '', 'MOCK_SECOND': '', 'MOCK_STATUS': '10'})
        self.assertEqual(result.returncode, 1)
        self.assertIn('passwd_delete', self.events())
        self.assertNotIn('usermod', self.events())
        self.assertNotIn('密碼設定已完成', result.stdout)
        self.assertTrue((self.path / 'marker').exists())

    def test_eof_at_either_password_prompt_never_means_empty_password(self):
        for at in ('1', '2'):
            with self.subTest(prompt=at):
                result = self.user({'READ_FAIL_AT': at, 'MOCK_FIRST': '', 'MOCK_SECOND': ''})
                self.assertEqual(result.returncode, 1)
                self.assertNotIn('passwd_delete', self.events())
                self.assertNotIn('useradd', self.events())
                self.assertTrue((self.path / 'marker').exists())

    def test_eof_at_username_or_display_name_preserves_marker(self):
        for environment in ({'PRESET_USER_NAME': ''}, {'PRESET_DEFAULT_REALNAME': ''}):
            with self.subTest(environment=environment):
                result = self.user(environment)
                self.assertEqual(result.returncode, 1)
                self.assertNotIn('useradd', self.events())
                self.assertTrue((self.path / 'marker').exists())

    def test_mismatch_with_empty_second_input_has_bounded_failure(self):
        result = self.user({'MOCK_SECOND': ''})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.events().count('password_read'), 6)
        self.assertNotIn('useradd', self.events())
        self.assertTrue((self.path / 'marker').exists())

    def test_later_configuration_failure_does_not_print_account_completed(self):
        result = self.user(fail_late=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('passwd_set', self.events())
        self.assertNotIn('密碼設定已完成', result.stdout)
        self.assertTrue((self.path / 'marker').exists())


class RootAndGateTests(LocalCase):
    def root(self, environment=None):
        body = r'''
REPEATS=3
while :; do
    first_input="$MOCK_FIRST"; second_input="$MOCK_SECOND"; password="$MOCK_SECOND"
''' + ROOT_DECISION + r'''
done
printf 'root_ready=%s\nadvanced\n' "$ROOT_PASSWORD_READY" >> "$CASE_ROOT/events"
'''
        return self.execute(body, environment)

    def test_root_empty_input_never_calls_passwd(self):
        result = self.root({'MOCK_FIRST': '', 'MOCK_SECOND': ''})
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('passwd_set', self.events())
        self.assertNotIn('advanced', self.events())
        self.assertTrue((self.path / 'marker').exists())

    def test_root_passwd_failure_does_not_advance(self):
        result = self.root({'MOCK_STATUS': '10'})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.events().count('passwd_set'), 3)
        self.assertNotIn('advanced', self.events())

    def test_root_automated_failure_is_not_retried(self):
        result = self.root({'MOCK_STATUS': '10', 'PRESET_ROOT_PASSWORD': '合成預設值'})
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.events().count('passwd_set'), 1)

    def test_root_success_preserves_literal_input_and_waits_for_user_completion(self):
        result = self.root({'MOCK_FIRST': '-n', 'MOCK_SECOND': '-n'})
        self.assertEqual(result.returncode, 0)
        self.assertIn('input_preserved', self.events())
        self.assertIn('root_ready=1', self.events())
        self.assertTrue((self.path / 'marker').exists())

    def test_cleanup_rejects_falsely_set_success_for_incomplete_user(self):
        result = self.execute('ROOT_PASSWORD_READY=1; USER_SETUP_STARTED=1; FIRSTLOGIN_SUCCESS=1; exit 0\n')
        self.assertEqual(result.returncode, 0)
        self.assertTrue((self.path / 'marker').exists())
        self.assertNotIn('marker_removed', self.events())

    def test_cleanup_requires_root_success_even_if_user_complete(self):
        self.execute('USER_SETUP_COMPLETE=1; FIRSTLOGIN_SUCCESS=1; exit 0\n')
        self.assertTrue((self.path / 'marker').exists())

    def test_cleanup_after_both_successes_removes_marker(self):
        self.execute('ROOT_PASSWORD_READY=1; USER_SETUP_STARTED=1; USER_SETUP_COMPLETE=1; FIRSTLOGIN_SUCCESS=1; exit 0\n')
        self.assertFalse((self.path / 'marker').exists())

    def test_explicit_skip_before_user_setup_preserves_existing_policy(self):
        result = self.execute(ABORT + '\nROOT_PASSWORD_READY=1; check_abort\n')
        self.assertEqual(result.returncode, 0)
        self.assertFalse((self.path / 'marker').exists())

    def test_abort_after_user_setup_started_keeps_marker_for_next_login(self):
        result = self.execute(ABORT + '\nROOT_PASSWORD_READY=1; USER_SETUP_STARTED=1; check_abort\n')
        self.assertEqual(result.returncode, 1)
        self.assertTrue((self.path / 'marker').exists())


class TerminalTests(unittest.TestCase):
    def test_partial_input_eof_is_not_confirmation(self):
        result = subprocess.run(['/bin/bash', '--noprofile', '--norc', '-c', READER + '\nread_password 測試'],
                                input=b'fixture-only', env={'PATH': '/nonexistent', 'LANG': 'C.UTF-8'},
                                capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 1)

    def test_reader_has_no_manual_stty_and_eof_returns_nonzero(self):
        self.assertNotIn('\tstty ', READER)
        result = subprocess.run(['/bin/bash', '--noprofile', '--norc', '-c', READER + '\nread_password 測試'],
                                stdin=subprocess.DEVNULL, env={'PATH': '/nonexistent', 'LANG': 'C.UTF-8'},
                                capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 1)

    def pty_case(self, input_data=None, interrupt=None, initial_echo=True, completed=False, user_abort=False):
        master, slave = pty.openpty()
        process = None
        try:
            saved = termios.tcgetattr(slave)
            expected = list(saved)
            expected[3] = expected[3] | termios.ECHO if initial_echo else expected[3] & ~termios.ECHO
            termios.tcsetattr(slave, termios.TCSANOW, expected)
            script = GATE_VALUES + CAN_COMPLETE + '\n_firstlogin_cleanup() { :; }\n' + INTERRUPTED
            script += '\ntrap _firstlogin_interrupted HUP INT TERM\n'
            if completed:
                script += 'ROOT_PASSWORD_READY=1; USER_SETUP_STARTED=1; USER_SETUP_COMPLETE=1; FIRSTLOGIN_SUCCESS=1\n'
            if user_abort:
                script += ABORT + '\nROOT_PASSWORD_READY=1; USER_SETUP_STARTED=1; trap check_abort INT\n'
            script += READER + '\nread_password 測試\nexit $?\n'
            process = subprocess.Popen(['/bin/bash', '--noprofile', '--norc', '-c', script],
                                       stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
                                       env={'PATH': '/nonexistent', 'LANG': 'C.UTF-8'})
            received = b''
            deadline = time.monotonic() + 3
            while '密碼：'.encode() not in received:
                self.assertLess(time.monotonic(), deadline, '偽終端未進入密碼讀取')
                ready, _, _ = select.select([master], [], [], .1)
                if ready:
                    received += os.read(master, 4096)
            # 提示輸出可能早於 read 完成訊號／終端設定；只在本次子程序確實
            # 阻塞於 stdin 的讀取時送入事件，避免把測試啟動競態誤判為終端缺陷。
            read_syscall = {'x86_64': '0', 'aarch64': '63', 'riscv64': '63'}.get(os.uname().machine)
            self.assertIsNotNone(read_syscall, '本機架構尚未定義 PTY 測試的 read 系統呼叫編號')
            deadline = time.monotonic() + 3
            while True:
                state = Path(f'/proc/{process.pid}/syscall').read_text().split()
                if len(state) >= 2 and state[0] == read_syscall and state[1] == '0x0':
                    break
                self.assertLess(time.monotonic(), deadline, '偽終端尚未進入 stdin 讀取')
                time.sleep(.005)
            if interrupt is not None:
                process.send_signal(interrupt)
            else:
                os.write(master, input_data)
            status = process.wait(timeout=3)
            actual = termios.tcgetattr(slave)
            self.assertEqual(actual, expected, '讀取後終端旗標未還原原狀')
            while select.select([master], [], [], 0)[0]:
                received += os.read(master, 4096)
            if input_data and b'fixture-only' in input_data:
                self.assertNotIn(b'fixture-only', received)
            return status
        finally:
            if process is not None and process.poll() is None:
                process.kill(); process.wait(timeout=3)
            os.close(master); os.close(slave)

    def test_pty_normal_input_restores_echo(self):
        self.assertEqual(self.pty_case(b'fixture-only\n'), 0)

    def test_pty_original_echo_off_is_preserved(self):
        self.assertEqual(self.pty_case(b'fixture-only\n', initial_echo=False), 0)

    def test_pty_eof_restores_terminal_and_returns_failure(self):
        self.assertEqual(self.pty_case(b'\x04'), 1)

    def test_pty_term_restores_terminal_and_returns_failure(self):
        self.assertEqual(self.pty_case(interrupt=signal.SIGTERM), 1)

    def test_pty_hup_restores_terminal_and_returns_failure(self):
        self.assertEqual(self.pty_case(interrupt=signal.SIGHUP), 1)

    def test_pty_signal_after_completed_setup_keeps_success_status(self):
        self.assertEqual(self.pty_case(interrupt=signal.SIGHUP, completed=True), 0)

    def test_pty_user_ctrl_c_restores_terminal_and_keeps_incomplete_status(self):
        self.assertEqual(self.pty_case(interrupt=signal.SIGINT, user_abort=True), 1)


class SourceTests(unittest.TestCase):
    def test_full_script_bash_syntax_without_execution(self):
        result = subprocess.run(['/bin/bash', '--noprofile', '--norc', '-n'], input=SOURCE, text=True,
                                env={'PATH': '/nonexistent', 'LANG': 'C.UTF-8'}, capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0)

    def test_root_and_user_calls_check_reader_result_and_final_gate(self):
        calls = [line for line in SOURCE.splitlines() if 'read_password "' in line]
        self.assertEqual(len(calls), 4)
        self.assertTrue(all(' || {' in line for line in calls))
        self.assertIn("_firstlogin_can_complete || { printf '首次設定尚未完成，保留標記。", SOURCE)


if __name__ == '__main__':
    os.umask(0o077)
    unittest.main()
