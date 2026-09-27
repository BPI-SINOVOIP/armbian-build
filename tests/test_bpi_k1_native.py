"""核對原生配置、快取、封存邊界與真實 ext4 中繼資料；不下載、不掛載。"""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bpi_k1_native", ROOT / "tools/bpi_k1_native.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)
import bpi_k1_native_rootfs as INTEGRATION


class NativeContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def config(self, **overrides):
        values = dict(board="bananapif3", branch="current", release="noble", desktop="gnome",
                      tier="minimal", outputs="sd,emmc", release_id="20260924-rc1")
        values.update(overrides)
        return MOD.configuration(**values)

    def write_json(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False))

    def test_both_board_profiles_have_distinct_fixed_pairings(self):
        f3 = self.config()
        cm6 = self.config(board="bananapicm6", branch="legacy", camera="dual-imx415")
        self.assertEqual(f3["profile"]["kernel_pvr"], "24.2@6603887")
        self.assertEqual(cm6["profile"]["kernel_pvr"], "23.2@6460340")
        self.assertNotEqual(f3["profile_sha256"], cm6["profile_sha256"])
        self.assertEqual(f3["hardware_validation"], "pending")

    def test_four_aliases_bind_physical_board_media_and_default_camera(self):
        fingerprints = set()
        for name, target in MOD.targets.load_registry()["targets"].items():
            with self.subTest(board=name):
                config = self.config(board=name, branch=target["branch"], outputs=target["storage"])
                self.assertEqual(config["armbian_board"], name)
                self.assertEqual(config["board"], target["board"])
                self.assertEqual(config["camera_profile"], target["camera_profile"])
                self.assertEqual(config["outputs"], [target["storage"]])
                self.assertIn(MOD.targets.REGISTRY, config["source_locks"])
                self.assertIn("tools/bpi_k1_board_targets.py", config["asset_inputs"])
                fingerprints.add(config["cache_sha256"])
        self.assertEqual(len(fingerprints), 4)

    def test_alias_rejects_wrong_media_branch_camera_and_similar_unknown_name(self):
        base = {"board": "bananapicm6-titan-emmc", "branch": "legacy", "outputs": "emmc"}
        for invalid in ({"outputs": "sd"}, {"outputs": "sd,emmc"}, {"branch": "current"},
                        {"camera": "none"}, {"board": "bananapicm6-titan-emmc-other"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.config(**{**base, **invalid})

    def test_configuration_rejects_wrong_board_branch_and_direct_flash(self):
        for values in ({"board": "bananapim4zero"}, {"branch": "legacy"},
                       {"card_device": "/dev/mmcblk0"},
                       {"board": "bananapicm6", "branch": "current"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.config(**values)

    def test_configuration_rejects_wrong_desktop_camera_and_output_contracts(self):
        for values in ({"release": "trixie"}, {"desktop": "xfce"}, {"tier": "full"},
                       {"build_desktop": "no"}, {"build_minimal": "yes"},
                       {"camera": "dual-imx415"}, {"outputs": "sd,sd"},
                       {"outputs": ""}, {"outputs": "sd,nvme"},
                       {"release_id": "20260924-native1"}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.config(**values)

    def test_profile_identity_changes_with_camera_but_not_delivery_choice(self):
        plain = self.config(board="bananapicm6", branch="legacy")
        camera = self.config(board="bananapicm6", branch="legacy", camera="dual-imx415")
        alternate = self.config(board="bananapicm6", branch="legacy", outputs="sd", release_id="20260925-rc2")
        self.assertNotEqual(plain["profile_sha256"], camera["profile_sha256"])
        self.assertEqual(plain["profile_sha256"], alternate["profile_sha256"])

    def test_saved_configuration_cannot_hide_changed_source_lock(self):
        path = self.base / "config.json"
        record = self.config()
        self.write_json(path, record)
        self.assertEqual(MOD.read_configuration(path), record)
        record["source_locks"][MOD.ACCELERATION_LOCK] = "0" * 64
        self.write_json(path, record)
        with self.assertRaisesRegex(ValueError, "配置或來源鎖"):
            MOD.read_configuration(path)

    def isolated_source_copy(self):
        """只複製受控設定與工具，測試輸入變更不碰實際工作樹。"""
        config = self.config(board="bananapicm6", branch="legacy", camera="dual-imx415")
        root = self.base / "來源副本"
        names = set(config["build_inputs"]) | set(config["source_locks"]) | set(config["asset_inputs"])
        for name in names:
            destination = root / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, destination)
        return root

    def test_asset_fingerprint_binds_each_asset_generator(self):
        root = self.isolated_source_copy()
        with mock.patch.object(MOD, "ROOT", root):
            for board, branch, name in (
                    ("bananapif3", "current", "fetch_bpi_k1_vendor_reference.py"),
                    ("bananapif3", "current", "bpi_k1_acceleration.py"),
                    ("bananapif3", "current", "bpi_k1_board_targets.py"),
                    ("bananapicm6", "legacy", "build_bpi_cm6_bluetooth.py"),
                    ("bananapicm6", "legacy", "package_bpi_cm6_bluetooth.py")):
                with self.subTest(name=name):
                    before = self.config(board=board, branch=branch)
                    path = root / "tools" / name
                    path.write_bytes(path.read_bytes() + "\n# 測試修改\n".encode())
                    after = self.config(board=board, branch=branch)
                    self.assertEqual(before["profile_sha256"], after["profile_sha256"])
                    self.assertNotEqual(before["asset_sha256"], after["asset_sha256"])

    def test_connectivity_service_changes_cm6_assets_and_both_build_identities(self):
        root = self.isolated_source_copy()
        name = "config/spacemit-k1-connectivity/bpi-cm6-bluetooth.service"
        with mock.patch.object(MOD, "ROOT", root):
            f3 = self.config()
            cm6 = self.config(board="bananapicm6", branch="legacy")
            path = root / name
            path.write_bytes(path.read_bytes() + "\n# 服務變更\n".encode())
            new_f3 = self.config()
            new_cm6 = self.config(board="bananapicm6", branch="legacy")
        self.assertNotEqual(cm6["asset_sha256"], new_cm6["asset_sha256"])
        self.assertEqual(f3["asset_sha256"], new_f3["asset_sha256"])
        for before, after in ((f3, new_f3), (cm6, new_cm6)):
            self.assertNotEqual(before["build_inputs"][name], after["build_inputs"][name])
            self.assertNotEqual(before["cache_sha256"], after["cache_sha256"])

    def test_board_registry_invalidates_assets_for_original_boards_too(self):
        root = self.isolated_source_copy()
        with mock.patch.object(MOD, "ROOT", root):
            before = [self.config(), self.config(board="bananapicm6", branch="legacy")]
            registry = root / MOD.targets.REGISTRY
            registry.write_bytes(registry.read_bytes() + b"\n")
            after = [self.config(), self.config(board="bananapicm6", branch="legacy")]
        for old, new in zip(before, after):
            self.assertIn(MOD.targets.REGISTRY, old["asset_inputs"])
            self.assertNotEqual(old["asset_sha256"], new["asset_sha256"])

    def test_board_loader_and_framework_inputs_invalidate_build_cache(self):
        root = self.isolated_source_copy()
        names = ("config/boards/bananapicm6.wip", "config/boards/bananapif3.conf",
                 "config/boards/include/bpi-k1-board-targets.inc",
                 "lib/functions/main/config-prepare.sh", "lib/functions/host/basic-deps.sh")
        with mock.patch.object(MOD, "ROOT", root):
            for name in names:
                with self.subTest(name=name):
                    before = self.config()
                    self.assertIn(name, before["build_inputs"])
                    path = root / name
                    path.write_bytes(path.read_bytes() + "\n# 契約回歸\n".encode())
                    after = self.config()
                    self.assertNotEqual(before["cache_sha256"], after["cache_sha256"])
                    self.assertEqual(before["asset_sha256"], after["asset_sha256"])

    def test_asset_fingerprint_ignores_pycache_but_tracks_new_connectivity_input(self):
        root = self.isolated_source_copy()
        with mock.patch.object(MOD, "ROOT", root):
            before = self.config(board="bananapicm6", branch="legacy")
            cache = root / "config/spacemit-k1-connectivity/__pycache__"
            cache.mkdir()
            (cache / "module.pyc").write_bytes(b"cache")
            self.assertEqual(before, self.config(board="bananapicm6", branch="legacy"))
            (cache.parent / "controlled-extra.conf").write_text("受控設定\n")
            after = self.config(board="bananapicm6", branch="legacy")
        self.assertNotEqual(before["asset_sha256"], after["asset_sha256"])
        self.assertIn("config/spacemit-k1-connectivity/controlled-extra.conf", after["asset_inputs"])

    def test_general_packaging_change_reuses_valid_assets(self):
        root = self.isolated_source_copy()
        with mock.patch.object(MOD, "ROOT", root):
            before = self.config()
            path, _, record = self.assets(before)
            tool = root / "tools/package_bpi_k1_vendor.py"
            tool.write_bytes(tool.read_bytes() + "\n# 測試修改\n".encode())
            after = self.config()
            self.assertEqual(before["asset_sha256"], after["asset_sha256"])
            self.assertNotEqual(before["cache_sha256"], after["cache_sha256"])
            config_path = self.base / "config.json"
            self.write_json(config_path, after)
            with mock.patch.object(MOD.reference, "fetch", side_effect=AssertionError("封裝修改不應重下載資產")):
                self.assertEqual(MOD.prepare_assets(config_path, path), record)

    def test_assets_reject_old_record_and_changed_generator_identity(self):
        root = self.isolated_source_copy()
        with mock.patch.object(MOD, "ROOT", root):
            path, before, original = self.assets()
            old = dict(original)
            old.pop("asset_sha256")
            self.write_json(path / "assets.json", old)
            with self.assertRaisesRegex(ValueError, "生成工具或輸入"):
                MOD.verify_assets(path, before)
            self.write_json(path / "assets.json", original)
            tool = root / "tools/fetch_bpi_k1_vendor_reference.py"
            tool.write_bytes(tool.read_bytes() + "\n# 測試修改\n".encode())
            with self.assertRaisesRegex(ValueError, "生成工具或輸入"):
                MOD.verify_assets(path, self.config())

    def test_new_assets_record_contains_verified_generation_identity(self):
        config_path = self.base / "config.json"
        config = self.config()
        self.write_json(config_path, config)
        def vendor(lock, destination):
            destination.mkdir()
            (destination / "元件.bin").write_bytes(b"vendor")
        def acceleration(lock, lock_path, board, cache, report):
            cache.mkdir()
            report.mkdir()
            (cache / "套件.deb").write_bytes(b"package")
            (report / "prepared.json").write_text("{}\n")
        with mock.patch.object(MOD.reference, "fetch", side_effect=vendor), \
             mock.patch.object(MOD.acceleration, "prepare", side_effect=acceleration):
            result = MOD.prepare_assets(config_path, self.base / "new-assets")
        self.assertEqual(result["asset_sha256"], config["asset_sha256"])
        self.assertEqual(result, MOD.verify_assets(self.base / "new-assets", config))

    def test_optional_build_evidence_tool_changes_only_build_cache_identity(self):
        root = self.isolated_source_copy()
        evidence = root / "tools/bpi_k1_native_evidence.py"
        evidence.unlink(missing_ok=True)
        with mock.patch.object(MOD, "ROOT", root):
            before = self.config()
            evidence.write_text("# 建置來源證據工具\n")
            after = self.config()
        self.assertEqual(before["asset_sha256"], after["asset_sha256"])
        self.assertNotEqual(before["cache_sha256"], after["cache_sha256"])
        self.assertIn("tools/bpi_k1_native_evidence.py", after["build_inputs"])

    def assets(self, config=None):
        path = self.base / "assets"
        path.mkdir()
        (path / "內容.bin").write_bytes(b"known input\x00")
        config = self.config() if config is None else config
        record = {"schema_version": 1, "profile_sha256": config["profile_sha256"],
                  "asset_sha256": config["asset_sha256"],
                  "board": config["board"], "files": MOD.file_records(path),
                  "hardware_validation": "pending"}
        self.write_json(path / "assets.json", record)
        return path, config, record

    def test_cached_assets_are_checked_without_download(self):
        path, config, record = self.assets()
        config_path = self.base / "config.json"
        self.write_json(config_path, config)
        with mock.patch.object(MOD.reference, "fetch", side_effect=AssertionError("快取不可重新下載")):
            self.assertEqual(MOD.prepare_assets(config_path, path), record)

    def test_assets_missing_changed_or_extra_file_are_rejected(self):
        path, config, _ = self.assets()
        original = (path / "內容.bin").read_bytes()
        (path / "內容.bin").write_bytes(original + b"changed")
        with self.assertRaises(ValueError):
            MOD.verify_assets(path, config)
        (path / "內容.bin").unlink()
        with self.assertRaises(ValueError):
            MOD.verify_assets(path, config)
        (path / "內容.bin").write_bytes(original)
        (path / "額外.bin").write_bytes(b"extra")
        with self.assertRaises(ValueError):
            MOD.verify_assets(path, config)

    def test_assets_reject_wrong_schema_board_and_profile(self):
        path, config, original = self.assets()
        for key, value in (("schema_version", 0), ("board", "bpi-cm6"), ("profile_sha256", "0" * 64)):
            record = {**original, key: value}
            self.write_json(path / "assets.json", record)
            with self.subTest(key=key), self.assertRaises(ValueError):
                MOD.verify_assets(path, config)

    def test_assets_reject_unrecorded_symlink_to_host_directory(self):
        path, config, _ = self.assets()
        host = self.base / "主機資料"
        host.mkdir()
        (host / "不可打包").write_text("主機檔案")
        (path / "未記錄連結").symlink_to(host, target_is_directory=True)
        with self.assertRaises(ValueError):
            MOD.verify_assets(path, config)

    def test_tree_inventory_preserves_links_ownership_and_xattrs_without_following(self):
        root = self.base / "tree"
        root.mkdir()
        first = root / "a"
        first.write_bytes(b"rootfs payload")
        first.chmod(0o640)
        os.link(first, root / "b")
        os.setxattr(first, "user.proof", b"extended attribute")
        outside = self.base / "主機資料"
        outside.mkdir()
        (outside / "主機機密").write_text("不應讀取")
        (root / "escape").symlink_to(outside, target_is_directory=True)
        inventory = MOD.tree_inventory(root)
        self.assertEqual(set(inventory), {"a", "b", "escape"})
        self.assertEqual(inventory["a"]["mode"], 0o640)
        self.assertEqual(inventory["a"]["uid"], first.stat().st_uid)
        self.assertEqual(inventory["b"]["hardlink"], "a")
        self.assertEqual(inventory["escape"]["target"], str(outside))
        self.assertEqual(inventory["a"]["xattrs"]["user.proof"], hashlib.sha256(b"extended attribute").hexdigest())
        before = MOD.canonical(inventory)
        os.setxattr(first, "user.proof", b"changed")
        self.assertNotEqual(before, MOD.canonical(MOD.tree_inventory(root)))

    def test_tree_inventory_rejects_fifo(self):
        root = self.base / "tree"
        root.mkdir()
        os.mkfifo(root / "pipe")
        with self.assertRaisesRegex(ValueError, "FIFO"):
            MOD.tree_inventory(root)

    def export_fixture(self, **overrides):
        config = self.config(**overrides)
        config_path = self.base / "config.json"
        self.write_json(config_path, config)
        root = self.base / "rootfs"
        (root / "etc").mkdir(parents=True)
        (root / "etc/armbian-release").write_text("BOARD=" + config["armbian_board"] + "\n")
        (root / "etc/os-release").write_text("ID=ubuntu\nVERSION_CODENAME=noble\n")
        (root / "boot").mkdir()
        release = config["profile"]["kernel_release"]
        (root / "boot" / ("vmlinuz-" + release)).write_bytes(b"24.2@6603887")
        (root / "boot" / ("config-" + release)).write_text("CONFIG_TEST=y\n")
        (root / "var/lib/dpkg").mkdir(parents=True)
        status = root / "var/lib/dpkg/status"
        status.write_text("Package: libc6\nVersion: 2.39\nArchitecture: riscv64\nStatus: install ok installed\n\n"
                          "Package: linux-image-current-spacemit\nVersion: 1.0\nArchitecture: riscv64\nStatus: install ok installed\n")
        identity = {"source_kind": "armbian-native-rootfs", "board": config["board"], "build_id": "fixture-1",
                    "acceleration_lock_sha256": config["source_locks"][MOD.ACCELERATION_LOCK]}
        if MOD.targets.target_for(config["armbian_board"]):
            identity["armbian_board"] = config["armbian_board"]
        self.write_json(root / "etc/bpi-k1-native.json", {**identity, "desktop": "gnome-wayland", "hardware_validation": "pending"})
        work = self.base / "integration"
        work.mkdir()
        packages = INTEGRATION.installed_packages(root)
        inventory = {"schema_version": 1, "count": len(packages), "packages": packages,
                     "dpkg_status_sha256": MOD.digest(status)}
        self.write_json(work / "installed-packages.json", inventory)
        lock = MOD.acceleration.load_lock(ROOT / MOD.ACCELERATION_LOCK)
        integrated = {"schema_version": 1, "source_kind": "armbian-native-rootfs", "status": "complete",
                      "identity": identity, "desktop": "gnome-wayland", "hardware_validation": "pending",
                      "media_policy_applied": False, "kernel": INTEGRATION.kernel_evidence(root, config["board"], lock, packages),
                      "preflight": {"passed": True}, "source_packages": [], "verified_sources": {},
                      "installed_packages": {"file": "installed-packages.json", "count": len(packages),
                                             "sha256": MOD.digest(work / "installed-packages.json")}}
        self.write_json(work / "integration.json", integrated)
        return config_path, root, work, integrated

    def assert_export_rejected(self, config_path, root, work, output):
        # 此處只隔離耗時封存與加速內容預檢；身分、JSON、SHA 和檔案證據實際核對。
        with mock.patch.object(MOD.os, "geteuid", return_value=0), \
             mock.patch.object(MOD.acceleration, "preflight", return_value={"passed": True, "errors": []}), \
             mock.patch.object(MOD, "run", side_effect=AssertionError("拒絕條件必須先於 rsync/mkfs")):
            with self.assertRaises(ValueError):
                MOD.export_rootfs(config_path, root, work, output)

    def test_export_accepts_matching_integration_before_copy_boundary(self):
        config, root, work, _ = self.export_fixture()
        with mock.patch.object(MOD.os, "geteuid", return_value=0), \
             mock.patch.object(MOD.acceleration, "preflight", return_value={"passed": True, "errors": []}), \
             mock.patch.object(MOD, "run", side_effect=RuntimeError("已到達 rsync 邊界")):
            with self.assertRaisesRegex(RuntimeError, "rsync 邊界"):
                MOD.export_rootfs(config, root, work, self.base / "output")

    def test_export_alias_requires_exact_rootfs_board_before_copy(self):
        config, root, work, _ = self.export_fixture(board="bananapif3-vendor-sd", outputs="sd")
        for wrong in ("bananapif3-titan-emmc", "bananapif3", "bananapicm6-vendor-sd"):
            (root / "etc/armbian-release").write_text("BOARD=" + wrong + "\n")
            with self.subTest(wrong=wrong):
                self.assert_export_rejected(config, root, work, self.base / wrong)

    def test_export_rejects_alias_config_from_other_medium(self):
        config, root, work, _ = self.export_fixture(board="bananapif3-vendor-sd", outputs="sd")
        self.write_json(config, self.config(board="bananapif3-titan-emmc", outputs="emmc"))
        self.assert_export_rejected(config, root, work, self.base / "mixed-output")

    def test_export_accepts_exact_alias_before_copy_boundary(self):
        config, root, work, _ = self.export_fixture(board="bananapif3-vendor-sd", outputs="sd")
        with mock.patch.object(MOD.os, "geteuid", return_value=0), \
             mock.patch.object(MOD.acceleration, "preflight", return_value={"passed": True, "errors": []}), \
             mock.patch.object(MOD, "run", side_effect=RuntimeError("已到達 rsync 邊界")):
            with self.assertRaisesRegex(RuntimeError, "rsync 邊界"):
                MOD.export_rootfs(config, root, work, self.base / "output")

    def test_export_rejects_host_root(self):
        config, root, work, _ = self.export_fixture()
        self.assert_export_rejected(config, Path("/"), work, self.base / "output")

    def test_export_rejects_output_parent_symlink_into_rootfs(self):
        config, root, work, _ = self.export_fixture()
        alias = self.base / "rootfs-alias"
        alias.symlink_to(root, target_is_directory=True)
        self.assert_export_rejected(config, root, work, alias / "output")
        self.assertFalse((root / "output").exists())

    def test_export_rejects_integration_from_wrong_schema_or_source_kind(self):
        config, root, work, original = self.export_fixture()
        for key, value in (("schema_version", 0), ("source_kind", "historical-img")):
            self.write_json(work / "integration.json", {**original, key: value})
            with self.subTest(key=key):
                self.assert_export_rejected(config, root, work, self.base / ("output-" + key))

    def test_export_rejects_inventory_hash_tampering(self):
        config, root, work, _ = self.export_fixture()
        path = work / "installed-packages.json"
        path.write_text(path.read_text() + "\n")
        self.assert_export_rejected(config, root, work, self.base / "output")

    def test_export_rejects_marker_from_another_build(self):
        config, root, work, _ = self.export_fixture()
        marker = root / "etc/bpi-k1-native.json"
        value = json.loads(marker.read_text())
        value["build_id"] = "another-build"
        self.write_json(marker, value)
        self.assert_export_rejected(config, root, work, self.base / "output")

    def test_export_rejects_changed_core_file(self):
        config, root, work, integrated = self.export_fixture()
        (root / "boot" / ("vmlinuz-" + integrated["kernel"]["release"])).write_bytes(b"changed")
        self.assert_export_rejected(config, root, work, self.base / "output")

    def test_export_rejects_changed_installed_package_list(self):
        config, root, work, _ = self.export_fixture()
        status = root / "var/lib/dpkg/status"
        status.write_text(status.read_text().replace("Version: 2.39", "Version: 2.40"))
        self.assert_export_rejected(config, root, work, self.base / "output")

    @unittest.skipUnless(all(shutil.which(name) for name in ("sudo", "mkfs.ext4", "debugfs", "e2fsck")),
                         "需要本機 sudo、mkfs.ext4、debugfs 及 e2fsck")
    def test_actual_mkfs_populates_permissions_xattrs_and_hardlinks(self):
        if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode:
            self.skipTest("未提供免互動 sudo，略過擁有者保存測試")
        root = self.base / "ext4-tree"
        root.mkdir()
        file = root / "a"
        file.write_bytes(b"payload\n")
        file.chmod(0o640)
        os.link(file, root / "b")
        (root / "link").symlink_to("/a")
        image = self.base / "small.ext4"
        with image.open("wb") as stream:
            stream.truncate(32 * 1024 * 1024)
        program = ("import os,struct,sys; p=sys.argv[1]; os.chown(p,12345,23456); "
                   "os.setxattr(p,'user.proof',b'native-mkfs'); "
                   "os.setxattr(p,'security.capability',struct.pack('<IIIII',0x02000001,1024,0,0,0))")
        subprocess.run(["sudo", "-n", sys.executable, "-c", program, str(file)], check=True, capture_output=True)
        subprocess.run(["sudo", "-n", "mkfs.ext4", "-q", "-F", "-d", str(root), str(image)], check=True, capture_output=True)
        subprocess.run(["e2fsck", "-fn", str(image)], check=True, capture_output=True)
        def debug(command):
            return subprocess.check_output(["debugfs", "-R", command, str(image)], text=True, stderr=subprocess.DEVNULL)
        first, second, link = debug("stat /a"), debug("stat /b"), debug("stat /link")
        self.assertRegex(first, r"Mode:\s+0640")
        self.assertRegex(first, r"User:\s+12345\s+Group:\s+23456")
        self.assertRegex(first, r"Links:\s+2")
        self.assertEqual(first.split()[1], second.split()[1])
        self.assertIn('"/a"', link)
        attributes = debug("ea_list /a")
        self.assertIn("user.proof", attributes)
        self.assertIn("security.capability", attributes)
        self.assertIn("native-mkfs", debug("ea_get /a user.proof"))


if __name__ == "__main__":
    unittest.main()
