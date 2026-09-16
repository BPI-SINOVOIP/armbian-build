#!/usr/bin/env python3
"""獨立救援建置的離線契約；不連線、不掛載、不讀寫真實區塊裝置。"""

import argparse
import base64
from contextlib import ExitStack
import hashlib
import importlib.util
import io
import json
import lzma
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import build_bpi_h618_rescue as builder  # noqa: E402

sys.path.insert(0, str(builder.ASSETS))
spec = importlib.util.spec_from_file_location("rescue_runtime", builder.ASSETS / "runtime.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
KERNEL = "6.6.75-current-sunxi64"


def elf(machine=183, program_type=1):
    data = bytearray(120)
    data[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<HH", data, 16, 2, machine)
    struct.pack_into("<Q", data, 32, 64)
    struct.pack_into("<HH", data, 54, 56, 1)
    struct.pack_into("<I", data, 64, program_type)
    return data


class RescueTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="bpi-rescue-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.busybox = self.put("busybox", elf(), 0o755)
        self.args = argparse.Namespace(output=str(self.base / "new"), busybox=str(self.busybox),
                                       busybox_sha256=builder.sha256(self.busybox), kernel=KERNEL,
                                       authorized_key=None)

    def put(self, name, data, mode=0o644):
        path = self.base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)
        path.chmod(mode)
        return path

    def environment(self):
        bins = {name: str(self.put("bin/" + name, elf(), 0o755))
                for name in builder.RUNTIME_BINARIES + builder.BUILD_BINARIES}
        for name in ("modules.dep", "modules.alias", "modules.builtin", "modules.builtin.modinfo"):
            self.put(f"system/lib/modules/{KERNEL}/{name}", "")
        keys = ("BLK_DEV_INITRD", "RD_GZIP", "DEVTMPFS", "PROC_FS", "SYSFS", "TMPFS", "UNIX", "INET", "PACKET", "MODULES")
        self.put(f"system/boot/config-{KERNEL}", "".join(f"CONFIG_{name}=y\n" for name in keys))
        for name in ("brcm/brcmfmac43455-sdio.bin", "brcm/brcmfmac43455-sdio.txt",
                     "brcm/brcmfmac43455-sdio.clm_blob", "regulatory.db", "regulatory.db.p7s"):
            self.put("system/lib/firmware/" + name, b"\x00")
        for name in ("hook-functions", "scripts/functions"):
            self.put("system/usr/share/initramfs-tools/" + name, "# 測試替身\n")
        self.put("system/etc/ssl/certs/ca-certificates.crt", "測試憑證\n")
        self.put("system/usr/lib/python3.11/os.py", "# 測試標準函式庫\n")

        def mapped(value):
            path = Path(value)
            if str(path).startswith(("/lib/", "/boot", "/usr/share/initramfs-tools", "/usr/lib/python3.", "/etc/ssl/")):
                return self.base / "system" / path.relative_to("/")
            return path

        def check(command, **unused):
            if "--list" in command:
                return "\n".join(builder.APPLET_NAMES)
            if command[0] == "modinfo" and "filename" in command:
                return str(mapped(f"/lib/modules/{KERNEL}/{command[-1]}.ko"))
            if command[0] == "modinfo" and "vermagic" in command:
                return KERNEL + " SMP mod_unload aarch64"
            if "-c" in command:
                return json.dumps("/usr/lib/python3.11")
            return ""

        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(builder, "Path", side_effect=mapped))
        stack.enter_context(patch.object(builder.platform, "machine", return_value="aarch64"))
        stack.enter_context(patch.object(builder.platform, "release", return_value=KERNEL))
        stack.enter_context(patch.object(builder.os, "geteuid", return_value=0))
        stack.enter_context(patch.object(builder.shutil, "which", side_effect=lambda name, **kw: bins.get(name)))
        mock_checked = stack.enter_context(patch.object(builder, "checked", side_effect=check))
        return bins, mock_checked

    def test_output_rejects_existing_file_directory_and_symlinks(self):
        self.put("existing", "不得覆寫")
        (self.base / "dir").mkdir()
        (self.base / "link").symlink_to(self.base / "dir")
        (self.base / "broken").symlink_to(self.base / "absent")
        for value in ("relative", self.base / "existing", self.base / "dir", self.base / "broken",
                      self.base / "link/new", self.base / "../new", "/boot/new-rescue", "/dev/new-rescue"):
            with self.subTest(value=str(value)), self.assertRaises(ValueError):
                builder.output_path(str(value))
        self.assertEqual((self.base / "existing").read_text(), "不得覆寫")
        self.assertEqual(builder.output_path(self.args.output), self.base / "new")

    def test_elf_rejects_wrong_arch_dynamic_truncated_and_nonfile(self):
        builder.elf_aarch64(self.busybox, static=True)
        for data in (elf(62), elf(program_type=2), elf(program_type=3), b"#!/bin/sh\n", elf()[:70]):
            self.busybox.write_bytes(data)
            with self.assertRaises(ValueError):
                builder.elf_aarch64(self.busybox, static=True)
        with self.assertRaises(ValueError):
            builder.elf_aarch64(self.base, static=True)
        self.busybox.write_bytes(elf())
        self.busybox.chmod(0o644)
        with self.assertRaises(ValueError):
            builder.elf_aarch64(self.busybox)

    def test_key_rejects_private_options_multiline_and_malformed(self):
        path = self.put("pubkey", "")
        for value in ("-----BEGIN OPENSSH PRIVATE KEY-----\n", 'command="id" ssh-ed25519 AAAA\n',
                      "ssh-ed25519 !!!!\n", "ssh-ed25519 AAAA\nssh-ed25519 AAAA\n"):
            path.write_text(value)
            with self.assertRaises(ValueError):
                builder.authorized_key(path)
        blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes(32)
        key = "ssh-ed25519 " + base64.b64encode(blob).decode()
        path.write_text(key + " 不帶入的註解\n", encoding="utf-8")
        # ssh-keygen 只做離線語法核對，註解不納入救援包。
        self.assertEqual(builder.authorized_key(path), key + "\n")

    def test_preflight_success_has_no_output_or_installs(self):
        self.environment()
        result = builder.preflight(self.args)
        self.assertEqual(result[1], KERNEL)
        self.assertFalse((self.base / "new").exists())

    def test_preflight_rejects_platform_and_kernel_mismatch(self):
        self.environment()
        with patch.object(builder.platform, "machine", return_value="x86_64"), self.assertRaisesRegex(ValueError, "AArch64"):
            builder.preflight(self.args)
        with patch.object(builder.os, "geteuid", return_value=1000), self.assertRaisesRegex(ValueError, "root"):
            builder.preflight(self.args)
        for kernel in ("../6.6", "other-kernel"):
            self.args.kernel = kernel
            with self.assertRaises(ValueError):
                builder.preflight(self.args)

    def test_preflight_rejects_module_directory_index_and_config_mismatch(self):
        self.environment()
        modules = self.base / f"system/lib/modules/{KERNEL}"
        modules.rename(modules.with_name("wrong"))
        modules.symlink_to("wrong")
        with self.assertRaisesRegex(ValueError, "模組目錄不符"):
            builder.preflight(self.args)
        modules.unlink()
        modules.with_name("wrong").rename(modules)
        (modules / "modules.dep").unlink()
        with self.assertRaisesRegex(ValueError, "現成模組索引"):
            builder.preflight(self.args)
        self.put(f"system/lib/modules/{KERNEL}/modules.dep", "")
        self.put(f"system/boot/config-{KERNEL}", "CONFIG_BLK_DEV_INITRD=n\n")
        with self.assertRaisesRegex(ValueError, "CONFIG_BLK_DEV_INITRD"):
            builder.preflight(self.args)

    def test_preflight_rejects_missing_binary_busybox_hash_and_firmware(self):
        bins, checked = self.environment()
        del bins["curl"]
        with self.assertRaisesRegex(ValueError, "curl"):
            builder.preflight(self.args)
        bins["curl"] = str(self.base / "bin/curl")
        self.args.busybox_sha256 = "0" * 64
        with self.assertRaisesRegex(ValueError, "SHA-256 不符"):
            builder.preflight(self.args)
        self.args.busybox_sha256 = builder.sha256(self.busybox)
        (self.base / "system/lib/firmware/brcm/brcmfmac43455-sdio.txt").unlink()
        with self.assertRaisesRegex(ValueError, "韌體"):
            builder.preflight(self.args)

    def test_static_init_and_ssh_contract(self):
        init = (builder.ASSETS / "init").read_text()
        for fs in ("proc", "sysfs", "devtmpfs", "tmpfs", "devpts"):
            self.assertIn("mount -t " + fs, init)
        self.assertRegex(init, r"\(/usr/sbin/bpi-rescue-ssh[^\n]+\) &")
        for forbidden in ("switch_root", "pivot_root", "mount -a", "/dev/mmc", "mkswap", "swapon"):
            self.assertNotIn(forbidden, init)
        config = (builder.ASSETS / "sshd_config").read_text()
        for expected in ("PasswordAuthentication no", "KbdInteractiveAuthentication no", "UsePAM no",
                         "AuthenticationMethods publickey", "PermitRootLogin prohibit-password",
                         "HostKey /run/ssh/", "AuthorizedKeysFile /etc/ssh/rescue_authorized_keys"):
            self.assertIn(expected, config)
        self.assertNotIn("Include ", config)
        namespace = (builder.ASSETS / "build-namespace").read_text()
        self.assertIn('"$mkinitramfs" -d "$work/conf"', namespace)
        self.assertIn('mount --bind "$work/share" /usr/share/initramfs-tools', namespace)
        hook = (builder.ASSETS / "hook").read_text()
        self.assertIn('copy_exec "$source" "$target"', hook)
        self.assertIn('"$work"/tmp/mkinitramfs_*', hook)
        for name in ("init", "hook", "build-namespace", "ssh-start", "udhcpc-script"):
            subprocess.run(["/bin/sh", "-n", str(builder.ASSETS / name)], check=True)

    def test_mock_mkinitramfs_success_and_exclusive_output(self):
        stdlib = self.put("stdlib/os.py", "# 測試\n").parent
        self.put("stdlib/tests/omit.py", "# 不納入\n")
        bins = {name: "/usr/bin/" + name for name in builder.RUNTIME_BINARIES + builder.BUILD_BINARIES}
        preflight = (self.base / "new", KERNEL, bins, self.busybox, "", [], stdlib)
        listing = "\n".join(("init", "usr/bin/busybox", "usr/bin/curl", "usr/bin/python3",
                              "usr/sbin/sshd", "usr/sbin/bpi-rescue", "etc/bpi-rescue.json"))

        def mkinitramfs(command, output, log):
            self.assertIn("--mount", command)
            self.assertIn("--propagation", command)
            self.assertEqual(command[-2], KERNEL)
            self.assertEqual(Path(command[-3]), output)
            self.assertFalse(list((output / "share/hooks").iterdir()))
            self.assertFalse(list((output / "share/conf-hooks.d").iterdir()))
            self.assertIn("MODULES=list", (output / "conf/initramfs.conf").read_text())
            self.assertFalse((output / "seed" / stdlib.relative_to("/") / "tests").exists())
            (output / "rescue-initramfs.img").write_bytes("離線替身".encode())
            return 0

        with patch.object(builder, "preflight", return_value=preflight), \
                patch.object(builder, "run_build", side_effect=mkinitramfs), \
                patch.object(builder, "checked", return_value=listing):
            report = builder.build(self.args)
            self.assertFalse(report["hardware_tested"])
            self.assertEqual(report["status"], "建置完成，尚未實板驗證")
            self.assertGreater(report["python_stdlib_bytes"], 0)
            with self.assertRaises(FileExistsError):
                builder.build(self.args)
        self.assertEqual(json.loads((self.base / "new/build-report.json").read_text()), report)

    def test_mock_mkinitramfs_failure_leaves_report_and_no_success_hash(self):
        stdlib = self.put("stdlib/os.py", "# 測試\n").parent
        bins = {name: "/usr/bin/" + name for name in builder.RUNTIME_BINARIES + builder.BUILD_BINARIES}
        preflight = (self.base / "new", KERNEL, bins, self.busybox, "", [], stdlib)
        with patch.object(builder, "preflight", return_value=preflight), \
                patch.object(builder, "run_build", return_value=1):
            with self.assertRaisesRegex(ValueError, "建置失敗"):
                builder.build(self.args)
        report = json.loads((self.base / "new/build-report.json").read_text())
        self.assertEqual(report["status"], "未完成")
        self.assertFalse((self.base / "new/SHA256SUMS").exists())

    def test_chinese_help_errors_and_timeouts(self):
        help_text = builder.parser().format_help()
        self.assertIn("用法：", help_text)
        self.assertNotIn("usage:", help_text)
        self.assertNotIn("options:", help_text)
        with patch("sys.stderr", new_callable=io.StringIO) as error, self.assertRaises(SystemExit):
            builder.parser().parse_args([])
        self.assertIn("參數錯誤", error.getvalue())
        self.assertNotIn("required", error.getvalue())
        with patch.object(builder.subprocess, "run", side_effect=subprocess.TimeoutExpired("ldd", 30)), \
                self.assertRaisesRegex(ValueError, "逾時"):
            builder.checked(["ldd", self.busybox])
        with patch.object(builder.subprocess, "Popen") as popen, patch.object(builder.os, "killpg") as kill:
            process = popen.return_value.__enter__.return_value
            process.wait.side_effect = [subprocess.TimeoutExpired("mkinitramfs", 600), 0]
            process.pid = 12345
            with self.assertRaises(subprocess.TimeoutExpired):
                builder.run_build(["mkinitramfs"], self.base, io.StringIO())
            kill.assert_called_once_with(12345, builder.signal.SIGKILL)
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            self.assertEqual(popen.call_args.kwargs["env"]["TMPDIR"], str(self.base / "tmp"))

    def test_ready_requires_kernel_and_schema(self):
        config = self.put("identity", json.dumps({"schema": runtime.SCHEMA, "kernel": KERNEL}))
        ready = self.base / "ready.json"
        with patch.object(runtime, "Path", side_effect=lambda p: config if p == "/etc/bpi-rescue.json" else ready), \
                patch.object(runtime.os, "uname", return_value=argparse.Namespace(release=KERNEL)), \
                patch("builtins.print") as printed:
            runtime.ready()
            self.assertTrue(printed.call_args.args[0].startswith("BPI_RESCUE_READY "))
            config.write_text(json.dumps({"schema": runtime.SCHEMA, "kernel": "wrong"}))
            with self.assertRaisesRegex(ValueError, "核心不符"):
                runtime.ready()
            config.write_text(json.dumps({"schema": "wrong", "kernel": KERNEL}))
            with self.assertRaisesRegex(ValueError, "schema"):
                runtime.ready()

    def test_inventory_uses_sysfs_not_block_devices(self):
        for name, value in {"device/type": "MMC", "device/cid": "123456", "device/name": "test",
                            "size": "2048", "ro": "0", "queue/logical_block_size": "512"}.items():
            self.put("sys/mmcblk2/" + name, value)
        self.put("proc/self/mountinfo", "測試掛載資訊\n")
        self.put("proc/swaps", "測試交換資訊\n")
        result = runtime.inventory(self.base / "sys", self.base / "proc")
        self.assertTrue(result["read_only_inventory"])
        self.assertEqual(result["devices"][0]["bytes"], 1048576)
        self.assertEqual(result["devices"][0]["device/cid"], "123456")

    def test_verify_complete_hash_xz_and_bounded_raw_data(self):
        data = b"bpi-rescue\n" * 8192
        compressed = lzma.compress(data)
        path = self.put("image.xz", compressed)
        args = argparse.Namespace(file=str(path), sha256=hashlib.sha256(compressed).hexdigest(),
                                  raw_sha256=hashlib.sha256(data).hexdigest(), raw_size=len(data))
        with patch.object(runtime, "ram_file", return_value=path):
            self.assertEqual(runtime.verify(args)["raw_size"], len(data))
            args.raw_size -= 1
            with self.assertRaisesRegex(ValueError, "超過可信長度"):
                runtime.verify(args)
            args.raw_size += 1
            args.raw_sha256 = "0" * 64
            with self.assertRaisesRegex(ValueError, "解壓長度或 SHA"):
                runtime.verify(args)
            args.sha256 = "0" * 64
            with self.assertRaisesRegex(ValueError, "壓縮映像 SHA"):
                runtime.verify(args)
            path.write_bytes(compressed[:-8])
            args.sha256 = builder.sha256(path)
            with self.assertRaises(subprocess.CalledProcessError):
                runtime.verify(args)

    def test_verify_caps_file_raw_size_memory_and_time(self):
        path = self.put("image.xz", b"x")
        args = argparse.Namespace(file=str(path), sha256="0" * 64, raw_sha256="0" * 64,
                                  raw_size=runtime.MAX_RAW_BYTES + 1)
        with patch.object(runtime, "ram_file", return_value=path), patch.object(runtime, "file_hash") as digest:
            with self.assertRaisesRegex(ValueError, "32 GiB"):
                runtime.verify(args)
            args.raw_size = 1
            with patch.object(runtime, "MAX_COMPRESSED_BYTES", 0), self.assertRaisesRegex(ValueError, "512 MiB"):
                runtime.verify(args)
            digest.assert_not_called()
        command = runtime.xz_command("--test", "--", str(path))
        self.assertEqual(command[:7], ["/bin/busybox", "timeout", "-s", "KILL", "600", "xz", "--memlimit-decompress=256MiB"])
        # 只執行本機短暫 sleep 替身，驗證 BusyBox 的期限確實終止受控程序。
        result = subprocess.run(["/bin/busybox", "timeout", "-s", "KILL", "1", "/bin/sleep", "10"],
                                timeout=5, check=False)
        self.assertNotEqual(result.returncode, 0)

    def test_runtime_rejects_nonram_credentials_and_module_options(self):
        with self.assertRaisesRegex(ValueError, "/run"):
            runtime.ram_file(str(self.busybox))
        with patch.object(runtime.subprocess, "run") as execute:
            for value in ("--help", "bad alias", ""):
                runtime.load_module(value)
            execute.assert_not_called()

    def test_dhcp_validates_mask_and_never_evaluates_network_data(self):
        args = argparse.Namespace(event="bound")
        values = {"interface": "wlan0", "ip": "192.0.2.3", "subnet": "255.255.0.0",
                  "router": "192.0.2.1", "dns": "192.0.2.53"}
        with patch.dict(os.environ, values, clear=True), patch.object(runtime, "run") as run, \
                patch.object(runtime, "Path", return_value=self.base / "resolv.conf"):
            runtime.dhcp(args)
            self.assertIn("192.0.2.3/16", run.call_args_list[1].args[0])
            os.environ["dns"] = "$(touch /boot/forbidden)"
            with self.assertRaises(ValueError):
                runtime.dhcp(args)


if __name__ == "__main__":
    unittest.main()
