"""客戶系統一次性引導的純離線守門；所有串口、SRAM 與裝置皆為替身。"""

from collections import deque
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bpi_h618_customer_boot as customer


BRIDGE = b"offline-bridge"
BRIDGE_SHA = hashlib.sha256(BRIDGE).hexdigest()
KERNEL = "6.18.49-current-sunxi64"


def fixtures():
    base = "/boot/dtb-" + KERNEL + "/allwinner/"
    paths = {"kernel": "/boot/vmlinuz-" + KERNEL, "initrd": "/boot/uInitrd-" + KERNEL,
             "dtb": base + customer.DTB, "overlay": base + "overlay/" + customer.OVERLAY,
             "fixup": base + "overlay/sun50i-h616-fixup.scr"}
    source = {"compressed": {"bytes": 512, "sha256": "a" * 64},
              "raw": {"bytes": 4096, "sha256": "b" * 64}}
    components = {"schema": "bpi-h618-customer-components-v1", "board": "bananapim4zeroemac",
                  "os": "bookworm", "desktop": "minimal", "kernel_release": KERNEL,
                  "image": {"path": "/never-read/source.img.xz", **copy.deepcopy(source)},
                  "root_uuid": "62cd58de-498d-4325-8528-96a15da7d902", "partuuid": "22d064d3-01",
                  "files": {name: {"path": path, "bytes": 1024 + index, "sha256": "c" * 64,
                                   "crc32": f"{index + 1:08x}"} for index, (name, path) in enumerate(paths.items())},
                  "original_env": {"verbosity": "1", "bootlogo": "false", "console": "both",
                                   "disp_mode": "1920x1080p60", "overlay_prefix": "sun50i-h616",
                                   "overlays": "bananapi-m4-zero-emac-sdio-wifi-bt", "fdtfile": customer.DTB,
                                   "rootdev": "UUID=62cd58de-498d-4325-8528-96a15da7d902",
                                   "rootfstype": "ext4", "extraargs": "cma=256M"},
                  "preflight": {key: True for key in
                                ("source_verified", "legacy_initrd_verified", "mmc_support_verified")},
                  "hardware_validated": False}
    request = {"expected": copy.deepcopy(customer.EXPECTED), "protected_sd": copy.deepcopy(customer.PROTECTED_SD),
               "source": copy.deepcopy(source), "confirm_overwrite": True, "backup_verified": True,
               "backup_manifest_sha256": "d" * 64}
    sd = {"identity": {**customer.PROTECTED_SD, "type": "SD", "device": "/dev/mmcblk0", "devnum": "179:0",
                       "bytes": 63864569856}, "prefix": {"bytes": 4194304, "sha256": "e" * 64}}
    state = {"range": {"start": 0, "end_exclusive": 4096}, "bootable": False, "boot_selected": False,
             "boot_verified": False, "identity": {**customer.EXPECTED, "device": "/dev/mmcblk2",
                                                 "devnum": "179:16", "type": "MMC", "mounted": False,
                                                 "swap": False, "holders": False},
             "rescue": {"schema": "bpi-h618-rescue-v1", "root_ram": True, "root_fs": "tmpfs"},
             "status": "verified", "bytes_written": 4096, "attempted_end": 4096,
             "source": copy.deepcopy(source), "readback": copy.deepcopy(source["raw"]),
             "sd_before": copy.deepcopy(sd), "sd_after": copy.deepcopy(sd)}
    receipt = {"schema": "bpi-h618-emmc-deploy-v1", "status": "verified", "ok": True,
               "confirm_overwrite": True, "ssh_exitcode": 0, "request": request,
               "source": copy.deepcopy(components["image"]), "range": copy.deepcopy(state["range"]),
               "compressed_bytes_sent": 512, "remote_state": state}
    return components, receipt


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class Wire:
    """發送即產生包含 ANSI、長回顯與多個提示的 RX；不開啟任何裝置。"""

    def __init__(self, components, clock):
        self.components, self.clock = components, clock
        self.timeout = self.write_timeout = 0.1
        self.incoming = deque([b"U-Boot\r\nHit any key to stop autoboot:  2\r\n"])
        self.sent, self.commands = [], []
        self.overrides = {}
        self.failed_command = None
        self.missing_marker = False
        self.fragment = False
        self.login = True
        self.observed_kernel = KERNEL
        self.all_rx = bytearray()

    def read(self, size):
        self.clock.value += min(self.timeout, 0.01)
        if not self.incoming:
            return b""
        data = self.incoming.popleft()
        width = min(size, 7) if self.fragment else size
        if len(data) > width:
            self.incoming.appendleft(data[width:])
            data = data[:width]
        self.all_rx.extend(data)
        return data

    def write(self, wire):
        self.sent.append(wire)
        if wire == b" ":
            self.incoming.append(b"=> ")
        elif wire.startswith(b"booti "):
            data = (wire + b"\r\n[    0.000000] Linux version " + self.observed_kernel.encode()
                    + b" (builder) #1 SMP\r\n")
            if self.login:
                data += b"Debian GNU/Linux 12\r\nbananapim4zeroemac login: "
            self.incoming.append(data)
        else:
            match = re.fullmatch(rb"if (.*); then echo BPI_'([0-9a-f]{16})'_OK; else echo BPI_'\2'_FAIL; fi\n", wire)
            if not match:
                raise AssertionError("非預期命令：" + repr(wire))
            command, nonce = match[1].decode(), match[2]
            self.commands.append(command)
            body = self.overrides.get(command, self.response(command)).encode()
            echo = b"\x1b[?2004h" + wire.replace(b"\n", b"\r\n")
            suffix = b"FAIL" if command == self.failed_command else b"OK"
            marker = b"\r\nBPI_" + nonce + b"_" + suffix + b"\r\n=> "
            self.incoming.append(echo + body + (b"" if self.missing_marker else marker))
        return len(wire)

    def response(self, command):
        if command == "mmc list":
            return "mmc@4020000: 0\r\nmmc@4022000: 1 (eMMC)\r\n"
        if command == "mmc info":
            return "Device: mmc@4022000\r\nMMC version 5.1\r\nCapacity: 29.1 GiB\r\n"
        if command == "part uuid mmc 1:1":
            return self.components["partuuid"] + "\r\n"
        if command.startswith("load "):
            item = next(item for item in self.components["files"].values() if item["path"] == command.split()[-1])
            return f"{item['bytes']} bytes read in 5 ms\r\n"
        if command.startswith("crc32 "):
            address = int(command.split()[1], 16)
            name = next(name for name, start, _ in customer.LOADS if start == address)
            item = self.components["files"][name]
            return f"CRC32 for {address:x} ... {address + item['bytes'] - 1:x} ==> {item['crc32']}\r\n"
        if command.startswith("fdt print "):
            status = "disabled" if "4020000" in command else "okay"
            return f'status = "{status}";\r\n'
        return ""


class CustomerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.components, self.receipt = fixtures()
        self.clock = Clock()
        self.wire = Wire(self.components, self.clock)
        # 不論守門是否意外退化，測試皆不可開啟真串口。
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.serial = stack.enter_context(patch.object(customer.rescue, "open_serial", side_effect=AssertionError("禁止真 UART")))
        stack.enter_context(patch.object(customer.rescue, "BRIDGE_SHA", BRIDGE_SHA))
        self.supervisor = stack.enter_context(patch.object(customer.rescue, "LabSupervisorSession"))
        self.supervisor.return_value.probe.return_value = {"abi": 3}
        self.supervisor.return_value.run.return_value = {"ran": True}

    def files(self):
        components = self.root / "components.json"
        receipt = self.root / "receipt.json"
        bridge = self.root / "bridge.spl2"
        components.write_text(json.dumps(self.components))
        receipt.write_text(json.dumps(self.receipt))
        bridge.write_bytes(BRIDGE)
        return {"port": "/dev/ttyUSB0", "bridge": bridge, "components": components,
                "components_sha256": hashlib.sha256(components.read_bytes()).hexdigest(),
                "deploy_receipt": receipt, "output": self.root / "new"}

    def start(self, timeout=300):
        self.records = []
        with customer.rescue.RecordedChannel(self.wire, log_path=self.root / "uart.bin", monotonic=self.clock) as console:
            return customer.boot(console, BRIDGE, self.components, self.receipt, self.records,
                                 timeout=timeout, monotonic=self.clock)

    def test_metadata_and_supported_original_arguments(self):
        args = customer.validate_metadata(self.components, self.receipt)
        for value in ("root=UUID=" + self.components["root_uuid"], "rootwait", "rootfstype=ext4",
                      "console=ttyS0,115200", "console=tty1", "cma=256M", "cgroup_enable=memory",
                      "ubootpart=" + self.components["partuuid"], "loglevel=6", "panic=0"):
            self.assertIn(value, args.split())
        self.assertTrue(all("systemd.mask=" + name in args.split() for name in customer.MASKS))
        self.components["original_env"]["usbstoragequirks"] = "1234:abcd:u,0123:4567:tu"
        self.assertIn("usb-storage.quirks=1234:abcd:u,0123:4567:tu", customer.validate_metadata(self.components, self.receipt))

    def test_other_emac_batch_os_uses_same_contract(self):
        self.components.update(os="trixie", desktop="xfce_desktop")
        self.components["original_env"]["bootlogo"] = "true"
        args = customer.validate_metadata(self.components, self.receipt).split()
        self.assertIn("splash", args)
        self.assertIn("plymouth.ignore-serial-consoles", args)
        self.assertNotIn("splash=verbose", args)

    def test_bad_schema_board_kernel_or_uuid(self):
        for key, value in (("schema", "unknown"), ("board", "bananapim4zero"), ("kernel_release", "6.6;reset"),
                           ("root_uuid", "$(reset)"), ("partuuid", "22d064d3-02"), ("hardware_validated", True)):
            with self.subTest(key=key):
                document = copy.deepcopy(self.components)
                document[key] = value
                with self.assertRaises(ValueError):
                    customer.validate_metadata(document, self.receipt)

    def test_each_preflight_is_mandatory_true(self):
        for key in self.components["preflight"]:
            for value in (False, 1, None):
                with self.subTest(key=key, value=value):
                    document = copy.deepcopy(self.components)
                    document["preflight"][key] = value
                    with self.assertRaises(ValueError):
                        customer.validate_metadata(document, self.receipt)

    def test_unsupported_env_and_script_injection_rejected(self):
        for key, value in (("param_spidev_spi_bus", "0"), ("param_", ""), ("extraargs", "cma=256M; reset"),
                           ("user_overlays", "evil"), ("bootcmd", "reset"), ("rootdev", "/dev/mmcblk0p1"),
                           ("overlays", "bananapi-m4-zero-emac-sdio-wifi-bt pwm34"), ("bootlogo", "true; reset"),
                           ("console", "serial"), ("disp_mode", "different"), ("extraargs", None)):
            with self.subTest(key=key, value=value):
                document = copy.deepcopy(self.components)
                document["original_env"][key] = value
                with self.assertRaises(ValueError):
                    customer.validate_metadata(document, self.receipt)

    def test_missing_path_crc_hash_and_memory_overflow(self):
        for mutate in (lambda d: d["files"].pop("fixup"),
                       lambda d: d["files"]["dtb"].update(path="/boot/ordinary-zero.dtb"),
                       lambda d: d["files"]["kernel"].update(path="/boot/evil;reset"),
                       lambda d: d["files"]["initrd"].update(bytes=64 * 1024**2 + 1),
                       lambda d: d["files"]["dtb"].update(bytes=65537),
                       lambda d: d["files"]["overlay"].update(crc32="not-a-crc"),
                       lambda d: d["files"]["fixup"].update(sha256="a"),
                       lambda d: d["files"]["initrd"].update(bytes=True)):
            with self.subTest(mutate=mutate):
                document = copy.deepcopy(self.components)
                mutate(document)
                with self.assertRaises(ValueError):
                    customer.validate_metadata(document, self.receipt)

    def test_receipt_failure_identity_source_sd_and_range_mismatch(self):
        mutations = (lambda r: r.update(ok=False), lambda r: r.update(status="failed"),
                     lambda r: r.update(ssh_exitcode=True), lambda r: r.update(confirm_overwrite=False),
                     lambda r: r["request"]["expected"].update(cid="0" * 32),
                     lambda r: r["request"]["expected"].update(bytes=31289507841),
                     lambda r: r["request"]["expected"].update(controller="/sys/devices/platform/soc/4020000.mmc"),
                     lambda r: r["request"].update(backup_verified=False),
                     lambda r: r["source"]["compressed"].update(sha256="f" * 64),
                     lambda r: r["source"]["raw"].update(bytes=8192),
                     lambda r: r["source"].update(path="/other.xz"),
                     lambda r: r["remote_state"]["identity"].update(mounted=True),
                     lambda r: r["remote_state"]["identity"].update(type="SD"),
                     lambda r: r["remote_state"]["sd_after"]["prefix"].update(sha256="0" * 64),
                     lambda r: r["remote_state"]["sd_after"]["identity"].update(cid="0" * 32),
                     lambda r: r["remote_state"]["readback"].update(sha256="0" * 64),
                     lambda r: r["remote_state"].update(bytes_written=1),
                     lambda r: r["remote_state"]["rescue"].update(root_ram=False),
                     lambda r: r["range"].update(start=512))
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                receipt = copy.deepcopy(self.receipt)
                mutate(receipt)
                with self.assertRaises(ValueError):
                    customer.validate_metadata(self.components, receipt)

    def test_load_inputs_only_reads_metadata_not_xz(self):
        args = self.files()
        document, receipt, hashes = customer.load_inputs(args["components"], args["components_sha256"], args["deploy_receipt"])
        self.assertEqual(document, self.components)
        self.assertEqual(receipt, self.receipt)
        self.assertEqual(hashes["components"]["sha256"], args["components_sha256"])
        self.serial.assert_not_called()

    def test_bad_components_trust_and_partial_receipt_refused_before_uart(self):
        args = self.files()
        for changes in ({"components_sha256": "0" * 64}, {"components_sha256": ""},
                        {"deploy_receipt": self.root / "receipt.json.partial"},
                        {"port": "/dev/ttyUSB1"}, {"timeout": float("nan")}, {"timeout": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                customer.run(**(args | changes))
        self.assertFalse(args["output"].exists())
        self.serial.assert_not_called()

    def test_symlink_inputs_and_output_refused(self):
        args = self.files()
        for key in ("components", "deploy_receipt", "bridge"):
            original = args[key]
            destination = self.root / (original.name + ".original")
            original.rename(destination)
            original.symlink_to(destination)
            with self.subTest(key=key), self.assertRaises((ValueError, OSError)):
                customer.run(**args)
            original.unlink()
            destination.rename(original)
        args["output"].symlink_to(self.root, target_is_directory=True)
        with self.assertRaises((ValueError, OSError)):
            customer.run(**args)
        self.serial.assert_not_called()

    def test_existing_output_and_symlink_parent_refused(self):
        args = self.files()
        args["output"].mkdir()
        with self.assertRaises(OSError):
            customer.run(**args)
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            customer.run(**(args | {"output": alias / "other"}))
        self.serial.assert_not_called()

    def test_wrong_bridge_rejected_before_supervisor_or_uart(self):
        args = self.files()
        args["bridge"].write_bytes(b"wrong")
        with self.assertRaises(ValueError):
            customer.run(**args)
        self.serial.assert_not_called()
        self.supervisor.assert_not_called()

    def test_duplicate_and_oversize_json_rejected(self):
        args = self.files()
        for data in (b'{"schema":1,"schema":2}', b" " * 65537):
            args["components"].write_bytes(data)
            with self.assertRaises(ValueError):
                customer.load_inputs(args["components"], hashlib.sha256(data).hexdigest(), args["deploy_receipt"])

    def test_success_uses_slot3_emmc_crc_dtb_protection_and_legacy_initrd(self):
        result = self.start()
        self.supervisor.return_value.begin_load.assert_called_once_with("S", 3)
        self.supervisor.return_value.run.assert_called_once_with()
        self.assertEqual(result["status"], "login_observed")
        self.assertFalse(result["system_verified"])
        self.assertFalse(result["root_cid_verified"])
        self.assertFalse(result["original_boot_chain_verified"])
        loads = [c for c in self.wire.commands if c.startswith("load ")]
        self.assertEqual(len(loads), 4)
        self.assertTrue(all(c.startswith("load mmc 1:1 ") for c in loads))
        self.assertEqual(sum(c.startswith("crc32 ") for c in self.wire.commands), 4)
        self.assertEqual(self.wire.sent[-1], b"booti 40080000 4ff00000 4fa00000\n")
        self.assertLess(self.wire.commands.index("fdt apply 45000000"),
                        self.wire.commands.index("fdt set /soc/mmc@4020000 status disabled"))
        forbidden = ("saveenv", "reset", "source ", "mmc write", "mmc erase", "boot.scr", "fixup.scr", "load mmc 0")
        self.assertTrue(all(not any(value in c for value in forbidden) for c in self.wire.commands))
        self.assertEqual((self.root / "uart.bin").read_bytes(), self.wire.all_rx)
        self.assertEqual(stat.S_IMODE((self.root / "uart.bin").stat().st_mode), 0o600)

    def test_fragmented_echo_and_prompt_tails_are_not_lost(self):
        self.wire.fragment = True
        self.assertTrue(self.start()["login_observed"])

    def test_kernel_and_login_in_same_rx_read_keep_tail(self):
        result = self.start()
        self.assertEqual(result["kernel_release_observed"], KERNEL)
        self.assertEqual(self.records[-1], {"login_host_observed": "bananapim4zeroemac"})

    def test_wrong_mmc_mapping_stops_before_device_selection_or_load(self):
        self.wire.overrides["mmc list"] = "mmc@4020000: 1\r\nmmc@4022000: 0\r\n"
        with self.assertRaises(ValueError):
            self.start()
        self.assertEqual(self.wire.commands, ["mmc list"])

    def test_wrong_info_type_or_controller_refused(self):
        for info in ("Device: mmc@4022000\nSD version 3.0\n", "Device: mmc@4020000\nMMC version 5.1\n"):
            with self.subTest(info=info), self.assertRaises(ValueError):
                customer.verify_mmc_info(info)

    def test_wrong_partuuid_stops_before_load(self):
        self.wire.overrides["part uuid mmc 1:1"] = "00000000-01\r\n"
        with self.assertRaises(ValueError):
            self.start()
        self.assertFalse(any(c.startswith("load ") for c in self.wire.commands))

    def test_load_length_mismatch_stops_without_booti(self):
        self.wire.overrides[f"load mmc 1:1 40080000 {self.components['files']['kernel']['path']}"] = "1 bytes read\r\n"
        with self.assertRaises(ValueError):
            self.start()
        self.assertFalse(any(c.startswith(b"booti ") for c in self.wire.sent))

    def test_crc_mismatch_stops_without_booti(self):
        self.wire.overrides["crc32 40080000 400"] = "CRC32 for 40080000 ... 400803ff ==> deadbeef\r\n"
        with self.assertRaises(ValueError):
            self.start()
        self.assertFalse(any(c.startswith(b"booti ") for c in self.wire.sent))

    def test_overlay_failure_does_not_fall_back_or_boot(self):
        self.wire.failed_command = "fdt apply 45000000"
        with self.assertRaises(ValueError):
            self.start()
        self.assertEqual(self.wire.commands[-1], "fdt apply 45000000")
        self.assertFalse(self.records[-1]["ok"])

    def test_sd_disabled_status_must_be_observed(self):
        self.wire.overrides["fdt print /soc/mmc@4020000 status"] = 'status = "okay";\r\n'
        with self.assertRaises(ValueError):
            self.start()
        self.assertFalse(any(c.startswith(b"booti ") for c in self.wire.sent))

    def test_real_uboot_status_without_semicolon(self):
        self.wire.overrides["fdt print /soc/mmc@4020000 status"] = 'status = "disabled"\r\n'
        self.wire.overrides["fdt print /soc/mmc@4021000 status"] = 'status = "okay"\r\n'
        self.wire.overrides["fdt print /soc/mmc@4022000 status"] = 'status = "okay"\r\n'
        self.assertTrue(self.start()["login_observed"])

    def test_echo_without_marker_cannot_succeed_and_deadline_is_bounded(self):
        self.wire.missing_marker = True
        with self.assertRaises((ValueError, TimeoutError)):
            self.start(timeout=0.5)
        self.assertLessEqual(self.clock.value, 0.51)
        self.assertEqual(self.wire.commands, ["mmc list"])

    def test_wrong_nonce_marker_cannot_spoof_success(self):
        self.wire.missing_marker = True
        self.wire.overrides["mmc list"] = "\r\nBPI_0000000000000000_OK\r\n=> "
        with patch.object(customer.rescue.secrets, "token_hex", return_value="1111111111111111"):
            with self.assertRaises((ValueError, TimeoutError)):
                self.start(timeout=0.5)

    def test_wrong_kernel_stops_before_login_acceptance(self):
        self.wire.observed_kernel = "6.6.75-current-sunxi64"
        with self.assertRaises(ValueError):
            self.start()
        self.assertEqual(self.records[-1]["kernel_release_observed"], self.wire.observed_kernel)

    def test_no_login_timeout_does_not_retry_handoff(self):
        self.wire.login = False
        with self.assertRaises((ValueError, TimeoutError)):
            self.start(timeout=3)
        self.supervisor.return_value.run.assert_called_once_with()
        self.assertEqual(sum(c.startswith(b"booti ") for c in self.wire.sent), 1)

    def test_cli_missing_required_arguments_is_offline(self):
        with redirect_stderr(io.StringIO()) as output:
            self.assertEqual(customer.main([]), 1)
        self.assertFalse(json.loads(output.getvalue())["ok"])
        self.serial.assert_not_called()

    def test_cli_help_is_chinese_and_offline(self):
        with redirect_stdout(io.StringIO()) as output, self.assertRaises(SystemExit) as exc:
            customer.main(["--help"])
        self.assertEqual(exc.exception.code, 0)
        self.assertIn("用法：", output.getvalue())
        self.serial.assert_not_called()

    def test_run_writes_private_observation_and_releases_borrowed_uart(self):
        args = self.files()
        @contextmanager
        def opened(*_):
            yield self.wire
        self.serial.side_effect = opened
        with patch.object(customer, "boot", return_value={"status": "login_observed", "login_observed": True}):
            report = customer.run(**args)
        self.assertTrue(report["ok"])
        self.assertTrue(report["uart_released"])
        self.assertFalse(report["system_verified"])
        self.assertFalse(report["sd_after_boot_verified"])
        self.assertEqual(stat.S_IMODE(args["output"].stat().st_mode), 0o700)
        for path in args["output"].iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        saved = json.loads((args["output"] / "report.json.partial").read_text())
        self.assertEqual(saved["ram_dtb_changes"][0]["value"], "disabled")

    def test_report_fsync_failure_is_cli_failure(self):
        args = self.files()
        @contextmanager
        def opened(*_):
            yield self.wire
        self.serial.side_effect = opened
        fsync = os.fsync
        def fail_report(fd):
            if stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_size > 0:
                raise OSError("注入證據同步失敗")
            fsync(fd)
        with patch.object(customer, "boot", return_value={"status": "login_observed"}), \
                patch.object(customer.os, "fsync", side_effect=fail_report), \
                patch.object(customer, "run", wraps=customer.run), redirect_stderr(io.StringIO()) as errors:
            argv = [value for key, value in args.items() for value in ("--" + key.replace("_", "-"), str(value))]
            self.assertEqual(customer.main(argv), 1)
        self.assertFalse(json.loads(errors.getvalue())["ok"])
        self.assertFalse((args["output"] / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
