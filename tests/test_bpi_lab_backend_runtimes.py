#!/usr/bin/env python3
"""原配來源、後端選擇與真正 UART runner 的離線整合；不開啟實體設備。"""

import copy
import hashlib
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_bpi_lab_backend as data
import test_bpi_lab_allwinner as allwinner_data
import test_bpi_lab_lifecycle as lifecycle_data
import test_bpi_lab_original_entry as entry_data
import test_bpi_lab_special_runtime as special_data
import test_bpi_lab_realtek_rescue as rescue_data
from bpi_lab_evidence_fixture import EvidenceFixture

backend = data.backend
life = backend.life


class RuntimeIntegrationTests(EvidenceFixture, data.BackendFixture):
    def setUp(self):
        popen = subprocess.Popen
        self.original_family_builder = backend.allwinner.build_uboot_config
        super().setUp()
        self.common_rescue = copy.deepcopy(self.rescue)
        self.initial_contract, self.initial_pairing = copy.deepcopy((self.contract, self.pairing))
        self.initial_lifecycle = copy.deepcopy(self.lifecycle)
        def local_dtb_only(argv, *args, **kwargs):
            if argv[0] not in ("/usr/bin/dtc", "/usr/bin/fdtget") and list(argv) != ["/sbin/ldconfig", "-p"]:
                raise AssertionError("整合測試只允許本機 DTB 編譯與唯讀解析")
            return popen(argv, *args, **kwargs)
        mock.patch.object(subprocess, "Popen", side_effect=local_dtb_only).start()

    def prepare_h618(self, board):
        helper = allwinner_data.AllwinnerTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.files, helper.row, helper.policy, helper.release = allwinner_data.image_fixture(board)
        manifest = helper.prepare()
        self.assertEqual(manifest["status"], "prepared", manifest)
        self.family_builder.side_effect = self.original_family_builder
        self.contract, self.pairing = copy.deepcopy((self.initial_contract, self.initial_pairing))
        self.lifecycle, self.rescue = copy.deepcopy((self.initial_lifecycle, self.common_rescue))
        hardware = "independent-fixture-" + board
        self.contract["hardware_id"] = hardware
        self.contract["authorization"]["hardware_id"] = hardware
        self.resources = self.pairing["resources"]
        self.pairing.update(hardware_id=hardware, rescue=self.contract["rescue"])
        pairing = self.write_json("pairing.json", self.pairing)
        build = self.write_json("h618-mainline-qualification.json", {"record": "全新主線合成核定，不是實板資格"})
        template, _ = lifecycle_data.boot_data.fixture(source="tftp", initrd="legacy")
        template["kernel_release"] = manifest["kernel_release"]
        template["uboot"].update(pairing_sha256=pairing["sha256"], qualification_sha256=build["sha256"], line_limit=4096)
        template["bootargs"] = [arg.replace("${partuuid}", "12345678-01").replace("${devtype}", "tftp")
                                for arg in manifest["bootargs_template"]]
        rendered = self.original_family_builder(manifest, template=template, artifact_root=helper.output)
        tftp = self.root / (board + "-published")
        tftp.mkdir()
        transport = {"kind": "tftp-published", "root": str(tftp), "serverip": template["source"]["serverip"]}
        native = backend.bind_transport({"transport": transport}, rendered, {})
        blobs = {role: (helper.output / rendered["files"][role]["path"]).read_bytes() for role in native["files"]}
        self.expected.update(architecture="arm64", kernel_release=manifest["kernel_release"],
                             dt_compatible=manifest["profile"]["compatible"],
                             root={**self.contract["expected"], "uuid": manifest["root_uuid"], "media_type": "MMC"})
        parent = self.root / (board + "-prepare")
        parent.mkdir()
        def child(name, value):
            ref = self.write_json(str(parent / name), value)
            return {"path": name, "sha256": ref["sha256"], "bytes": Path(ref["path"]).stat().st_size}
        extraction = {"schema": "bpi-lab-image-v1", "ok": True, "source_verified": True,
                      "source_digest": self.source_record["compressed"], "raw": self.source_record["raw"],
                      "filesystem_uuid": manifest["root_uuid"]}
        preparation = {"schema": "bpi-lab-prepare-v1", "status": "prepared", "hardware_validated": False,
                       "board": board, "kernel_release": manifest["kernel_release"],
                       "source": self.source_record["compressed"], "raw": self.source_record["raw"],
                       "root_uuid": manifest["root_uuid"], "root_uuid_verified": True, "root_identity_verified": True,
                       "components": child("manifest.json", manifest), "extraction": child("extraction.json", extraction)}
        self.rescue["uboot"].update(pairing_sha256=pairing["sha256"], qualification_sha256=build["sha256"])
        self.lifecycle.update(hardware_id=hardware, pairing_sha256=pairing["sha256"],
                              rescue_uboot=self.write_json("rescue-uboot.json", self.rescue), rescue_qualification=build)
        self.bundle.update(board=board, preparation=self.write_json(str(parent / "preparation.json"), preparation),
                           uboot=self.write_json("customer-uboot.json", native), uboot_qualification=build,
                           uboot_template=self.write_json("uboot-template.json", template), artifact_root=str(helper.output),
                           linux_expected=self.write_json("linux.json", self.expected), transport=transport)
        self.config_document.update(hardware_id=hardware, pairing=pairing, resources=self.resources,
                                    deploy=self.write_json("deploy.json", self.contract),
                                    lifecycle=self.write_json("lifecycle.json", self.lifecycle),
                                    images={self.request["image_sha256"]: self.write_json("bundle.json", self.bundle)})
        self.sync_config()
        self.request.update(hardware_id=hardware, boot_config_sha256=self.config_ref["sha256"])
        self.request["image"]["board"] = board
        return native, lifecycle_data.Channel(native, blobs, lifecycle_data.boot_data.Clock())

    def prepare_runtime(self, board, *, original=False, split=False, compressed=False, vendor_rescue=True, fit_kernel=False):
        self.rescue = copy.deepcopy(self.common_rescue)
        vendor = not original and board in life.special_runtime.VENDOR_BOARDS
        helper = entry_data.OriginalEntryTests() if original else special_data.RuntimeTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        if original:
            native = helper.make(board, split=split, compressed=compressed, fit_kernel=fit_kernel)
            emmc, sd, hardware = entry_data.EMMC_CID, entry_data.SD_CID, native["hardware_id"]
            artifact_root = helper.output
            extraction = copy.deepcopy(helper.extracted)
        else:
            native, initial_context, blobs = helper.configuration(board)
            emmc, sd, hardware = special_data.CID, "f" * 32, "offline-fixture-only"
            artifact_root = Path(native["artifact_root"])
            extraction = backend.deploy.load(native["execution"]["transport"]["extraction"])
        self.contract["hardware_id"] = hardware
        self.contract["expected"]["cid"] = emmc
        self.contract["expected"]["bytes"] = 16 * 1024**2
        self.contract["protected_sd"]["cid"] = sd
        if vendor:
            identity = {"schema": "bpi-lab-rescue-fixture-v1", "kernel": initial_context["core"]["kernel_release"]}
            identity_ref = helper.reference("identity-contract.json", identity)
            self.contract["rescue"] = {**identity, "identity_sha256": identity_ref["sha256"]}
            self.contract["sd_prefix"] = {"bytes": 4 * 1024**2,
                                          "sha256": hashlib.sha256(bytes(4 * 1024**2)).hexdigest()}
        self.contract["authorization"].update(hardware_id=hardware, media_identity="cid:" + emmc)
        self.resources["media"] = "cid:" + emmc
        self.pairing.update(hardware_id=hardware, resources=self.resources, emmc=self.contract["expected"],
                            protected_sd=self.contract["protected_sd"], rescue=self.contract["rescue"])
        pairing = self.write_json("pairing.json", self.pairing)
        native["pairing"] = pairing
        extraction.update(source_digest=self.source_record["compressed"], raw=self.source_record["raw"])
        if vendor:
            extraction.update(filesystem_label="BPI-ROOT", filesystem_label_unique=True, filesystem_labels_complete=True)
        extraction_ref = helper.save("extraction.json", extraction) if original else helper.reference("extraction.json", extraction)
        if original:
            native["uboot"]["pairing_sha256"] = pairing["sha256"]
            native["components"]["extraction"] = extraction_ref
            native = helper.qualify(native)
            family = helper.manifest
            template = {key: family[key] for key in
                        ("board", "kernel_release", "root_uuid", "entry", "runtime_requirements", "bootargs_template")}
            template.update(schema=backend.extlinux.TEMPLATE_SCHEMA, pairing_sha256=pairing["sha256"],
                            firmware_review_sha256=native["qualification"]["sha256"],
                            dtb_checks={key: family["checks"][key] for key in ("dtb", "overlay_application")})
            transport = {"kind": "mmc-original", "image_paths": {role: family["files"][role]["path"]
                                                                  for role in ("kernel", "initrd", "dtb")}}
        else:
            native["execution"]["transport"]["extraction"] = extraction_ref
            if not vendor:
                helper.qualify(native)
            family = backend.station._json_loads((artifact_root / "manifest.json").read_bytes())
            template, transport = native["template"], native["execution"]["transport"]
        view = initial_context["core"] if vendor else life.customer_view(native)
        uuid = extraction["filesystem_uuid"]
        self.expected.update(architecture=view["arch"], kernel_release=view["kernel_release"],
                             root={**self.contract["expected"], "uuid": uuid, "media_type": "MMC"})
        self.lifecycle.update(hardware_id=hardware, pairing_sha256=pairing["sha256"],
                              mmc={"emmc": view["source"]["device"], "sd": native["mmc"]["sd"] if original else 2})
        self.rescue["source"]["device"] = self.lifecycle["mmc"]["sd"]
        self.rescue["uboot"]["pairing_sha256"] = pairing["sha256"]
        self.lifecycle["rescue_uboot"] = self.write_json("rescue-uboot.json", self.rescue)
        def child(ref):
            return {"path": str(Path(ref["path"]).relative_to(helper.root)),
                    "sha256": ref["sha256"], "bytes": Path(ref["path"]).stat().st_size}
        prepared = {"schema": "bpi-lab-prepare-v1", "status": "prepared", "hardware_validated": False,
                    "root_uuid_verified": not vendor, "root_identity_verified": True,
                    "board": board, "kernel_release": view["kernel_release"],
                    "source": self.source_record["compressed"], "raw": self.source_record["raw"],
                    "root_uuid": uuid, "components": child(self.reference(artifact_root / "manifest.json")),
                    "extraction": child(extraction_ref)}
        prepared["root_binding"] = ({"method": "label", "label": "BPI-ROOT", "uuid": uuid,
                                     "unique_in_image": True, "unique_on_hardware": False} if vendor else
                                    {"method": "uuid", "uuid": uuid})
        preparation_ref = helper.save("preparation.json", prepared) if original else helper.reference("preparation.json", prepared)
        if vendor:
            transport["root_preparation"] = preparation_ref
            template["source"]["identity_sha256"] = extraction_ref["sha256"]
            template["vendor"]["root_identity"]["evidence_sha256"] = preparation_ref["sha256"]
            native = backend.station._json_loads(backend.deploy.encode(native))
            helper.qualify(native)
            if vendor_rescue:
                rescue_helper = rescue_data.RescueTests()
                rescue_helper.setUp()
                self.addCleanup(rescue_helper.doCleanups)
                self.rescue, rescue_blobs = rescue_helper.make_rescue(board, platform=native, blobs=blobs)
                _, self.rescue_channel = rescue_helper.rescue_session(self.rescue, rescue_blobs)
                self.lifecycle["mmc"]["sd"] = 0
                self.lifecycle.update(rescue_uboot=self.write_json("rescue-uboot.json", self.rescue),
                                      rescue_qualification=self.rescue["qualification"],
                                      rescue_artifact_root=self.rescue["artifact_root"],
                                      rescue_expected={"architecture": view["arch"],
                                                       "dt_compatible": self.expected["dt_compatible"]})
        self.bundle.update(board=board, preparation=preparation_ref, artifact_root=str(artifact_root), transport=transport,
                           uboot=self.write_json("customer-uboot.json", native), uboot_qualification=native["qualification"],
                           uboot_template=self.write_json("uboot-template.json", template),
                           linux_expected=self.write_json("linux.json", self.expected))
        self.config_document.update(hardware_id=hardware, pairing=pairing,
                                    deploy=self.write_json("deploy.json", self.contract),
                                    lifecycle=self.write_json("lifecycle.json", self.lifecycle),
                                    images={self.source_record["compressed"]["sha256"]: self.write_json("bundle.json", self.bundle)})
        self.sync_config()
        self.request.update(hardware_id=hardware, boot_config_sha256=self.config_ref["sha256"])
        self.request["image"]["board"] = board
        if original:
            channel = entry_data.OriginalChannel(native, helper.media, entry_data.Clock())
        else:
            context = life.special_runtime._context(native)
            channel_core = copy.deepcopy(context["core"])
            channel_core["files"].update(context["firmware"])
            channel = special_data.SpecialChannel(channel_core, blobs, special_data.Clock())
            channel.runtime_config = native
            channel.kernel_response = ("\r\nLinux version " + channel_core["kernel_release"] +
                                       " (fixture)\r\nroot@fixture:~# ").encode()
        return native, channel

    def console_runtime(self, native, channel):
        runtime = lifecycle_data.Runtime(life.customer_view(native), self.blobs)
        channel.queue.clear()
        channel.kernel_response = ("\r\nLinux version " + life.customer_view(native)["kernel_release"] +
                                   " (fixture)\r\nroot@fixture:~# ").encode()
        runtime.channel, runtime.clock = channel, channel.clock
        original_write = channel.write
        def write(wire):
            if wire == b"\n":
                channel.queue.append(b"\r\nroot@fixture:~# ")
            elif wire in (b"/bin/busybox poweroff -f\n", b"systemctl poweroff\n"):
                channel.queue.append(b"\r\nreboot: Power down\r\n")
            elif wire == b" ":
                channel.queue.append(b"\r\n" + channel.prompt)
            else:
                return original_write(wire)
            channel.writes.append(wire)
            return len(wire)
        channel.write = write
        return runtime

    def cycle(self, native, channel, output):
        runtime = self.console_runtime(native, channel)
        request = {**self.request, "stage": "boot"}
        observed = {"boot_id": lifecycle_data.AFTER, "identity": {"root": {"devnum": "179:1"}}, "ssh": self.ssh}
        cycle = life.cycle
        def simulated_cycle(*args, **kwargs):
            return cycle(*args, **kwargs, runtime=runtime)
        with mock.patch.object(life.session, "uart_identity", side_effect=[{"boot_id": lifecycle_data.BEFORE},
                               {"boot_id": lifecycle_data.AFTER, "root": observed["identity"]["root"]}]), \
                mock.patch.object(life.session, "check_boot_root", return_value={}), \
                mock.patch.object(life.session, "establish", return_value=observed) as establish, \
                mock.patch.object(life.linux, "collect", return_value={}), \
                mock.patch.object(life.linux, "validate", return_value={"ok": True}), \
                mock.patch.object(life, "cycle", side_effect=simulated_cycle), \
                mock.patch.object(life.uboot, "boot", side_effect=AssertionError("不得降級為通用 runner")):
            binding = backend.deploy.load(self.bundle["preparation"]).get("root_binding")
            context = (self.config_document, self.contract, {**self.bundle, "_root_binding": binding}, native, self.expected, request)
            current = {"status": "verified", "stage": "deploy", "phase": "rescue", "boot_id": lifecycle_data.BEFORE}
            result = backend.NativeRuntime().execute(context, current, output, 600)
        establish.assert_called_once()
        self.assertEqual(runtime.actions, ["status", "off", "status", "on"])
        self.assertTrue(result["customer_kernel_verified"])
        self.assertTrue(result["simulated"])
        return result

    def test_native_five_stages_publish_and_recover_for_both_runtime_families(self):
        for index, (board, original) in enumerate((("bpi-ai2n", False), ("bpi-r3", True), ("bpi-cm6", True),
                                                    ("bpi-m4", False), ("bpi-w2", False),
                                                    *((board, False) for board in sorted(backend.H618_BOARDS)))):
            with self.subTest(board=board):
                native, channel = (self.prepare_h618(board) if board in backend.H618_BOARDS else
                                   self.prepare_runtime(board, original=original, fit_kernel=board == "bpi-cm6"))
                self.request["attempt_id"] = "native-cycle-" + str(index)
                customer_runtime = self.console_runtime(native, channel)
                vendor = board in life.special_runtime.VENDOR_BOARDS
                rescue_runtime = (self.console_runtime(self.rescue, self.rescue_channel) if vendor else
                                  lifecycle_data.Runtime(self.rescue, self.blobs))
                with backend.resource_lock(self.config_document):
                    previous = backend.StateStore(self.config_document).read()
                boot_id = previous["boot_id"] if previous else lifecycle_data.BEFORE
                current_observed = self.observed("rescue", {**self.request, "stage": "preflight"}, boot_id)
                current_request = None
                seen = []
                def observe(*args, mode, previous_boot_id=None, **kwargs):
                    nonlocal current_observed
                    self.assertTrue(previous_boot_id is None or previous_boot_id == boot_id)
                    current_observed = self.observed(mode, args[6], boot_id)
                    return current_observed
                def uart_identity(*args, **kwargs):
                    return current_observed["identity"]
                def establish(*args, **kwargs):
                    nonlocal boot_id, current_observed
                    mode, request = args[1], args[7]
                    boot_id = lifecycle_data.AFTER if mode == "customer" else "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
                    current_observed = self.observed(mode, request, boot_id)
                    return current_observed
                def preflight(contract, source, output, **kwargs):
                    self.assertEqual(contract["ssh"], current_observed["ssh"])
                    self.assertEqual(source, self.source_record)
                    seen.append((current_request["stage"], "preflight"))
                    return self.media_proof()
                def deploy(contract, source, output, *, confirm_overwrite=False, **kwargs):
                    self.assertTrue(confirm_overwrite)
                    self.assertEqual(contract["expected"], self.contract["expected"])
                    self.assertEqual(source, self.source_record)
                    seen.append((current_request["stage"], "deploy"))
                    return self.media_proof(final=True)
                with mock.patch.object(life, "observe", side_effect=observe), \
                        mock.patch.object(life.session, "uart_identity", side_effect=uart_identity), \
                        mock.patch.object(life.session, "check_boot_root", return_value={}), \
                        mock.patch.object(life.session, "establish", side_effect=establish), \
                        mock.patch.object(backend.deploy, "preflight", side_effect=preflight), \
                        mock.patch.object(backend.deploy, "deploy", side_effect=deploy), \
                        mock.patch.object(life.linux, "collect", side_effect=lambda **kwargs: self.collection(current_observed)), \
                        mock.patch.object(life, "NativeRuntime") as port:
                    for stage in backend.station.STAGES:
                        current_request = {**self.request, "stage": stage}
                        port.return_value = rescue_runtime if stage == "recovery" else customer_runtime
                        report = backend.run_stage(self.config_ref["path"], self.config_ref["sha256"], current_request)
                        self.assertEqual(report["status"], "passed", report)
                        self.assertIs(report["needs_recovery"], False)
                        backend.station.validate_report(report, current_request)
                        state = backend.StateStore(self.config_document).read()
                        self.assertEqual((state["stage"], state["status"], state["boot_id"]), (stage, "verified", boot_id))
                        self.assertIs(state["writer_unresolved"], False)
                self.assertEqual(seen, [("preflight", "preflight"), ("deploy", "deploy"), ("recovery", "preflight")])
                self.assertEqual(state["phase"], "rescue")
                self.assertEqual(customer_runtime.actions, ["status", "off", "status", "on"])
                self.assertEqual(rescue_runtime.actions, ["status", "off", "status", "on"])
                self.assertIn("bpirescue boot" if vendor else "mmc dev " + str(self.lifecycle["mmc"]["sd"]),
                              rescue_runtime.channel.commands)

    def test_special_four_boards_from_preparation_to_real_runtime(self):
        for board in sorted(life.special_runtime.SUPPORTED):
            with self.subTest(board=board):
                native, channel = self.prepare_runtime(board)
                _, selected, _ = backend.selected_image(self.config_document, self.contract, self.request)
                self.assertEqual(selected, native)
                result = self.cycle(selected, channel, self.root / (board + "-cycle"))
                self.assertEqual(result["uboot"]["schema"], "bpi-lab-special-runtime-result-v1")
                if board == "bpi-ai2n":
                    self.assertTrue({"opencva", "codec"}.issubset({row["role"] for row in result["uboot"]["observed_ram_hashes"]}))

    def test_realtek_customer_label_runner_and_missing_vendor_sd_rescue_are_distinct(self):
        for board in sorted(life.special_runtime.VENDOR_BOARDS):
            with self.subTest(board=board):
                native, channel = self.prepare_runtime(board, vendor_rescue=False)
                bundle = self.bundle
                prepared = backend.deploy.load(bundle["preparation"])
                self.assertIs(prepared["root_uuid_verified"], False)
                self.assertEqual(prepared["root_binding"]["method"], "label")
                self.assertEqual(self.expected["root"]["uuid"], prepared["root_binding"]["uuid"])
                family = backend.station._json_loads((Path(bundle["artifact_root"]) / "manifest.json").read_bytes())
                selected = backend.render_family(family, backend.deploy.load(bundle["uboot_template"]),
                                                 Path(bundle["artifact_root"]), runtime_config=native)
                with life.console_api.ConsoleSession(channel, log_path=self.root / (board + "-uart.bin"),
                                                     monotonic=channel.clock) as console:
                    result = life.boot_driver(selected).boot(console, selected, [], timeout=600, monotonic=channel.clock)
                self.assertEqual(channel.commands[-1], "bpilab boot")
                self.assertEqual(result["root_uuid"], self.expected["root"]["uuid"])
                self.assertFalse(any(command.startswith(("booti ", "go ", "gosd")) for command in channel.commands))
                with self.assertRaisesRegex(ValueError, "固定 SD RAM 救援"):
                    backend.selected_image(self.config_document, self.contract, self.request)
                report, execute = self.run_stage("preflight")
                self.assertEqual(report["status"], "blocked", report)
                self.assertIs(report["needs_recovery"], False)
                execute.assert_not_called()

    def test_realtek_label_cannot_be_claimed_hardware_unique_or_mapped_to_another_uuid(self):
        self.prepare_runtime("bpi-w2")
        original = backend.deploy.load(self.bundle["preparation"])
        target = Path(self.bundle["preparation"]["path"])
        for change in ({"unique_on_hardware": True}, {"uuid": "11111111-2222-3333-4444-555555555555"}):
            prepared = copy.deepcopy(original)
            prepared["root_binding"].update(change)
            # 保留擷取相對路徑所在目錄，避免以缺檔掩蓋根識別拒絕。
            target.write_bytes(backend.deploy.encode(prepared))
            self.bundle["preparation"] = self.reference(target)
            self.config_document["images"][self.request["image_sha256"]] = self.write_json("bundle.json", self.bundle)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "LABEL"):
                backend.selected_image(self.config_document, self.contract, self.request)

    def test_original_entry_script_extlinux_split_and_compressed_dispatch(self):
        cases = (("bpi-r3", False, False), ("bpi-m7", False, False), ("bpi-r3", True, False), ("bpi-f3", False, True))
        for index, (board, split, compressed) in enumerate(cases):
            with self.subTest(board=board, split=split, compressed=compressed):
                native, channel = self.prepare_runtime(board, original=True, split=split, compressed=compressed)
                _, selected, _ = backend.selected_image(self.config_document, self.contract, self.request)
                result = self.cycle(selected, channel, self.root / ("entry-cycle-" + str(index)))
                self.assertEqual(result["uboot"]["schema"], "bpi-lab-original-entry-result-v1")
                self.assertTrue(channel.commands[-1].startswith("source " if board == "bpi-m7" else "sysboot "))

    def test_special_transport_or_template_drift_rejected_before_runtime(self):
        self.prepare_runtime("bpi-ai2n")
        self.bundle["transport"] = {**self.bundle["transport"], "media": "protected_sd"}
        self.config_document["images"][self.request["image_sha256"]] = self.write_json("bundle.json", self.bundle)
        with self.assertRaisesRegex(ValueError, "special 傳輸"):
            backend.selected_image(self.config_document, self.contract, self.request)

    def test_original_entry_extraction_from_other_source_rejected(self):
        native, _ = self.prepare_runtime("bpi-r3", original=True)
        native["components"]["extraction"] = self.write_json("other.json", {"fixture": True})
        with self.assertRaisesRegex(ValueError, "原入口不是本次"):
            prepared = backend.deploy.load(self.bundle["preparation"])
            extraction_ref = {"path": str(Path(self.bundle["preparation"]["path"]).parent / prepared["extraction"]["path"]),
                              "sha256": prepared["extraction"]["sha256"]}
            backend.bind_runtime(self.bundle, native, {}, extraction_ref, self.config_document)

    def test_fit_lifecycle_projection_never_replaces_original_runtime_config(self):
        native, channel = self.prepare_runtime("bpi-cm6", original=True, fit_kernel=True)
        view = life.customer_view(native)
        self.assertEqual(native["files"]["kernel"]["format"], "FIT")
        self.assertEqual(view["files"]["kernel"]["format"], "Image")
        _, selected, _ = backend.selected_image(self.config_document, self.contract, self.request)
        self.assertEqual(selected["files"]["kernel"]["format"], "FIT")
        result = self.cycle(selected, channel, self.root / "cm6-fit-cycle")
        self.assertEqual(result["uboot"]["schema"], "bpi-lab-original-entry-result-v1")
        self.assertTrue(channel.commands[-1].startswith("sysboot "))


if __name__ == "__main__":
    unittest.main()
