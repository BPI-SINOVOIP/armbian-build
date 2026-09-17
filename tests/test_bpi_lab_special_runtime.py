#!/usr/bin/env python3
"""合成資格與假 UART 的離線回歸；不構成實板資格，不開串口或操作媒體。"""

from contextlib import redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bpi_lab_console as console
from tools import bpi_lab_special as special
from tools import bpi_lab_special_runtime as runtime
from tools import bpi_lab_uboot as uboot
from tests.test_bpi_lab_special import UUID, fixture, release, template
from tests.test_bpi_lab_uboot import Channel, Clock


CID = "112233445566778899aabbccddeeff00"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def memory_output(core, loaded=(), *, include_lmb=True):
    lines = []
    for index, span in enumerate(core["ram"]["banks"]):
        lines.extend([f"DRAM bank = 0x{index:x}", f"-> start = 0x{span['start']:x}",
                      f"-> size = 0x{span['size']:x}"])
    reserved = core["ram"]["reserved"]
    lines.extend(["irq_sp = 0x0", "TLB addr = 0x0", f"relocaddr = 0x{reserved[0]['start'] + 0x100000:x}",
                  "monitor_size = 0x100000", f"malloc_start = 0x{reserved[0]['start'] + 0x300000:x}",
                  f"malloc_end = 0x{reserved[0]['start'] + 0x400000:x}",
                  f"sp start = 0x{reserved[0]['start'] + 0x200000:x}"])
    if not include_lmb:
        return "\r\n".join(lines).encode() + b"\r\n"
    spans = [{**span, "flags": "no-overwrite"} for span in reserved]
    spans += [{"start": address, "size": size, "flags": "none"} for address, size in loaded]
    spans.sort(key=lambda span: span["start"])
    lines.extend(["lmb_dump_all:", f" reserved.count = 0x{len(spans):x}"])
    for index, span in enumerate(spans):
        lines.append(f" reserved[{index}] [0x{span['start']:x}-0x{span['start'] + span['size'] - 1:x}], "
                     f"0x{span['size']:x} bytes, flags: {span['flags']}")
    return "\r\n".join(lines).encode() + b"\r\n"


class SpecialChannel(Channel):
    """沿用真正 nonce、ConsoleSession 與 RAM 雜湊模擬，不替換執行器核對。"""

    def response(self, command):
        if command in self.overrides:
            return super().response(command)
        if command in ("bdinfo", "bpilab memory"):
            return memory_output(self.config, [(address, len(data)) for address, data in self.memory.items()],
                                 include_lmb=command == "bdinfo")
        if command == "bpilab probe":
            return ("BPI_LAB_V1 " + runtime.scope_digest(self.runtime_config) + "\r\nBPI_LAB_STATE 0 0\r\n" +
                    "".join(f"CID[{i}]: 0x{CID[i * 8:(i + 1) * 8]}\r\n" for i in range(4)) +
                    "BPI_LAB_PART " + self.config["source"]["partuuid"] + "\r\n").encode()
        if command.startswith("bpilab load "):
            item = self.config["files"][command.split()[-1]]
            return super().response(f"load mmc 0:1 {item['address']:x} {item['path']} {item['bytes'] + 1:x} 0")
        if command.startswith("bpilab hash "):
            item = self.config["files"][command.split()[-1]]
            return super().response(f"hash sha256 {item['address']:x} {item['bytes']:x}")
        if command.startswith("mmc reg read cid "):
            index = int(command.split()[-1])
            return f"CID[{index}]: 0x{CID[index * 8:(index + 1) * 8]}\r\n".encode()
        return super().response(command)

    def write(self, wire):
        if wire != b"bpilab boot\n":
            return super().write(wire)
        self.commands.append("bpilab boot")
        self.writes.append(wire)
        output = self.response("bpilab probe")
        output += b"".join(self.response("bpilab hash " + role) for role in self.config["files"])
        self.queue.append(wire.replace(b"\n", b"\r\n") + output + self.kernel_response)
        return len(wire)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="bpi-special-runtime-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.counter = 0

    def reference(self, name, value):
        path = self.root / name
        data = value if isinstance(value, bytes) else special.encoded(value)
        path.write_bytes(data)
        return {"path": str(path), "sha256": digest(data)}

    def qualify(self, config, mutate=None):
        q = {"schema": runtime.QUALIFICATION_SCHEMA, "scope_sha256": runtime.scope_digest(config),
             "board": config["board"], "hardware_id": "offline-fixture-only", "approved": True,
             "hardware_validated": True, "kernel_may_write_root": True, "firmware_loads_approved": True,
             "source_evidence": [self.reference("synthetic-proof.txt", "僅為合成測資，不是實板資格。".encode())],
             "memory_evidence": self.reference("memory.txt", memory_output(config["template"])),
             "dependencies": {name: digest((Path(runtime.__file__).parent / name).read_bytes())
                              for name in runtime.DEPENDENCIES},
             "required_commands": runtime.required_commands(config["board"])}
        if mutate:
            mutate(q)
        if config["board"] in runtime.VENDOR_BOARDS:
            q["root_label_scope_approved"] = True
            q["vendor_build"] = {"source_sha256": runtime.vendor_source(config)["sha256"],
                                 **{name: self.reference("synthetic-" + name, b"offline-fixture-only\n")
                                    for name in ("binary", "build_config", "link_map")}}
        config["qualification"] = self.reference("qualification.json", q)

    def configuration(self, board="bpi-f2p", *, kernel_placement="original"):
        self.counter += 1
        output = self.root / f"components-{self.counter}"
        files = fixture(board)
        def read(path):
            if path not in files:
                raise FileNotFoundError(path)
            return files[path]
        prepared = special.prepare(read, board=board, kernel_release=release(board), output=output)
        self.assertEqual(prepared["status"], "prepared", prepared["blockers"])
        t = template(board)
        vendor = board in runtime.VENDOR_BOARDS
        t["ram"]["reserved"] = [{"start": 0x70000000 if vendor else 0xb0000000, "size": 0x1000000}]
        recipe = runtime.bootconfig(output, template=t, kernel_placement=kernel_placement)
        loads = {item["role"]: item for item in recipe["loads"]}
        pairing = {"schema": "bpi-lab-pairing-v1", "approved": True, "hardware_id": "offline-fixture-only",
                   "uart": {"stable_path": "/dev/serial/by-id/offline-fixture", "baud": 115200},
                   "resources": {"uart": "/dev/serial/by-id/offline-fixture"},
                   "emmc": {"cid": CID}, "protected_sd": {"cid": "f" * 32}}
        partition = {"index": 1, "partuuid": t["source"]["partuuid"]}
        extraction = {"schema": "bpi-lab-image-v1", "ok": True, "source_verified": True,
                      "filesystem_labels_complete": True,
                      "filesystem_label": "BPI-ROOT", "filesystem_label_unique": True,
                      "hardware_validated": False, "filesystem_uuid": UUID, "partition": partition,
                      "partitions": [partition], "files": {item["image_path"]: {
                          "volume_index": 1, "resolved": "/" + Path(item["image_path"]).name,
                          "digest": {key: item[key] for key in ("bytes", "sha256")}} for item in loads.values()}}
        entry = 0x80000 if vendor else 0x480000 if board == "bpi-ai2n" else t["addresses"]["kernel"]
        config = {"schema": runtime.SCHEMA, "board": board, "artifact_root": str(output), "template": t,
                  "execution": {"uboot": {"prompt": "BPI=> ", "version": "U-Boot 2025.01 (BPI)",
                                           "address_bits": 32 if board.startswith("bpi-f2") else 64,
                                           "line_limit": 1024, "abi": "mainline-v2025.01"},
                                "boot_region": {"start": 0, "size": 0x70000000 if vendor else 0xb0000000},
                                "capacities": {role: 0x20000 if role != "kernel" else 0x100000 for role in loads},
                                "kernel_entry": entry, "fdt_extra": 4096,
                                "transport": {"kind": "mmc-original", "media": "emmc",
                                              "extraction": self.reference("extraction.json", extraction),
                                              "image_paths": {role: item["image_path"] for role, item in loads.items()}}},
                  "pairing": self.reference("pairing.json", pairing), "qualification": {}}
        if vendor:
            if kernel_placement != "original":
                config["execution"]["kernel_placement"] = kernel_placement
            family = "139x" if board == "bpi-m4" else "129x"
            root = Path("/media/pi/SMCI/armbian/bpi-v26.2.1-bananapi-parallel/cache/sources/linux-kernel-worktree") / f"4.9__realtek-rtd{family}-bpi__arm64/u-boot-rtk"
            if not root.is_dir():
                self.skipTest("此來源契約測試需要已核對的本機 Realtek BSP")
            config["execution"]["vendor_sources"] = {name: {"path": str(root / name), "sha256": digest((root / name).read_bytes())}
                                                      for name in runtime.vendor_files(board)}
            config["execution"]["uboot"].update(abi=runtime.VENDOR_ABI, line_limit=640)
            transport = config["execution"]["transport"]
            t["source"].update(device="mmc", identity_sha256=transport["extraction"]["sha256"])
            prep = {"schema": "bpi-lab-prepare-v1", "board": board, "status": "prepared", "hardware_validated": False,
                    "root_uuid": UUID, "root_identity_verified": True,
                    "root_binding": {"label": "BPI-ROOT", "unique_in_image": True, "unique_on_hardware": False},
                    "extraction": transport["extraction"]}
            transport["root_preparation"] = self.reference("preparation.json", prep)
            t["vendor"]["root_identity"]["evidence_sha256"] = transport["root_preparation"]["sha256"]
        self.qualify(config)
        context = runtime._context(config)
        blobs = {role: (output / item["path"]).read_bytes() for role, item in loads.items()}
        return config, context, blobs

    def session(self, context, blobs):
        self.counter += 1
        clock = Clock()
        core = copy.deepcopy(context["core"])
        core["files"].update(context["firmware"])
        channel = SpecialChannel(core, blobs, clock)
        channel.runtime_config = context["config"]
        channel.kernel_response = ("\r\nStarting kernel ...\r\n[ 0.000000] Linux version "
                                   + core["kernel_release"] + " (test)\r\nlogin: ").encode()
        log = self.root / f"rx-{self.counter}.bin"
        session = console.ConsoleSession(channel, log_path=log, monotonic=clock)
        self.addCleanup(session.close)
        return session, channel, clock, log

    def w2_snapshot(self):
        """真實載荷加合成配對／RAM 核定，只供離線測試及 BSP 編譯。"""
        root = Path(special.__file__).resolve().parents[1] / "output/evidence/bpi-multiboard-integrate-20260917-w2-003"
        extraction_path = root / "extraction/extraction.json"
        if not extraction_path.is_file():
            self.skipTest("真 W2-003 唯讀擷取不在本機")
        self.assertEqual(digest(extraction_path.read_bytes()), "f8b931854e2d120f4df0ba60fb0bb7b877018717ceada5470c73ee3675306f0e")
        config, _, _ = self.configuration("bpi-w2", kernel_placement="direct-final")
        config["artifact_root"] = str(root / "components")
        manifest = special.validate(config["artifact_root"])
        extraction = json.loads(extraction_path.read_bytes())
        t, ex = config["template"], config["execution"]
        t["kernel_release"] = manifest["kernel_release"]
        t["source"].update(identity_sha256=digest(extraction_path.read_bytes()),
                           partuuid=next(row["partuuid"] for row in extraction["partitions"] if row["index"] == 1))
        prep_path = root / "preparation.json"
        t["vendor"]["root_identity"].update(uuid=extraction["filesystem_uuid"], evidence_sha256=digest(prep_path.read_bytes()))
        t["ram"]["kernel_work"] = {"start": manifest["components"]["kernel"]["text_offset"],
                                    "size": manifest["components"]["kernel"]["image_size"] + 65536}
        ex["kernel_entry"] = t["ram"]["kernel_work"]["start"]
        ex["capacities"] = {role: max(item["bytes"] + 1, item.get("image_size", 0)) + (65536 if role == "dtb" else 0)
                            for role, item in manifest["components"].items()}
        ex["transport"].update(extraction={"path": str(extraction_path), "sha256": digest(extraction_path.read_bytes())},
                               root_preparation={"path": str(prep_path), "sha256": digest(prep_path.read_bytes())},
                               image_paths={role: item["image_path"] for role, item in manifest["components"].items()})
        self.qualify(config)
        context = runtime._context(config)
        blobs = {role: (root / "components" / item["path"]).read_bytes() for role, item in manifest["components"].items()}
        return config, context, blobs

    def stopped(self, config, context, blobs, mutate):
        session, channel, clock, _ = self.session(context, blobs)
        mutate(channel)
        records = []
        with self.assertRaises((ValueError, OSError, TimeoutError)):
            runtime.boot(session, config, records, timeout=3, monotonic=clock)
        self.assertFalse(any(cmd.startswith(("bootm ", "booti ", "bpilab boot")) for cmd in channel.commands))
        self.assertFalse(channel.closed)
        return channel, records

    def test_four_boards_execute_real_runner_and_keep_raw_rx(self):
        for board in sorted(runtime.SUPPORTED):
            with self.subTest(board=board):
                config, context, blobs = self.configuration(board)
                session, channel, clock, log = self.session(context, blobs)
                records = []
                result = runtime.boot(session, config, records, monotonic=clock)
                self.assertEqual(result["status"], "kernel-marker-observed")
                self.assertEqual(result["observed_media_cid"], CID)
                self.assertEqual({item["role"] for item in result["observed_ram_hashes"]}, set(blobs))
                self.assertFalse(result["hardware_validated"] or result["root_verified"] or result["smoke_verified"])
                self.assertFalse(result["firmware_execution_verified"] or result["environment_saved"] or result["boot_chain_changed"])
                self.assertEqual(session.buffered, b"login: ")
                self.assertFalse(channel.closed)
                self.assertIn(b"CID[0]:", log.read_bytes())
                commands = channel.commands
                self.assertEqual(commands[-1], context["recipe"]["boot_command"])
                self.assertIn("mmc dev 0 0", commands)
                self.assertEqual(sum(cmd.startswith("mmc reg read cid") for cmd in commands), 8)
                self.assertLess(max(i for i, item in enumerate(records) if item["check"] == "length"),
                                min(i for i, item in enumerate(records) if item["check"] == "sha256"))
                self.assertEqual({item["component"] for item in records if item["check"] == "load-sha256"}, set(blobs))
                self.assertEqual(sum(item["check"] == "length" for item in records), len(blobs))
                self.assertTrue(all(record["status"] == "verified" for record in records[:-1]))
                for command in commands:
                    self.assertNotRegex(command, r"saveenv|env save|bootcmd|boot_targets|mmc (write|erase|partconf)|sf |reset|fatwrite|ext4write")
                if board == "bpi-ai2n":
                    for role in ("opencva", "codec"):
                        self.assertEqual(sum(item["role"] == role for item in result["observed_ram_hashes"]), 2)

    def test_lifecycle_binding_is_identity_view_not_firmware_execution(self):
        config, _, _ = self.configuration("bpi-ai2n")
        binding = runtime.backend_binding(config)
        self.assertTrue(binding["requires_runtime_dispatch"])
        self.assertEqual(binding["firmware_roles"], ["codec", "opencva"])
        self.assertEqual(uboot.validate_config(binding["lifecycle_view"]), runtime.lifecycle_view(config))
        self.assertEqual(runtime.validate_config(config), config)
        self.assertFalse(runtime.validate_artifacts(config)["uart_payloads_verified"])
        self.assertFalse(runtime.render(config)["executed"])

    def test_franklin_dispatch_and_bounded_console_preserve_runtime(self):
        from tools import bpi_lab_backend as backend
        from tools import bpi_lab_lifecycle as life
        config, context, blobs = self.configuration("bpi-ai2n")
        self.assertIs(life.boot_driver(config), runtime)
        self.assertEqual(life.customer_view(config), runtime.lifecycle_view(config))
        self.assertEqual(backend.render_family(special.validate(config["artifact_root"]), config["template"],
                                              config["artifact_root"], runtime_config=config), config)
        session, channel, clock, _ = self.session(context, blobs)
        bounded = life.BoundedConsole(session, life.Deadline(60, clock=clock))
        records = []
        result = life.boot_driver(config).boot(bounded, config, records, monotonic=clock)
        self.assertEqual(result["status"], "kernel-marker-observed")
        self.assertEqual({r["component"] for r in records if r["check"] == "sha256"}, set(blobs))
        self.assertFalse(channel.closed)

    def test_cid_and_partition_mismatch_stop_before_any_load(self):
        config, context, blobs = self.configuration()
        for command, reply in (("mmc reg read cid 2", b"CID[2]: 0x00000000\r\n"),
                               ("mmc reg read cid 0", b"CID[1]: 0x11223344\r\n"),
                               ("part uuid mmc 0:1", b"87654321-01\r\n")):
            channel, records = self.stopped(config, context, blobs, lambda c: c.overrides.update({command: reply}))
            self.assertFalse(any(cmd.startswith("load ") for cmd in channel.commands))
            self.assertEqual(records[-1]["status"], "failed")

    def test_franklin_vendor_dispatch_keeps_full_config_and_qualification(self):
        from tools import bpi_lab_backend as backend
        from tools import bpi_lab_lifecycle as life
        for board, placement in [(board, mode) for board in sorted(runtime.VENDOR_BOARDS) for mode in ("original", "direct-final")]:
            config, context, blobs = self.configuration(board, kernel_placement=placement)
            self.assertIs(life.boot_driver(config), runtime)
            view = life.customer_view(config)
            self.assertEqual(view["uboot"]["qualification_sha256"], config["qualification"]["sha256"])
            self.assertEqual(view["uboot"]["pairing_sha256"], config["pairing"]["sha256"])
            selected = backend.render_family(special.validate(config["artifact_root"]), config["template"],
                                             config["artifact_root"], runtime_config=config)
            self.assertEqual(selected, config)
            session, channel, clock, _ = self.session(context, blobs)
            bounded = life.BoundedConsole(session, life.Deadline(60, clock=clock))
            result = life.boot_driver(selected).boot(bounded, selected, monotonic=clock)
            self.assertEqual(result["vendor_lab_final_ram_roles"], list(context["core"]["files"]))
            self.assertEqual(channel.commands[-1], "bpilab boot")

    def test_unavailable_cid_or_hash_command_fails_closed(self):
        config, context, blobs = self.configuration()
        for command in ("help mmc", "mmc reg read cid 0", "help hash"):
            self.stopped(config, context, blobs, lambda c: c.failures.add(command))

    def test_firmware_actual_hash_length_and_filesize_not_declared_flags(self):
        config, context, blobs = self.configuration("bpi-ai2n")
        damaged = dict(blobs, codec=b"\x03" * len(blobs["codec"]))
        _, records = self.stopped(config, context, damaged, lambda _: None)
        self.assertEqual(records[-1]["check"], "load-sha256")
        self.assertEqual(records[-1]["component"], "codec")
        for data in (blobs["codec"][:-1], blobs["codec"] + b"\x00"):
            _, records = self.stopped(config, context, dict(blobs, codec=data), lambda _: None)
            self.assertEqual(records[-1]["check"], "length")
        self.stopped(config, context, blobs, lambda c: c.overrides.update({"printenv filesize": b"filesize=ffff\r\n"}))

    def test_later_load_and_late_fdt_corruption_of_firmware_detected(self):
        config, context, blobs = self.configuration("bpi-ai2n")
        for late in (False, True):
            def mutate(channel):
                command = "fdt resize 1000" if late else next(step["command"] for step in runtime.render(config)["steps"]
                                                              if step.get("component") == "codec" and step["check"] == "length")
                def response():
                    channel.overrides.pop(command)
                    output = channel.response(command)
                    address = context["firmware"]["opencva"]["address"]
                    channel.memory[address] = b"\xff" * len(blobs["opencva"])
                    return output
                channel.overrides[command] = response
            _, records = self.stopped(config, context, blobs, mutate)
            self.assertEqual(records[-1]["component"], "opencva")
            self.assertEqual(records[-1]["check"], "sha256")

    def test_lmb_proof_mismatch_truncation_and_late_change(self):
        config, context, blobs = self.configuration()
        original = memory_output(context["core"])
        for reply in (original.replace(b"reserved.count = 0x1", b"reserved.count = 0x2"),
                      original.replace(b"flags: no-overwrite", b"flags: none"),
                      original.replace(b"reserved.count", b"unknown.count")):
            self.stopped(config, context, blobs, lambda c: c.overrides.update({"bdinfo": reply}))
        def mutate(channel):
            channel.overrides["bdinfo"] = lambda: original.replace(b"flags: no-overwrite", b"flags: none") if channel.memory else original
        self.stopped(config, context, blobs, mutate)

    def test_load_lmb_tracks_exact_lengths_and_preserves_initial_regions(self):
        config, context, blobs = self.configuration("bpi-ai2n")
        session, channel, clock, _ = self.session(context, blobs)
        self.assertEqual(runtime.boot(session, config, monotonic=clock)["status"], "kernel-marker-observed")
        actual = runtime._lmb(channel.response("bdinfo"))
        for item in context["loads"].values():
            self.assertIn({"start": item["address"], "size": item["bytes"], "flags": "none"}, actual)
        for kind in ("missing", "extra-byte", "guard", "unknown"):
            def corrupt(channel, kind=kind):
                def response():
                    loaded = [(address, len(data)) for address, data in channel.memory.items()]
                    if loaded:
                        if kind == "missing":
                            loaded.pop()
                        elif kind == "extra-byte":
                            loaded[0] = (loaded[0][0], loaded[0][1] + 1)
                        elif kind == "unknown":
                            loaded.append((0x60000000, 4096))
                    raw = memory_output(channel.config, loaded)
                    return raw.replace(b"flags: none", b"flags: no-overwrite") if kind == "guard" else raw
                channel.overrides["bdinfo"] = response
            with self.subTest(kind=kind):
                self.stopped(config, context, blobs, corrupt)

    def test_sd_is_rejected_offline_in_both_mainline_and_vendor_paths(self):
        for board in ("bpi-ai2n", "bpi-w2"):
            config, context, blobs = self.configuration(board)
            config["execution"]["transport"]["media"] = "protected_sd"
            channel, _ = self.stopped(config, context, blobs, lambda _: None)
            self.assertEqual(channel.writes, [])

    def test_lmb_adjacent_same_flag_loads_coalesce_without_extra_space(self):
        _, context, _ = self.configuration()
        kernel = context["loads"]["kernel"]
        initrd = context["loads"]["initrd"]
        initrd["address"] = kernel["address"] + kernel["bytes"]
        context["loaded"] = ["kernel", "initrd"]
        for extra in (0, 1):
            raw = memory_output(context["core"], [(kernel["address"], kernel["bytes"] + initrd["bytes"] + extra)])
            if extra:
                with self.assertRaisesRegex(ValueError, "LMB"):
                    runtime._memory(raw, context)
            else:
                runtime._memory(raw, context)

    def test_realtek_direct_final_loads_only_final_destination(self):
        for board in sorted(runtime.VENDOR_BOARDS):
            config, context, blobs = self.configuration(board, kernel_placement="direct-final")
            self.assertEqual(context["loads"]["kernel"]["address"], config["execution"]["kernel_entry"])
            self.assertNotEqual(context["loads"]["kernel"]["address"], config["template"]["addresses"]["kernel"])
            session, channel, clock, _ = self.session(context, blobs)
            self.assertEqual(runtime.boot(session, config, monotonic=clock)["status"], "kernel-marker-observed")
            self.assertNotIn(config["template"]["addresses"]["kernel"], channel.memory)
            for mutate in (lambda c: c["template"]["ram"]["reserved"].append({"start": 0x80000, "size": 4096}),
                           lambda c: c["template"]["ram"]["kernel_work"].update(size=1024),
                           lambda c: c["execution"].update(kernel_placement="unknown"),
                           lambda c: c["execution"]["capacities"].update(kernel=0x2000000),
                           lambda c: c["template"]["bindings"].update(board="bpi-other"),
                           lambda c: c["template"]["addresses"].update(kernel=0x04000000)):
                broken = copy.deepcopy(config)
                mutate(broken)
                with self.assertRaises(ValueError):
                    runtime.vendor_source(broken)

    def test_true_w2_original_overlap_remains_rejected_but_lab_direct_final_runs(self):
        config, context, blobs = self.w2_snapshot()
        with self.assertRaisesRegex(ValueError, "原始載入區撞到保護區"):
            special.bootconfig(config["artifact_root"], template=config["template"])
        self.assertEqual(context["loads"]["kernel"]["address"], 0x280000)
        self.assertEqual(context["loads"]["kernel"]["image_size"], 21200896)
        self.assertFalse(context["recipe"]["original_staging_used"])
        session, channel, clock, _ = self.session(context, blobs)
        result = runtime.boot(session, config, monotonic=clock)
        self.assertEqual(result["status"], "kernel-marker-observed")
        self.assertFalse(result["hardware_validated"] or result["root_verified"])
        self.assertNotIn(0x3000000, channel.memory)
        self.assertEqual(channel.memory[0x280000], blobs["kernel"])

    def test_qualification_and_artifact_changes_send_nothing(self):
        config, context, blobs = self.configuration("bpi-ai2n")
        for mutate in (lambda q: q.update(approved=False), lambda q: q.update(hardware_validated=False),
                       lambda q: q.update(firmware_loads_approved=False), lambda q: q.update(kernel_may_write_root=False),
                       lambda q: q.update(scope_sha256="a" * 64), lambda q: q.update(required_commands=[]),
                       lambda q: q["dependencies"].update({"bpi_lab_special.py": "b" * 64})):
            self.qualify(config, mutate)
            channel, _ = self.stopped(config, context, blobs, lambda _: None)
            self.assertEqual(channel.writes, [])
        self.qualify(config)
        item = context["loads"]["codec"]
        (Path(config["artifact_root"]) / item["path"]).write_bytes(b"\x00" * item["bytes"])
        channel, _ = self.stopped(config, context, blobs, lambda _: None)
        self.assertEqual(channel.writes, [])

    def test_transport_and_capacity_mismatch_rejected_offline(self):
        config, context, blobs = self.configuration("bpi-ai2n")
        for mutate in (lambda c: c["execution"]["transport"].update(kind="tftp-ram"),
                       lambda c: c["execution"]["transport"]["image_paths"].update(codec="/boot/other.bin"),
                       lambda c: c["execution"]["capacities"].update(codec=0x300001),
                       lambda c: c["execution"].update(kernel_entry=0x300000),
                       lambda c: c["execution"]["uboot"].update(abi="vendor"),
                       lambda c: c.update(execution_ready=True)):
            changed = copy.deepcopy(config)
            mutate(changed)
            channel, _ = self.stopped(changed, context, blobs, lambda _: None)
            self.assertEqual(channel.writes, [])

    def test_wrong_root_uuid_and_incomplete_label_inventory_rejected(self):
        for board in ("bpi-ai2n", "bpi-w2"):
            config, context, blobs = self.configuration(board)
            transport = config["execution"]["transport"]
            original = json.loads(Path(transport["extraction"]["path"]).read_bytes())
            for change in ({"filesystem_uuid": "00000000-0000-0000-0000-000000000001"},
                           {"filesystem_labels_complete": False} if board == "bpi-w2" else {"filesystem_uuid": None}):
                changed = {**original, **change}
                transport["extraction"] = self.reference("extraction.json", changed)
                session, channel, clock, _ = self.session(context, blobs)
                with self.assertRaisesRegex(runtime.RuntimeError, "UUID|標籤"):
                    runtime.boot(session, config, monotonic=clock)
                self.assertEqual(channel.writes, [])

    def test_proof_bytes_and_symlinks_not_trusted(self):
        config, context, blobs = self.configuration()
        path = Path(config["pairing"]["path"])
        raw = path.read_bytes()
        path.write_bytes(raw + b" ")
        channel, _ = self.stopped(config, context, blobs, lambda _: None)
        self.assertEqual(channel.writes, [])
        path.unlink()
        actual = self.root / "actual-pairing.json"
        actual.write_bytes(raw)
        path.symlink_to(actual)
        channel, _ = self.stopped(config, context, blobs, lambda _: None)
        self.assertEqual(channel.writes, [])

    def test_fragmented_rx_echo_timeout_and_no_automatic_retry(self):
        config, context, blobs = self.configuration()
        session, channel, clock, _ = self.session(context, blobs)
        channel.fragment = True
        self.assertEqual(runtime.boot(session, config, monotonic=clock)["status"], "kernel-marker-observed")
        channel, records = self.stopped(config, context, blobs, lambda c: setattr(c, "echo_only", True))
        self.assertEqual(channel.commands, ["version"])
        self.assertEqual(records[-1]["status"], "failed")

    def test_wrong_kernel_or_return_to_prompt_not_success(self):
        config, context, blobs = self.configuration()
        for output in (b"\r\nBPI=> ", b"\r\nLinux version 0 (test)\r\n"):
            session, channel, clock, _ = self.session(context, blobs)
            channel.kernel_response = output
            records = []
            with self.assertRaises(uboot.UBootError):
                runtime.boot(session, config, records, monotonic=clock)
            self.assertEqual(records[-1]["status"], "failed")
            self.assertEqual(sum(cmd.startswith("bootm ") for cmd in channel.commands), 1)

    def test_realtek_refuses_without_uart_or_mainline_substitution(self):
        config, context, blobs = self.configuration()
        for board, reason in (("bpi-w2", "fatload"), ("bpi-m4", "IPC")):
            changed = dict(config, board=board)
            session, channel, clock, _ = self.session(context, blobs)
            with self.assertRaisesRegex(runtime.RuntimeError, reason):
                runtime.boot(session, changed, monotonic=clock)
            self.assertEqual(channel.writes, [])

    def test_realtek_lab_source_and_uart_runtime_both_boards(self):
        for board in sorted(runtime.VENDOR_BOARDS):
            config, context, blobs = self.configuration(board)
            generated = runtime.vendor_source(config)
            self.assertFalse(generated["hardware_validated"] or generated["deployment_verified"])
            self.assertEqual(generated["sha256"], digest(generated["source"].encode()))
            self.assertIn("p->size + 1", generated["source"])
            self.assertIn("received != p->size", generated["source"])
            self.assertIn("do_go_all_fw();" if board == "bpi-m4" else "return rtk_call_booti();", generated["source"])
            session, channel, clock, _ = self.session(context, blobs)
            records = []
            result = runtime.boot(session, config, records, monotonic=clock)
            self.assertEqual(result["status"], "kernel-marker-observed")
            self.assertEqual(set(result["vendor_lab_final_ram_roles"]), set(blobs))
            self.assertFalse(result["hardware_validated"])
            self.assertEqual(channel.commands[-1], "bpilab boot")
            self.assertFalse(any(cmd.startswith(("gosd", "booti", "bootm", "saveenv", "go ")) for cmd in channel.commands))
            self.assertEqual(session.buffered, b"login: ")

    def test_realtek_wrong_actual_payload_or_probe_stops_before_handoff(self):
        config, context, blobs = self.configuration("bpi-m4")
        for data in (b"\xff" * len(blobs["audio"]), blobs["audio"] + b"\x00", blobs["audio"][:-1]):
            self.stopped(config, context, dict(blobs, audio=data), lambda _: None)
        for field in (b"BPI_LAB_STATE 0 0", b"CID[1]: 0x55667788", b"BPI_LAB_PART 12345678-01"):
            def mutate(channel):
                channel.overrides["bpilab probe"] = channel.response("bpilab probe").replace(field, b"wrong")
            channel, _ = self.stopped(config, context, blobs, mutate)
            self.assertFalse(any(cmd.startswith("bpilab load") for cmd in channel.commands))

    def test_realtek_final_report_is_not_just_preload_summary(self):
        config, context, blobs = self.configuration("bpi-w2")
        session, channel, clock, _ = self.session(context, blobs)
        original = channel.write
        def write(wire):
            if wire == b"bpilab boot\n":
                item = context["loads"]["audio"]
                channel.memory[item["address"]] = b"\xff" * item["bytes"]
            return original(wire)
        channel.write = write
        with self.assertRaisesRegex(uboot.UBootError, "SHA-256"):
            runtime.boot(session, config, monotonic=clock)

    def test_realtek_build_source_and_root_proof_are_required(self):
        config, context, blobs = self.configuration("bpi-m4")
        for name in ("binary", "build_config", "link_map"):
            qualification = json.loads(Path(config["qualification"]["path"]).read_bytes())
            qualification["vendor_build"][name]["sha256"] = "0" * 64
            config["qualification"] = self.reference("qualification.json", qualification)
            channel, _ = self.stopped(config, context, blobs, lambda _: None)
            self.assertEqual(channel.writes, [])
            self.qualify(config)
        config["execution"]["transport"]["root_preparation"]["sha256"] = "0" * 64
        channel, _ = self.stopped(config, context, blobs, lambda _: None)
        self.assertEqual(channel.writes, [])

    def test_register_source_cannot_be_replaced_by_self_reported_digest(self):
        for board in sorted(runtime.VENDOR_BOARDS):
            config, _, _ = self.configuration(board)
            name = next(name for name in runtime.VENDOR_PINNED[board] if name.endswith("nand_reg.h"))
            config["execution"]["vendor_sources"][name] = self.reference("forged-register.h", b"#define OTP_BIT_SECUREBOOT 0\n")
            with self.assertRaisesRegex(ValueError, "來源偏離"):
                runtime.vendor_source(config)

    def test_generated_c_compiles_and_enforces_boundaries_before_vendor_handoff(self):
        if not shutil.which("cc"):
            self.skipTest("C 行為測試需要本機 cc 與 OpenSSL 開發檔")
        for board, placement in [(board, mode) for board in sorted(runtime.VENDOR_BOARDS) for mode in ("original", "direct-final")]:
            config, context, blobs = self.configuration(board, kernel_placement=placement)
            source = runtime.vendor_source(config)["source"]
            folder = self.root / (board + "-" + placement + "-c-harness")
            folder.mkdir()
            for header in ("mmc.h", "part.h", "fs.h", "malloc.h", "u-boot/sha256.h"):
                target = folder / header
                target.parent.mkdir(exist_ok=True)
                target.write_text("/* 主機替身標頭；不是 BSP 建置證據。 */\n")
            for index, role in enumerate(context["core"]["files"]):
                (folder / f"{index}.bin").write_bytes(blobs[role])
            # 編譯真正產生的 C 函式，以匿名主機 RAM／假 MMC 與 OpenSSL 實算 SHA-256。
            prefix = r'''
#define _GNU_SOURCE
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/mman.h>
#include <openssl/sha.h>
#define CONFIG_SHA256 1
#define CONFIG_PARTITION_UUIDS 1
#define CONFIG_GENERIC_MMC 1
#define CONFIG_RTD1395 1
#define CONFIG_RTD1295 1
#define CONFIG_NR_DRAM_BANKS 1
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define SWAPEND32(a) __builtin_bswap32(a)
#define MIPS_KSEG0BASE 0x80000000U
#define CLOCK_ENABLE2_reg 0x98000010UL
#define _BIT4 0x10U
#define OTP_REG_BASE 0x98017000UL
#define OTP_BIT_SECUREBOOT 3494
#define IS_SD(m) ((m)->version)
#define NONE_SECURE_BOOT 0
#define FS_TYPE_FAT 1
#define CMD_RET_FAILURE 1
#define CMD_RET_USAGE 2
#define CHUNKSZ_SHA256 65536
#define BOOT_RESCUE_MODE 3
#define U_BOOT_CMD(...)
#define setenv lab_setenv
typedef unsigned long ulong;
typedef int cmd_tbl_t;
typedef struct { char uuid[37]; } disk_partition_t;
struct mmc { unsigned int cid[4]; int version, part_num, block_dev; };
static struct mmc media = {{0x11223344, 0x55667788, 0x99aabbcc, 0xddeeff00}, 0, 0, 0};
static struct { struct { ulong start, size; } bi_dram[1]; } bd = {{{0,0x80000000}}};
static struct { typeof(bd) *bd; ulong relocaddr, start_addr_sp, irq_sp, mon_len;
                struct { ulong tlb_addr; } arch; void *fdt_blob, *new_fdt; ulong fdt_size;
              } gd_value = {&bd,0x70100000,0x70200000,0,0x100000,{0},NULL,NULL,0};
static typeof(gd_value) *gd = &gd_value;
static ulong mem_malloc_start=0x70300000, mem_malloc_end=0x70400000;
static struct { unsigned int audio_fw_entry_pt; } ipc_shm;
static int secure_mode, audio_fw_state, ipc_ir_set, boot_mode, called, fault;
static unsigned int rtd_inl(ulong reg) {
    if (reg == OTP_REG_BASE + (OTP_BIT_SECUREBOOT / 32) * 4) return secure_mode << (OTP_BIT_SECUREBOOT % 32);
    assert(reg == CLOCK_ENABLE2_reg); return fault == 17 ? _BIT4 : 0;
}
static unsigned int rtk_get_secure_boot_type(void) { return 0; }
static struct mmc *find_mmc_device(int dev) { return dev == 0 ? &media : NULL; }
static int mmc_init(struct mmc *m) { return m != &media; }
static int get_partition_info(int *device, int part, disk_partition_t *out)
{ assert(device == &media.block_dev && part == 1); strcpy(out->uuid, fault == 4 ? "87654321-01" : "12345678-01"); return 0; }
static int lab_setenv(const char *name, const char *value)
{ assert(strcmp(name,"bootcmd") && strcmp(name,"boot_targets")); return 0; }
static int setenv_hex(const char *name, ulong value) { return 0; }
static int fs_set_blk_dev(const char *device, const char *part, int type)
{ assert(!strcmp(device,"mmc") && !strcmp(part,"0:1") && type == FS_TYPE_FAT); return 0; }
static int fs_read(const char *, ulong, loff_t, loff_t, loff_t *);
static void sha256_csum_wd(const unsigned char *data, unsigned int size, unsigned char *out, unsigned int chunk)
{ assert(SHA256(data,size,out)); }
static int do_go_all_fw(void) { ++called; return 0; }
static int do_go_audio_fw(void) { ++called; return 0; }
static int rtk_call_booti(void) { ++called; return 0; }
'''
            suffix = r'''
static int fs_read(const char *path, ulong address, loff_t offset, loff_t count, loff_t *received)
{
    unsigned int i;
    char name[64];
    FILE *input;
    for (i=0; i<ARRAY_SIZE(bpi_lab_payloads); ++i) {
        const struct bpi_lab_payload *p = &bpi_lab_payloads[i];
        if (strcmp(p->path,path)) continue;
        assert(address == p->address && offset == 0 && count == (loff_t)p->size + 1);
        snprintf(name,sizeof(name),"%u.bin",i);
        input=fopen(name,"rb"); assert(input);
        *received=fread((void *)address,1,count,input); fclose(input);
        if (fault == 5) --*received;
        if (fault == 6) { ((char *)address)[p->size]=0; ++*received; }
        return fault == 7 ? -1 : 0;
    }
    return -1;
}
int main(int argc, char **argv)
{
    unsigned int i;
    char *probe[]={"bpilab","probe",NULL};
    char *load[]={"bpilab","load",NULL,NULL};
    char *boot[]={"bpilab","boot",NULL};
    fault=argc>1 ? atoi(argv[1]) : 0;
    (void)boot_mode;
    for (i=0; i<ARRAY_SIZE(bpi_lab_payloads); ++i) {
        const struct bpi_lab_payload *p=&bpi_lab_payloads[i];
        size_t bytes=(p->size+4096)&~4095UL;
        assert(mmap((void *)p->address,bytes,PROT_READ|PROT_WRITE,
                    MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0)==(void *)p->address);
    }
    if (fault==1) secure_mode=1;
    if (fault==2) media.cid[2]^=1;
    if (fault==3) gd->start_addr_sp=0x2100000;
    if (fault==10) audio_fw_state=1;
    if (fault==11) mem_malloc_end=0x72000000;
    if (fault==12) gd->mon_len=0x2000000;
    if (fault==13) { gd->fdt_blob=(void *)0x2100000; gd->fdt_size=4096; }
    if (fault==14) ipc_shm.audio_fw_entry_pt=1;
    if (fault==15) media.version=1;
    if (fault==16) media.part_num=1;
    if ((fault>=1 && fault<=4) || fault>=10) {
        assert(do_bpilab(NULL,0,2,probe)!=0 && called==0); return 0;
    }
    assert(do_bpilab(NULL,0,2,probe)==0);
    assert(do_bpilab(NULL,0,2,boot)!=0 && called==0);
    for (i=0; i<ARRAY_SIZE(bpi_lab_payloads); ++i) {
        load[2]=(char *)bpi_lab_payloads[i].role;
        if (fault>=5 && fault<=7) { assert(do_bpilab(NULL,0,3,load)!=0 && called==0); return 0; }
        if (fault==9 && i==0) continue;
        assert(do_bpilab(NULL,0,3,load)==0);
    }
    if (fault==8) ((char *)bpi_lab_payloads[0].address)[0]^=1;
    if (fault==8 || fault==9) { assert(do_bpilab(NULL,0,2,boot)!=0 && called==0); return 0; }
    assert(do_bpilab(NULL,0,2,boot)==0);
    assert(called==EXPECTED_HANDOFF_CALLS);
    assert(do_bpilab(NULL,0,2,boot)!=0 && called==EXPECTED_HANDOFF_CALLS);
    return 0;
}
'''
            unit = folder / "test.c"
            unit.write_text(prefix + source + suffix)
            built = subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                                    "-Wno-unused-function", f"-DEXPECTED_HANDOFF_CALLS={1 if board == 'bpi-m4' else 2}",
                                    "-I", str(folder), str(unit), "-lcrypto", "-o", str(folder / "test")],
                                   capture_output=True, text=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stderr)
            for scenario in range(18):
                tested = subprocess.run([str(folder / "test"), str(scenario)], cwd=folder,
                                        capture_output=True, text=True, timeout=5)
                self.assertEqual(tested.returncode, 0, f"{board}:{scenario}\n{tested.stdout}\n{tested.stderr}")

    def test_original_vendor_booti_setup_skips_copy_at_final_destination(self):
        if not shutil.which("cc"):
            self.skipTest("原廠搬移函式行為測試需要本機 cc")
        for board in sorted(runtime.VENDOR_BOARDS):
            config, _, _ = self.configuration(board)
            raw = runtime._reference(config["execution"]["vendor_sources"]["common/cmd_bootm.c"]).decode()
            start = raw.index("#ifdef CONFIG_ARM64_IMAGE_LEGACY\n", raw.index("#ifdef CONFIG_CMD_BOOTI\n"))
            end = raw.index("\n/*\n * Image booting support", start)
            # 保留原廠結構與完整 booti_setup 函式；只替換主機無法使用的硬體介面。
            prefix = r'''
#define _GNU_SOURCE
#include <stdint.h>
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
#include <sys/mman.h>
#define CONFIG_GZIP_KERNEL_MAX_LEN 0x8000000
#define CONFIG_GZIP_DECOMPRESS_KERNEL_ADDR 0x2000000
#define le32_to_cpu(x) ((uint32_t)(x))
#define debug(...) ((void)0)
typedef struct { unsigned long ep; } bootm_headers_t;
static struct { struct { unsigned long start; } bi_dram[1]; } bd = {{{0}}};
static struct { typeof(bd) *bd; } gd_value = {&bd};
static typeof(gd_value) *gd = &gd_value;
static unsigned int copies;
static void *map_sysmem(unsigned long address, unsigned long size) { return (void *)address; }
static int gunzip(void *dst, int size, unsigned char *src, unsigned long *len) { return 1; }
static void *tracked_move(void *dst, const void *src, size_t size) { ++copies; return memmove(dst, src, size); }
#define memmove tracked_move
'''
            suffix = r'''
int main(void) {
    struct Image_header *original = (void *)0x03000000, *final = (void *)0x00280000;
    bootm_headers_t images = {(unsigned long)original};
    assert(mmap(original,4096,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0)==original);
    assert(mmap(final,4096,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0)==final);
    memset(original,0xa5,4096);
    original->magic=LINUX_ARM64_IMAGE_MAGIC; original->text_offset=(uintptr_t)final; original->image_size=4096;
    assert(booti_setup(&images)==0 && images.ep==(uintptr_t)final && copies==1);
    assert(!memcmp(original,final,4096));
    memset(original,0x5a,4096);
    assert(booti_setup(&images)==0 && images.ep==(uintptr_t)final && copies==1);
    assert(final->magic==LINUX_ARM64_IMAGE_MAGIC);
    final->magic=0;
    assert(booti_setup(&images)!=0 && copies==1);
    return 0;
}
'''
            unit = self.root / (board + "-original-booti.c")
            unit.write_text(prefix + raw[start:end] + suffix)
            built = subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                                    str(unit), "-o", str(unit.with_suffix(""))], capture_output=True, text=True, timeout=30)
            self.assertEqual(built.returncode, 0, built.stderr)
            run = subprocess.run([str(unit.with_suffix(""))], capture_output=True, text=True, timeout=5)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_offline_cli_and_duplicate_json(self):
        config, _, _ = self.configuration()
        path = self.reference("runtime.json", config)["path"]
        for action in ("validate", "render"):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(runtime.main([action, "--config", path]), 0)
            self.assertIsInstance(json.loads(output.getvalue()), dict)
        with self.assertRaises(runtime.RuntimeError):
            runtime._json(b'{"schema": 1, "schema": 2}')
        with self.assertRaises(runtime.RuntimeError):
            runtime._json(b'{"x": NaN}')


if __name__ == "__main__":
    unittest.main()
