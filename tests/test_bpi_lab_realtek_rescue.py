#!/usr/bin/env python3
"""固定 SD 救援的真來源／runner 離線回歸；所有資格及媒體均為合成測資。"""

import copy
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import bpi_lab_realtek_rescue as rescue
from tools import bpi_lab_console as console_api
import test_bpi_lab_special_runtime as data
from test_bpi_lab_special import initrd


class RescueChannel(data.SpecialChannel):
    def response(self, command):
        if command in self.overrides:
            return super().response(command)
        if command.startswith("bpirescue "):
            if command.endswith(" probe"):
                return ("BPI_LAB_V1 " + rescue.scope_digest(self.rescue_config) + "\r\nBPI_LAB_STATE 0 0\r\n" +
                        "".join(f"CID[{i}]: 0x{self.cid[i * 8:(i + 1) * 8]}\r\n" for i in range(4)) +
                        "BPI_LAB_PART " + self.config["source"]["partuuid"] + "\r\n").encode()
            return super().response("bpilab " + command.split(" ", 1)[1])
        return super().response(command)

    def write(self, wire):
        if wire != b"bpirescue boot\n":
            return super().write(wire)
        self.commands.append("bpirescue boot")
        self.writes.append(wire)
        output = self.response("bpirescue probe")
        output += b"".join(self.response("bpirescue hash " + role) for role in self.config["files"])
        self.queue.append(wire.replace(b"\n", b"\r\n") + output + self.kernel_response)
        return len(wire)


class RescueFixture:
    # 不繼承其他代理的測試，僅使用其來源測資建構器。
    def make_rescue(self, board="bpi-m4", platform=None, blobs=None):
        if platform is None:
            platform, _, blobs = self.configuration(board)
            pairing = rescue.deploy.load(platform["pairing"])
            pairing["protected_sd"].update(bytes=16 * 1024**2, controller="/sys/devices/platform/fixture-sd")
            platform["pairing"] = self.reference("rescue-pairing.json", pairing)
            platform = rescue.vendor._json(rescue.vendor.special.encoded(platform))
            self.qualify(platform)
        context = rescue.vendor._vendor_context(platform, qualified=False)
        identity = {"schema": "bpi-lab-rescue-fixture-v1", "kernel": context["core"]["kernel_release"]}
        identity_ref = self.reference("identity.json", identity)
        identity_blob = Path(identity_ref["path"]).read_bytes()
        blobs = {**blobs, "initrd": initrd(identity["kernel"], extra=[("etc/bpi-rescue.json", identity_blob)])}
        root = self.root / ("sd-artifacts-" + board)
        root.mkdir()
        files, evidence = {}, {}
        for role in rescue.ROLES:
            blob = blobs[role]
            (root / (role + ".bin")).write_bytes(blob)
            digest = {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
            logical = "/rescue/" + role + ".bin"
            files[role] = {**digest, "path": role + ".bin", "image_path": logical}
            evidence[logical] = {"digest": digest, "volume_index": 1, "resolved": logical}
        extraction = {"schema": "bpi-lab-image-v1", "ok": True, "source_verified": True,
                      "hardware_validated": False, "partition": {"index": 1, "partuuid": "abcdef12-01"}, "files": evidence}
        bsp = Path(platform["execution"]["vendor_sources"]["common/cmd_boot.c"]["path"]).parents[1]
        config = {"schema": rescue.SCHEMA, "platform": self.reference("platform.json", platform),
                  "artifact_root": str(root), "files": files, "identity": identity_ref,
                  "sd": {"device": "sd", "index": 0, "block_device": 0, "partition": 1, "partuuid": "abcdef12-01",
                         "extraction": self.reference("sd-extraction.json", extraction),
                         "prefix": {"bytes": 4 * 1024**2, "sha256": hashlib.sha256(bytes(4 * 1024**2)).hexdigest()}},
                  "sd_sources": {name: {"path": str(bsp / name), "sha256": hashlib.sha256((bsp / name).read_bytes()).hexdigest()}
                                 for name in rescue.sd_source_names(board)}, "qualification": {}}
        self.qualify_rescue(config)
        return config, blobs

    def qualify_rescue(self, config):
        platform = rescue.deploy.load(config["platform"])
        original = rescue.deploy.load(platform["qualification"])
        source = rescue.vendor_source(config)
        q = {"schema": rescue.QUALIFICATION_SCHEMA, "scope_sha256": rescue.scope_digest(config),
             "hardware_id": "offline-fixture-only", "approved": True, "hardware_validated": True,
             "ram_rescue_verified": True, "protected_sd_verified": True,
             "source_evidence": [self.reference("rescue-fixture.txt", "僅為合成測資，不是實板資格。".encode())],
             "memory_evidence": original["memory_evidence"], "required_commands": rescue.required_commands(),
             "dependencies": {name: hashlib.sha256((Path(rescue.__file__).parent / name).read_bytes()).hexdigest()
                              for name in rescue.DEPENDENCIES},
             "vendor_build": {**original["vendor_build"], "source_sha256": source["sha256"]}}
        config["qualification"] = self.reference("rescue-qualification.json", q)

    def rescue_session(self, config, blobs):
        core = rescue.lifecycle_view(config)
        channel = RescueChannel(core, blobs, data.Clock())
        channel.rescue_config = config
        channel.cid = rescue.deploy.load(rescue.deploy.load(config["platform"])["pairing"])["protected_sd"]["cid"]
        channel.kernel_response = ("\r\nLinux version " + core["kernel_release"] + " (fixture)\r\nlogin: ").encode()
        self.counter += 1
        console = console_api.ConsoleSession(channel, log_path=self.root / f"sd-uart-{self.counter}.bin", monotonic=channel.clock)
        self.addCleanup(console.close)
        return console, channel


class RescueTests(unittest.TestCase):
    setUp = data.RuntimeTests.setUp
    reference = data.RuntimeTests.reference
    configuration = data.RuntimeTests.configuration
    qualify = data.RuntimeTests.qualify
    make_rescue = RescueFixture.make_rescue
    qualify_rescue = RescueFixture.qualify_rescue
    rescue_session = RescueFixture.rescue_session

    def test_both_boards_generate_combined_source_and_execute_fixed_sd_runner(self):
        for board in sorted(rescue.vendor.VENDOR_BOARDS):
            with self.subTest(board=board):
                config, blobs = self.make_rescue(board)
                code = rescue.vendor_source(config)["source"]
                self.assertIn("U_BOOT_CMD(bpilab,", code)
                self.assertIn("U_BOOT_CMD(bpirescue,", code)
                self.assertIn('fs_set_blk_dev("sd", "0:1", FS_TYPE_FAT)', code)
                self.assertIn("find_sd_device()", code)
                self.assertIn("bpi_rescue_prefix()", code)
                core = rescue.lifecycle_view(config)
                self.assertEqual(core["source"]["type"], "vendor-sd")
                self.assertIn("root=/dev/ram0", core["bootargs"])
                self.assertFalse(any("LABEL=" in arg for arg in core["bootargs"]))
                console, channel = self.rescue_session(config, blobs)
                report = rescue.boot(console, config, [], timeout=300, monotonic=channel.clock)
                self.assertEqual(channel.commands[-1], "bpirescue boot")
                self.assertEqual(report["observed_media_cid"], channel.cid)
                self.assertFalse(report["ram_root_verified"])
                self.assertFalse(any(cmd.startswith(("gosd", "go ", "booti ", "load mmc", "saveenv")) for cmd in channel.commands))

    def test_source_generation_does_not_fabricate_qualification(self):
        config, _ = self.make_rescue()
        config["qualification"] = {}
        self.assertFalse(rescue.vendor_source(config)["hardware_validated"])
        with self.assertRaises(ValueError):
            rescue.validate_config(config)

    def test_source_has_no_binary_qualification_hash_cycle(self):
        config, _ = self.make_rescue()
        before = rescue.vendor_source(config)
        platform = rescue.deploy.load(config["platform"])
        platform["qualification"] = self.reference("future-qualified-binary.json", {"future_binary": "fixture-only"})
        config["platform"] = self.reference("platform-with-built-binary.json", platform)
        after = rescue.vendor_source(config)
        self.assertEqual(before["sha256"], after["sha256"])
        self.assertNotIn("rtk_get_secure_boot_type()", after["source"])

    def test_declared_or_different_build_is_not_rescue_qualification(self):
        config, _ = self.make_rescue()
        q = rescue.deploy.load(config["qualification"])
        for change in ({"ram_rescue_verified": False}, {"protected_sd_verified": False},
                       {"vendor_build": {**q["vendor_build"], "source_sha256": "0" * 64}},
                       {"vendor_build": {**q["vendor_build"], "binary": self.reference("other-binary", b"fixture-only")}}):
            config["qualification"] = self.reference("invalid-q.json", {**q, **change})
            with self.subTest(change=change), self.assertRaises(ValueError):
                rescue.validate_config(config)

    def test_wrong_uart_cid_stops_before_any_load(self):
        config, blobs = self.make_rescue()
        console, channel = self.rescue_session(config, blobs)
        channel.cid = "0" * 32
        with self.assertRaises(ValueError):
            rescue.boot(console, config, [], timeout=300, monotonic=channel.clock)
        self.assertFalse(any("load " in command or command == "bpirescue boot" for command in channel.commands))

    def test_rescue_identity_must_be_inside_fixed_initrd(self):
        config, _ = self.make_rescue()
        config["identity"] = self.reference("wrong-identity.json", {"schema": "other-rescue-v1", "kernel": "fixture"})
        with self.assertRaisesRegex(ValueError, "initrd 內身分"):
            rescue.vendor_source(config)

    def test_sd_original_mapping_and_prefix_are_mandatory(self):
        config, _ = self.make_rescue()
        for path, value in (("device", "mmc"), ("index", 2), ("partition", 2),
                            ("prefix", {"bytes": 512, "sha256": "0" * 64})):
            changed = copy.deepcopy(config)
            changed["sd"][path] = value
            with self.subTest(path=path), self.assertRaises(ValueError):
                rescue.vendor_source(changed)
        config["files"]["kernel"]["image_path"] = "/files/local-only.bin"
        with self.assertRaisesRegex(ValueError, "真實 SD 原檔"):
            rescue.vendor_source(config)

    def test_validate_and_source_never_open_uart(self):
        config, _ = self.make_rescue()
        with mock.patch.object(console_api.uart, "open_serial", side_effect=AssertionError("禁止 UART")) as port:
            rescue.vendor_source(config)
            rescue.validate_artifacts(config)
        port.assert_not_called()

    def test_combined_c_enforces_sd_and_handoff_boundaries(self):
        if not shutil.which("cc"):
            self.skipTest("合併 C 行為回歸需要本機 cc 與 OpenSSL 開發檔")
        for board in sorted(rescue.vendor.VENDOR_BOARDS):
            config, blobs = self.make_rescue(board)
            directory = self.root / (board + "-c")
            directory.mkdir()
            for name in ("mmc.h", "part.h", "fs.h", "malloc.h", "libfdt.h", "u-boot/sha256.h"):
                location = directory / name
                location.parent.mkdir(exist_ok=True)
                location.write_text("/* 僅主機替身標頭。 */\n")
            for index, role in enumerate(rescue.ROLES):
                (directory / f"{index}.bin").write_bytes(blobs[role])
            tests = Path(__file__).parent
            source = ((tests / "bpi_lab_realtek_rescue_harness.c").read_text() +
                      rescue.vendor_source(config)["source"] +
                      (tests / "bpi_lab_realtek_rescue_harness_tail.c").read_text())
            (directory / "test.c").write_text(source)
            command = ["cc", "-std=gnu11", "-Wall", "-Werror", "-Wno-unused-function", "-Wno-unused-variable",
                       "-Wno-deprecated-declarations", "-I", str(directory), str(directory / "test.c"),
                       "-lcrypto", "-o", str(directory / "test")]
            compiled = subprocess.run(command, capture_output=True, text=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            for fault in range(18):
                with self.subTest(board=board, fault=fault):
                    result = subprocess.run([str(directory / "test"), str(fault)], cwd=directory,
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
