"""原生根系統兩階段契約回歸；使用真實小型 DEB，不執行 APT 或掛載。"""
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bpi_k1_native_rootfs", REPO / "tools/bpi_k1_native_rootfs.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


def ssh_layout(root):
    """建立本次已核對的 Noble 單元布局；真正金鑰另由 ssh-keygen 產生。"""
    files = {**{"usr/lib/systemd/system/" + name: contents for name, contents in MOD.SSH_UNIT_CONTRACTS.items()},
             "etc/default/ssh": "SSHD_OPTS=\n", "etc/ssh/sshd_config": "Include /etc/ssh/sshd_config.d/*.conf\n"}
    for name, source in (
        ("usr/lib/systemd/system/armbian-firstrun.service", "lib/systemd/system/armbian-firstrun.service"),
        ("usr/lib/armbian/armbian-firstrun", "usr/lib/armbian/armbian-firstrun"),
        ("etc/default/armbian-firstrun", "etc/default/armbian-firstrun.dpkg-dist"),
    ):
        files[name] = (REPO / "packages/bsp/common" / source).read_text()
    for name, contents in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    for name, target in (
        ("sockets.target.wants/ssh.socket", "/usr/lib/systemd/system/ssh.socket"),
        ("ssh.service.requires/ssh.socket", "/usr/lib/systemd/system/ssh.socket"),
        ("multi-user.target.wants/armbian-firstrun.service", "/usr/lib/systemd/system/armbian-firstrun.service"),
    ):
        path = root / "etc/systemd/system" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)


@unittest.skipUnless(shutil.which("dpkg-deb"), "需要本機 dpkg-deb 建立測試套件")
class NativeRootfsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "框架根系統"
        self.root.mkdir()
        self.cache = self.base / "debs"
        self.cache.mkdir()
        self.work = self.base / "兩階段紀錄"
        self.lock_path = self.base / "配套鎖.json"
        self.lock = json.loads(MOD.DEFAULT_LOCK.read_text())
        self.board = "bpi-f3"
        keep = {"img-gpu-powervr", "libegl-mesa0", "libgbm1", "libgl1-mesa-dri", "libglapi-mesa", "libglx-mesa0"}
        selected = [key for key in self.lock["profiles"][self.board]["packages"]
                    if self.lock["packages"][key]["Package"] in keep]
        self.lock["profiles"] = {self.board: self.lock["profiles"][self.board]}
        self.lock["profiles"][self.board]["packages"] = selected
        self.lock["packages"] = {key: self.lock["packages"][key] for key in selected}
        for key in selected:
            row = self.lock["packages"][key]
            row.pop("Depends", None)
            row.pop("Pre-Depends", None)
            path = self.make_deb(row["Package"], row["Version"], row["Architecture"])
            row["Filename"] = "pool/test/" + path.name
            row["Size"] = path.stat().st_size
            row["SHA256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.lock_path.write_text(json.dumps(self.lock))
        self.file("usr/lib/os-release", "ID=ubuntu\nVERSION_CODENAME=noble\n")
        (self.root / "etc").mkdir()
        (self.root / "etc/os-release").symlink_to("/usr/lib/os-release")
        self.file("etc/armbian-release", "BOARD=bananapif3\n")
        self.release = self.lock["profiles"][self.board]["kernel_release"]
        self.file("boot/vmlinuz-" + self.release, "Rogue_DDK_Linux_WS 24.2@6603887\n")
        self.file("boot/config-" + self.release,
                  "\n".join(name + "=y" for name in self.lock["required_kernel_options"]) + "\n")
        self.initial_rows = [{"Package": "libc6", "Version": "2.39", "Architecture": "riscv64"},
                             {"Package": "linux-image-current-spacemit", "Version": "1.0", "Architecture": "riscv64"}]
        self.write_status(self.initial_rows)
        # 簽章與遠端下載另有既有回歸；此處僅隔離網路，DEB、SHA、控制欄位及預檢均實際執行。
        self.verify = mock.patch.object(MOD.acceleration, "verify_sources", return_value={"測試索引": {"signature": "已通過"}})
        self.verify.start()
        self.addCleanup(self.verify.stop)

    def file(self, name, text, mode=0o644):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)
        return path

    def make_deb(self, name, version, architecture):
        stage = self.base / ("pkg-" + name)
        (stage / "DEBIAN").mkdir(parents=True)
        (stage / "DEBIAN/control").write_text(
            f"Package: {name}\nVersion: {version}\nArchitecture: {architecture}\n"
            "Maintainer: 測試 <test@example.invalid>\nDescription: 契約回歸用小型套件\n")
        target = self.cache / f"{name}_{version}_{architecture}.deb"
        subprocess.run(["dpkg-deb", "--build", "--root-owner-group", str(stage), str(target)],
                       check=True, capture_output=True)
        return target

    def write_status(self, rows):
        blocks = []
        for row in rows:
            values = {**row, "Status": "install ok installed"}
            blocks.append("\n".join(key + ": " + str(value) for key, value in values.items()))
        self.file("var/lib/dpkg/status", "\n\n".join(blocks) + "\n")

    def stage(self, armbian_board=None):
        return MOD.stage(self.board, self.root, self.work, "20260924-rc1.f3", self.cache, self.lock_path,
                         armbian_board=armbian_board)

    def emulate_installed_files(self, *, omit=None):
        ssh_layout(self.root)
        rows = list(self.initial_rows)
        for key in self.lock["profiles"][self.board]["packages"]:
            item = self.lock["packages"][key]
            rows.append({name: item[name] for name in ("Package", "Version", "Architecture")})
        for name in MOD.DESKTOP_PACKAGES:
            if name != omit:
                rows.append({"Package": name, "Version": "1.0", "Architecture": "riscv64"})
        self.write_status(rows)
        for name, path in MOD.acceleration.GNOME_DESKTOP_COMPONENTS.items():
            if name != omit:
                self.file(path.lstrip("/"), "#!/bin/sh\nexit 0\n", 0o755)
        for name in ("usr/lib/libspacemit_ep.so.1.2.2", "usr/lib/libonnxruntime.so.1.18.1",
                     "usr/lib/libspacemit_mpp.so.0.0.15", "usr/lib/libpvr_dri_support.so",
                     "etc/vulkan/icd.d/powervr_icd.json", "lib/firmware/linlon-v52_v76-80-2/h264dec.fwb",
                     "lib/firmware/linlon-v52_v76-80-2/hevcdec.fwb", "usr/share/wayland-sessions/gnome.desktop"):
            self.file(name, "回歸用執行期檔案\n")

    def finish(self):
        return MOD.finish(self.root, self.work, "20260924-rc1.f3")

    def test_stage_contains_real_debs_and_framework_install_arguments(self):
        result = self.stage()
        self.assertEqual(result["source_kind"], "armbian-native-rootfs")
        self.assertEqual(result["status"], "staged")
        self.assertIn("gjs", result["install_args"])
        self.assertTrue(all(path.startswith("/var/tmp/bpi-k1-native-") for path in result["install_args"] if path.endswith(".deb")))
        for row in result["packages"]:
            path = self.root / result["stage_path"].lstrip("/") / row["filename"]
            self.assertEqual(MOD.digest(path), row["sha256"])
        self.assertFalse((self.root / "etc/bpi-k1-native.json").exists())

    def test_stage_cli_stdout_contains_only_json(self):
        argv = ["bpi_k1_native_rootfs.py", "stage", "--board", self.board,
                "--rootfs", str(self.root), "--work-dir", str(self.work),
                "--build-id", "20260924-rc1.f3", "--deb-cache", str(self.cache),
                "--lock", str(self.lock_path)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch("sys.argv", argv), mock.patch("sys.stdout", stdout), mock.patch("sys.stderr", stderr):
            result = MOD.main()
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "staged")
        self.assertEqual(stderr.getvalue(), "")

    def test_finish_with_real_preflight_records_packages_and_cleans_only_own_stage(self):
        state = self.stage()
        self.emulate_installed_files()
        unrelated = self.file("var/tmp/其他工作", "保留\n")
        result = self.finish()
        self.assertEqual(result["status"], "complete")
        self.assertTrue(result["preflight"]["passed"])
        self.assertFalse(result["media_policy_applied"])
        self.assertEqual(result["hardware_validation"], "pending")
        self.assertEqual(result["kernel"], state["kernel"])
        self.assertFalse((self.root / state["stage_path"].lstrip("/")).exists())
        self.assertEqual(unrelated.read_text(), "保留\n")
        inventory = json.loads((self.work / "installed-packages.json").read_text())
        self.assertTrue(any(row["package"] == "gjs" for row in inventory["packages"]))
        self.assertEqual(result["installed_packages"]["sha256"], MOD.digest(self.work / "installed-packages.json"))

    def test_framework_mount_dns_and_media_policy_are_untouched(self):
        sentinels = {name: self.file(name, "框架原有設定\n") for name in (
            "etc/fstab", "etc/resolv.conf", "usr/sbin/policy-rc.d", "usr/bin/armbian-install",
            "usr/lib/u-boot/platform_install.sh", "etc/systemd/system/armbian-resize-filesystem.service")}
        self.stage()
        self.emulate_installed_files()
        with mock.patch.object(MOD.common, "protect_media_tools", side_effect=AssertionError("不可套用媒體政策")), \
             mock.patch.object(MOD.common, "run", side_effect=AssertionError("不可執行掛載或 chroot")):
            self.finish()
        for path in sentinels.values():
            self.assertEqual(path.read_text(), "框架原有設定\n")
        self.assertFalse((self.root / "root/.no_rootfs_resize").exists())
        self.assertFalse((self.root / "etc/bpi-k1-vendor.json").exists())
        self.assertFalse((self.root / "etc/systemd/system/bpi-k1-grow-rootfs.service").exists())

    def test_wrong_board_is_rejected_before_staging(self):
        self.file("etc/armbian-release", "BOARD=bananapicm6\n")
        with self.assertRaisesRegex(ValueError, "板型"):
            self.stage()
        self.assertFalse(self.work.exists())

    def test_alias_two_stage_roundtrip_keeps_exact_board(self):
        alias = "bananapif3-vendor-sd"
        self.file("etc/armbian-release", "BOARD=" + alias + "\n")
        state = self.stage(alias)
        self.assertEqual(state["identity"]["armbian_board"], alias)
        self.emulate_installed_files()
        result = self.finish()
        self.assertEqual(result["identity"]["armbian_board"], alias)
        self.assertEqual(result["preflight"]["armbian_board"], alias)
        self.assertFalse(result["media_policy_applied"])
        self.assertEqual(json.loads((self.root / "etc/bpi-k1-native.json").read_text())["armbian_board"], alias)

    def test_alias_without_explicit_binding_and_cross_board_are_rejected(self):
        self.file("etc/armbian-release", "BOARD=bananapif3-vendor-sd\n")
        for expected in (None, "bananapif3-titan-emmc", "bananapicm6-vendor-sd", "bananapif3-vendor-sd-other"):
            with self.subTest(expected=expected), self.assertRaises(ValueError):
                self.stage(expected)
        self.assertFalse(self.work.exists())

    def test_finish_rejects_board_alias_changed_after_stage(self):
        self.file("etc/armbian-release", "BOARD=bananapif3-titan-emmc\n")
        self.stage("bananapif3-titan-emmc")
        self.emulate_installed_files()
        self.file("etc/armbian-release", "BOARD=bananapif3-vendor-sd\n")
        with self.assertRaisesRegex(ValueError, "板型"):
            self.finish()
        self.assertFalse((self.root / "etc/bpi-k1-native.json").exists())

    def test_rootfs_rejects_duplicate_board_assignments(self):
        self.file("etc/armbian-release", "BOARD=bananapif3\nBOARD=bananapicm6\n")
        with self.assertRaisesRegex(ValueError, "板型"):
            self.stage()

    def test_wrong_distribution_is_rejected(self):
        self.file("usr/lib/os-release", "ID=debian\nVERSION_CODENAME=trixie\n")
        with self.assertRaisesRegex(ValueError, "Noble"):
            self.stage()

    def test_deb_cache_tampering_is_rejected(self):
        item = next(iter(self.lock["packages"].values()))
        path = self.cache / Path(item["Filename"]).name
        path.write_bytes(path.read_bytes() + b"tampered")
        with self.assertRaisesRegex(ValueError, "雜湊"):
            self.stage()

    def test_correct_hash_with_wrong_deb_control_is_rejected(self):
        item = next(iter(self.lock["packages"].values()))
        item["Architecture"] = "riscv64" if item["Architecture"] == "all" else "all"
        self.lock_path.write_text(json.dumps(self.lock))
        with self.assertRaisesRegex(ValueError, "控制欄位"):
            self.stage()

    def test_deb_changed_while_verifying_indices_is_rejected(self):
        item = next(iter(self.lock["packages"].values()))
        path = self.cache / Path(item["Filename"]).name
        def changed(*args):
            path.write_bytes(path.read_bytes() + b"changed")
            return {}
        with mock.patch.object(MOD.acceleration, "verify_sources", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "核對期間"):
                self.stage()
        self.assertFalse(self.work.exists())

    def test_stage_deb_tampering_is_rejected_before_configuration(self):
        state = self.stage()
        self.emulate_installed_files()
        path = self.root / state["stage_path"].lstrip("/") / state["packages"][0]["filename"]
        path.write_bytes(path.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "stage DEB"):
            self.finish()
        self.assertFalse((self.root / "etc/bpi-k1-native.json").exists())

    def test_stage_record_tampering_is_rejected(self):
        self.stage()
        path = self.work / "stage.json"
        record = json.loads(path.read_text())
        record["packages"] = []
        path.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, "紀錄遭到變更"):
            self.finish()

    def test_build_identity_and_rootfs_identity_are_both_bound(self):
        self.stage()
        with self.assertRaisesRegex(ValueError, "建置識別"):
            MOD.finish(self.root, self.work, "other")
        other = self.base / "另一個根系統"
        shutil.copytree(self.root, other, symlinks=True)
        with self.assertRaisesRegex(ValueError, "同一個"):
            MOD.finish(other, self.work, "20260924-rc1.f3")

    def test_changed_source_lock_is_rejected(self):
        self.stage()
        self.lock_path.write_text(self.lock_path.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "來源鎖"):
            self.finish()

    def test_unexpected_kernel_change_is_rejected(self):
        self.stage()
        self.emulate_installed_files()
        self.file("boot/vmlinuz-" + self.release, "24.2@6603887 changed\n")
        with self.assertRaisesRegex(ValueError, "核心檔案"):
            self.finish()

    def test_missing_gjs_cannot_finish(self):
        self.stage()
        self.emulate_installed_files(omit="gjs")
        with self.assertRaisesRegex(ValueError, "必要套件"):
            self.finish()

    def test_real_preflight_failure_preserves_debs_and_records_failure(self):
        state = self.stage()
        self.emulate_installed_files()
        (self.root / "usr/bin/gjs").unlink()
        with self.assertRaisesRegex(ValueError, "預檢失敗"):
            self.finish()
        self.assertEqual(json.loads((self.work / "integration.json").read_text())["status"], "failed")
        self.assertTrue((self.root / state["stage_path"].lstrip("/")).is_dir())

    def test_symlinked_write_directory_cannot_escape_rootfs(self):
        self.stage()
        self.emulate_installed_files()
        outside = self.base / "外部目錄"
        outside.mkdir()
        (self.root / "etc/gdm3").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "符號連結"):
            self.finish()
        self.assertEqual(list(outside.iterdir()), [])

    def test_existing_work_and_stage_are_not_overwritten(self):
        self.work.mkdir()
        sentinel = self.work / "既有"
        sentinel.write_text("保留")
        with self.assertRaisesRegex(ValueError, "已存在"):
            self.stage()
        self.assertEqual(sentinel.read_text(), "保留")

    def test_invalid_build_ids_are_rejected(self):
        for value in ("../escape", "/root", "..", "a b", "a" * 97):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MOD.build_id(value)
        self.assertEqual(MOD.build_id("20260924-rc1.cm6_A"), "20260924-rc1.cm6_A")
        self.assertEqual(MOD.build_id("_framework-id"), "_framework-id")

    def test_cm6_private_firmware_contents_are_verified(self):
        path = self.file("usr/lib/bpi-cm6-bluetooth/firmware/fw", "固定韌體")
        item = {"path": "/usr/lib/bpi-cm6-bluetooth/firmware/fw", "bytes": path.stat().st_size,
                "sha256": MOD.digest(path)}
        record = {"hardware_contract": {"firmware": item, "firmware_config": copy.deepcopy(item)}}
        MOD.verify_bluetooth_files(self.root, record)
        path.write_text("遭修改")
        with self.assertRaisesRegex(ValueError, "韌體內容"):
            MOD.verify_bluetooth_files(self.root, record)

    def test_cm6_configuration_preserves_gpu_environment_and_masks_camera(self):
        self.file("etc/environment", "COGL_DRIVER=gles2\nMUTTER_DEBUG_DISABLE_HW_CURSORS=0\n")
        self.work.mkdir()
        (self.work / "acceleration.lock.json").write_text(json.dumps(self.lock))
        state = {"identity": {"board": "bpi-cm6", "cm6_camera_packages": MOD.common.CM6_CAMERA_PACKAGES}, "packages": []}
        MOD.configure(self.root, "bpi-cm6", state, self.work, MOD.installed_packages(self.root))
        self.assertEqual((self.root / "etc/environment").read_text(), "COGL_DRIVER=gles2\nMUTTER_DEBUG_DISABLE_HW_CURSORS=1\n")
        self.assertEqual((self.root / "etc/systemd/system/camera.service").readlink(), Path("/dev/null"))
        self.assertIn('MODE="0660"', (self.root / "etc/udev/rules.d/99-bpi-cm6-camera.rules").read_text())
        self.assertFalse((self.root / "etc/fstab").exists())

    def desktop_runtime(self):
        import bpi_k1_desktop as desktop
        self.file("usr/lib/bpi-k1-configng/bin/armbian-config", "建置用入口\n", 0o755)
        self.file("usr/lib/bpi-k1-configng/lib/armbian-config/config.system.sh", "建置用模組\n")
        runtime = self.root / "usr/lib/bpi-k1-configng"
        profile = desktop.load_profile(desktop.DEFAULT_PROFILE)
        manifest = {"schema_version": 1, "fingerprint": desktop.fingerprint(desktop.DEFAULT_PROFILE),
                    "repository": profile["repository"], "commit": profile["commit"],
                    "profile_sha256": MOD.digest(desktop.DEFAULT_PROFILE),
                    "tool_sha256": MOD.digest(Path(desktop.__file__)), "files": desktop.inventory(runtime)}
        evidence = self.file("usr/share/doc/bpi-k1-desktop/manifest.json", json.dumps(manifest))
        return runtime, evidence

    def test_finish_removes_verified_build_runtime_and_keeps_manifest(self):
        runtime, evidence = self.desktop_runtime()
        before = evidence.read_bytes()
        self.stage()
        self.emulate_installed_files()
        result = self.finish()
        self.assertEqual(result["desktop_build_runtime"]["status"], "removed")
        self.assertEqual(result["desktop_build_runtime"]["source_manifest_sha256"], MOD.digest(evidence))
        self.assertFalse(runtime.exists())
        self.assertEqual(evidence.read_bytes(), before)

    def test_runtime_cleanup_rejects_changed_contents_or_missing_manifest(self):
        runtime, evidence = self.desktop_runtime()
        entry = runtime / "bin/armbian-config"
        entry.write_text("已變更")
        with self.assertRaisesRegex(ValueError, "內容或權限"):
            MOD.cleanup_desktop_runtime(self.root)
        self.assertTrue(entry.exists())
        evidence.unlink()
        with self.assertRaisesRegex(ValueError, "缺少"):
            MOD.cleanup_desktop_runtime(self.root)
        self.assertTrue(entry.exists())

    def test_runtime_cleanup_rejects_symlinked_parent_without_touching_host_files(self):
        runtime, evidence = self.desktop_runtime()
        external = self.base / "主機程式"
        shutil.move(self.root / "usr/lib", external)
        (self.root / "usr/lib").symlink_to(external, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "符號連結"):
            MOD.cleanup_desktop_runtime(self.root)
        self.assertTrue((external / "bpi-k1-configng/bin/armbian-config").is_file())
        self.assertTrue(evidence.is_file())

    def test_runtime_cleanup_rejects_wrong_source_identity(self):
        runtime, evidence = self.desktop_runtime()
        manifest = json.loads(evidence.read_text())
        manifest["commit"] = "0" * 40
        evidence.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "來源紀錄"):
            MOD.cleanup_desktop_runtime(self.root)
        self.assertTrue(runtime.is_dir())

    def bluetooth_identity(self):
        return {"board": "bpi-cm6", "cm6_bluetooth_package_sha256": "1" * 64,
                "connectivity_lock_sha256": MOD.digest(MOD.CONNECTIVITY_LOCK)}

    def test_cm6_initial_bluetooth_policy_seeds_exact_hci_once(self):
        with mock.patch.object(MOD.os, "fchown") as owner:
            result = MOD.seed_bluetooth_initial_policy(self.root, "bpi-cm6", self.bluetooth_identity())
        path = self.root / MOD.CM6_HCI_STATE
        self.assertEqual(path.read_bytes(), b"0\n")
        self.assertEqual(path.stat().st_mode & 0o777, 0o644)
        self.assertEqual(owner.call_args.args[1:], (0, 0))
        self.assertEqual(result["status"], "seeded")
        self.assertEqual(result["hardware_validation"], "pending")
        self.assertEqual(result["sha256"], MOD.digest(path))
        path.write_bytes(b"1\n")
        before = path.stat()
        with mock.patch.object(MOD.os, "fchown", side_effect=AssertionError("不可修改既有偏好")):
            again = MOD.seed_bluetooth_initial_policy(self.root, "bpi-cm6", self.bluetooth_identity())
        self.assertEqual(again["status"], "preserved")
        self.assertEqual(path.read_bytes(), b"1\n")
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)

    def test_cm6_existing_generic_bluetooth_off_prevents_new_default(self):
        previous = self.file("var/lib/systemd/rfkill/bluetooth", "1\n")
        result = MOD.seed_bluetooth_initial_policy(self.root, "bpi-cm6", self.bluetooth_identity())
        self.assertEqual(result["status"], "preserved")
        self.assertEqual(previous.read_text(), "1\n")
        self.assertFalse((self.root / MOD.CM6_HCI_STATE).exists())

    def test_initial_bluetooth_policy_does_not_touch_f3_or_cm6_without_package(self):
        for board, identity in (("bpi-f3", self.bluetooth_identity()), ("bpi-cm6", {"board": "bpi-cm6"})):
            with self.subTest(board=board, identity=identity):
                result = MOD.seed_bluetooth_initial_policy(self.root, board, identity)
                self.assertEqual(result["status"], "not-applicable")
                self.assertFalse((self.root / "var/lib/systemd").exists())

    def test_initial_bluetooth_policy_rejects_symlinked_directory(self):
        outside = self.base / "主機偏好"
        outside.mkdir()
        directory = self.root / "var/lib/systemd"
        directory.mkdir(parents=True)
        (directory / "rfkill").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "符號連結"):
            MOD.seed_bluetooth_initial_policy(self.root, "bpi-cm6", self.bluetooth_identity())
        self.assertEqual(list(outside.iterdir()), [])

    def test_initial_bluetooth_policy_rejects_unknown_or_linked_state(self):
        directory = self.root / "var/lib/systemd/rfkill"
        directory.mkdir(parents=True)
        state = directory / "bluetooth"
        state.write_text("unexpected")
        with self.assertRaisesRegex(ValueError, "偏好內容"):
            MOD.seed_bluetooth_initial_policy(self.root, "bpi-cm6", self.bluetooth_identity())
        state.unlink()
        state.symlink_to(self.root / "var/lib/dpkg/status")
        with self.assertRaisesRegex(ValueError, "連結或未知布局"):
            MOD.seed_bluetooth_initial_policy(self.root, "bpi-cm6", self.bluetooth_identity())
        self.assertFalse((self.root / MOD.CM6_HCI_STATE).exists())

    def test_cm6_configure_reports_policy_and_preserves_wifi(self):
        wifi = self.file("var/lib/systemd/rfkill/platform-d4280800.sdh:wlan", "1\n")
        self.work.mkdir()
        (self.work / "acceleration.lock.json").write_text(json.dumps(self.lock))
        (self.work / "bluetooth-package.json").write_text("{}\n")
        state = {"identity": self.bluetooth_identity(), "packages": []}
        with mock.patch.object(MOD.os, "fchown"):
            result = MOD.configure(self.root, "bpi-cm6", state, self.work, MOD.installed_packages(self.root))
        self.assertEqual(result["bluetooth_initial_policy"]["status"], "seeded")
        self.assertEqual(wifi.read_text(), "1\n")


@unittest.skipUnless(shutil.which("ssh-keygen"), "需要本機 ssh-keygen 驗證真實金鑰流程")
class NativeSshHostKeyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="bpi-k1-ssh-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "root"
        self.root.mkdir()
        ssh_layout(self.root)

    def make_keys(self):
        subprocess.run(["ssh-keygen", "-A", "-f", str(self.root)], check=True, capture_output=True)

    def helper(self):
        namespace = {"__name__": "hostkey_guard_test"}
        path = self.root / "usr/lib/bpi-k1-native/ssh-hostkeys"
        exec(compile(path.read_text(), str(path), "exec"), namespace)
        return namespace

    def test_build_removes_only_host_keys_and_keeps_first_login_and_upstream_units(self):
        self.make_keys()
        user_key = self.root / "root/.ssh/id_ed25519"
        user_key.parent.mkdir(parents=True)
        user_key.write_text("此為不應掃描或修改的使用者資料")
        marker = self.root / "root/.not_logged_in_yet"
        marker.touch()
        units = {name: (self.root / "usr/lib/systemd/system" / name).read_bytes() for name in MOD.SSH_UNIT_CONTRACTS}
        result = MOD.prepare_ssh_host_keys(self.root)
        self.assertEqual(len(result["removed_build_key_paths"]), 6)
        self.assertFalse(list((self.root / "etc/ssh").glob("ssh_host_*")))
        self.assertTrue(marker.exists())
        self.assertEqual(user_key.read_text(), "此為不應掃描或修改的使用者資料")
        self.assertIn("OPENSSHD_REGENERATE_HOST_KEYS=false", (self.root / "etc/default/armbian-firstrun").read_text())
        for name, contents in units.items():
            self.assertEqual((self.root / "usr/lib/systemd/system" / name).read_bytes(), contents)
            dropin = MOD.unit_directives((self.root / f"etc/systemd/system/{name}.d/10-bpi-k1-hostkeys.conf").read_text())
            self.assertEqual(dropin[("Unit", "Requires")], ["bpi-k1-ssh-hostkeys.service"])
            self.assertEqual(dropin[("Unit", "After")], ["bpi-k1-ssh-hostkeys.service"])

    def test_first_boot_generates_real_keys_and_later_boot_preserves_all_bytes(self):
        MOD.prepare_ssh_host_keys(self.root)
        helper = self.helper()
        helper["setup"](self.root)
        keys = list((self.root / "etc/ssh").glob("ssh_host_*"))
        self.assertEqual(len(keys), 6)
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in keys}
        helper["setup"](self.root)
        self.assertTrue(before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in keys},
                        "再次啟動不得更換既有主機金鑰；失敗時亦不輸出金鑰內容")

    def test_runtime_symlink_or_invalid_private_key_fails_without_replacement(self):
        MOD.prepare_ssh_host_keys(self.root)
        helper = self.helper()
        helper["setup"](self.root)
        key = self.root / "etc/ssh/ssh_host_rsa_key"
        outside = self.base / "外部檔案"
        outside.write_text("保留")
        key.unlink()
        key.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "連結"):
            helper["setup"](self.root)
        self.assertEqual(outside.read_text(), "保留")
        key.unlink()
        key.write_text("無效私鑰")
        key.chmod(0o600)
        with self.assertRaisesRegex(ValueError, "無法驗證"):
            helper["setup"](self.root)
        self.assertTrue(key.read_text() == "無效私鑰", "無效既有金鑰不得被覆寫")

    def test_keygen_failure_is_not_success(self):
        MOD.prepare_ssh_host_keys(self.root)
        helper = self.helper()
        with mock.patch.object(helper["subprocess"], "run", return_value=subprocess.CompletedProcess([], 1)):
            with self.assertRaisesRegex(ValueError, "產生失敗"):
                helper["setup"](self.root)
        self.assertFalse(list((self.root / "etc/ssh").glob("ssh_host_*")))

    def test_unknown_unit_dropin_key_name_and_hardlink_rejected_before_mutation(self):
        cases = ("unit", "dropin", "unknown-key", "hardlink", "symlink")
        for case in cases:
            with self.subTest(case=case):
                root = self.base / case
                root.mkdir()
                ssh_layout(root)
                key = root / "etc/ssh/ssh_host_rsa_key"
                key.write_text("建置時金鑰測試資料")
                original = (root / "etc/default/armbian-firstrun").read_bytes()
                if case == "unit":
                    path = root / "usr/lib/systemd/system/ssh.service"
                    path.write_text(path.read_text().replace("ExecStartPre=/usr/sbin/sshd -t", "ExecStartPre=/bin/true"))
                elif case == "dropin":
                    path = root / "etc/systemd/system/ssh.socket.d/custom.conf"
                    path.parent.mkdir(parents=True)
                    path.write_text("[Socket]\nListenStream=2222\n")
                elif case == "unknown-key":
                    (root / "etc/ssh/ssh_host_unknown_key").write_text("未知")
                elif case == "hardlink":
                    os.link(key, self.base / "外部硬連結")
                else:
                    key.unlink()
                    key.symlink_to(self.base / "不存在的外部檔案")
                with self.assertRaises(ValueError):
                    MOD.prepare_ssh_host_keys(root)
                self.assertTrue(os.path.lexists(key))
                self.assertEqual((root / "etc/default/armbian-firstrun").read_bytes(), original)
                self.assertFalse((root / "usr/lib/bpi-k1-native/ssh-hostkeys").exists())

    def test_unknown_first_run_or_custom_key_source_rejected(self):
        for case in ("firstrun", "defaults", "key-source", "ssh-options"):
            with self.subTest(case=case):
                root = self.base / case
                root.mkdir()
                ssh_layout(root)
                if case == "firstrun":
                    path = root / "usr/lib/armbian/armbian-firstrun"
                    path.write_text(path.read_text() + "\nexit 0\n")
                elif case == "defaults":
                    path = root / "etc/default/armbian-firstrun"
                    path.write_text("OPENSSHD_REGENERATE_HOST_KEYS=false\n")
                elif case == "key-source":
                    (root / "etc/ssh/sshd_config").write_text("HostKey /elsewhere/key\n")
                else:
                    (root / "etc/default/ssh").write_text("SSHD_OPTS=-h /elsewhere/key\n")
                with self.assertRaises(ValueError):
                    MOD.prepare_ssh_host_keys(root)
                self.assertFalse((root / "usr/lib/bpi-k1-native/ssh-hostkeys").exists())

    @unittest.skipUnless(shutil.which("systemd-analyze") and Path("/usr/sbin/sshd").exists(),
                         "需要本機 systemd-analyze 與 sshd 路徑核對，僅作靜態檢查")
    def test_systemd_dependency_graph_has_no_cycle_and_detects_conflicting_order(self):
        MOD.prepare_ssh_host_keys(self.root)
        units = self.root / "usr/lib/systemd/system"
        for name in MOD.SSH_UNIT_CONTRACTS:
            shutil.copytree(self.root / f"etc/systemd/system/{name}.d", units / f"{name}.d")
        guard = units / "bpi-k1-ssh-hostkeys.service"
        # 靜態檢查只改暫存副本的執行檔位置；所有順序與相依指令保持產品內容。
        guard.write_text(guard.read_text().replace("/usr/lib/bpi-k1-native/ssh-hostkeys",
                                                  str(self.root / "usr/lib/bpi-k1-native/ssh-hostkeys")))
        environment = dict(os.environ, SYSTEMD_UNIT_PATH=str(units) + ":/usr/lib/systemd/system:/lib/systemd/system")
        command = ["systemd-analyze", "verify", "--man=no", str(guard), str(units / "ssh.service"), str(units / "ssh.socket")]
        good = subprocess.run(command, capture_output=True, text=True, env=environment)
        self.assertEqual(good.returncode, 0, good.stderr)
        guard.write_text(guard.read_text().replace("After=local-fs.target systemd-random-seed.service",
                                                  "After=local-fs.target systemd-random-seed.service ssh.service ssh.socket"))
        bad = subprocess.run(command, capture_output=True, text=True, env=environment)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("ordering cycle", bad.stderr)


if __name__ == "__main__":
    unittest.main()
