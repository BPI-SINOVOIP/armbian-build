#!/usr/bin/env python3
"""核對 K1 桌面來源固定、安裝一致性與一般 Armbian 相容行為。"""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("bpi_k1_desktop", ROOT / "tools/bpi_k1_desktop.py")
desktop = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(desktop)


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "upstream"
        self.source.mkdir()
        self.profile = desktop.load_profile(desktop.DEFAULT_PROFILE)
        self.profile_path = self.root / "profile.json"
        self.prepared = self.root / "prepared"
        self.rootfs = self.root / "rootfs"
        self.rootfs.mkdir()

    def write(self, relative, text, mode=0o644):
        path = self.source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        path.chmod(mode)
        return path

    def fixture(self):
        gnome = self.write("tools/modules/desktops/yaml/gnome.yaml", yaml.safe_dump({
            "name": "gnome", "display_manager": "gdm3", "status": "supported",
            "tiers": {"minimal": {"packages": self.profile["required_packages"]}},
            "releases": {"noble": {"architectures": ["arm64", "amd64"]}},
        }, sort_keys=False))
        self.write("tools/modules/desktops/scripts/parse_desktop_yaml.py", '''import sys, pathlib, yaml
p = yaml.safe_load((pathlib.Path(sys.argv[1]) / "gnome.yaml").read_text())
r = p["releases"][sys.argv[3]]
packages = set(p["tiers"]["minimal"]["packages"] + r.get("packages", [])) - set(r.get("packages_remove", []))
print('DESKTOP_AVAILABLE="' + ("yes" if sys.argv[4] in r["architectures"] else "no") + '"')
print('DESKTOP_DM="gdm3"')
print('DESKTOP_PACKAGES="' + " ".join(sorted(packages)) + '"')
''')
        self.write("tools/modules/system/runner-cleanup/test-data", "測試資料\n")
        self.write("bin/armbian-config", "#!/bin/bash\nexit 0\n", 0o755)
        self.write("share/test-data", "測試資料\n")
        self.write("LICENSE", "固定來源內容\n")
        libraries = " ".join(desktop.REQUIRED_LIBRARIES)
        self.write("tools/config-assemble.sh", f'''#!/bin/bash
set -eu
mkdir -p lib/armbian-config
for name in {libraries}; do
    printf '#!/bin/bash\\ntrue\\n' > "lib/armbian-config/config.$name.sh"
done
printf '{{"menu": []}}\\n' > lib/armbian-config/config.jobs.json
''', 0o755)
        subprocess.run(["git", "init", "-q", str(self.source)], check=True)
        subprocess.run(["git", "-C", str(self.source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.source), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "測試固定桌面來源"], check=True)
        self.profile["commit"] = subprocess.check_output(["git", "-C", str(self.source), "rev-parse", "HEAD"], text=True).strip()
        self.profile["upstream_gnome_sha256"] = desktop.digest(gnome.read_bytes())
        self.profile_path.write_text(json.dumps(self.profile, ensure_ascii=False))

    def test_target_gate_rejects_unvalidated_combinations(self):
        for board in ("bananapicm6", "bananapif3"):
            desktop.validate_target(self.profile, board, "noble", "riscv64", "gnome", "minimal")
        base = ["bananapicm6", "noble", "riscv64", "gnome", "minimal"]
        for index, value in enumerate(("other", "jammy", "arm64", "xfce", "full")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                args = base.copy()
                args[index] = value
                desktop.validate_target(self.profile, *args)

    def test_four_registered_aliases_share_physical_desktop_profile(self):
        for board in desktop.targets.load_registry()["targets"]:
            with self.subTest(board=board):
                desktop.validate_target(self.profile, board, "noble", "riscv64", "gnome", "minimal")
                with self.assertRaises(ValueError):
                    desktop.validate_target(self.profile, board + "-unknown", "noble", "riscv64", "gnome", "minimal")

    def test_pinned_git_object_ignores_dirty_worktree_and_installs_identical_runtime(self):
        self.fixture()
        self.write("LICENSE", "這是未提交內容，不應進入封裝\n")
        manifest = desktop.prepare(self.source, self.prepared, self.profile_path)
        self.assertEqual((self.prepared / "runtime/LICENSE").read_text(), "固定來源內容\n")
        self.assertEqual(manifest["resolved"]["DESKTOP_AVAILABLE"], "yes")
        desktop.install(self.prepared, self.rootfs, self.profile_path)
        self.assertEqual(desktop.inventory(self.prepared / "runtime"), desktop.inventory(self.rootfs / desktop.INSTALL_PATH))
        installed = json.loads((self.rootfs / "usr/share/doc/bpi-k1-desktop/manifest.json").read_text())
        self.assertEqual(installed, manifest)
        self.assertEqual(desktop.prepare(self.source, self.prepared, self.profile_path), manifest)

    def test_patch_refuses_changed_upstream_without_writing(self):
        self.fixture()
        path = self.source / "tools/modules/desktops/yaml/gnome.yaml"
        path.write_text(path.read_text() + "\n# 改動\n")
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "雜湊不符"):
            desktop.patch_gnome(self.source, self.profile)
        self.assertEqual(path.read_bytes(), before)

    def test_cached_content_and_permissions_are_verified(self):
        self.fixture()
        desktop.prepare(self.source, self.prepared, self.profile_path)
        path = self.prepared / "runtime/bin/armbian-config"
        path.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "偏離來源"):
            desktop.verify(self.prepared, self.profile_path)
        path.chmod(0o755)
        path.write_text("#!/bin/bash\nexit 1\n")
        with self.assertRaisesRegex(ValueError, "偏離來源"):
            desktop.install(self.prepared, self.rootfs, self.profile_path)
        self.assertFalse((self.rootfs / desktop.INSTALL_PATH).exists())

    def test_profile_change_invalidates_cache(self):
        self.fixture()
        desktop.prepare(self.source, self.prepared, self.profile_path)
        self.profile["add_packages"].append("gnome-calculator")
        self.profile_path.write_text(json.dumps(self.profile))
        with self.assertRaisesRegex(ValueError, "工具／設定不一致"):
            desktop.verify(self.prepared, self.profile_path)

    def test_runtime_missing_required_component_is_rejected(self):
        self.fixture()
        desktop.patch_gnome(self.source, self.profile)
        profile = dict(self.profile, required_packages=[*self.profile["required_packages"], "missing-package"])
        with self.assertRaisesRegex(ValueError, "缺少必要套件"):
            desktop.resolved_desktop(self.source, profile)

    def test_install_rejects_host_root_and_symlink_escape(self):
        self.fixture()
        desktop.prepare(self.source, self.prepared, self.profile_path)
        with self.assertRaisesRegex(ValueError, "獨立"):
            desktop.install(self.prepared, Path("/"), self.profile_path)
        (self.rootfs / "usr").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "連結跳轉"):
            desktop.install(self.prepared, self.rootfs, self.profile_path)

    def test_source_archive_rejects_symlinks(self):
        self.fixture()
        (self.source / "escape").symlink_to("/etc/passwd")
        subprocess.run(["git", "-C", str(self.source), "add", "escape"], check=True)
        subprocess.run(["git", "-C", str(self.source), "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "測試拒絕連結"], check=True)
        commit = subprocess.check_output(["git", "-C", str(self.source), "rev-parse", "HEAD"], text=True).strip()
        with self.assertRaisesRegex(ValueError, "連結或特殊檔案"):
            desktop.unpack_git_archive(self.source, commit, self.root / "unpacked")

    def test_default_framework_keeps_original_source_and_command(self):
        parser = self.root / "cache/sources/armbian-configng/tools/modules/desktops/scripts/parse_desktop_yaml.py"
        parser.parent.mkdir(parents=True)
        parser.write_text("# 測試不進入互動解析\n")
        script = '''set -e
source "$FRAMEWORK_CONFIG"
display_alert() { :; }
call_extension_method() { cat >/dev/null; }
fetch_from_repo() { printf '%s\\n' "$@"; }
interactive_desktop_main_configuration
printf '%s\\n' "$DESKTOP_SOURCE_DIRECTORY"
'''
        env = dict(os.environ, FRAMEWORK_CONFIG=str(ROOT / "lib/functions/configuration/config-desktop.sh"),
                   SRC=str(self.root), BUILD_DESKTOP="yes", DESKTOP_ENVIRONMENT="gnome", DESKTOP_TIER="minimal",
                   DESKTOP_APPGROUPS_SELECTED="")
        for variable in ("CONFIGNG_REPOSITORY", "CONFIGNG_REF", "CONFIGNG_CACHE_NAME"):
            env.pop(variable, None)
        output = subprocess.check_output(["bash", "-c", script], env=env, text=True).splitlines()
        self.assertEqual(output, ["https://github.com/armbian/configng", "armbian-configng", "branch:main", str(parser.parents[4])])


if __name__ == "__main__":
    unittest.main()
