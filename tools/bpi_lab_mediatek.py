#!/usr/bin/env python3
"""MT7623 與六款 Filogic 原配組件解析；不接觸前置載入器或實體媒體。"""

import re

if __package__:
    from . import bpi_lab_extlinux as core
    from .bpi_lab_rockchip import script_pair
else:
    import bpi_lab_extlinux as core
    from bpi_lab_rockchip import script_pair


POLICIES = {
    "bpi-r2": "mt7623-r2-current", "bpi-r64": "filogic-mt7622-r64-current",
    "bpi-r3": "filogic-mt7986-r3-current", "bpi-r3mini": "filogic-mt7986-r3mini-current",
    "bpi-r4": "filogic-mt7988-r4-current", "bpi-r4lite": "filogic-mt7987-r4lite-current",
    "bpi-r4pro": "filogic-mt7988-r4pro-current",
}
validate_template = core.validate_template


def _r2(e, p, release):
    script_pair(e, p, "boot-mt7623.cmd", "822a4afad456b46be41657860fb474a565d06f476ab60fe99fbd802216149c57")
    core.components(e, p, release, "/boot/zImage", "/boot/uInitrd")
    blob = e.get("/boot/armbianEnv.txt", "env", required=True, maximum=core.MAX_TEXT)
    if blob is None:
        return
    env = e.check("env_parse", lambda: core.binary._env(blob))
    if env is None:
        return
    e.manifest["original_env"] = env
    core.require(not set(env) - {"rootdev", "rootfs", "verbosity", "fdtfile", "overlay_prefix", "rootfstype"},
                 "unsupported_env", "MT7623 環境含原腳本不支援的欄位，不得靜默忽略")
    # 建置器可能產生 rootfstype，但此腳本實際只讀 rootfs；只容許等值重複。
    rootfs = env.get("rootfs", "ext4")
    core.require("rootfstype" not in env or env["rootfstype"] == rootfs, "unused_env", "MT7623 不讀取 rootfstype，且其值與 rootfs 不同")
    core.require(env.get("overlay_prefix", "mt7623") == "mt7623", "unused_env", "MT7623 overlay_prefix 不符；腳本不套用 overlay")
    core.require(rootfs in {"ext2", "ext3", "ext4", "btrfs", "f2fs", "xfs"}
                 and re.fullmatch("[0-7]", env.get("verbosity", "1")), "env_value", "MT7623 根檔案系統或記錄等級無效")
    args, root = core.bootargs("console=ttyS2,115200n1 root=" + env.get("rootdev", "")
                              + " rw rootfstype=" + rootfs + " rootwait audit=0 loglevel=" + env.get("verbosity", "1"))
    e.manifest.update(bootargs_template=args, root_uuid=root)
    name = env.get("fdtfile", "mt7623n-bananapi-bpi-r2.dtb")
    selected = core.path("/boot/dtb/" + name)
    core.require(name in {p["dtb"], "mediatek/" + p["dtb"], "mediatek/" + p["dtb"].removesuffix(".dtb")},
                 "dtb_name", "MT7623 DTB 路徑不是已知原腳本或板級環境的 R2 名稱")
    # env import 會覆寫腳本預設；不得補副檔名、移除子目錄或改載另一份 DTB。
    e.manifest["checks"]["dtb_selection"] = {
        "source": "armbianEnv.txt" if "fdtfile" in env else "boot.cmd",
        "fdtfile": name, "path": selected, "rewritten": False,
    }
    e.get(selected, "dtb", required=True, maximum=16 * 1024**2)
    e.manifest["runtime_requirements"].update(boot_mount="/", prefix="boot/", devtype="mmc", mmcpart="external-review",
                                              filesystem="ext4", fdtfile=name)
    e.manifest["limitations"].append("MT7623 腳本固定 ext4load；eMMC 早期 U-Boot 卡住的既有來源限制未解決，SD 與 eMMC 前置鏈不可互換。")
    e.manifest["overlay_order"] = []
    core.overlays(e, p, [])


def prepare(read_file, *, board="bpi-r3", kernel_release, output):
    return core.prepare_adapter(read_file, board=board, kernel_release=kernel_release, output=output,
                                policies=POLICIES, handler=_r2 if board == "bpi-r2" else core.extlinux,
                                family="mediatek")
