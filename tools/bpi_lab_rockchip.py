#!/usr/bin/env python3
"""九款 Rockchip 板原配組件與腳本語意的離線核對。"""

import re

if __package__:
    from . import bpi_lab_extlinux as core
else:
    import bpi_lab_extlinux as core


POLICIES = {
    "bpi-forge1": "rockchip-rk3506-forge1-vendor",
    "bpi-aim7": "rockchip-rk3588-aim7-vendor",
    "bpi-cm5pro": "rockchip-rk3576-cm5pro-vendor",
    "bpi-m1super": "rockchip-rk3528-m1super-vendor",
    "bpi-m5pro": "rockchip-rk3576-m5pro-edge",
    "bpi-m7": "rockchip-rk3588-m7-current",
    "bpi-p2pro": "rockchip-rk3308-current",
    "bpi-r2pro": "rockchip-rk3568-r2pro-current",
    "bpi-w3": "rockchip-rk3588-w3-vendor",
}
SCRIPTS = {
    "rk3506": ("boot-rk3506-forge1.cmd", "35ab3deca2bfb760ae5db5d7dd238501e89ff227ad0086a3a60b7626f521094b"),
    "rockchip64": ("boot-rockchip64.cmd", "91b5e22d036bb2defcfeb1612d502510b6ebe9e19d2f1eeeebad4dc08a1fad37"),
    "rk35xx": ("boot-rk35xx.cmd", "877dbc09005b9e655392a05bb1d82aa9262b9e2915cb2590a4faa7e623dd1efb"),
    "rk3576": ("boot-rk3576.cmd", "9f4238e96863d03d413acf5a4146880ef3c11dad1d932eb05250d43d61a80e28"),
}
ENV_KEYS = set("rootdev rootfstype verbosity console bootlogo docker_optimizations earlycon logo "
               "fdtfile overlay_prefix overlays user_overlays usbstoragequirks extraargs extraboardargs".split())
validate_template = core.validate_template


def script_pair(e, p, filename, expected_sha):
    source = e.source("config/bootscripts/" + filename)
    core.require(core.digest(source)["sha256"] == expected_sha, "source_changed", "已實作的腳本來源已變動，需重審")
    cmd = e.get("/boot/boot.cmd", "boot_cmd", required=True, maximum=core.MAX_TEXT)
    scr = e.get("/boot/boot.scr", "boot_scr", required=True, maximum=core.MAX_TEXT)
    if cmd is not None:
        e.check("boot_source", lambda: core.require(cmd == source, "boot_script", "boot.cmd 與已實作來源不同"))
    if scr is not None:
        e.check("boot_scr", lambda: core.require(core.legacy(scr, arch=p["arch"], script=True) == cmd,
                                                "boot_script", "boot.scr 與 boot.cmd 不同"))
        e.manifest["entry"] = {"kind": "script", "path": "/boot/boot.scr", "sha256": core.digest(scr)["sha256"]}
    for alternative in ("/boot/extlinux/extlinux.conf", "/extlinux/extlinux.conf", "/boot/boot.scr.uimg", "/boot/uEnv.txt"):
        if e.get(alternative, "alternate_" + str(len(e.cache)), maximum=core.MAX_TEXT) is not None:
            e.block("alternate_entry", "存在其他引導入口，優先序尚未核定：" + alternative)


def _environment(env, p):
    core.require(not set(env) - ENV_KEYS, "unsupported_env", "環境含未實作欄位：" + ", ".join(sorted(set(env) - ENV_KEYS)))
    core.require(re.fullmatch("UUID=" + core.UUID, env.get("rootdev", "")), "root_uuid", "原環境須明示 rootdev=UUID")
    core.require(env.get("fdtfile", p["dtb"]) == p["dtb"], "dtb_name", "fdtfile 與板型不符")
    core.require(env.get("overlay_prefix") == p["overlay_prefix"], "overlay_prefix", "overlay_prefix 與板型來源不符")
    for key, default, values in (
        ("console", "both", {"serial", "display", "both"} | ({"ttyFIQ0,1500000n8"} if p["boot_profile"] == "rk3506" else set())),
        ("bootlogo", "false", {"true", "false"}), ("docker_optimizations", "on", {"on", "off"}),
        ("earlycon", "off", {"on", "off"}), ("rootfstype", "ext4", {"ext2", "ext3", "ext4", "btrfs", "f2fs", "xfs"}),
    ):
        core.require(env.get(key, default) in values, "env_value", "環境欄位值不支援：" + key)
    core.require(re.fullmatch("[0-7]", env.get("verbosity", "1")), "env_value", "verbosity 必須介於 0 至 7")
    for key in ("extraargs", "extraboardargs", "usbstoragequirks"):
        args = env.get(key, "").split()
        core.require(all(re.fullmatch(core.binary.TOKEN, a) and not a.startswith("-") for a in args),
                     "bootargs", "核心參數含未支援字元")
        core.require(not any(a.split("=", 1)[0] in {"root", "rootfstype", "rootwait", "console", "ubootpart", "initrd"} for a in args),
                     "bootargs", "額外參數不得覆寫根媒體或引導身分")
    core.require(not env.get("usbstoragequirks") or " " not in env["usbstoragequirks"], "env_value", "usbstoragequirks 不得含空白")
    return True


def _bootargs(env, profile):
    console = env.get("console", "both")
    if console == "ttyFIQ0,1500000n8":
        console = "both"
    tty = {"rk3576": "ttyS0,1500000", "rk3506": "ttyFIQ0,1500000n8"}.get(profile, "ttyS2,1500000")
    consoles = (["console=" + tty] if console != "display" else []) + (["console=tty1"] if console != "serial" else [])
    if env.get("earlycon", "off") == "on":
        consoles.insert(0, "earlycon")
    consoles = (["splash", "plymouth.ignore-serial-consoles"] if env.get("bootlogo", "false") == "true" else ["splash=verbose"]) + consoles
    args = (["earlyprintk"] if profile == "rk3506" else []) + ["root=" + env["rootdev"], "rootwait", "rootfstype=" + env.get("rootfstype", "ext4")]
    args += consoles + ["consoleblank=0", "loglevel=" + env.get("verbosity", "1"), "ubootpart=${partuuid}", "usb-storage.quirks=" + env.get("usbstoragequirks", "")]
    args += env.get("extraargs", "").split() + env.get("extraboardargs", "").split()
    if env.get("docker_optimizations", "on") == "on":
        args += ["cgroup_enable=cpuset", "cgroup_memory=1", "cgroup_enable=memory"]
    return args


def _script(e, p, release):
    mode = p["boot_profile"]
    if p["family"] == "rockchip-rk3588":
        cmd = e.get("/boot/boot.cmd", "boot_cmd", required=True, maximum=core.MAX_TEXT)
        if cmd is not None and core.digest(cmd)["sha256"] == SCRIPTS["rockchip64"][1]:
            mode = "rockchip64"
        # 家族只在 vendor／legacy 分支覆寫共同腳本；實際內容仍須匹配受控摘要。
        e.manifest["runtime_requirements"]["kernel_branch"] = "current-or-edge" if mode == "rockchip64" else "vendor-or-legacy"
    e.manifest["script_profile"] = mode
    core.require(mode in SCRIPTS, "boot_profile", "未知 Rockchip 腳本")
    script_pair(e, p, *SCRIPTS[mode])
    core.components(e, p, release, "/boot/" + ("zImage" if p["arch"] == "arm32" else "Image"), "/boot/uInitrd")
    blob = e.get("/boot/armbianEnv.txt", "env", required=True, maximum=core.MAX_TEXT)
    if blob is None:
        return
    env = e.check("env_parse", lambda: core.binary._env(blob))
    if env is None:
        return
    e.manifest["original_env"] = env
    if e.check("environment", lambda: _environment(env, p)):
        e.manifest["root_uuid"] = env["rootdev"][5:].lower()
        e.manifest["bootargs_template"] = _bootargs(env, mode)
    e.manifest["runtime_requirements"].update(boot_mount="/", prefix="boot/", devtype="mmc",
                                              partuuid="external-review", consoleargs="")
    if "fdtfile" not in env and mode != "rk3506":
        e.manifest["runtime_requirements"]["fdtfile"] = p["dtb"]
    if mode != "rk3506":
        e.manifest["runtime_requirements"]["kaslrseed"] = "original-script"
    else:
        e.manifest["runtime_requirements"].update(partition=1, ramdisk_addr_r="0x02800000")
    fdt = env.get("fdtfile", p["dtb"])
    e.get(core.path("/boot/dtb/" + fdt), "dtb", required=True, maximum=16 * 1024**2)
    prefix = env.get("overlay_prefix", "")
    core.require(re.fullmatch(core.NAME, prefix), "overlay_prefix", "overlay_prefix 格式不安全")
    directory = "/boot/dtb/" + ("" if mode == "rk3506" else "rockchip/") + "overlay/"
    roles = []
    for key in ("overlays", "user_overlays"):
        names = core.binary._overlay_names(env, key)
        for name in names:
            role = f"overlay_{len(roles):02d}"
            candidate = directory + prefix + "-" + name + ".dtbo" if key == "overlays" else "/boot/overlay-user/" + name + ".dtbo"
            data = e.get(candidate, role, maximum=16 * 1024**2)
            if data is None and key == "overlays" and mode in ("rk35xx", "rk3576"):
                data = e.get(directory + name + ".dtbo", role, maximum=16 * 1024**2)
            if data is None:
                e.block("overlay_missing", "原腳本指定 overlay 不存在；不靜默跳過：" + name)
            roles.append(role)
    for name, role in ((directory + prefix + "-fixup.scr", "kernel_fixup"), ("/boot/fixup.scr", "user_fixup")):
        data = e.get(name, role, maximum=core.MAX_TEXT)
        if data is not None:
            e.block("unsupported_fixup", "fixup 的條件及 DT 修改尚未實作，不執行或省略：" + name)
    e.manifest["overlay_order"] = roles
    core.overlays(e, p, roles)


def prepare(read_file, *, board="bpi-m5pro", kernel_release, output):
    handler = core.extlinux if board == "bpi-r2pro" else _script
    return core.prepare_adapter(read_file, board=board, kernel_release=kernel_release, output=output,
                                policies=POLICIES, handler=handler, family="rockchip")
