"""D4 離線契約與五階段回歸；不開啟真實 UART、SSH、電源或媒體。"""

import base64
from contextlib import contextmanager, redirect_stdout
import copy
import hashlib
import io
import lzma
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

from tools import bpi_lab_external_backend as lab
from test_bpi_lab_external import ExternalFixture, fingerprint
import test_bpi_lab_uboot as boot_data


class Fixture(unittest.TestCase):
    ROOT_GROWTH = False
    def ref(self, path):
        return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(lab.deploy.encode(value))
        return self.ref(path)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(lab.deploy.core.subprocess, "Popen", side_effect=AssertionError("禁止真實子程序")).start()
        mock.patch.object(lab.life.console_api.uart, "open_serial", side_effect=AssertionError("禁止真實 UART")).start()
        self.root = Path(self.temp.name)
        self.d2 = ExternalFixture(self.root)
        self.d2.prepare()
        self.contract = self.d2.contract
        self.locks = self.root / "locks"
        mock.patch.object(lab.core, "LOCK_ROOT", self.locks).start()
        self.boot_id = str(uuid.UUID(int=1))
        self.establish_impl = lab.establish
        self.sequence = 0
        self.key = "ssh-ed25519 " + base64.b64encode(b"\0\0\0\x0bssh-ed25519\0\0\0\x20" + bytes(32)).decode()
        self.expected = lab.linux.expected_from_contract(self.contract, architecture="arm64",
                                                        kernel_release="6.6.1-test", dt_compatible=["fixture,board"])
        self.original_partitions = [{"index": self.contract["root"]["partition_index"],
                                    **{key: self.contract["root"][key] for key in ("start_lba", "sectors")}}]
        if self.ROOT_GROWTH:
            self.expected["root_growth"] = {"policy": "last-primary-to-media-end", "partitions": self.original_partitions}
        self.resources = {"uart": "/dev/serial/by-id/fixture", "power": "power:fixture", "media": lab.media.media_identity(self.d2.target)}
        self.pairing = {"schema": "bpi-lab-external-pairing-v1", "approved": True, "record": "fixture-pair",
                        "hardware_id": self.contract["hardware_id"], "resources": self.resources,
                        "uart": {"stable_path": self.resources["uart"], "baud": 115200},
                        "power": {"driver": "bpi-pw", "name": "fixture", "ip": "192.0.2.9", "mac": "02:00:00:00:00:09"},
                        "sd_device": 0, **{key: self.contract[key] for key in ("target", "protected_sd", "rescue")}}
        pairing_ref = self.write("pairing.json", self.pairing)
        self.guard_manifest = self.make_guard()
        self.boot, self.blobs = boot_data.fixture()
        self.boot["bootargs"][1] = "root=UUID=" + self.contract["root"]["uuid"]
        self.boot["source"]["device"] = 0
        self.boot["files"]["initrd"].update({key: self.guard_manifest["derived_initrd"][key] for key in ("bytes", "sha256")})
        self.boot["files"]["initrd"]["capacity"] = 4 * 1024**2
        self.blobs["initrd"] = Path(self.guard_manifest["derived_initrd"]["path"]).read_bytes()
        qualification = self.write("uboot-qualified.json", {"record": "離線建置替身"})
        self.boot["uboot"].update(pairing_sha256=pairing_ref["sha256"], qualification_sha256=qualification["sha256"])
        self.rescue = copy.deepcopy(self.boot)
        self.rescue["kernel_release"] = self.contract["rescue"]["kernel"]
        self.rescue["bootargs"] = ["console=ttyS0,115200", "root=/dev/ram0"]
        artifacts = self.root / "artifacts"
        for role, item in self.boot["files"].items():
            path = artifacts / item["path"].lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.blobs[role])
        sources = {}
        for name, boot in (("boot", self.boot), ("rescue", self.rescue)):
            sources[name] = self.write(name + "-source.json", {"schema": "bpi-lab-external-boot-source-v1", "kind": "sd-prepositioned",
                "uboot": self.write(name + "-uboot.json", boot), "qualification": qualification, "artifact_root": str(artifacts)})
        prepared = {"schema": "bpi-lab-image-v1", "ok": True, "source_verified": True,
                    "source_digest": self.d2.source["compressed"], "raw": self.d2.source["raw"],
                    "filesystem_uuid": self.contract["root"]["uuid"], "partition": {"index": 1},
                    "files": {self.boot["files"][key]["path"]: {"digest": fingerprint(self.blobs[key])} for key in ("kernel", "dtb")}}
        prepared["files"]["/boot/original-initrd"] = {"digest": fingerprint(Path(self.guard_manifest["original_initrd"]["path"]).read_bytes())}
        prepared["partitions"] = self.original_partitions
        self.bundle = {"schema": "bpi-lab-external-image-v1", "board": "bpi-fixture", "image": self.d2.source,
                       "deployment": self.write("deployment.json", self.contract), "preparation": self.write("preparation.json", prepared),
                       "original_components": {"kernel": self.boot["files"]["kernel"]["path"], "dtb": self.boot["files"]["dtb"]["path"],
                                               "initrd": "/boot/original-initrd"}, "boot_source": sources["boot"],
                       "guard": self.ref(self.root / "guard/guard-manifest.json"), "linux_expected": self.write("expected.json", self.expected),
                       "customer_ssh": self.contract["ssh"]}
        power = self.root / "bpi-pw"
        power.write_bytes(b"#!/bin/sh\nexit 1\n")
        power.chmod(0o700)
        self.lifecycle = {"schema": "bpi-lab-external-lifecycle-v1", "hardware_id": self.contract["hardware_id"],
            "pairing_sha256": pairing_ref["sha256"], "uart_device": "/dev/ttyUSB7", "power_program": self.ref(power), "power_dependencies": [],
            "autoboot": {"stop_text": "Hit any key to stop autoboot:", "stop_key_hex": "20"},
            "login": {mode: {"kind": "root-shell", "shell_prompt": "root@fixture:~# "} for mode in ("customer", "rescue")},
            "shutdown_marker": "reboot: Power down", "off_seconds": 10,
            "authorization": {"record": "fixture-power", "normal_shutdown": True, "cold_cycle": True, "customer_boot_may_write_target": True,
                              "fault_poweroff": True, "firstboot_account_changes": False, "install_test_ssh_key": False},
            "ssh_setup": {mode: {"host_key_path": "/etc/ssh/ssh_host_ed25519_key.pub", "install_key": False,
                                  "public_key": None, "authorized_keys": "/root/.ssh/authorized_keys", "peer_ipv4": "192.0.2.100",
                                  "wait_for": "existing"} for mode in ("customer", "rescue")},
            "rescue_expected": {"architecture": "arm64", "dt_compatible": ["fixture,rescue"]}}
        self.config = {"schema": "bpi-lab-external-backend-v1", "station_id": "fixture-station", "hardware_id": self.contract["hardware_id"],
                       "test_version": "fixture-v1", "resources": self.resources, "pairing": pairing_ref,
                       **{key: self.contract[key] for key in ("target", "protected_sd", "rescue")},
                       "lifecycle": self.write("lifecycle.json", self.lifecycle), "rescue_source": sources["rescue"],
                       "images": {self.d2.source["compressed"]["sha256"]: self.write("bundle.json", self.bundle)},
                       "output_root": str(self.root / "evidence"), "timeout_seconds": 600,
                       "dependencies": {name: hashlib.sha256((Path(lab.__file__).parent / name).read_bytes()).hexdigest()
                                        for name in lab.DEPENDENCIES}}
        (self.root / "evidence").mkdir()
        self.config_ref = self.write("config.json", self.config)
        self.request = {"schema": "bpi-lab-request-v1", "work_key": "f" * 64, "attempt_id": "fixture-attempt", "stage": "preflight",
            "station_id": self.config["station_id"], "hardware_id": self.config["hardware_id"], "test_version": self.config["test_version"],
            "image_sha256": self.d2.source["compressed"]["sha256"], "boot_config_sha256": self.config_ref["sha256"], "mode": "hardware",
            "image_root": str(self.root), "image": {"board": "bpi-fixture", "relative_path": self.d2.image.name,
                                                    "compressed_bytes": len(self.d2.compressed)}}

    def reusable_images(self):
        """兩份不可變原始 XZ 共用同一實體測試區與唯一初始完整備份。"""
        variants = []
        for name, raw in (("larger", self.d2.raw + b"L" * 32768), ("smaller", self.d2.raw)):
            compressed = lzma.compress(raw)
            image = self.root / (name + ".img.xz")
            image.write_bytes(compressed)
            source = {"path": str(image), "compressed": fingerprint(compressed), "raw": fingerprint(raw)}
            contract = copy.deepcopy(self.contract)
            plan = {"schema": "bpi-lab-external-write-v1", "source_sha256": lab.external.source_digest(source),
                    "ranges": [{"offset": 0, **source["raw"]}, {"offset": len(raw),
                               **lab.external.zero_digest(contract["target"]["bytes"] - len(raw))}], "tail_policy": "zero"}
            contract["write_plan"] = plan
            contract["authorization"].update(scope="reusable-test-area", source_sha256=plan["source_sha256"],
                                               write_plan_sha256=lab.media.digest(plan))
            lab.external.validate_contract(contract)
            bundle = copy.deepcopy(self.bundle)
            prepared = lab.deploy.load(bundle["preparation"])
            prepared.update(source_digest=source["compressed"], raw=source["raw"])
            bundle.update(image=source, deployment=self.write(name + "/deployment.json", contract),
                          preparation=self.write(name + "/preparation.json", prepared))
            variants.append({"contract": contract, "bundle": bundle, "raw": raw, "compressed": compressed,
                             "ref": self.write(name + "/bundle.json", bundle)})
        self.config["images"] = {row["bundle"]["image"]["compressed"]["sha256"]: row["ref"] for row in variants}
        self.config_ref = self.write("two-images-config.json", self.config)
        return variants

    def select_variant(self, variant, attempt):
        self.contract, self.bundle = variant["contract"], variant["bundle"]
        self.d2.contract, self.d2.source = self.contract, self.bundle["image"]
        self.d2.raw, self.d2.compressed = variant["raw"], variant["compressed"]
        self.d2.image = Path(self.d2.source["path"])
        self.request.update(work_key=hashlib.sha256(attempt.encode()).hexdigest(), attempt_id=attempt,
                            boot_config_sha256=self.config_ref["sha256"], image_sha256=self.d2.source["compressed"]["sha256"],
                            image={"board": self.bundle["board"], "relative_path": self.d2.image.name,
                                   "compressed_bytes": len(self.d2.compressed)})

    def make_guard(self):
        guard = lab.guard
        init = b"#!/bin/sh\nmountroot\nrun_scripts /scripts/init-bottom\nexec run-init /root /sbin/init\n"
        functions = b'run_scripts()\n{\n initdir=${1}\n [ ! -d "${initdir}" ] && return\n shift\n . "${initdir}/ORDER"\n}\n'
        entries = {}
        for name, blob, mode in (("init", init, 0o100755), ("scripts/functions", functions, 0o100644),
                                 ("scripts/init-bottom/ORDER", b"", 0o100644), ("bin/sh", b"\x7fELFtest", 0o100755)):
            guard._put(entries, name, guard.entry(blob, mode))
        original = self.root / "original-initrd"
        original.write_bytes(guard.archive(entries))
        additions = {}
        for name in ("usr/bin/python3", "usr/sbin/blkid"):
            guard._put(additions, name, guard.entry(b"\x7fELFtest", 0o100755))
        for name in ("usr/lib/python3", "lib"):
            guard._put(additions, name, guard.entry(b"", 0o40755))
        archive = self.root / "runtime.cpio"
        archive.write_bytes(guard.archive(additions))
        bundle = {"schema": guard.BUNDLE_SCHEMA, "architecture": "arm64", "archive": self.ref(archive),
                  "python": "/usr/bin/python3", "blkid": "/usr/sbin/blkid", "library_dirs": ["/lib"], "stdlib_dirs": ["/usr/lib/python3"],
                  "init_profile": {"init_sha256": fingerprint(init)["sha256"], "functions_sha256": fingerprint(functions)["sha256"]}}
        mock.patch.dict(guard.INIT_PROFILES, {(fingerprint(init)["sha256"], fingerprint(functions)["sha256"]): "合成測資"}).start()
        mock.patch.object(guard, "elf_info", return_value={"interpreter": None, "needed": []}).start()
        mock.patch.object(guard, "_probe", return_value={"schema": "bpi-lab-external-runtime-probe-v1", "hardware_validated": False,
            "architecture": "arm64", "python": {"python_major": 3, "machine": "aarch64", "imports": "complete"},
            "blkid_stdout_sha256": "a" * 64, "emulator": None, "runtime_archive_sha256": bundle["archive"]["sha256"]}).start()
        return guard.build(self.ref(original), self.expected, self.write("runtime.json", bundle), self.root / "guard")

    def observation(self, mode, nonce):
        self.sequence += 2
        target = lab.media.inspect(self.contract["target"], **self.d2.kwargs)
        sd = lab.media.inspect_sd(self.contract["protected_sd"], **self.d2.kwargs)
        common = {"hardware_validated": False, "nonce": nonce, "boot_id": self.boot_id, "host_key": self.key,
                  "target": target, "protected_sd": sd, "sd_readback": {"bytes": sd["bytes"], "sha256": sd["full_sha256"]},
                  "sampling_started_ns": self.sequence, "sampling_finished_ns": self.sequence + 1, "machine": "aarch64"}
        if mode == "rescue":
            return {**common, "schema": "bpi-lab-external-rescue-observation-v1", "kernel": self.contract["rescue"]["kernel"],
                    "dt_compatible": self.lifecycle["rescue_expected"]["dt_compatible"],
                    "rescue": {**self.contract["rescue"], "root_ram": True, "root_fs": "rootfs", "root_dev": "0:1"}}
        part = {"index": 1, "start_lba": 8, "sectors": 120, "devnum": "8:1", "sysfs_path": target["sysfs_path"] + "/sda1"}
        target["partitions"] = [part]
        def row(item, readonly):
            return {"name": item["name"], "device": item["device"], "devnum": item["devnum"], "sysfs_path": item["sysfs_path"],
                    "parent": item["sysfs_path"], "bytes": item["bytes"], "partition_index": None, "uuid": None,
                    "label": None, "fs_type": None, "ioctl_bytes": item["bytes"], "ioctl_read_only": readonly, "sysfs_read_only": readonly}
        block = {"name": "sda1", "device": str(self.d2.dev / "sda1"), "devnum": "8:1", "sysfs_path": part["sysfs_path"],
                 "parent": target["sysfs_path"], "bytes": 120 * 512, "partition_index": 1, "uuid": self.contract["root"]["uuid"],
                 "label": None, "fs_type": "ext4", "ioctl_bytes": 120 * 512, "ioctl_read_only": 0, "sysfs_read_only": 0}
        inventory = [row(target, 0), block, row(sd, 1)]
        mount = {"id": 31, "parent_id": 1, "devnum": "8:1", "mount_root": "/", "mount_point": "/", "options": ["rw"],
                 "fs_type": "ext4", "source": "/dev/sda1"}
        root = {"mount": mount, "stat": {"devnum": "8:1", "inode": 2}, "block": block}
        value = {**common, "schema": lab.linux.OBSERVATION_SCHEMA, "architecture": "arm64", "kernel": self.expected["kernel_release"],
                "dt_compatible": self.expected["dt_compatible"], "root": root, "root_after": copy.deepcopy(root),
                "target_after": copy.deepcopy(target), "protected_sd_after": copy.deepcopy(sd), "inventory": inventory,
                "inventory_after": copy.deepcopy(inventory), "mounts": [mount], "swaps": [], "swaps_after": [], "guard": None}
        prior = copy.deepcopy(value)
        prior.update(nonce=lab.linux.digest(self.expected), host_key=None, sampling_started_ns=0, sampling_finished_ns=1)
        prior["root"]["mount"]["mount_point"] = "/root"
        prior["root_after"] = copy.deepcopy(prior["root"])
        value["guard"] = {"schema": "bpi-lab-external-guard-result-v1", "status": "protected", "hardware_validated": False,
                          "expected_sha256": lab.linux.digest(self.expected), "observation": prior, "fstab_sha256": "a" * 64,
                          "fstab": [], "read_only_changes": [{"devnum": sd["devnum"], "bytes": sd["bytes"], "before": 0, "after": 1}],
                          "guard_source_sha256": lab.linux.guard_source_digest()}
        return value

    def session(self, mode, context, output, deadline, previous_boot_id=None):
        original = self.establish_impl
        uart = None
        def sampled(console, program, budget):
            nonlocal uart
            import re
            # nonce 由固定 program 的參數取出，不假設主機秘密值。
            match = re.search(r"base64.b64decode\('([^']+)'\)", program)
            import json
            import zlib
            payload = json.loads(zlib.decompress(base64.b64decode(match[1])))
            uart = self.observation(mode, payload["nonce"])
            return uart
        def collected(expected, ssh, directory, *, nonce, uart_observation, timeout):
            remote = self.observation(mode, nonce)
            directory = lab.deploy.new_directory(directory)
            fixed = lab.deploy.snapshot_ssh(ssh, directory)
            return {"schema": lab.linux.COLLECTION_SCHEMA, "status": "collected", "hardware_validated": False,
                    "nonce": nonce, "collector_sha256": hashlib.sha256(lab.linux.program(expected, nonce).encode()).hexdigest(),
                    "ssh_config": fixed, "known_hosts": ssh["known_hosts"], "expected_sha256": lab.linux.digest(expected),
                    "uart_observation_sha256": lab.linux.digest(uart_observation), "observation": remote,
                    "validation": lab.linux.validate_observation(remote, expected, nonce, uart_observation=uart_observation)}
        def rescued(program, ssh, directory, budget):
            directory = lab.deploy.new_directory(directory)
            return self.observation(mode, uart["nonce"]), lab.deploy.snapshot_ssh(ssh, directory)
        with mock.patch.object(lab, "uart_program", side_effect=sampled), mock.patch.object(lab.linux, "collect", side_effect=collected), \
                mock.patch.object(lab, "ssh_rescue", side_effect=rescued):
            return original(mock.Mock(), mode, context, output, deadline, previous_boot_id)

    def fake_result(self, context, current, output, timeout):
        req = context[-1]
        stage = req["stage"]
        mode = current["phase"] if "resume" in req else "customer" if stage in ("boot", "smoke") else "rescue"
        if stage in ("boot", "recovery"):
            self.boot_id = str(uuid.UUID(int=uuid.UUID(self.boot_id).int + 1))
        output = lab.deploy.new_directory(output)
        session = self.session(mode, context, output / "session", lab.life.Deadline(timeout))
        receipt = boot = None
        if stage == "deploy" or stage == "preflight" and "resume" not in req:
            fixed = {**self.contract, "ssh": session["ssh"]}
            options = {"confirm_overwrite": True} if stage == "deploy" else {}
            raw = getattr(lab.external, stage)(fixed, self.d2.source, output / "media", transport=self.d2.transport, **options)
            lab.validate_media_receipt(raw, self.contract, self.d2.source, stage)
            receipt = self.write(str((output / "media.json").relative_to(self.root)), raw)
        if stage in ("boot", "recovery"):
            selected = self.boot if stage == "boot" else self.rescue
            clock = boot_data.Clock()
            channel = boot_data.Channel(selected, self.blobs, clock)
            channel.kernel_response = b"\r\nLinux version " + selected["kernel_release"].encode() + b" (fixture)\r\n"
            steps = []
            with boot_data.console.ConsoleSession(channel, log_path=output / "uart.bin", monotonic=clock) as console:
                marker = lab.uboot.boot(console, selected, steps, monotonic=clock)
            boot = {"source": self.bundle["boot_source"] if stage == "boot" else self.config["rescue_source"],
                    "pairing": self.config["pairing"], "marker": marker,
                    "steps": self.write(str((output / "steps.json").relative_to(self.root)), steps), "off_seconds": 10,
                    "forced_poweroff": False, "normal_shutdown_verified": True,
                    "power": [{"action": action, "on": on, "identity_verified": True} for action, on in
                              (("status", True), ("off", False), ("status", False), ("on", True))]}
        return {"schema": "bpi-lab-external-operation-v1", "action": stage, "status": "verified", "simulated": False,
                "binding": lab.operation_binding(req, self.contract, self.bundle), "session": session,
                "media_receipt": receipt, "boot": boot, "validation": lab.validation_checks(mode, stage, receipt is not None)}

    @contextmanager
    def native_edges(self):
        original_boot = lab.uboot.boot
        original_deploy, original_preflight = lab.external.deploy, lab.external.preflight
        power_on = True
        @contextmanager
        def console(*args):
            yield mock.Mock()
        def power(config, pairing, action, deadline):
            nonlocal power_on
            if action != "status":
                power_on = action == "on"
            return {"ok": True, "verified": True, "device": {**pairing["power"], "identity_verified": True, "on": power_on}}
        def boot(console, selected, steps, **kwargs):
            clock = boot_data.Clock()
            channel = boot_data.Channel(selected, self.blobs, clock)
            channel.kernel_response = b"\r\nLinux version " + selected["kernel_release"].encode() + b" (fixture)\r\n"
            self.sequence += 1
            with boot_data.console.ConsoleSession(channel, log_path=self.root / ("native-boot-" + str(self.sequence)), monotonic=clock) as serial:
                marker = original_boot(serial, selected, steps, monotonic=clock)
            self.boot_id = str(uuid.UUID(int=uuid.UUID(self.boot_id).int + 1))
            return marker
        def session(console, mode, context, output, deadline, previous_boot_id=None):
            return self.session(mode, context, output, deadline, previous_boot_id)
        with mock.patch.object(lab.life.NativeRuntime, "console", side_effect=console), \
                mock.patch.object(lab.life.NativeRuntime, "power", side_effect=power), \
                mock.patch.object(lab.life.NativeRuntime, "drain"), mock.patch.object(lab.life.NativeRuntime, "sleep"), \
                mock.patch.object(lab.life, "login"), mock.patch.object(lab.uboot, "boot", side_effect=boot), \
                mock.patch.object(lab, "establish", side_effect=session), \
                mock.patch.object(lab.external, "deploy", side_effect=lambda *a, **k: original_deploy(*a, **k, transport=self.d2.transport)), \
                mock.patch.object(lab.external, "preflight", side_effect=lambda *a, **k: original_preflight(*a, **k, transport=self.d2.transport)):
            yield

    def run_stage(self, stage, **extra):
        req = {**self.request, "stage": stage, **extra}
        with mock.patch.object(lab, "check_qualification", return_value={"approved_images": [req["image_sha256"]]}), \
                mock.patch.object(lab.NativeRuntime, "execute", side_effect=self.fake_result) as execute:
            return lab.run_stage(self.config_ref["path"], self.config_ref["sha256"], req), execute


class BackendTests(Fixture):
    def test_firstrun_wait_is_bounded_and_precedes_any_key_write(self):
        self.lifecycle["ssh_setup"]["customer"]["wait_for"] = "armbian-firstrun"
        self.config["lifecycle"] = self.write("lifecycle.json", self.lifecycle)
        context = (self.config, self.contract, self.bundle, self.boot, self.expected, self.request)
        for ready in (True, False):
            clock = boot_data.Clock()
            console = mock.Mock()
            console.run_shell.side_effect = ([SimpleNamespace(exitcode=1), SimpleNamespace(exitcode=0)] if ready
                                            else lambda *a, **kw: SimpleNamespace(exitcode=1))
            deadline = lab.life.Deadline(600, clock, lambda delay: setattr(clock, "value", clock.value + delay))
            with mock.patch.object(lab, "uart_program", side_effect=ValueError("已到身分核對")) as sample, \
                    mock.patch.object(lab.core.session, "install_key") as install:
                with self.assertRaisesRegex(ValueError, "已到身分核對" if ready else "首次啟動 SSH"):
                    lab.establish(console, "customer", context, self.root / ("firstrun-" + str(ready)), deadline)
                self.assertEqual(sample.call_count, int(ready))
                self.assertEqual(console.run_shell.call_count, 2 if ready else 90)
                install.assert_not_called()

    def test_authorized_fresh_customer_key_uses_checked_root_and_same_boot(self):
        setup = self.lifecycle["ssh_setup"]["customer"]
        key = self.root / "customer.pub"
        key.write_text(self.key + "\n")
        setup.update(install_key=True, public_key=self.ref(key))
        self.lifecycle["authorization"]["install_test_ssh_key"] = True
        self.config["lifecycle"] = self.write("lifecycle.json", self.lifecycle)
        lab.load_inputs(self.write("key-config.json", self.config))
        context = (self.config, self.contract, self.bundle, self.boot, self.expected, self.request)
        with mock.patch.object(lab.core.session, "install_key") as install:
            result = self.session("customer", context, self.root / "key-session", lab.life.Deadline(60))
        self.assertEqual(result["boot_id"], self.boot_id)
        install.assert_called_once()
        self.assertEqual(install.call_args.args[2], {"boot_id": self.boot_id, "root": {"devnum": "8:1"}})
        self.assertTrue((self.root / "key-session/ssh-setup.json").is_file())
        self.lifecycle["authorization"]["install_test_ssh_key"] = False
        self.config["lifecycle"] = self.write("lifecycle.json", self.lifecycle)
        with mock.patch.object(lab.core.session, "install_key") as install, self.assertRaises(ValueError):
            self.session("customer", context, self.root / "key-denied", lab.life.Deadline(60))
        install.assert_not_called()

    def test_fresh_key_cannot_cross_mount_or_precede_root_validation(self):
        key = self.root / "customer.pub"
        key.write_text(self.key + "\n")
        self.lifecycle["ssh_setup"]["customer"].update(install_key=True, public_key=self.ref(key))
        self.lifecycle["authorization"]["install_test_ssh_key"] = True
        self.config["lifecycle"] = self.write("lifecycle.json", self.lifecycle)
        context = (self.config, self.contract, self.bundle, self.boot, self.expected, self.request)
        original = self.observation
        for failure in ("mount", "uuid"):
            def observation(mode, nonce):
                value = original(mode, nonce)
                if failure == "mount":
                    value["mounts"].append({**value["mounts"][0], "id": 77, "devnum": "8:9", "mount_point": "/root/.ssh"})
                else:
                    value["root"]["block"]["uuid"] = "ffffffff-ffff-ffff-ffff-ffffffffffff"
                return value
            with mock.patch.object(self, "observation", side_effect=observation), \
                    mock.patch.object(lab.core.session, "install_key") as install, self.assertRaises(ValueError):
                self.session("customer", context, self.root / ("key-" + failure), lab.life.Deadline(60))
            install.assert_not_called()

    def test_fixed_rescue_program_executes_and_closes_only_fake_media(self):
        nonce = "1" * 64
        program = lab.rescue_program(self.contract, nonce, 600)
        namespace = {}
        ops, read = self.d2.ops, self.d2.ops.read
        def fixed_read(path, maximum=65536):
            literal = {"/proc/sys/kernel/random/boot_id": self.boot_id.encode(),
                       "/etc/ssh/ssh_host_ed25519_key.pub": self.key.encode(),
                       "/sys/firmware/devicetree/base/compatible": b"fixture,rescue\0"}
            if str(path) in literal:
                return literal[str(path)]
            if str(path).startswith("/proc/"):
                path = self.d2.proc / str(path)[6:]
            return read(path, maximum)
        with mock.patch.dict(lab.sys.modules):
            exec(compile(program.removesuffix(lab.RESCUE_OBSERVER), "<fixture-rescue-bootstrap>", "exec"), namespace)
            generated = namespace["m"]
            inspect, inspect_sd = generated.inspect, generated.inspect_sd
            locations = {key: self.d2.kwargs[key] for key in ("sysroot", "procroot", "devroot")}
            with mock.patch.object(generated, "NativeOps", return_value=ops), \
                    mock.patch.object(generated, "inspect", side_effect=lambda *a, **k: inspect(*a, **k, **locations)), \
                    mock.patch.object(generated, "inspect_sd", side_effect=lambda *a, **k: inspect_sd(*a, **k, **locations)), \
                    mock.patch.object(ops, "read", side_effect=fixed_read), \
                    mock.patch.object(ops, "uname", return_value=SimpleNamespace(release=self.contract["rescue"]["kernel"], machine="aarch64")):
                output = io.StringIO()
                with redirect_stdout(output):
                    exec(compile(lab.RESCUE_OBSERVER, "<fixture-rescue-observer>", "exec"), namespace)
                observed = lab.linux.parse_json(output.getvalue().encode())
                lab.validate_rescue(observed, self.config, self.contract, nonce)
                self.assertFalse(ops.fds)
                self.assertFalse(ops.writes)
                self.assertEqual(observed["sd_readback"], fingerprint(bytes(self.contract["protected_sd"]["bytes"])))
                ops.event = True
                with self.assertRaises(ValueError):
                    exec(compile(lab.RESCUE_OBSERVER, "<fixture-rescue-hotplug>", "exec"), namespace)
                self.assertFalse(ops.fds)

    def test_uart_program_uses_bounded_lines_and_real_json_roundtrip(self):
        value = {"nonce": "a" * 64, "sample": "fixture" * 1000}
        observed, lines = b"", []
        def send(command, **kwargs):
            nonlocal observed
            lines.extend(command.splitlines())
            output = io.StringIO()
            with redirect_stdout(output):
                exec(compile("\n".join(lines[1:-1]), "<fixture-uart-wrapper>", "exec"), {})
            observed = output.getvalue().encode()
        def expect(pattern, **kwargs):
            found = lab.re.search(pattern, observed)
            self.assertIsNotNone(found)
            return SimpleNamespace(groups=found.groups())
        console = SimpleNamespace(send=send, expect_regex=expect)
        self.assertEqual(lab.uart_program(console, "print(" + repr(lab.deploy.encode(value).decode()) + ")",
                                         lab.life.Deadline(600)), value)
        self.assertLess(max(map(len, lines)), 512)

    def test_configuration_does_not_require_cycle_qualification(self):
        self.assertEqual(lab.load_inputs(self.config_ref), self.config)
        with self.assertRaises(ValueError):
            lab.check_qualification(self.config)
        lab.selected_image(self.config, self.request)

    def test_reusable_plan_uses_canonical_source_target_and_scope_checks(self):
        variants = self.reusable_images()
        self.select_variant(variants[0], "fixture-range")
        with mock.patch.object(lab.external, "_source_binding", wraps=lab.external._source_binding) as validator:
            lab.selected_image(self.config, self.request)
        validator.assert_called_once_with(self.contract, self.d2.source)
        for mutate in (lambda row: row["authorization"].pop("scope"),
                       lambda row: row["write_plan"]["ranges"][1].update(offset=1),
                       lambda row: row["write_plan"].update(source_sha256="a" * 64),
                       lambda row: row["target"]["identity"].update(serial="another-disk"),
                       lambda row: row["authorization"].update(backup_sha256="b" * 64)):
            changed = copy.deepcopy(self.contract)
            mutate(changed)
            changed["authorization"]["write_plan_sha256"] = lab.media.digest(changed["write_plan"])
            bundle = {**self.bundle, "deployment": self.write("invalid-deployment.json", changed)}
            config = {**self.config, "images": {self.request["image_sha256"]: self.write("invalid-bundle.json", bundle)}}
            with self.assertRaises(ValueError):
                lab.selected_image(config, self.request)

    def test_d2_manifest_fsync_failure_keeps_writer_isolated(self):
        self.assertEqual(self.run_stage("preflight")[0]["status"], "passed")
        saved = lab.external._save
        def failed(directory, name, value):
            result = saved(directory, name, value)
            if name == "manifest.json" and value.get("operation") == "deploy":
                raise OSError("合成 manifest 同步故障")
            return result
        with mock.patch.object(lab.external, "_save", side_effect=failed):
            result, _ = self.run_stage("deploy")
        self.assertIs(result["needs_recovery"], True)
        manifest = Path(result["evidence_path"]) / "operation/media/manifest.json"
        raw = lab.deploy.load(self.ref(manifest))
        self.assertEqual(raw["status"], "verified")
        with self.assertRaises((ValueError, OSError)):
            lab.validate_media_receipt(raw, self.contract, self.d2.source, "deploy")
        store = lab.StateStore(self.config, self.contract, self.d2.source)
        before = (self.locks / store.name).read_bytes()
        for _ in range(2):
            report, runtime = self.run_stage("recovery")
            self.assertEqual(report["status"], "blocked")
            runtime.assert_not_called()
            self.assertEqual((self.locks / store.name).read_bytes(), before)
        self.assertTrue(store.read()["writer_unresolved"])

    def test_reusable_tail_only_readback_failure_keeps_writer_isolated(self):
        self.select_variant(self.reusable_images()[0], "fixture-tail-failure")
        self.assertEqual(self.run_stage("preflight")[0]["status"], "passed")
        ops, size = self.d2.ops, self.d2.source["raw"]["bytes"]
        original = ops.pread
        def damaged(fd, count, offset):
            data = original(fd, count, offset)
            if data and ops.fds[fd][1] and ops.writes and offset >= size:
                return b"!" + data[1:]
            return data
        with mock.patch.object(ops, "pread", side_effect=damaged):
            result, _ = self.run_stage("deploy")
        self.assertEqual(result["status"], "failed", result)
        self.assertIs(result["needs_recovery"], True)
        manifest = Path(result["evidence_path"]) / "operation/media/manifest.json"
        receipt = lab.deploy.load(self.ref(manifest))
        self.assertEqual(receipt["remote"]["readback"], self.d2.source["raw"])
        self.assertIs(receipt["full_readback_verified"], False)
        with self.assertRaises(ValueError):
            lab.validate_media_receipt(receipt, self.contract, self.d2.source, "deploy")
        store = lab.StateStore(self.config, self.contract, self.d2.source)
        self.assertIs(store.read()["writer_unresolved"], True)
        prior = (self.locks / store.name).read_bytes()
        for _ in range(2):
            recovered, runtime = self.run_stage("recovery")
            self.assertEqual(recovered["status"], "blocked")
            runtime.assert_not_called()
            self.assertEqual((self.locks / store.name).read_bytes(), prior)

    def test_five_stages_use_actual_external_receipts_without_fake_cid(self):
        for stage in lab.STAGES:
            report, runtime = self.run_stage(stage)
            self.assertEqual(report["status"], "passed", report)
            self.assertIs(report["needs_recovery"], False)
            self.assertIs(report["original_boot_chain_verified"], False)
            self.assertEqual(runtime.call_count, 1)
        store = lab.StateStore(self.config, self.contract, self.d2.source)
        self.assertEqual((store.read()["stage"], store.read()["phase"]), ("recovery", "rescue"))
        self.assertNotIn("cid", self.contract["target"])

    def test_orphan_pending_blocks_without_runtime(self):
        with lab.core.resource_lock(self.config):
            store = lab.StateStore(self.config, self.contract, self.d2.source)
            lab.deploy.save(self.locks, store.pending, b"{}")
        result, execute = self.run_stage("preflight")
        self.assertEqual(result["status"], "blocked")
        execute.assert_not_called()
        self.assertTrue((self.locks / store.pending).exists())

    def test_deploy_interruption_never_allows_recovery(self):
        self.assertEqual(self.run_stage("preflight")[0]["status"], "passed")
        req = {**self.request, "stage": "deploy"}
        with mock.patch.object(lab, "check_qualification", return_value={"approved_images": [req["image_sha256"]]}), \
                mock.patch.object(lab.NativeRuntime, "execute", side_effect=TimeoutError):
            report = lab.run_stage(self.config_ref["path"], self.config_ref["sha256"], req)
        self.assertIs(report["needs_recovery"], True)
        for _ in range(2):
            result, execute = self.run_stage("recovery")
            self.assertEqual(result["status"], "blocked")
            execute.assert_not_called()
        self.assertIs(lab.StateStore(self.config, self.contract, self.d2.source).read()["writer_unresolved"], True)

    def test_falsey_runtime_cannot_construct_native_or_grant_success(self):
        class Falsey:
            def __bool__(self):
                return False
            execute = mock.Mock(side_effect=TimeoutError)
        with mock.patch.object(lab, "check_qualification", return_value={"approved_images": [self.request["image_sha256"]]}), \
                mock.patch.object(lab, "NativeRuntime", side_effect=AssertionError("不得建立原生入口")):
            report = lab.run_stage(self.config_ref["path"], self.config_ref["sha256"], self.request, runtime=Falsey())
        self.assertIs(report["hardware_validated"], False)
        self.assertIs(report["needs_recovery"], False)

    def test_native_runtime_five_stages_with_only_hardware_edges_replaced(self):
        with self.native_edges(), \
                mock.patch.object(lab, "check_qualification", return_value={"approved_images": [self.request["image_sha256"]]}):
            for stage in lab.STAGES:
                req = {**self.request, "stage": stage}
                report = lab.run_stage(self.config_ref["path"], self.config_ref["sha256"], req)
                self.assertEqual(report["status"], "passed", report)
        self.assertFalse(self.d2.ops.fds)
        self.assertTrue(self.d2.ops.writes)

    def test_each_native_evidence_field_is_required(self):
        self.assertEqual(self.run_stage("preflight")[0]["status"], "passed")
        self.assertEqual(self.run_stage("deploy")[0]["status"], "passed")
        current = lab.StateStore(self.config, self.contract, self.d2.source).read()
        req = {**self.request, "stage": "boot"}
        result = self.fake_result((self.config, self.contract, self.bundle, self.boot, self.expected, req), current,
                                  self.root / "negative", 600)
        for field in result:
            changed = copy.deepcopy(result)
            changed.pop(field)
            with self.subTest(field=field), self.assertRaises(ValueError):
                lab.validate_result("boot", changed, req, self.config, self.contract, self.bundle, self.boot, self.expected, current=current)
        for change in (lambda row: row.update(action="recovery"), lambda row: row.update(simulated=True),
                       lambda row: row["session"]["binding"].update(attempt_id="other"),
                       lambda row: row["session"].update(boot_id=current["boot_id"]),
                       lambda row: row["validation"]["checks"].pop()):
            changed = copy.deepcopy(result)
            change(changed)
            with self.assertRaises(ValueError):
                lab.validate_result("boot", changed, req, self.config, self.contract, self.bundle, self.boot, self.expected, current=current)

    def test_customer_root_sd_and_ssh_snapshot_are_replayed(self):
        req = {**self.request, "stage": "smoke"}
        context = self.config, self.contract, self.bundle, self.boot, self.expected, req
        session = self.session("customer", context, self.root / "customer-session", lab.life.Deadline(600))
        original = lab.deploy.load(session["ssh_observation"])
        for mutate in (lambda value: value["observation"]["protected_sd"].update(full_sha256="0" * 64),
                       lambda value: value["observation"]["root"]["block"].update(uuid="wrong"),
                       lambda value: value["observation"].update(boot_id=str(uuid.UUID(int=99))),
                       lambda value: value.update(validation={"ok": True})):
            changed = copy.deepcopy(original)
            mutate(changed)
            rebound = {**session, "ssh_observation": self.write("wrong-collection.json", changed)}
            with self.assertRaises(ValueError):
                lab.validate_session(rebound, req, "customer", self.config, self.contract, self.bundle, self.expected)
        path = Path(original["ssh_config"]["path"])
        path.chmod(0o600)
        path.write_bytes(path.read_bytes().replace(b"StrictHostKeyChecking yes", b"StrictHostKeyChecking no"))
        original["ssh_config"] = self.ref(path)
        with self.assertRaises(ValueError):
            lab.validate_session({**session, "ssh_observation": self.write("wrong-ssh.json", original)}, req, "customer",
                                 self.config, self.contract, self.bundle, self.expected)

    def test_recovery_retry_has_new_directory_and_keeps_prior_failure(self):
        self.assertEqual(self.run_stage("preflight")[0]["status"], "passed")
        req = {**self.request, "stage": "recovery"}
        with mock.patch.object(lab, "check_qualification", return_value={"approved_images": [req["image_sha256"]]}), \
                mock.patch.object(lab.NativeRuntime, "execute", side_effect=TimeoutError):
            report = lab.run_stage(self.config_ref["path"], self.config_ref["sha256"], req)
        self.assertIs(report["needs_recovery"], True)
        path = Path(report["evidence_path"]) / "report.json"
        before = path.read_bytes()
        recovered, _ = self.run_stage("recovery")
        self.assertEqual(recovered["status"], "passed", recovered)
        self.assertNotEqual(recovered["evidence_path"], report["evidence_path"])
        self.assertEqual(path.read_bytes(), before)

    def test_resume_rechecks_customer_without_deploying(self):
        reports = []
        for stage in lab.STAGES[:3]:
            report, _ = self.run_stage(stage)
            self.assertEqual(report["status"], "passed", report)
            reports.append({"stage": stage, **self.ref(Path(report["evidence_path"]) / "report.json")})
        writes = list(self.d2.ops.writes)
        result, execute = self.run_stage("preflight", resume={"next_stage": "smoke", "previous_reports": reports})
        self.assertEqual(result["status"], "passed", result)
        self.assertIs(result["resume_state_verified"], True)
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(self.d2.ops.writes, writes)
        self.assertEqual(self.run_stage("smoke")[0]["status"], "passed")

    def test_publication_deadline_failure_retains_recovery_signal(self):
        original = lab.StateStore.publish
        now = 0
        def late(store, *args):
            nonlocal now
            original(store, *args)
            now = 1000
        with mock.patch.object(lab.time, "monotonic", side_effect=lambda: now), \
                mock.patch.object(lab.StateStore, "publish", autospec=True, side_effect=late):
            report, _ = self.run_stage("preflight")
        self.assertEqual(report["status"], "failed")
        self.assertIs(report["hardware_validated"], False)
        self.assertIs(report["needs_recovery"], True)
        self.assertEqual(lab.StateStore(self.config, self.contract, self.d2.source).read()["status"], "failed")

    def test_guard_source_target_and_original_initrd_cannot_be_rebound(self):
        for mutate in (lambda row: row["target"]["identity"].update(wwid="naa.5000000000000009"),
                       lambda row: row["root"].update(uuid=str(uuid.UUID(int=99))),
                       lambda row: row["protected_sd"].update(full_sha256="f" * 64)):
            expected = copy.deepcopy(self.expected)
            mutate(expected)
            with self.assertRaises(ValueError):
                lab.validate_guard(self.bundle, self.config, self.contract, self.boot, expected)
        changed = copy.deepcopy(self.bundle)
        changed["original_components"]["initrd"] = self.bundle["original_components"]["kernel"]
        with self.assertRaises(ValueError):
            lab.validate_guard(changed, self.config, self.contract, self.boot, self.expected)

    def test_native_recovery_rejects_writer_before_console(self):
        req = {**self.request, "stage": "recovery"}
        current = {"stage": "deploy", "status": "failed", "phase": "rescue", "writer_unresolved": True}
        with mock.patch.object(lab.life.NativeRuntime, "console", side_effect=AssertionError("不得開啟 UART")):
            with self.assertRaises(ValueError):
                lab.NativeRuntime().execute((self.config, self.contract, self.bundle, self.boot, self.expected, req), current,
                                            self.root / "blocked-recovery", 600)


class GrowthBackendTests(Fixture):
    ROOT_GROWTH = True

    def observation(self, mode, nonce):
        value = super().observation(mode, nonce)
        if mode == "customer":
            size = self.contract["target"]["bytes"] // 512 - self.contract["root"]["start_lba"]
            for key in ("target", "target_after"):
                value[key]["partitions"][0]["sectors"] = size
            for key in ("root", "root_after"):
                value[key]["block"].update(bytes=size * 512, ioctl_bytes=size * 512)
            for key in ("inventory", "inventory_after"):
                for row in value[key]:
                    if row["partition_index"] == 1 and row["parent"] == value["target"]["sysfs_path"]:
                        row.update(bytes=size * 512, ioctl_bytes=size * 512)
        return value

    def test_two_native_cycles_with_grown_root_then_smaller_image(self):
        variants = self.reusable_images()
        original = Path(self.contract["backup"]["path"]).read_bytes()
        approved = [item["bundle"]["image"]["compressed"]["sha256"] for item in variants]
        with self.native_edges(), mock.patch.object(lab, "check_qualification", return_value={"approved_images": approved}):
            for index, variant in enumerate(variants):
                self.select_variant(variant, "growth-cycle-" + str(index))
                for stage in lab.STAGES:
                    request = {**self.request, "stage": stage}
                    report = lab.run_stage(self.config_ref["path"], self.config_ref["sha256"], request)
                    self.assertEqual(report["status"], "passed", report)
                self.d2.ops.data[str(self.d2.dev / "sda")][-32:] = b"C" * 32
        self.assertEqual(Path(self.contract["backup"]["path"]).read_bytes(), original)
        self.assertFalse(self.d2.ops.fds)

    def test_growth_must_match_prepared_source_partitions(self):
        prepared = lab.deploy.load(self.bundle["preparation"])
        prepared["partitions"][0]["sectors"] += 1
        self.bundle["preparation"] = self.write("changed-preparation.json", prepared)
        self.config["images"][self.request["image_sha256"]] = self.write("changed-bundle.json", self.bundle)
        with self.assertRaisesRegex(ValueError, "完整分割"):
            lab.selected_image(self.config, self.request)


if __name__ == "__main__":
    unittest.main()
