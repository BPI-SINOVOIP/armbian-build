#!/usr/bin/env python3
"""同次 UART／SSH 與明示測試公鑰安裝的離線測試。"""

import base64
import copy
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from tools import bpi_lab_session as session
from tools import bpi_lab_lifecycle as life
import test_bpi_lab_backend as data

NONCE = "c" * 64
BOOT_ID = "11111111-2222-3333-4444-555555555555"
KEY = "ssh-ed25519 " + base64.b64encode(b"\0\0\0\x0bssh-ed25519\0\0\0\x20" + bytes(range(32))).decode()


class SessionTests(data.BackendFixture):
    def identity(self, mode="customer"):
        media = []
        for key, name, kind, number in (("expected", "mmcblk7", "MMC", "179:56"),
                                        ("protected_sd", "mmcblk3", "SD", "179:24")):
            item = self.contract[key]
            media.append({**item, "name": name, "type": kind, "devnum": number,
                          "sysfs": item["controller"] + "/mmc_host/mmc7/mmc7:0001/block/" + name})
        root = {"devnum": "179:58", "fs": "ext4", "mount_root": "/", "sysfs": media[0]["sysfs"] + "/mmcblk7p2",
                "parent": media[0]["sysfs"], "parent_devnum": media[0]["devnum"], "uuid": self.expected["root"]["uuid"]}
        if mode == "rescue":
            root = {"devnum": "0:21", "fs": "tmpfs", "mount_root": "/", "sysfs": None,
                    "parent": None, "parent_devnum": None, "uuid": None}
        return {"nonce": NONCE, "uid": 0, "kernel": self.expected["kernel_release"] if mode == "customer" else self.contract["rescue"]["kernel"],
                "machine": "aarch64", "boot_id": BOOT_ID, "dt_compatible": self.expected["dt_compatible"],
                "root": root, "media": media, "rescue": self.contract["rescue"] if mode == "rescue" else None,
                "host_key": KEY}

    def test_uart_customer_requires_exact_root_and_two_media(self):
        value = self.identity()
        session.validate_identity(value, "customer", self.contract, self.expected, NONCE)
        for mutate in (lambda item: item["root"].update(uuid="WRONG"),
                       lambda item: item["root"].update(fs="overlay"),
                       lambda item: item["root"].update(parent=item["media"][1]["sysfs"]),
                       lambda item: item["media"][0].update(cid="0" * 32),
                       lambda item: item.update(kernel="WRONG"),
                       lambda item: item.update(dt_compatible=["other,board"]),
                       lambda item: item.update(nonce="0" * 64)):
            wrong = copy.deepcopy(value)
            mutate(wrong)
            with self.assertRaises(ValueError):
                session.validate_identity(wrong, "customer", self.contract, self.expected, NONCE)

    def test_uart_rescue_requires_ram_and_exact_identity(self):
        value = self.identity("rescue")
        session.validate_identity(value, "rescue", self.contract, self.lifecycle["rescue_expected"], NONCE)
        value["root"]["devnum"] = "179:58"
        with self.assertRaises(ValueError):
            session.validate_identity(value, "rescue", self.contract, self.lifecycle["rescue_expected"], NONCE)

    def test_same_session_creates_fresh_hostkey_file(self):
        value = self.identity()
        console = mock.Mock()
        def shell(command, **kwargs):
            value["nonce"] = shlex.split(command)[-2]
            return SimpleNamespace(exitcode=0, output=json.dumps(value).encode())
        console.run_shell.side_effect = shell
        def ssh(argv, deadline, clock):
            self.assertIn("UserKnownHostsFile", (Path(argv[argv.index("-F") + 1])).read_text())
            yield "stdout", json.dumps(value).encode()
            yield "exit", 0
        result = session.establish(console, "customer", self.contract, self.expected,
                                   {**self.ssh, "known_hosts": None}, self.root / "session", life.Deadline(60),
                                   self.request, previous_boot_id=BOOT_ID, transport=ssh)
        self.assertEqual(result["hostkey_source"], "same-session-uart")
        self.assertTrue(result["ssh_verified"])
        self.assertIn(KEY, Path(result["ssh"]["known_hosts"]["path"]).read_text())
        self.assertEqual(Path(result["ssh"]["known_hosts"]["path"]).stat().st_mode & 0o777, 0o600)

    def test_different_ssh_boot_id_is_rejected(self):
        value = self.identity()
        console = mock.Mock()
        def shell(command, **kwargs):
            value["nonce"] = shlex.split(command)[-2]
            return SimpleNamespace(exitcode=0, output=json.dumps(value).encode())
        console.run_shell.side_effect = shell
        def ssh(*args):
            yield "stdout", json.dumps({**value, "boot_id": "00000000-0000-0000-0000-000000000000"}).encode()
            yield "exit", 0
        with self.assertRaises(ValueError):
            session.establish(console, "customer", self.contract, self.expected, self.ssh,
                              self.root / "session", life.Deadline(60), self.request, transport=ssh)
        self.assertFalse((self.root / "session/session.json").exists())

    def test_old_boot_id_stops_before_ssh(self):
        transport = mock.Mock(side_effect=AssertionError("不得接 SSH"))
        with mock.patch.object(session, "uart_identity", return_value=self.identity()), self.assertRaises(ValueError):
            session.establish(mock.Mock(), "customer", self.contract, self.expected, self.ssh,
                              self.root / "session", life.Deadline(60), self.request,
                              previous_boot_id="00000000-0000-0000-0000-000000000000", transport=transport)
        transport.assert_not_called()

    def key_setup(self):
        public = self.root / "public-key"
        public.write_text(KEY + "\n")
        return {**self.lifecycle["ssh_setup"]["customer"], "install_key": True, "public_key": self.reference(public)}

    def test_test_key_install_requires_explicit_authorization(self):
        with self.assertRaises(ValueError):
            session.validate_setup(self.key_setup(), False)
        session.validate_setup(self.key_setup(), True)

    def test_installer_is_fixed_bound_program_not_arbitrary_command(self):
        console = mock.Mock()
        console.run_shell.return_value = SimpleNamespace(exitcode=0, output=b"")
        session.install_key(console, self.key_setup(), self.identity(), life.Deadline(60))
        args = shlex.split(console.run_shell.call_args.args[0])
        self.assertEqual(args[:4], ["python3", "-I", "-B", "-c"])
        compile(args[4], "<離線公鑰安裝>", "exec")
        self.assertIn(BOOT_ID, args[4])
        self.assertIn("restrict ", args[4])
        self.assertIn("O_NOFOLLOW", args[4])
        self.assertNotIn("FIXTURE_OLD", args[4])

    def test_unknown_key_path_is_rejected(self):
        setup = self.key_setup()
        setup["authorized_keys"] = "/etc/shadow"
        with self.assertRaises(ValueError):
            session.validate_setup(setup, True)

    def test_identity_program_compiles_and_fits_console(self):
        compile(session.IDENTITY, "<離線身分程式>", "exec")
        command = session.identity_command(NONCE)
        wire = "sh -c " + shlex.quote(command)
        self.assertLess(len(wire), 3700)

    def test_root_uuid_is_checked_before_firstboot_account_writes(self):
        compile(session.ROOT_UUID_CHECK, "<離線根媒體預檢>", "exec")
        value = self.identity("rescue")
        console = mock.Mock()
        matches = [{"parent": value["media"][0]["sysfs"]}]
        def reply(command, **kwargs):
            nonce = shlex.split(command)[-1]
            return SimpleNamespace(exitcode=0, output=json.dumps({"nonce": nonce, "matches": matches}).encode())
        console.run_shell.side_effect = reply
        session.check_boot_root(console, self.expected, value, life.Deadline(60))
        matches.append({"parent": value["media"][1]["sysfs"]})
        with self.assertRaises(ValueError):
            session.check_boot_root(console, self.expected, value, life.Deadline(60))

    def test_label_requires_unique_uuid_and_label_on_same_emmc_partition(self):
        value = self.identity("rescue")
        binding = {"method": "label", "label": "BPI-ROOT", "uuid": self.expected["root"]["uuid"],
                   "unique_in_image": True, "unique_on_hardware": False}
        target = {"name": "mmcblk7p2", "parent": value["media"][0]["sysfs"], "devnum": "179:58",
                  "label": binding["label"], "uuid": binding["uuid"]}
        record = {"matches": [target], "label_matches": [target], "inventory": [["mmcblk7p2", "179:58"]]}
        console = mock.Mock()
        def reply(command, **kwargs):
            self.assertLess(len(command), 3400)
            arguments = shlex.split(command)
            self.assertEqual(arguments[-3:-1], [binding["uuid"], binding["label"]])
            return SimpleNamespace(exitcode=0, output=json.dumps({**record, "nonce": arguments[-1]}).encode())
        console.run_shell.side_effect = reply
        session.check_boot_root(console, self.expected, value, life.Deadline(60), root_binding=binding)
        other = {**target, "name": "sda1", "parent": "/sys/devices/fixture-usb/block/sda", "devnum": "8:1"}
        for labels in ([target, other], [other], []):
            record["label_matches"] = labels
            with self.subTest(labels=labels), self.assertRaises(ValueError):
                session.check_boot_root(console, self.expected, value, life.Deadline(60), root_binding=binding)

    def test_label_program_includes_usb_and_nvme_not_only_mmc(self):
        nodes = {"mmcblk7p2": (179, 58, self.expected["root"]["uuid"], "BPI-ROOT"),
                 "sda1": (8, 1, "USB-UUID", "BPI-ROOT"), "nvme0n1p1": (259, 1, "NVME-UUID", "OTHER")}
        handles, closed, calls = {}, [], []
        class FakePath:
            def __init__(self, path):
                self.path = str(path)
            def __truediv__(self, child):
                return FakePath(self.path + "/" + str(child))
            def __lt__(self, other):
                return self.path < other.path
            def __str__(self):
                return self.path
            @property
            def name(self):
                return self.path.rsplit("/", 1)[-1]
            @property
            def parent(self):
                return FakePath(self.path.rsplit("/", 1)[0])
            def iterdir(self):
                return [self / name for name in nodes]
            def read_text(self):
                node = self.path.split("/")[-2]
                return "8192" if self.name == "size" else "%d:%d" % nodes[node][:2]
            def exists(self):
                return self.name == "partition"
            def resolve(self, strict=False):
                return FakePath("/sys/devices/fixture/" + self.name + "/" + self.name)
        def opened(path, flags):
            self.assertTrue(flags & os.O_EXCL)
            self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
            fd = 10000 + len(handles)
            handles[fd] = path.rsplit("/", 1)[-1]
            return fd
        def fstat(fd):
            row = nodes[handles[fd]]
            return SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=os.makedev(*row[:2]))
        def probe(argv, *, pass_fds, **kwargs):
            self.assertEqual(argv[:8], ["/usr/sbin/blkid", "-p", "-s", "UUID", "-s", "LABEL", "-o", "export"])
            name = handles[pass_fds[0]]
            calls.append(name)
            row = nodes[name]
            return SimpleNamespace(returncode=0, stdout=("UUID=" + row[2] + "\nLABEL=" + row[3] + "\n").encode(), stderr=b"")
        out = io.StringIO()
        with mock.patch("pathlib.Path", FakePath), mock.patch.object(os, "open", side_effect=opened), \
                mock.patch.object(os, "fstat", side_effect=fstat), mock.patch.object(os, "close", side_effect=closed.append), \
                mock.patch.object(subprocess, "run", side_effect=probe), \
                mock.patch.object(sys, "argv", ["fixture", self.expected["root"]["uuid"], "BPI-ROOT", NONCE]), redirect_stdout(out):
            exec(compile(session.ROOT_LABEL_CHECK, "<離線 LABEL 盤點>", "exec"), {})
        record = json.loads(out.getvalue())
        self.assertEqual(set(calls), set(nodes))
        self.assertEqual(set(closed), set(handles))
        self.assertEqual(len(record["matches"]), 1)
        self.assertEqual({row["name"] for row in record["label_matches"]}, {"mmcblk7p2", "sda1"})


if __name__ == "__main__":
    unittest.main()
