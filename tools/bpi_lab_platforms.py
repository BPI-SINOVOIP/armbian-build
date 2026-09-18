#!/usr/bin/env python3
"""跨平台候選資料的唯讀守門；不執行 shell，也不授予硬體或寫入資格。"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys

try:
    from . import bpi_lab_catalog as catalog
except ImportError:
    import bpi_lab_catalog as catalog


SCHEMA = "bpi-lab-platforms-v1"
ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "docs/evidence/bpi-lab-handoff-F-20260918/board-registry.json"
PLATFORMS = "config/bpi-lab/platforms.json"
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_SOURCE_BYTES = 256 * 1024
ARCH_SOURCE = {"arm32": "armhf", "arm64": "arm64", "riscv64": "riscv64"}
FAMILIES = {
    "sun6i": ("allwinner32", "arm32"),
    "sun7i": ("allwinner32", "arm32"),
    "sun8i": ("allwinner32", "arm32"),
    "sun50iw1": ("allwinner64", "arm64"),
    "sun50iw9-bpi": ("allwinner-h618", "arm64"),
    "meson-g12b": ("amlogic", "arm64"),
    "meson-sm1": ("amlogic", "arm64"),
    "rockchip": ("rockchip32", "arm32"),
    "rockchip64": ("rockchip64", "arm64"),
    "rk35xx": ("rockchip64", "arm64"),
    "rockchip-rk3588": ("rockchip64", "arm64"),
    "mt7623": ("mediatek32", "arm32"),
    "filogic": ("mediatek64", "arm64"),
    "spacemit": ("spacemit-k1", "riscv64"),
    "spacemit-k3-bpi": ("spacemit-k3", "riscv64"),
    "sunplus-sp7021-bpi": ("sunplus", "arm32"),
    "renesas-rzv2n-bpi": ("renesas", "arm64"),
    "realtek-rtd129x-bpi": ("realtek", "arm64"),
    "realtek-rtd139x-bpi": ("realtek", "arm64"),
    "vs680": ("synaptics", "arm64"),
}
K1_PATCH = "patch/u-boot/legacy/u-boot-spacemit-k1/004-Update-SpacemiT-K1-U-Boot-Environment.patch"
CM6_PATCH = "patch/u-boot/legacy/u-boot-spacemit-k1-cm6/001-add-extlinux-boot.patch"
K3_CONFIG = "packages/blobs/riscv64/spacemit-k3/bpi-sm10/uboot.config"
K3_ENV = "packages/blobs/riscv64/spacemit-k3/bpi-sm10/env_k3.txt"
BSP = "lib/functions/bsp/armbian-bsp-cli-deb.sh"
EXTLINUX = "lib/functions/rootfs/distro-agnostic.sh"
SUNPLUS = "config/sources/families/include/sunplus_sp7021_bpi_legacy_common.inc"
REALTEK = "config/sources/families/include/realtek_bpi_legacy_common.inc"

# 此表是人工核對過的候選入口，不是完整 shell 求值器或可執行後端。
PROFILE_RULES = {
    "sunxi32": ("bootz", "zImage", "config/bootscripts/boot-sunxi.cmd", "bootz ${kernel_addr_r}"),
    "sunxi64": ("booti", "Image", "config/bootscripts/boot-sun50i-next.cmd", "booti ${kernel_addr_r}"),
    "meson64": ("booti", "Image", "config/bootscripts/boot-meson64.cmd", "booti ${kernel_addr_r}"),
    "rk3506": ("bootz", "zImage", "config/bootscripts/boot-rk3506-forge1.cmd", "bootz ${kernel_addr_r}"),
    "rockchip64": ("booti", "Image", "config/bootscripts/boot-rockchip64.cmd", "booti ${kernel_addr_r}"),
    "r2pro-extlinux": (None, "Image", BSP, "elif [[ $SRC_EXTLINUX == yes ]]"),
    "rk35xx": ("booti", "Image", "config/bootscripts/boot-rk35xx.cmd", "booti ${kernel_addr_r}"),
    "rk3576": ("booti", "Image", "config/bootscripts/boot-rk3576.cmd", "booti ${kernel_addr_r}"),
    "mt7623": ("bootz", "zImage", "config/bootscripts/boot-mt7623.cmd", "bootz ${kernel_addr_r}"),
    "filogic-extlinux": ("bootflow", "Image", BSP, "elif [[ $SRC_EXTLINUX == yes ]]"),
    "k1-extlinux": ("sysboot", "Image", K1_PATCH, "sysboot ${devtype}"),
    "cm6-extlinux": ("sysboot", "Image", CM6_PATCH, "sysboot ${devtype}"),
    "k3-vendor": ("booti", "Image", K3_CONFIG, "CONFIG_CMD_BOOTI=y"),
    "sunplus-uenv": ("bootm", "uImage", SUNPLUS, "aboot=bootm"),
    "renesas": ("booti", "Image", "config/bootscripts/boot-renesas-rzv2n-bpi.cmd", "booti ${kernel_addr_r}"),
    "realtek-vendor": (None, "unknown", REALTEK, '${vendor_linux_dir}/uImage'),
    "vs680": ("booti", "Image", "config/bootscripts/boot-vs680.cmd", "booti 0x04a80000 0x0ca00000 0x15a00000"),
}
GROUP_PROFILE = {
    "allwinner32": "sunxi32", "allwinner64": "sunxi64", "allwinner-h618": "sunxi64",
    "amlogic": "meson64", "rockchip32": "rk3506", "mediatek32": "mt7623",
    "mediatek64": "filogic-extlinux", "spacemit-k3": "k3-vendor",
    "sunplus": "sunplus-uenv", "renesas": "renesas", "realtek": "realtek-vendor",
    "synaptics": "vs680",
}
SOFTWARE = {
    "source_mapping": "static_reviewed",
    "uboot_tool": "integration_pending",
    "linux_tool": "integration_pending",
    "deployment": "not_implemented",
    "recovery": "not_implemented",
    "end_to_end": "not_implemented",
}
DEPENDENCIES = ["tools/bpi_lab_uboot.py", "tools/bpi_lab_linux.py", "tools/bpi_lab_h618.py"]
SHARED_RULES = {
    DEPENDENCIES[0]: {
        "supported": ["arm32:zImage:bootz", "arm32:uImage:bootm", "arm64:Image:booti",
                      "arm64:uImage:bootm", "riscv64:Image:booti", "riscv64:uImage:bootm"],
        "unsupported": ["FIT", "compressed-uImage", "vendor-container", "extlinux", "deployment", "recovery"],
        "hardware_ids": [], "anchor": "def validate_config(config):",
    },
    DEPENDENCIES[1]: {
        "supported": ["arm32", "arm64", "riscv64", "direct-mmc-sd-root", "read-only-preflight"],
        "unsupported": ["overlay", "NFS", "Btrfs", "LVM", "dm-crypt", "NVMe-root", "USB-root", "smoke", "stress"],
        "hardware_ids": [], "anchor": "def validate(report, expected):",
    },
    DEPENDENCIES[2]: {
        "supported": ["preflight", "deploy", "boot", "smoke", "recovery", "resume"],
        "unsupported": ["other-hardware", "original-boot-chain"],
        "hardware_ids": ["bpi-m4zero-0845"], "anchor": 'IMPLEMENTED = station.STAGES',
    },
}
STORAGE_STATES = {"board_declared", "family_conditional", "not_declared", "candidate_excluded"}


class PlatformError(ValueError):
    """資料不完整或來源不一致時封閉拒絕。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _require(condition, code, message):
    if not condition:
        raise PlatformError(code, message)


def _shape(value, keys, location):
    _require(isinstance(value, dict) and set(value) == set(keys.split()),
             "schema", f"{location}：欄位缺漏或含未定義欄位")


def _text(value, location):
    _require(isinstance(value, str) and bool(value.strip()) and len(value) <= 4096,
             "schema", f"{location}：須為非空字串")


def _strings(value, location, allow_empty=False):
    _require(isinstance(value, list) and (allow_empty or bool(value)),
             "schema", f"{location}：須為清單")
    for item in value:
        _text(item, location)
    _require(len(value) == len(set(value)), "duplicate", f"{location}：不接受重複項目")


def _pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result, "duplicate_key", f"JSON 鍵重複：{key}")
        result[key] = value
    return result


def _json(text):
    try:
        return json.loads(text, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              PlatformError("schema", "JSON 不接受非有限數值")))
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, PlatformError):
            raise
        raise PlatformError("invalid_json", "JSON 格式無效或巢狀過深") from exc


def expected_profile(board):
    group = FAMILIES[board["family"]][0]
    if group == "rockchip64":
        if board["board"] == "bpi-r2pro":
            return "r2pro-extlinux"
        if board["board"] in ("bpi-cm5pro", "bpi-m5pro"):
            return "rk3576"
        return "rockchip64" if board["family"] == "rockchip64" else "rk35xx"
    if group == "spacemit-k1":
        return "cm6-extlinux" if board["board"] == "bpi-cm6" else "k1-extlinux"
    return GROUP_PROFILE[group]


def _evidence(refs, texts, location):
    _require(isinstance(refs, list) and bool(refs), "evidence", f"{location}：缺少來源引用")
    paths = set()
    for ref in refs:
        _shape(ref, "path line token", location)
        path, number, token = ref["path"], ref["line"], ref["token"]
        _text(path, location)
        _text(token, location)
        _require(path in texts, "evidence", f"{location}：引用未核對的來源 {path}")
        lines = texts[path].splitlines()
        if path in DEPENDENCIES and number is None:
            _require("\n" not in token and any(token in line for line in lines),
                     "evidence", f"{path}：共用工具來源錨點不符")
            paths.add(path)
            continue
        _require(type(number) is int and 1 <= number <= len(lines),
                 "evidence", f"{location}：來源行號無效")
        _require("\n" not in token and token in lines[number - 1],
                 "evidence", f"{path}:{number}：來源片段不符")
        paths.add(path)
    return paths


def _qualification(row, location):
    for field in ("hardware_qualified", "execution_ready", "backend_complete"):
        _require(row[field] is False, "qualification", f"{location}：{field} 必須為 false")


def _validate(root_fd, manifest):
    _shape(manifest, "schema registry board_count hardware_qualified execution_ready backend_complete "
           "scope sources boot_profiles boards dependencies shared_tools", "平台資料")
    _require(manifest["schema"] == SCHEMA, "schema", "平台資料版本不符")
    _require(type(manifest["board_count"]) is int and manifest["board_count"] == 45,
             "coverage", "本契約必須涵蓋既有 45 板")
    _qualification(manifest, "平台資料")
    _require(manifest["scope"] == "reviewed_local_build_inputs", "schema", "不得擴大來源核對範圍")
    _require(manifest["dependencies"] == DEPENDENCIES, "software", "共用工具僅列為待整合依賴")
    _shape(manifest["registry"], "path sha256", "registry")
    _require(manifest["registry"]["path"] == REGISTRY, "registry", "必須使用指定 registry")
    raw, digest, identity = catalog._read_small(root_fd, REGISTRY, MAX_JSON_BYTES)
    _require(digest == manifest["registry"]["sha256"], "source_changed", f"{REGISTRY}：SHA-256 不符")
    registry = _json(raw)
    _require(isinstance(registry, dict) and registry.get("schema") == "bpi-lab-board-registry-v1"
             and registry.get("board_count") == 45 and isinstance(registry.get("boards"), list)
             and len(registry["boards"]) == 45, "registry", "registry 格式或板數不符")
    registry_boards = {}
    expected_hashes = {}
    for board in registry["boards"]:
        _require(isinstance(board, dict) and isinstance(board.get("board"), str)
                 and isinstance(board.get("family"), str) and board["family"] in FAMILIES
                 and isinstance(board.get("config_sources"), list)
                 and 1 <= len(board["config_sources"]) <= 64
                 and all(isinstance(board.get(key), str) for key in
                         ("artifact_board", "architecture", "config_path", "config_sha256")),
                 "registry", "registry 板型資料無效")
        name = board["board"]
        _require(name not in registry_boards, "duplicate", f"registry 板型重複：{name}")
        registry_boards[name] = board
        for source in board["config_sources"]:
            _shape(source, "path sha256", "registry 來源")
            path = source["path"]
            _text(path, "registry 來源")
            catalog._parts(path)
            _require(path.startswith(("config/boards/", "config/sources/families/"))
                     and Path(path).suffix in (".conf", ".inc", ".csc", ".wip", ".eos", ".tvb"),
                     "source_scope", f"{path}：registry 引用超出設定來源範圍")
            _require(isinstance(source["sha256"], str)
                     and re.fullmatch(r"[0-9a-f]{64}", source["sha256"]),
                     "registry", f"{path}：registry 來源摘要無效")
            old = expected_hashes.setdefault(path, source["sha256"])
            _require(old == source["sha256"], "registry", f"{path}：registry 來源摘要互相矛盾")
        _require(expected_hashes.get(board["config_path"]) == board["config_sha256"],
                 "registry", f"{name}：主設定未綁定來源摘要")

    supplemental = {rule[2] for rule in PROFILE_RULES.values()}
    supplemental.update((BSP, EXTLINUX, K3_ENV))
    supplemental.update(DEPENDENCIES)
    supplemental.update(f"config/sources/{arch}.conf" for arch in ARCH_SOURCE.values())
    required_sources = set(expected_hashes) | supplemental
    _require(isinstance(manifest["sources"], list), "schema", "來源須為清單")
    texts, identities = {}, {REGISTRY: identity}
    for source in manifest["sources"]:
        _shape(source, "path sha256", "來源")
        path, expected = source["path"], source["sha256"]
        _text(path, "來源")
        _require(path in required_sources, "source_scope", f"{path}：不在受控的小型來源集合")
        _require(path not in texts, "duplicate", f"來源重複：{path}")
        _require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected),
                 "schema", f"{path}：SHA-256 格式無效")
        if path in expected_hashes:
            _require(expected == expected_hashes[path], "registry", f"{path}：與 registry 摘要不符")
        text, actual, info = catalog._read_small(root_fd, path, MAX_SOURCE_BYTES)
        _require(actual == expected, "source_changed", f"{path}：SHA-256 不符，須重新審核")
        texts[path], identities[path] = text, info
    _require(set(texts) == required_sources, "source_missing", "來源集合不完整")

    shared = manifest["shared_tools"]
    _require(isinstance(shared, dict) and set(shared) == set(SHARED_RULES),
             "software", "共用工具狀態表不完整")
    for path, rule in SHARED_RULES.items():
        entry = shared[path]
        _shape(entry, "availability validation board_integration supported unsupported hardware_ids "
               "whole_adapter_ready hardware_qualified evidence limitations", path)
        _require(entry["availability"] == "implemented" and entry["validation"] == "source_reviewed"
                 and entry["board_integration"] == "pending" and entry["whole_adapter_ready"] is False
                 and entry["hardware_qualified"] is False, "software", f"{path}：不得擴大工具資格")
        for key in ("supported", "unsupported", "hardware_ids"):
            _require(entry[key] == rule[key], "software", f"{path}：適用範圍不符")
        _require(_evidence(entry["evidence"], texts, path) == {path},
                 "evidence", f"{path}：工具來源引用錯配")
        _require(any(rule["anchor"] in ref["token"] for ref in entry["evidence"]),
                 "evidence", f"{path}：缺少實作入口證據")
        _strings(entry["limitations"], path)

    profiles = manifest["boot_profiles"]
    _require(isinstance(profiles, dict) and set(profiles) == set(PROFILE_RULES),
             "profiles", "候選引導入口集合不符")
    for name, profile in profiles.items():
        _shape(profile, "command kernel_format kernel_file initrd_file dtb_required status evidence limitations", name)
        command, form, path, token = PROFILE_RULES[name]
        _require(profile["command"] == command and profile["kernel_format"] == form,
                 "profiles", f"{name}：命令或格式不符合已核對來源")
        _require(profile["dtb_required"] is True, "profiles", f"{name}：不得省略原配裝置樹核對")
        expected_status = "vendor_review_required" if name in ("k3-vendor", "realtek-vendor") else "source_candidate"
        _require(profile["status"] == expected_status, "qualification", f"{name}：不能升格為可執行")
        _require(profile["kernel_file"] == ("uImage" if form == "unknown" else form)
                 and profile["initrd_file"] == ("initramfs-generic.img" if name == "k3-vendor" else "uInitrd"),
                 "profiles", f"{name}：候選檔案名稱不符")
        _strings(profile["limitations"], name)
        _evidence(profile["evidence"], texts, name)
        _require(any(ref["path"] == path and token in ref["token"] for ref in profile["evidence"]),
                 "evidence", f"{name}：缺少實際入口的來源引用")

    rows = manifest["boards"]
    _require(isinstance(rows, list) and len(rows) == 45, "coverage", "板型資料必須恰有 45 筆")
    seen = set()
    for row in rows:
        _shape(row, "board artifact_board architecture family group soc soc_evidence config_path sources "
               "boot_profile boot_chain storage special_boot software hardware_qualified execution_ready "
               "backend_complete blockers", "板型")
        name = row["board"]
        _text(name, "板型")
        _require(name not in seen, "duplicate", f"板型重複：{name}")
        _require(name in registry_boards, "coverage", f"registry 未收錄板型：{name}")
        seen.add(name)
        original = registry_boards[name]
        for field in ("artifact_board", "architecture", "family", "config_path"):
            _require(row[field] == original[field], "board_mismatch", f"{name}：{field} 與 registry 不符")
        group, arch = FAMILIES[row["family"]]
        _require((row["group"], row["architecture"]) == (group, arch), "group_mismatch", f"{name}：家族或架構誤配")
        _require(row["boot_profile"] == expected_profile(row), "profile_mismatch", f"{name}：引導入口誤配")
        _qualification(row, name)
        _require(row["software"] == SOFTWARE, "software", f"{name}：共用工具尚未整合，不得升格後端")
        _strings(row["blockers"], name)
        _strings(row["sources"], name)
        _text(row["soc"], name)
        evidence_paths = _evidence(row["soc_evidence"], texts, name)
        _require(evidence_paths == {row["config_path"]}
                 and any(ref["token"] == row["soc"] for ref in row["soc_evidence"]),
                 "evidence", f"{name}：SoC 必須引用本板明示的來源片段")
        _shape(row["boot_chain"], "artifacts evidence note", name)
        _strings(row["boot_chain"]["artifacts"], name)
        _text(row["boot_chain"]["note"], name)
        evidence_paths |= _evidence(row["boot_chain"]["evidence"], texts, name)
        chain_text = "\n".join(texts[ref["path"]] for ref in row["boot_chain"]["evidence"])
        _require(all(artifact in chain_text for artifact in row["boot_chain"]["artifacts"]),
                 "evidence", f"{name}：引導組件缺少來源宣告")
        _shape(row["storage"], "spi emmc", name)
        for media, claim in row["storage"].items():
            _shape(claim, "status evidence note", name)
            _require(isinstance(claim["status"], str) and claim["status"] in STORAGE_STATES,
                     "storage", f"{name}：{media} 狀態無效")
            _text(claim["note"], name)
            if claim["status"] == "not_declared":
                _require(claim["evidence"] == [], "storage", f"{name}：未知不得偽裝有證據")
            else:
                evidence_paths |= _evidence(claim["evidence"], texts, name)
        expected_emmc = {"bpi-f2p": "candidate_excluded", "bpi-f2s": "board_declared",
                         "bpi-r4pro": "candidate_excluded", "bpi-r3mini": "board_declared"}
        if name in expected_emmc:
            _require(row["storage"]["emmc"]["status"] == expected_emmc[name],
                     "storage", f"{name}：不可抹除明示的媒體限制或候選差異")
        _shape(row["special_boot"], "status secure_boot_enabled evidence note", name)
        _require(row["special_boot"]["status"] == "review_required"
                 and row["special_boot"]["secure_boot_enabled"] is None,
                 "qualification", f"{name}：安全引導及特殊鏈必須另行核定")
        _text(row["special_boot"]["note"], name)
        evidence_paths |= _evidence(row["special_boot"]["evidence"], texts, name)
        profile = profiles[row["boot_profile"]]
        evidence_paths |= {ref["path"] for ref in profile["evidence"]}
        required = {source["path"] for source in original["config_sources"]} | evidence_paths
        required.add(f"config/sources/{ARCH_SOURCE[arch]}.conf")
        _require(set(row["sources"]) == required and required <= set(texts),
                 "source_missing", f"{name}：板型引用鏈不完整或含無關來源")
    _require(seen == set(registry_boards), "coverage", "板型集合與 registry 不一致")
    for path, identity in identities.items():
        catalog._unchanged(root_fd, path, identity)
    return manifest


def validate(root=ROOT, platforms=PLATFORMS):
    """只驗小型本機來源一致性；回傳資料不是硬體授權或後端完成證明。"""
    try:
        with catalog._root(root) as root_fd:
            raw, _, identity = catalog._read_small(root_fd, platforms, MAX_JSON_BYTES)
            manifest = _validate(root_fd, _json(raw))
            catalog._unchanged(root_fd, platforms, identity)
            return manifest
    except catalog.CatalogError as exc:
        raise PlatformError(exc.code, str(exc)) from exc
    except OSError as exc:
        raise PlatformError("io_error", "來源缺失或無法安全讀取一般檔案") from exc


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise PlatformError("arguments", "命令或參數無效，請使用 --help")

    def format_help(self):
        return super().format_help().replace("usage:", "用法：", 1)


def main(argv=None):
    parser = _Parser(description="核對跨平台來源候選；不執行引導、不操作硬體", add_help=False)
    parser._positionals.title = "位置參數"
    parser._optionals.title = "選項"
    parser.add_argument("-h", "--help", action="help", help="顯示說明")
    parser.add_argument("command", choices=("validate", "list"), help="驗證或列出平台")
    parser.add_argument("--root", default=str(ROOT), help="建置倉根目錄")
    parser.add_argument("--platforms", default=PLATFORMS, help="根目錄內的平台資料相對路徑")
    parser.add_argument("--board", help="列出指定板型；仍核對全部來源")
    parser.add_argument("--group", help="列出指定群組；仍核對全部來源")
    parser.add_argument("--json", action="store_true", help="輸出機器可讀 JSON")
    try:
        args = parser.parse_args(argv)
        _require(args.command == "list" or not (args.board or args.group), "arguments", "驗證命令不得篩選板型")
        manifest = validate(args.root, args.platforms)
        rows = manifest["boards"]
        if args.board:
            rows = [row for row in rows if row["board"] == args.board]
        if args.group:
            rows = [row for row in rows if row["group"] == args.group]
        _require(bool(rows), "selection", "沒有符合條件的板型")
        summary = {
            "status": "valid", "scope": "local_source_consistency", "board_count": 45,
            "source_count": len(manifest["sources"]), "local_sources_verified": True,
            "hardware_qualified": False, "execution_ready": False, "backend_complete": False,
            "group_counts": dict(sorted(Counter(row["group"] for row in manifest["boards"]).items())),
            "shared_tools": manifest["shared_tools"],
        }
        if args.command == "list" and args.json:
            summary.update(boards=rows, boot_profiles=manifest["boot_profiles"], sources=manifest["sources"])
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        elif args.command == "validate":
            print(f"來源核對通過：45 板／{summary['source_count']} 份來源；硬體資格、可執行資格與完整後端均為 false。")
        else:
            print("共用 U-Boot／Linux 工具已實作；H618 階段接線僅限 0845，整板整合仍未完成。")
            print("板型\t群組\tSoC\t候選入口\t軟體狀態\t硬體資格")
            for row in rows:
                profile = manifest["boot_profiles"][row["boot_profile"]]
                print("\t".join((row["board"], row["group"], row["soc"], profile["command"] or "待核定",
                                  "來源已核對；工具待整合；部署與救援未實作", "false")))
        return 0
    except PlatformError as exc:
        print(json.dumps({"status": "invalid", "code": exc.code, "message": str(exc),
                          "hardware_qualified": False, "execution_ready": False}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
