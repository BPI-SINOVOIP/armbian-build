#!/usr/bin/env python3
"""驗證官方媒體板型的真實設定入口；禁止下載、編譯或存取硬體。"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "bananapicm6-titan-emmc": ("legacy", "emmc", "dual-imx415"),
    "bananapicm6-vendor-sd": ("legacy", "sd", "dual-imx415"),
    "bananapif3-titan-emmc": ("current", "emmc", "none"),
    "bananapif3-vendor-sd": ("current", "sd", "none"),
}

PRELUDE = r'''
set -eo pipefail
SRC="$1"
BOARD="$2"
shift 2
USERPATCHES_PATH="${SRC}/tests/不存在的使用者設定目錄"
declare -A ARMBIAN_PARSED_CMDLINE_PARAMS=()
for assignment in "$@"; do
    name="${assignment%%=*}"
    value="${assignment#*=}"
    declare -g "${name}=${value}"
    ARMBIAN_PARSED_CMDLINE_PARAMS["${name}"]="${value}"
done
display_alert() { :; }
track_general_config_variables() { :; }
exit_with_error() { printf '%s\n' "$1" >&2; exit 91; }
dialog_if_terminal_set_vars() { printf '%s\n' '不應開啟互動選單' >&2; exit 92; }
dialog_menu() { printf '%s\n' '不應開啟桌面選單' >&2; exit 93; }
source "${SRC}/lib/functions/general/extensions.sh"
extension_manager_declare_globals
source "${SRC}/lib/functions/configuration/interactive.sh"
source "${SRC}/lib/functions/main/config-interactive.sh"
source "${SRC}/lib/functions/main/config-prepare.sh"
'''

ENTRYPOINT = PRELUDE + r'''
config_early_init
config_possibly_interactive_kernel_board
config_source_board_file
BUILDING_IMAGE=yes
config_possibly_interactive_branch_release_desktop_minimal
python3 - "$BOARD" "$BOARD_TYPE" "$KERNEL_CONFIGURE" "$KERNEL_TARGET" "$BRANCH" \
    "$RELEASE" "$BUILD_DESKTOP" "$BUILD_MINIMAL" "$DESKTOP_ENVIRONMENT" \
    "$DESKTOP_TIER" "$BPI_K1_OUTPUTS" "$BPI_CM6_CAMERA_PROFILE" "$BPI_K1_RELEASE_ID" \
    "$BPI_K1_BOARD_TARGET" "$BOOT_FDT_FILE" \
    "$(type -t post_build_image__850_bpi_k1_vendor_outputs)" \
    "$(type -t pre_desktop_sources__bpi_k1_desktop)" <<'PY'
import json
import sys
keys = ('board', 'board_type', 'kernel_configure', 'kernel_target', 'branch',
        'release', 'build_desktop', 'build_minimal', 'desktop', 'tier', 'outputs',
        'camera', 'release_id', 'target_marker', 'dtb', 'vendor_hook', 'desktop_hook')
print(json.dumps(dict(zip(keys, sys.argv[1:]))))
PY
'''


class BoardEntrypointTests(unittest.TestCase):
    def run_shell(self, script, board="bananapicm6-titan-emmc", assignments=(), root=ROOT):
        # 不繼承使用者的建置參數，避免改變測試範圍。
        env = {key: value for key, value in os.environ.items()
               if key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")}
        return subprocess.run(["bash", "-c", script, "測試入口", str(root), board, *assignments],
                              env=env, text=True, capture_output=True, check=False)

    def test_four_targets_skip_prompts_and_load_real_extensions(self):
        for board, (branch, storage, camera) in TARGETS.items():
            with self.subTest(board=board):
                result = self.run_shell(ENTRYPOINT, board)
                self.assertEqual(result.returncode, 0, result.stderr)
                config = json.loads(result.stdout)
                self.assertEqual(config["board"], board)
                self.assertEqual(config["target_marker"], board)
                self.assertEqual(config["board_type"], "wip")
                self.assertEqual(config["kernel_configure"], "no")
                self.assertEqual(config["kernel_target"], branch)
                self.assertEqual(config["branch"], branch)
                self.assertEqual(config["outputs"], storage)
                self.assertEqual(config["camera"], camera)
                self.assertEqual(config["release"], "noble")
                self.assertEqual(config["build_desktop"], "yes")
                self.assertEqual(config["build_minimal"], "no")
                self.assertEqual(config["desktop"], "gnome")
                self.assertEqual(config["tier"], "minimal")
                self.assertEqual(config["release_id"], "20260927-rc12")
                self.assertEqual(config["vendor_hook"], "function")
                self.assertEqual(config["desktop_hook"], "function")
                expected_dtb = ("spacemit/k1-x_bpi_cm6.dtb" if "cm6" in board
                                else "spacemit/k1-bananapi-f3.dtb")
                self.assertEqual(config["dtb"], expected_dtb)

    def test_conflicting_cli_values_fail_before_interactive_or_extensions(self):
        conflicts = ("RELEASE=bookworm", "RELEASE=", "BUILD_DESKTOP=no", "BUILD_MINIMAL=yes",
                     "BRANCH=current", "KERNEL_CONFIGURE=yes", "KERNEL_CONFIGURE=",
                     "DESKTOP_ENVIRONMENT=xfce", "DESKTOP_TIER=full", "BPI_K1_OUTPUTS=sd",
                     "BPI_CM6_CAMERA_PROFILE=none", "DESKTOP_APPGROUPS_SELECTED=office",
                     "CARD_DEVICE=/dev/禁止寫入", "BPI_K1_RELEASE_ID=")
        for assignment in conflicts:
            with self.subTest(assignment=assignment):
                result = self.run_shell(ENTRYPOINT, assignments=(assignment,))
                self.assertEqual(result.returncode, 91, result.stderr)
                self.assertNotIn("不應開啟", result.stderr)
                self.assertFalse(result.stdout)

    def test_matching_values_and_valid_release_override_remain_usable(self):
        result = self.run_shell(ENTRYPOINT, assignments=("BUILD_DESKTOP=yes", "RELEASE=noble",
                                "DESKTOP_TIER=minimal", "BPI_K1_RELEASE_ID=20260926-rc8"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["release_id"], "20260926-rc8")

    def test_ordinary_board_keeps_early_defaults_unchanged(self):
        script = PRELUDE + r'''
config_early_init
[[ -z ${KERNEL_CONFIGURE:-} && -z ${BUILD_DESKTOP:-} && -z ${BPI_K1_BOARD_TARGET:-} ]]
config_source_board_file
[[ -z ${BPI_K1_OUTPUTS:-} && -z ${RELEASE:-} ]]
[[ $(type -t post_build_image__850_bpi_k1_vendor_outputs) != function ]]
'''
        for board in ("bananapicm6", "bananapif3"):
            with self.subTest(board=board):
                result = self.run_shell(script, board)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_interactive_board_selection_can_apply_defaults_after_selection(self):
        # 實際選單先選核心設定，再選板型；此處模擬已選定「不修改核心」。
        script = PRELUDE + r'''
selected_board="$BOARD"
BOARD=""
config_early_init
[[ -z ${BUILD_DESKTOP:-} ]]
KERNEL_CONFIGURE=no
BOARD="$selected_board"
config_source_board_file
[[ "$BUILD_DESKTOP" == yes && "$BPI_K1_BOARD_TARGET" == "$BOARD" ]]
config_possibly_interactive_branch_release_desktop_minimal
'''
        result = self.run_shell(script)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_target_menu_keeps_candidate_classification(self):
        script = PRELUDE + r'''
WIP_STATE=unsupported
declare -a names=() options=()
declare -A types=() files=() descriptions=()
get_list_of_all_buildable_boards names options types files descriptions
for target in bananapicm6-titan-emmc bananapif3-titan-emmc bananapicm6-vendor-sd bananapif3-vendor-sd; do
    [[ "${types[$target]}" == wip && "${descriptions[$target]}" == *候選* ]]
done
[[ "${types[bananapif3]}" == conf && "${types[bananapicm6]}" == wip ]]
'''
        result = self.run_shell(script)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_tampered_registry_cannot_change_media_or_physical_board(self):
        original = json.loads((ROOT / "config/spacemit-k1-profiles/board-targets.json").read_text())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / "config/spacemit-k1-profiles/board-targets.json"
            path.parent.mkdir(parents=True)
            (root / "tools").mkdir()
            shutil.copyfile(ROOT / "tools/bpi_k1_board_targets.py", root / "tools/bpi_k1_board_targets.py")
            for key, value in (("storage", "sd"), ("board", "bpi-f3")):
                with self.subTest(key=key):
                    document = json.loads(json.dumps(original))
                    document["targets"]["bananapicm6-titan-emmc"][key] = value
                    path.write_text(json.dumps(document))
                    script = r'''
set -eo pipefail
SRC="$1"
BOARD="$2"
declare -A ARMBIAN_PARSED_CMDLINE_PARAMS=()
exit_with_error() { printf '%s\n' "$1" >&2; exit 91; }
source "$3"
bpi_k1_board_target_defaults
'''
                    result = self.run_shell(script, root=root, assignments=(
                        str(ROOT / "config/boards/include/bpi-k1-board-targets.inc"),))
                    self.assertEqual(result.returncode, 91, result.stderr)
                    self.assertIn("對照不符", result.stderr)


if __name__ == "__main__":
    unittest.main()
