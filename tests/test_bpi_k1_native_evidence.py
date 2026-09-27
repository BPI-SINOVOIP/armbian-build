"""以真實小檔核對原生證據發布、交付綁定及失敗邊界。"""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bpi_k1_native_evidence", ROOT / "tools/bpi_k1_native_evidence.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.prepared = self.base / "prepared"
        self.output = self.base / "output" / "bpi-f3"
        self.prepared.mkdir()
        self.output.mkdir(parents=True)
        self.kernel = "6.18.37-current-spacemit"
        profile = {"board": "bpi-f3", "branch": "current", "kernel_release": self.kernel,
                   "camera_profiles": ["none"]}
        identity_fields = {"armbian_board": "bananapif3", "board": "bpi-f3", "branch": "current",
                           "release": "noble", "desktop": "gnome", "tier": "minimal",
                           "camera_profile": "none", "profile": profile,
                           "source_locks": {MOD.ACCELERATION_LOCK: "a" * 64}}
        self.config = {"schema_version": 1, "source_kind": MOD.SOURCE_KIND, **identity_fields,
                       "profile_sha256": MOD.sha(MOD.canonical(identity_fields)), "release_id": "20260924-rc6",
                       "outputs": ["emmc", "sd"], "hardware_validation": "pending"}
        status = ("Package: libc6\nVersion: 2.39\nArchitecture: riscv64\nStatus: install ok installed\n\n"
                  "Package: linux-image-current-spacemit\nVersion: 1.0\nArchitecture: riscv64\n"
                  "Status: install ok installed\n").encode()
        self.packages = [{"package": "libc6", "version": "2.39", "architecture": "riscv64", "source": "libc6"},
                         {"package": "linux-image-current-spacemit", "version": "1.0", "architecture": "riscv64",
                          "source": "linux-image-current-spacemit"}]
        self.inventory = {"schema_version": 1, "count": 2, "packages": self.packages, "dpkg_status_sha256": MOD.sha(status)}
        kernel_files = {"/boot/vmlinuz-" + self.kernel: "核心測試內容".encode(), "/boot/config-" + self.kernel: b"CONFIG_TEST=y\n"}
        self.content = {name.lstrip("/"): {"type": "file", "size": len(data), "sha256": MOD.sha(data),
                                          "uid": 0, "gid": 0, "mode": 420}
                        for name, data in kernel_files.items()}
        self.content["var/lib/dpkg/status"] = {"type": "file", "size": len(status), "sha256": MOD.sha(status)}
        source_files = {"tools/來源.py": {"sha256": MOD.sha("固定來源".encode())}}
        self.snapshot = {"git_commit": "1" * 40, "files": source_files, "tree_sha256": MOD.sha(MOD.canonical(source_files))}
        self.identity = {"schema_version": 2, "source_kind": MOD.SOURCE_KIND, "board": "bpi-f3",
                         "camera_profile": "none", "profile_sha256": self.config["profile_sha256"],
                         "source_commit": self.snapshot["git_commit"], "build_source_sha256": self.snapshot["tree_sha256"],
                         "rootfs_tree_sha256": MOD.sha(MOD.canonical(self.content)),
                         "acceleration_lock_sha256": "a" * 64,
                         "native_dtb": {"path": "/dtb/spacemit/k1-bananapi-f3.dtb", "sha256": "d" * 64}}
        self.prep = {"schema_version": 2, "status": "complete", "identity": self.identity,
                     "kernel": self.kernel, "desktop": "gnome-wayland", "rootfs_sha256": "f" * 64,
                     "hardware_validation": "pending"}
        self.integrated = {"schema_version": 1, "source_kind": MOD.SOURCE_KIND, "status": "complete",
                           "desktop": "gnome-wayland", "hardware_validation": "pending",
                           "identity": {"source_kind": MOD.SOURCE_KIND, "build_id": "本次測試", "board": "bpi-f3",
                                        "acceleration_lock_sha256": "a" * 64},
                           "installed_packages": {"file": "installed-packages.json", "count": 2,
                                                  "sha256": MOD.sha(MOD.encoded(self.inventory))},
                           "kernel": {"release": self.kernel, "packages": self.packages[1:],
                                      "files": {name: {"bytes": len(data), "sha256": MOD.sha(data)}
                                                for name, data in kernel_files.items()}}}
        self.write("preparation.json", self.prep)
        self.write("source-snapshot.json", self.snapshot)
        self.write("native-config.json", self.config)
        self.write("rootfs-content.json", self.content)
        self.write("installed-packages.json", self.inventory)
        self.write("integration.json", self.integrated)
        self.write("acceleration-preflight.json", {"passed": True, "board": "bpi-f3", "stage": "installed"})
        (self.prepared / "dpkg-status").write_bytes(status)
        (self.prepared / "rootfs.ext4").write_bytes("不得複製的映像".encode())
        (self.prepared / "root-tree").mkdir()
        (self.prepared / "root-tree" / "不得複製").write_text("不在白名單")
        self.manifests = {}
        for medium in self.config["outputs"]:
            directory = self.output / medium
            directory.mkdir()
            suffix = "vendor-sd" if medium == "sd" else "titan-emmc"
            extension = ".img.zip" if medium == "sd" else ".zip"
            name = f"Armbian_Noble_bpi-f3_gnome_{suffix}_20260924-rc6{extension}"
            artifact = directory / name
            artifact.write_bytes(("已驗過封裝的小型成品：" + medium).encode())
            manifest = {"schema_version": 2, "board": "bpi-f3", "storage": medium, "kernel": self.kernel,
                        "release_id": "20260924-rc6", "release": "noble", "desktop": "gnome-wayland",
                        "hardware_validation": "pending", "release_status": "candidate_unverified",
                        "identity": {**copy.deepcopy(self.identity), "storage": medium, "release_id": "20260924-rc6"},
                        "artifact": {"name": name, **MOD.file_record(artifact)}}
            self.manifests[medium] = manifest
            self.write_media(medium, "manifest.json", manifest)
            self.write_media(medium, "verification.json", {"status": "passed", "scope": "offline-archive-integrity",
                             "board": "bpi-f3", "storage": medium, "hardware_validation": "pending",
                             "release_status": "candidate_unverified"})

    def write(self, name, data):
        (self.prepared / name).write_bytes(MOD.encoded(data))

    def write_media(self, medium, name, data):
        (self.output / medium / name).write_bytes(MOD.encoded(data))

    def rejected(self):
        with self.assertRaises((ValueError, OSError)):
            MOD.publish(self.prepared, self.output)
        self.assertFalse((self.output / "evidence").exists())
        self.assertEqual(list(self.output.glob(".evidence-*")), [])

    def test_publish_binds_both_media_and_only_copies_whitelist(self):
        result = MOD.publish(self.prepared, self.output, self.prepared / "native-config.json")
        evidence = self.output / "evidence"
        self.assertEqual(set(p.name for p in evidence.iterdir()), set(MOD.FILES) | {"evidence.json", "SHA256SUMS"})
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["hardware_validation"], "pending")
        self.assertEqual(set(result["media"]), {"sd", "emmc"})
        for name in MOD.FILES:
            self.assertEqual((evidence / name).read_bytes(), (self.prepared / name).read_bytes())
            self.assertEqual(result["files"][name], MOD.file_record(evidence / name))
        check = subprocess.run(["sha256sum", "--check", "SHA256SUMS"], cwd=evidence, capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)

    def test_selected_single_medium_does_not_require_the_other(self):
        self.config["outputs"] = ["sd"]
        self.write("native-config.json", self.config)
        (self.output / "emmc" / "manifest.json").unlink()
        self.assertEqual(list(MOD.publish(self.prepared, self.output)["media"]), ["sd"])

    def enable_alias(self, name="bananapif3-vendor-sd"):
        target = MOD.targets.target_for(name)
        self.config["armbian_board"] = name
        self.config["outputs"] = [target["storage"]]
        self.config["source_locks"][MOD.targets.REGISTRY] = "b" * 64
        self.config["profile_sha256"] = MOD.sha(MOD.canonical({k: self.config[k] for k in MOD.PROFILE_FIELDS}))
        self.identity.update(armbian_board=name, profile_sha256=self.config["profile_sha256"])
        self.integrated["identity"]["armbian_board"] = name
        self.write("native-config.json", self.config)
        self.write("preparation.json", self.prep)
        self.write("integration.json", self.integrated)
        self.write("acceleration-preflight.json", {"passed": True, "board": "bpi-f3", "stage": "installed", "armbian_board": name})
        medium = target["storage"]
        self.manifests[medium]["identity"].update(armbian_board=name, profile_sha256=self.config["profile_sha256"])
        self.write_media(medium, "manifest.json", self.manifests[medium])

    def test_alias_evidence_preserves_target_and_selects_only_its_medium(self):
        self.enable_alias()
        result = MOD.publish(self.prepared, self.output)
        self.assertEqual(result["armbian_board"], "bananapif3-vendor-sd")
        self.assertEqual(result["outputs"], ["sd"])

    def test_alias_evidence_rejects_other_media_even_with_rehashed_configuration(self):
        self.enable_alias()
        self.config["outputs"] = ["emmc"]
        self.write("native-config.json", self.config)
        self.rejected()

    def test_alias_evidence_rejects_integrated_root_from_other_alias(self):
        self.enable_alias()
        self.integrated["identity"]["armbian_board"] = "bananapif3-titan-emmc"
        self.write("integration.json", self.integrated)
        self.rejected()

    def test_alias_evidence_rejects_preflight_without_exact_alias(self):
        self.enable_alias()
        self.write("acceleration-preflight.json", {"passed": True, "board": "bpi-f3", "stage": "installed"})
        self.rejected()

    def test_config_mismatch_rejected(self):
        other = self.base / "另一份配置.json"
        other.write_bytes(MOD.encoded({**self.config, "release_id": "20260925-rc7"}))
        with self.assertRaisesRegex(ValueError, "指定配置"):
            MOD.publish(self.prepared, self.output, other)

    def test_legacy_or_incomplete_preparation_rejected(self):
        for values in ({"schema_version": 1}, {"status": "preparing"}, {"kernel": "錯誤核心"}):
            self.write("preparation.json", {**self.prep, **values})
            with self.subTest(values=values):
                self.rejected()

    def test_profile_summary_rejected(self):
        self.config["profile"]["branch"] = "legacy"
        self.config["branch"] = "legacy"
        self.write("native-config.json", self.config)
        self.rejected()

    def test_source_inventory_tampering_rejected(self):
        self.snapshot["files"]["新增檔案"] = {"sha256": "0" * 64}
        self.write("source-snapshot.json", self.snapshot)
        self.rejected()

    def test_root_inventory_tampering_rejected(self):
        self.content["boot/vmlinuz-" + self.kernel]["sha256"] = "0" * 64
        self.write("rootfs-content.json", self.content)
        self.rejected()

    def test_kernel_evidence_must_match_root_inventory(self):
        self.integrated["kernel"]["files"]["/boot/config-" + self.kernel]["sha256"] = "0" * 64
        self.write("integration.json", self.integrated)
        self.rejected()

    def test_dpkg_status_tampering_rejected(self):
        (self.prepared / "dpkg-status").write_bytes("遭修改".encode())
        self.rejected()

    def test_inventory_package_values_must_match_dpkg_status(self):
        self.inventory["packages"][0]["version"] = "另一版本"
        self.write("installed-packages.json", self.inventory)
        self.integrated["installed_packages"]["sha256"] = MOD.sha(MOD.encoded(self.inventory))
        self.write("integration.json", self.integrated)
        self.rejected()

    def test_dpkg_evidence_must_match_root_inventory(self):
        self.content["var/lib/dpkg/status"]["sha256"] = "0" * 64
        self.write("rootfs-content.json", self.content)
        self.identity["rootfs_tree_sha256"] = MOD.sha(MOD.canonical(self.content))
        self.write("preparation.json", self.prep)
        self.rejected()

    def test_other_rootfs_manifest_rejected(self):
        self.manifests["emmc"]["identity"]["rootfs_tree_sha256"] = "0" * 64
        self.write_media("emmc", "manifest.json", self.manifests["emmc"])
        self.rejected()

    def test_missing_second_manifest_rejected(self):
        (self.output / "sd" / "manifest.json").unlink()
        self.rejected()

    def test_missing_completed_archive_verification_rejected(self):
        (self.output / "sd" / "verification.json").unlink()
        self.rejected()

    def test_changed_artifact_rejected(self):
        path = self.output / "sd" / self.manifests["sd"]["artifact"]["name"]
        path.write_bytes("另一個映像".encode())
        self.rejected()

    def test_artifact_traversal_name_rejected(self):
        self.manifests["sd"]["artifact"]["name"] = "../../不可讀取"
        self.write_media("sd", "manifest.json", self.manifests["sd"])
        self.rejected()

    def test_source_symlink_rejected(self):
        path = self.prepared / "dpkg-status"
        content = path.read_bytes()
        path.unlink()
        external = self.base / "外部資料"
        external.write_bytes(content)
        path.symlink_to(external)
        self.rejected()

    def test_artifact_symlink_rejected(self):
        path = self.output / "sd" / self.manifests["sd"]["artifact"]["name"]
        external = self.base / "外部映像"
        path.rename(external)
        path.symlink_to(external)
        self.rejected()

    def test_intermediate_directory_symlink_rejected(self):
        alias = self.base / "連結"
        alias.symlink_to(self.output.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            MOD.publish(self.prepared, alias / "bpi-f3")

    def test_output_within_prepared_rejected(self):
        nested = self.prepared / "output"
        nested.mkdir()
        with self.assertRaisesRegex(ValueError, "互相包含"):
            MOD.publish(self.prepared, nested)

    def test_existing_evidence_is_not_overwritten(self):
        evidence = self.output / "evidence"
        evidence.mkdir()
        sentinel = evidence / "已交付資料"
        sentinel.write_bytes("保留".encode())
        with self.assertRaisesRegex(ValueError, "拒絕覆寫"):
            MOD.publish(self.prepared, self.output)
        self.assertEqual(sentinel.read_bytes(), "保留".encode())

    def test_failed_copy_does_not_publish_complete_or_leave_scratch(self):
        original = Path.write_bytes

        def fail(path, data):
            if path.name == "integration.json" and ".evidence-" in str(path):
                raise OSError("模擬儲存空間不足")
            return original(path, data)

        with mock.patch.object(Path, "write_bytes", fail):
            self.rejected()

    def test_cli_runs_with_real_files(self):
        result = subprocess.run(["python3", "-B", str(ROOT / "tools/bpi_k1_native_evidence.py"),
                                 "--prepared", str(self.prepared), "--output-root", str(self.output)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "complete")


if __name__ == "__main__":
    unittest.main()
