#!/usr/bin/env python3
"""核對板名輸出隔離與首次建置的桌面解析依賴。"""

import json
import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class BoardTargetPackagingTests(unittest.TestCase):
    def routing(self, board, physical, media, target, build_id):
        script = r'''
set -e
enable_extension() { :; }
display_alert() { :; }
source "$SRC/extensions/bpi-k1-vendor/bpi-k1-vendor.sh"
python3() { printf '%s\0' "$@"; printf '\0'; }
post_build_image__850_bpi_k1_vendor_outputs
'''
        env = {**os.environ, "SRC": str(ROOT), "BOARD": board, "BPI_K1_BOARD": physical,
               "BPI_K1_OUTPUTS": media, "BPI_K1_BOARD_TARGET": target,
               "BPI_K1_RELEASE_ID": "20260925-rc7", "ARMBIAN_BUILD_UUID": build_id,
               "BPI_K1_WORK_DIR": "/tmp/本次建置", "BPI_K1_ASSET_DIR": "/tmp/固定配套"}
        result = subprocess.run(["bash", "-c", script], env=env, check=True, capture_output=True)
        return [record.decode().split("\0") for record in result.stdout.rstrip(b"\0").split(b"\0\0")]

    def test_each_target_and_rebuild_get_distinct_output_and_evidence(self):
        targets = json.loads((ROOT / "config/spacemit-k1-profiles/board-targets.json").read_text())["targets"]
        roots = set()
        for board, target in targets.items():
            for build_id in ("first-build", "second-build"):
                calls = self.routing(board, target["board"], target["storage"], board, build_id)
                self.assertEqual(len(calls), 2)
                package, evidence = calls
                self.assertEqual(package[package.index("--storage") + 1], target["storage"])
                root = ROOT / "output/vendor-format/20260925-rc7" / board / build_id
                self.assertEqual(package[package.index("--output") + 1], str(root / target["storage"]))
                self.assertEqual(evidence[evidence.index("--output-root") + 1], str(root))
                self.assertNotIn(root, roots)
                roots.add(root)

    def test_original_extension_output_layout_remains_compatible(self):
        calls = self.routing("bananapicm6", "bpi-cm6", "sd,emmc", "", "original-build")
        self.assertEqual(len(calls), 3)
        for call, storage in zip(calls[:2], ("sd", "emmc")):
            self.assertEqual(call[call.index("--output") + 1],
                             str(ROOT / "output/vendor-format/20260925-rc7/bpi-cm6" / storage))

    def test_basic_dependencies_prepare_yaml_before_configuration(self):
        script = r'''
set -e
source "$SRC/lib/functions/host/basic-deps.sh"
which() { return 0; }
python3() { return "$YAML_STATUS"; }
is_root_or_sudo_prefix() { :; }
display_alert() { :; }
run_host_command_logged() { printf '%s\n' "$*"; }
prepare_host_basic
'''
        for status in (0, 1):
            result = subprocess.run(["bash", "-c", script], check=True, capture_output=True, text=True,
                                    env={**os.environ, "SRC": str(ROOT), "YAML_STATUS": str(status)})
            if status:
                self.assertIn("install -qq -y --no-install-recommends python3-yaml", result.stdout)
                self.assertIn("apt-get", result.stdout)
            else:
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
