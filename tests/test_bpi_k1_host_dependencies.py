#!/usr/bin/env python3
"""主機相依規劃與框架 APT 語法的離線驗證，不安裝套件或操作硬體。"""
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("hostdeps", ROOT / "tools/bpi_k1_host_dependencies.py")
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class K1HostDependenciesTests(unittest.TestCase):
    def plan(self, board="bananapicm6-titan-emmc", camera="dual-imx415"):
        return MOD.plan(board, camera, "noble", "amd64")

    def test_fixed_toolchain_comes_from_existing_lock(self):
        actual = self.plan()
        expected = {p["package"]: p["version"] for p in MOD.load(MOD.RPI_LOCK)["toolchain_packages"]}
        self.assertEqual(expected, actual["exact"])
        self.assertEqual(len(expected), 5)
        for name, version in expected.items():
            self.assertIn("native-toolchain::" + name + "=" + version, actual["packages"])
        self.assertIn("native-toolchain::gcc-riscv64-linux-gnu", actual["packages"])

    def test_camera_minimum_versions_and_packages_are_complete(self):
        actual = self.plan()
        expected = MOD.load(MOD.CAMERA_LOCK)["toolchain"]["host_requirements"]
        self.assertEqual(expected, actual["minimum"])
        self.assertTrue(all("build-tools::" + name in actual["packages"] for name in expected))
        MOD.check_installed(actual, {**actual["exact"], **actual["minimum"]})

    def test_reject_wrong_toolchain_version(self):
        actual = self.plan()
        installed = {**actual["exact"], **actual["minimum"], "gcc-13-riscv64-linux-gnu": "99"}
        with self.assertRaisesRegex(ValueError, "固定工具鏈版本不符"):
            MOD.check_installed(actual, installed)

    def test_reject_old_camera_dependency(self):
        actual = self.plan()
        installed = {**actual["exact"], **actual["minimum"], "libgmp10": "1"}
        with self.assertRaisesRegex(ValueError, "相機主機相依版本不足"):
            MOD.check_installed(actual, installed)

    def test_multiarch_query_selects_native_package(self):
        values = "libc6\tamd64\tinstalled\t2.39\nlibc6\ti386\tinstalled\t2.39\n"
        self.assertEqual(MOD.installed_version(values, "libc6"), "2.39")
        self.assertEqual(MOD.installed_version("libc6-riscv64-cross\tall\tinstalled\t2.39", "libc6-riscv64-cross"), "2.39")

    def test_foreign_only_package_does_not_satisfy_host(self):
        with self.assertRaisesRegex(ValueError, "缺少唯一"):
            MOD.installed_version("libc6\ti386\tinstalled\t2.39", "libc6")

    def test_unsupported_host_or_board_is_rejected(self):
        for release, architecture in [("jammy", "amd64"), ("noble", "arm64"), ("", "amd64")]:
            with self.subTest(release=release, architecture=architecture):
                with self.assertRaisesRegex(ValueError, "只支援"):
                    MOD.plan("bananapicm6", "none", release, architecture)
        with self.assertRaisesRegex(ValueError, "不適用"):
            MOD.plan("bananapim7", "none", "noble", "amd64")

    def test_f3_does_not_get_cm6_toolchain_constraints(self):
        actual = self.plan("bananapif3-titan-emmc", "none")
        self.assertEqual(actual["exact"], {})
        self.assertEqual(actual["minimum"], {})
        self.assertNotIn(MOD.BT_LOCK, actual["source_locks"])

    def test_unsafe_apt_tokens_are_rejected(self):
        original = MOD.load
        def modified(path):
            data = original(path)
            if path == MOD.RPI_LOCK:
                data["toolchain_packages"][0]["version"] = "13;unexpected"
            return data
        with mock.patch.object(MOD, "load", side_effect=modified):
            with self.assertRaisesRegex(ValueError, "版本不合法"):
                self.plan()

    def test_extension_emits_fixed_packages_without_installing(self):
        script = '''
set -euo pipefail
enable_extension() { :; }
exit_with_error() { printf '%s\\n' "$*" >&2; return 1; }
source "$SRC/extensions/bpi-k1-vendor/bpi-k1-vendor.sh"
BOARD=bananapicm6-titan-emmc
BPI_CM6_CAMERA_PROFILE=dual-imx415
host_release=noble
host_arch=amd64
declare -a EXTRA_BUILD_DEPS=()
add_host_dependencies__bpi_k1_vendor
printf '%s\\n' "${EXTRA_BUILD_DEPS[@]}"
'''
        result = subprocess.run(["bash", "-c", script], env={"PATH": "/usr/bin:/bin", "SRC": str(ROOT)},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), self.plan()["packages"])

    def test_framework_preserves_exact_version_for_apt_and_docker(self):
        wanted = "gcc-13-riscv64-linux-gnu=13.3.0-6ubuntu2~24.04.1cross1"
        script = '''
set -euo pipefail
source "$SRC/lib/functions/host/host-utils.sh"
source "$SRC/lib/functions/host/docker.sh"
dpkg-query() { printf '%s\\n' gcc-13-riscv64-linux-gnu; }
display_alert() { :; }
host_apt_get() { :; }
host_apt_get_install() { printf 'APT:%s\\n' "$@"; }
exit_with_error() { return 1; }
install_host_side_packages "native-toolchain::$WANTED"
declare -a host_dependencies=("native-toolchain::$WANTED")
declare -a BASIC_DEPS=(git)
docker_create_dockerfile_apt_install_runs ""
printf '%s\\n' "$DOCKERFILE_APT_INSTALL_RUNS"
'''
        result = subprocess.run(["bash", "-c", script], env={"PATH": "/usr/bin:/bin", "SRC": str(ROOT), "WANTED": wanted},
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("APT:" + wanted + "\n", result.stdout)
        self.assertIn("--no-install-recommends " + wanted, result.stdout)
        self.assertNotIn("native-toolchain::" + wanted, result.stdout)


if __name__ == "__main__":
    unittest.main()
