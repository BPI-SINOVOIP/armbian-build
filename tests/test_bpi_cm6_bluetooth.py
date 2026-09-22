#!/usr/bin/env python3
"""以假 sysfs 與來源夾具驗證 CM6 藍牙選址及拒絕條件，不碰真實硬體。"""

import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


RUNTIME = load("cm6_bluetooth_runtime", ROOT / "config/spacemit-k1-connectivity/bpi-cm6-bluetooth")
BUILD = load("cm6_bluetooth_build", ROOT / "tools/build_bpi_cm6_bluetooth.py")


class BluetoothHardwareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.sys = self.base / "sys"
        self.proc = self.base / "proc"
        self.tree = self.sys / "firmware/devicetree/base"
        self.write(self.tree / "compatible", b"bananapi,bpi-cm6\0spacemit,k1-x\0")
        self.write(self.tree / RUNTIME.UART_PATH / "compatible", b"spacemit,pxa-uart\0")
        self.write(self.tree / RUNTIME.UART_PATH / "status", b"okay\0")
        self.write(self.tree / RUNTIME.BT_PATH / "compatible", b"spacemit,bt-pwrseq\0")
        self.write(self.proc / "cmdline", b"console=ttyS0,115200 console=tty1 root=UUID=example\n")
        self.link(self.sys / "class/tty/ttyS1/device/of_node", self.tree / RUNTIME.UART_PATH)
        self.radio("rfkill7")

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def link(self, path, target):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)

    def radio(self, name, label="spacemit-bt", kind="bluetooth"):
        path = self.sys / "class/rfkill" / name
        for file, value in {"type": kind, "name": label, "hard": "0", "state": "0"}.items():
            self.write(path / file, (value + "\n").encode())
        self.link(path / "device/of_node", self.tree / RUNTIME.BT_PATH)

    def inspect(self):
        return RUNTIME.inspect_hardware(self.sys, self.proc)

    def test_selects_of_node_and_nonzero_rfkill_index(self):
        self.radio("rfkill0", "wlan", "wlan")
        result = self.inspect()
        self.assertEqual(result["tty"], "ttyS1")
        self.assertTrue(result["rfkill"].endswith("rfkill7"))
        self.assertEqual(result["hardware_validation"], "pending")

    def test_uart_name_is_resolved_instead_of_hardcoded(self):
        (self.sys / "class/tty/ttyS1").rename(self.sys / "class/tty/ttyS8")
        self.assertEqual(self.inspect()["tty"], "ttyS8")

    def test_wrong_board_is_rejected(self):
        self.write(self.tree / "compatible", b"bananapi,bpi-f3\0spacemit,k1-x\0")
        with self.assertRaisesRegex(ValueError, "板型"):
            self.inspect()

    def test_disabled_uart_is_rejected(self):
        self.write(self.tree / RUNTIME.UART_PATH / "status", b"disabled\0")
        with self.assertRaisesRegex(ValueError, "未啟用"):
            self.inspect()

    def test_wrong_uart_compatible_is_rejected(self):
        self.write(self.tree / RUNTIME.UART_PATH / "compatible", b"other,uart\0")
        with self.assertRaisesRegex(ValueError, "相容"):
            self.inspect()

    def test_cmdline_console_is_rejected(self):
        self.write(self.proc / "cmdline", b"console=ttyS1,115200\n")
        with self.assertRaisesRegex(ValueError, "主控台"):
            self.inspect()

    def test_active_console_is_rejected(self):
        self.write(self.sys / "class/tty/console/active", b"tty1 ttyS1\n")
        with self.assertRaisesRegex(ValueError, "主控台"):
            self.inspect()

    def test_stdout_alias_is_rejected(self):
        self.write(self.tree / "chosen/stdout-path", b"serial1:115200n8\0")
        self.write(self.tree / "aliases/serial1", b"/soc/uart@d4017100\0")
        with self.assertRaisesRegex(ValueError, "stdout-path"):
            self.inspect()

    def test_existing_hci_is_not_overwritten(self):
        (self.sys / "class/bluetooth/hci0").mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, "已有 HCI"):
            self.inspect()

    def test_multiple_uart_candidates_are_rejected(self):
        self.link(self.sys / "class/tty/ttyS2/device/of_node", self.tree / RUNTIME.UART_PATH)
        with self.assertRaisesRegex(ValueError, "UART 候選數量"):
            self.inspect()

    def test_missing_uart_is_rejected(self):
        (self.sys / "class/tty/ttyS1/device/of_node").unlink()
        with self.assertRaisesRegex(ValueError, "UART 候選數量"):
            self.inspect()

    def test_multiple_rfkill_candidates_are_rejected(self):
        self.radio("rfkill9")
        with self.assertRaisesRegex(ValueError, "電源候選數量"):
            self.inspect()

    def test_rfkill_name_and_type_are_required(self):
        self.write(self.sys / "class/rfkill/rfkill7/name", b"other-bt\n")
        with self.assertRaisesRegex(ValueError, "電源候選數量"):
            self.inspect()

    def test_rfkill_wrong_of_node_is_rejected(self):
        link = self.sys / "class/rfkill/rfkill7/device/of_node"
        link.unlink()
        link.symlink_to(self.tree / RUNTIME.UART_PATH)
        with self.assertRaisesRegex(ValueError, "電源候選數量"):
            self.inspect()

    def test_hard_block_is_rejected(self):
        self.write(self.sys / "class/rfkill/rfkill7/hard", b"1\n")
        with self.assertRaisesRegex(ValueError, "硬體封鎖"):
            self.inspect()

    def test_regular_file_cannot_impersonate_uart(self):
        self.write(self.base / "dev/ttyS1", b"")
        with self.assertRaisesRegex(ValueError, "字元裝置"):
            RUNTIME.check_device("ttyS1", self.sys, self.base / "dev")

    def test_open_descriptor_blocks_attach(self):
        device = self.base / "fake-uart"
        device.write_bytes(b"")
        self.link(self.proc / "123/fd/3", device)
        with self.assertRaisesRegex(ValueError, "123.*占用"):
            RUNTIME.check_busy(device, self.proc)

    def test_disappearing_process_does_not_claim_uart(self):
        device = self.base / "fake-uart"
        device.write_bytes(b"")
        self.link(self.proc / "123/fd/3", self.base / "missing")
        RUNTIME.check_busy(device, self.proc)

    def test_wrong_board_stops_before_mutating_operations(self):
        with mock.patch.object(RUNTIME.os, "geteuid", return_value=0), \
                mock.patch.object(RUNTIME, "inspect_hardware", side_effect=ValueError("板型不符")), \
                mock.patch.object(RUNTIME.os, "open") as opening, \
                mock.patch.object(RUNTIME.subprocess, "run") as command:
            with self.assertRaisesRegex(ValueError, "板型"):
                RUNTIME.start()
            opening.assert_not_called()
            command.assert_not_called()

    def test_start_resets_selected_radio_then_executes_foreground(self):
        selection = self.inspect()
        device = self.base / "fake-uart"
        device.write_bytes(b"")
        events = []
        state = self.sys / "class/rfkill/rfkill7/state"
        original_write = Path.write_text

        def write(path, value, *args, **kwargs):
            self.assertEqual(path, state)
            events.append(("state", value))
            return original_write(path, value, *args, **kwargs)

        def execute(path, argv):
            events.append(("exec", path, argv))

        with mock.patch.object(RUNTIME.os, "geteuid", return_value=0), \
                mock.patch.object(RUNTIME, "inspect_hardware", return_value=selection), \
                mock.patch.object(RUNTIME, "check_device", return_value=device), \
                mock.patch.object(RUNTIME, "check_busy"), \
                mock.patch.object(RUNTIME, "verify_assets"), \
                mock.patch.object(RUNTIME.os, "open", return_value=999), \
                mock.patch.object(RUNTIME.os, "close"), \
                mock.patch.object(RUNTIME.os, "set_inheritable"), \
                mock.patch.object(RUNTIME.fcntl, "flock"), \
                mock.patch.object(RUNTIME.subprocess, "run") as command, \
                mock.patch.object(RUNTIME.time, "sleep", side_effect=lambda delay: events.append(("sleep", delay))), \
                mock.patch.object(RUNTIME.os, "execv", side_effect=execute), \
                mock.patch.object(Path, "write_text", write), \
                mock.patch("builtins.print"):
            RUNTIME.start()
        self.assertEqual(events[:3], [("state", "0\n"), ("sleep", 1), ("state", "1\n")])
        self.assertEqual(events[3], ("exec", str(RUNTIME.BINARY),
                         [str(RUNTIME.BINARY), "-n", "-s", "115200", str(device), "rtk_h5"]))
        command.assert_called_once_with(["/usr/sbin/modprobe", "hci_uart"], check=True, timeout=15)

    def test_debian_unit_uses_owned_path_and_bounded_restart(self):
        unit = (ROOT / "config/spacemit-k1-connectivity/bpi-cm6-bluetooth.service").read_text()
        self.assertIn("ExecStart=/usr/sbin/bpi-cm6-bluetooth --start", unit)
        self.assertIn("RestartPreventExitStatus=2", unit)
        self.assertIn("StartLimitBurst=2", unit)
        self.assertIn("Before=bluetooth.service", unit)
        self.assertEqual(str(RUNTIME.BINARY), "/usr/lib/bpi-cm6-bluetooth/rtk_hciattach")


class BluetoothSourceTests(unittest.TestCase):
    def archive(self, names=None, link=False):
        buffer = io.BytesIO()
        commit = "a" * 40
        prefix = "rtk_hciattach-" + commit
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            for name in names or sorted(BUILD.SOURCE_FILES):
                entry = tarfile.TarInfo(prefix + "/" + name)
                if link:
                    entry.type = tarfile.SYMTYPE
                    entry.linkname = "/etc/passwd"
                    archive.addfile(entry)
                else:
                    entry.size = 1
                    archive.addfile(entry, io.BytesIO(b"0"))
        data = buffer.getvalue()
        return data, {"commit": commit, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def test_complete_locked_source_is_accepted(self):
        data, source = self.archive()
        self.assertEqual(set(BUILD.validate_archive(data, source)), BUILD.SOURCE_FILES)

    def test_wrong_source_sha_is_rejected(self):
        data, source = self.archive()
        source["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            BUILD.validate_archive(data, source)

    def test_missing_source_file_is_rejected(self):
        data, source = self.archive(["hciattach.c"])
        with self.assertRaisesRegex(ValueError, "缺少"):
            BUILD.validate_archive(data, source)

    def test_path_escape_is_rejected_even_with_matching_archive_sha(self):
        data, source = self.archive(["../../outside"])
        with self.assertRaisesRegex(ValueError, "不安全"):
            BUILD.validate_archive(data, source)

    def test_source_symlink_is_rejected(self):
        data, source = self.archive(["hciattach.c"], link=True)
        with self.assertRaisesRegex(ValueError, "連結"):
            BUILD.validate_archive(data, source)

    def test_wrong_elf_architecture_is_rejected(self):
        data = bytearray(64)
        data[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", data, 18, 183)
        struct.pack_into("<I", data, 48, 5)
        with self.assertRaisesRegex(ValueError, "RISC-V"):
            BUILD.validate_elf(data)

    def test_riscv_wrong_float_abi_is_rejected(self):
        data = bytearray(64)
        data[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", data, 18, 243)
        with self.assertRaisesRegex(ValueError, "ABI"):
            BUILD.validate_elf(data)
        struct.pack_into("<I", data, 48, 5)
        self.assertEqual(BUILD.validate_elf(data)["abi"], "lp64d")

    def test_tampered_local_patch_is_rejected(self):
        record = json.loads(BUILD.LOCK.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for item in record["patches"]:
                data = (BUILD.LOCK.parent / item["path"]).read_bytes()
                (base / item["path"]).write_bytes(data + b"\n")
            with self.assertRaisesRegex(ValueError, "補丁 SHA-256"):
                BUILD.validate_patches(record, base)

    def test_missing_local_patch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "必須提供"):
            BUILD.validate_patches({}, BUILD.LOCK.parent)

    def test_patch_targets_and_order_remain_fixed(self):
        record = json.loads(BUILD.LOCK.read_text())
        self.assertEqual(len(BUILD.validate_patches(record, BUILD.LOCK.parent)), 2)
        record["patches"][1]["target"] = "hciattach.c"
        with self.assertRaisesRegex(ValueError, "目標"):
            BUILD.validate_patches(record, BUILD.LOCK.parent)
        record["patches"][1]["target"] = "../../outside"
        with self.assertRaisesRegex(ValueError, "目標"):
            BUILD.validate_patches(record, BUILD.LOCK.parent)
        record["patches"].reverse()
        with self.assertRaisesRegex(ValueError, "固定順序"):
            BUILD.validate_patches(record, BUILD.LOCK.parent)

    def test_python_help_is_traditional_chinese(self):
        for path in (BUILD.__file__, RUNTIME.__file__):
            with self.subTest(path=path):
                result = subprocess.run(["python3", path, "--help"], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0)
                self.assertIn("用法：", result.stdout)
                self.assertIn("選項:", result.stdout)
                self.assertIn("顯示此說明後結束", result.stdout)
                for text in ("usage:", "options:", "show this help"):
                    self.assertNotIn(text, result.stdout)


class BluetoothFirmwareSourceTests(unittest.TestCase):
    def archive(self, kind=None, missing_copyright=False, duplicate=False):
        record = json.loads(BUILD.LOCK.read_text())
        payloads = {"rtl8852bs_fw": b"firmware-fixture", "rtl8852bs_config": b"config-fixture",
                    "copyright": "原授權追溯測試檔".encode()}
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:xz") as archive:
            selected = []
            for field, name in BUILD.FIRMWARE_NAMES.items():
                item = record["hardware_contract"][field]
                item.update(bytes=len(payloads[name]), sha256=BUILD.sha(payloads[name]))
                selected.append((record["firmware_source"]["selected_paths"][field], name))
            copyright_record = record["firmware_source"]["copyright"]
            copyright_record.update(bytes=len(payloads["copyright"]), sha256=BUILD.sha(payloads["copyright"]))
            if not missing_copyright:
                selected.append((copyright_record["path"], "copyright"))
            if duplicate:
                selected.append(selected[0])
            for path, name in selected:
                member = tarfile.TarInfo(path)
                if name == "rtl8852bs_fw" and kind is not None:
                    member.type = kind
                    member.linkname = "/etc/passwd"
                    archive.addfile(member)
                else:
                    member.size = len(payloads[name])
                    archive.addfile(member, io.BytesIO(payloads[name]))
        data = buffer.getvalue()
        record["firmware_source"].update(bytes=len(data), sha256=BUILD.sha(data))
        return data, record, payloads

    def test_fixed_firmware_and_original_copyright_are_selected(self):
        data, record, expected = self.archive()
        self.assertEqual(BUILD.validate_firmware_archive(data, record), expected)

    def test_archive_and_asset_hashes_are_both_checked(self):
        data, record, _ = self.archive()
        with self.assertRaisesRegex(ValueError, "壓縮檔大小或 SHA-256"):
            BUILD.validate_firmware_archive(data + b"\0", record)
        record["hardware_contract"]["firmware"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "成員大小或 SHA-256"):
            BUILD.validate_firmware_archive(data, record)

    def test_selected_file_size_and_copyright_hash_are_checked(self):
        data, record, _ = self.archive()
        record["hardware_contract"]["firmware_config"]["bytes"] += 1
        with self.assertRaisesRegex(ValueError, "成員大小"):
            BUILD.validate_firmware_archive(data, record)
        data, record, _ = self.archive()
        record["firmware_source"]["copyright"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "成員大小或 SHA-256"):
            BUILD.validate_firmware_archive(data, record)

    def test_selected_links_and_directories_are_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE):
            with self.subTest(kind=kind):
                data, record, _ = self.archive(kind=kind)
                with self.assertRaisesRegex(ValueError, "一般檔案"):
                    BUILD.validate_firmware_archive(data, record)

    def test_missing_copyright_and_duplicate_asset_are_rejected(self):
        data, record, _ = self.archive(missing_copyright=True)
        with self.assertRaisesRegex(ValueError, "缺少"):
            BUILD.validate_firmware_archive(data, record)
        data, record, _ = self.archive(duplicate=True)
        with self.assertRaisesRegex(ValueError, "重複"):
            BUILD.validate_firmware_archive(data, record)

    def test_archive_member_and_destination_paths_are_fixed(self):
        data, record, _ = self.archive()
        record["firmware_source"]["selected_paths"]["firmware"] = "../../outside"
        with self.assertRaisesRegex(ValueError, "路徑"):
            BUILD.validate_firmware_archive(data, record)
        data, record, _ = self.archive()
        record["hardware_contract"]["firmware"]["path"] = "/lib/firmware/rtlbt/rtl8852bs_fw"
        with self.assertRaisesRegex(ValueError, "私有目錄"):
            BUILD.validate_firmware_archive(data, record)

    def test_local_archive_requires_regular_file_and_exact_size(self):
        data, record, _ = self.archive()
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "firmware.tar.xz"
            source.write_bytes(data)
            self.assertEqual(BUILD.load_archive(source, record["firmware_source"]), data)
            linked = source.with_name("link.tar.xz")
            linked.symlink_to(source)
            with self.assertRaisesRegex(ValueError, "一般檔案"):
                BUILD.load_archive(linked, record["firmware_source"])
            source.write_bytes(data + b"\0")
            with self.assertRaisesRegex(ValueError, "大小"):
                BUILD.load_archive(source, record["firmware_source"])


class BluetoothPatchedHelperTests(unittest.TestCase):
    """實際執行打補丁後的 C 程式，所有 UART 系統呼叫均由假介面接管。"""

    @classmethod
    def setUpClass(cls):
        archive = Path(os.environ.get("BPI_CM6_BT_SOURCE_ARCHIVE",
                       "/media/pi/SMCI/bpi/f3-cm6-connectivity-20260922/bluetooth-build/source.tar.gz"))
        if not archive.is_file() or shutil.which("cc") is None:
            raise unittest.SkipTest("此離線 C 回歸需要固定官方來源壓縮檔及主機 C 編譯器")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.base = Path(cls.tmp.name)
        source = cls.base / "source"
        source.mkdir()
        record = json.loads(BUILD.LOCK.read_text())
        files = BUILD.validate_archive(archive.read_bytes(), record["source"])
        for name, data in files.items():
            (source / name).write_bytes(data)
        BUILD.apply_patches(source, cls.base, BUILD.validate_patches(record, BUILD.LOCK.parent))
        cls.patched_source = (source / "hciattach.c").read_text()
        cls.firmware_original = files["rtb_fwc.c"].decode()
        cls.firmware_patched = (source / "rtb_fwc.c").read_text()
        cls.uart = cls.base / "fake-uart"
        cls.uart.write_bytes(b"")
        harness = cls.base / "fake-uart.c"
        harness.write_text(r'''
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <termios.h>
#include <time.h>
int __real_open(const char *, int, ...);
int __wrap_open(const char *path, int flags, ...) {
    static int calls;
    const char *allowed = getenv("TEST_UART");
    if (!allowed || strcmp(path, allowed)) {
        fprintf(stderr, "測試拒絕未授權路徑\n");
        exit(77);
    }
    if (++calls != 1) {
        fprintf(stderr, "測試拒絕 helper 自行重啟 UART\n");
        exit(78);
    }
    return __real_open(path, flags);
}
int __wrap_tcgetattr(int fd, struct termios *value) {
    memset(value, 0, sizeof(*value));
    return 0;
}
int __wrap_tcsetattr(int fd, int action, const struct termios *value) { return 0; }
int __wrap_tcflush(int fd, int action) { return 0; }
int __wrap_ioctl(int fd, unsigned long request, ...) { return 0; }
int __wrap_ppoll(struct pollfd *fds, nfds_t count,
                 const struct timespec *timeout, const sigset_t *mask) {
    const char *event = getenv("TEST_EVENT");
    if (!strcmp(event, "STOP")) {
        raise(SIGTERM);
        errno = EINTR;
        return -1;
    }
    if (!strcmp(event, "ERROR")) {
        errno = EIO;
        return -1;
    }
    fds[0].revents = !strcmp(event, "HUP") ? POLLHUP :
                     !strcmp(event, "INVALID") ? POLLNVAL : POLLERR;
    return 1;
}
''')
        cls.binary = cls.base / "helper-test"
        command = ["cc", "-O0", *[str(source / name) for name in BUILD.SOURCES], str(harness),
                   "-Wl,--wrap=open,--wrap=tcgetattr,--wrap=tcsetattr,--wrap=tcflush,--wrap=ioctl,--wrap=ppoll",
                   "-o", str(cls.binary)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise AssertionError("C 假 UART 回歸編譯失敗：" + result.stderr)

    def run_event(self, event):
        return subprocess.run([str(self.binary), "-n", str(self.uart), "any"],
                              capture_output=True, text=True, timeout=5,
                              env={**os.environ, "TEST_UART": str(self.uart), "TEST_EVENT": event})

    def test_uart_failure_exits_nonzero_without_internal_power_reset(self):
        for event in ("HUP", "INVALID", "POLLERR", "ERROR"):
            with self.subTest(event=event):
                result = self.run_event(event)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn("交由服務重新初始化", result.stdout + result.stderr)
                self.assertNotIn("測試拒絕", result.stdout + result.stderr)
        unit = (ROOT / "config/spacemit-k1-connectivity/bpi-cm6-bluetooth.service").read_text()
        self.assertIn("Restart=on-failure", unit)
        self.assertIn("RestartPreventExitStatus=2", unit)

    def test_sigterm_still_exits_cleanly(self):
        result = self.run_event("STOP")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("交由服務重新初始化", result.stdout + result.stderr)

    def test_patched_source_has_no_fixed_rfkill_path(self):
        for text in ("RFKILL_NODE", "reset_bluetooth", "/sys/class/rfkill/", "goto start;"):
            self.assertNotIn(text, self.patched_source)

    def test_firmware_patch_only_changes_two_directory_definitions(self):
        expected = self.firmware_original.replace('"/lib/firmware/rtlbt/"',
                                                 '"/usr/lib/bpi-cm6-bluetooth/firmware/"')
        self.assertEqual(self.firmware_patched, expected)
        changed = [(before, after) for before, after in
                   zip(self.firmware_original.splitlines(), self.firmware_patched.splitlines()) if before != after]
        self.assertEqual(len(changed), 2)
        for name in ("FIRMWARE_DIRECTORY", "BT_CONFIG_DIRECTORY"):
            self.assertTrue(any(before.startswith("#define " + name) for before, _ in changed))
        self.assertIn(BUILD.PRIVATE_FIRMWARE_DIRECTORY.encode(), self.binary.read_bytes())

    def test_each_patch_has_independent_build_logs(self):
        for name in BUILD.PATCH_TARGETS:
            self.assertTrue((self.base / "patches" / (name + ".stdout")).is_file())
            self.assertTrue((self.base / "patches" / (name + ".stderr")).is_file())


if __name__ == "__main__":
    unittest.main()
