"""一次性救援載入工具的離線守門；不開串口或儲存裝置。"""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import bpi_h618_rescue_boot as boot


class FakeConsole:
    def __init__(self, result=b"OK", before=b""):
        self.result, self.before = result, before
        self.sent = []

    def send(self, value):
        self.sent.append(value)

    def expect_regex(self, pattern, timeout):
        self.pattern = pattern
        return SimpleNamespace(groups=(self.result,), before=self.before)

    def expect_literal(self, value, timeout):
        if value != b"=> ":
            raise AssertionError("提示不符")


class RescueBootTests(unittest.TestCase):
    def manifest(self):
        return {"inputs": [{"path": path, "bytes": 1024, "sha256": "a"*64, "crc32": "b"*8}
                           for path in boot.PATHS],
                "sd_prefix": {"bytes": 4194304, "sha256": "c"*64}}

    def check_manifest(self, doc):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.json"
            path.write_text(json.dumps(doc))
            return boot.input_manifest(path)

    def test_manifest_accepts_fixed_inputs(self):
        items, prefix = self.check_manifest(self.manifest())
        self.assertEqual(len(items), 4)
        self.assertEqual(prefix["bytes"], 4194304)
        doc = self.manifest()
        doc['inputs'][3]['path'] = '/root/bpi-lab/rescue-build-20260916-002/rescue-initramfs.img'
        self.assertEqual(self.check_manifest(doc)[0][3]['path'],doc['inputs'][3]['path'])

    def test_missing_duplicate_oversize_hash_rejected(self):
        for mutate in (
            lambda doc: doc["inputs"].pop(),
            lambda doc: doc["inputs"].append(doc["inputs"][0]),
            lambda doc: doc["inputs"][0].update(bytes=128*1024*1024),
            lambda doc: doc["inputs"][0].update(sha256="untrusted"),
            lambda doc: doc["inputs"][0].update(crc32="$(reboot)"),
            lambda doc: doc["inputs"][0].update(path="/boot/evil;reset"),
        ):
            with self.subTest(mutate=mutate):
                document = self.manifest()
                mutate(document)
                with self.assertRaises(ValueError):
                    self.check_manifest(document)

    def test_command_echo_cannot_be_marker(self):
        console = FakeConsole()
        with patch.object(boot.secrets, "token_hex", return_value="0123456789abcdef"):
            boot.UBoot(console, []).command("mmc info")
        self.assertNotIn("BPI_0123456789abcdef_OK", console.sent[0])
        self.assertIn(rb"(?:^|\r?\n)", console.pattern)

    def test_command_failure_keeps_record(self):
        records = []
        with self.assertRaises(ValueError):
            boot.UBoot(FakeConsole(b"FAIL", b"read failure"), records).command("mmc info")
        self.assertEqual(len(records), 1)
        self.assertFalse(records[0]["ok"])

    def test_load_checks_length_and_crc(self):
        uboot = boot.UBoot(FakeConsole(), [])
        item = {"path": boot.PATHS[0], "bytes": 1024, "crc32": "12345678"}
        with patch.object(uboot, "command", side_effect=["1024 bytes read in 2 ms", "CRC32 for 40080000 ... 400803ff ==> 12345678"]):
            uboot.load(item, 0x40080000)
        with patch.object(uboot, "command", side_effect=["1024 bytes read in 2 ms", "crc32 for 40080000 ... 400803ff ==> 12345678"]):
            uboot.load(item, 0x40080000)
        for responses in (["1 bytes read in 2 ms"],
                          ["1024 bytes read in 2 ms", "CRC32 for 40080000 ... 400803ff ==> 12345679"]):
            with patch.object(uboot, "command", side_effect=responses):
                with self.assertRaises(ValueError):
                    uboot.load(item, 0x40080000)

    def test_multiline_command_rejected_before_tx(self):
        console = FakeConsole()
        with self.assertRaises(ValueError):
            boot.UBoot(console, []).command("mmc info\nreset")
        self.assertEqual(console.sent, [])

    def test_inventory_preserves_whole_json_amid_background_messages(self):
        item = {"schema":"bpi-h618-rescue-v1","devices":[]}
        wire = json.dumps(item).encode()
        self.assertEqual(boot.parse_inventory(b"kernel message\r\n"+wire+b"\r\nSSH ready\r\n"),item)
        with self.assertRaises(ValueError):
            boot.parse_inventory(wire+b"\n"+wire)
        with self.assertRaises(ValueError):
            boot.parse_inventory(wire[:10]+b"kernel message"+wire[10:])

    def test_inventory_read_retry_is_readonly_bounded_and_recorded(self):
        from unittest.mock import Mock
        console = Mock()
        item = {"schema":"bpi-h618-rescue-v1","devices":[]}
        console.run_shell.side_effect = [SimpleNamespace(exitcode=0,output=b'{broken'),
                                         SimpleNamespace(exitcode=0,output=json.dumps(item).encode())]
        records=[]
        with patch.object(boot.time,"sleep"):
            self.assertEqual(boot.read_inventory(console,records),item)
        self.assertEqual([entry['ok'] for entry in records],[False,True])
        self.assertTrue(all(call.args==('bpi-rescue inventory',) for call in console.run_shell.call_args_list))
        console.run_shell.side_effect = [SimpleNamespace(exitcode=0,output=b'{broken')]*3
        with patch.object(boot.time,"sleep"),self.assertRaises(ValueError):
            boot.read_inventory(console,[])


if __name__ == "__main__":
    unittest.main()
