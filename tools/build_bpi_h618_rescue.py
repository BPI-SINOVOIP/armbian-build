#!/usr/bin/env python3
"""在原 Linux 建置獨立 H618 救援 initramfs；不安裝、不部署。"""

import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import stat
import struct
import subprocess
import sys

sys.dont_write_bytecode = True

if __package__:
    from .bpi_h618_rescue.bpi_rescue_cli import ChineseArgumentParser
    from . import bpi_h618_artifacts as safe
else:
    from bpi_h618_rescue.bpi_rescue_cli import ChineseArgumentParser
    import bpi_h618_artifacts as safe


ASSETS = Path(__file__).resolve().with_name("bpi_h618_rescue")
SCHEMA = "bpi-h618-rescue-v1"
LAB_SCHEMA = "bpi-lab-rescue-v1"
SEARCH_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
MODULES = ("brcmfmac", "brcmfmac_wcc", "brcmutil", "cfg80211", "rfkill", "mmc_block", "sunxi_mmc")
APPLET_NAMES = ("sh", "mount", "mkdir", "chmod", "cat", "sleep", "uname", "setsid",
                "cttyhack", "udhcpc", "sha256sum", "dmesg", "ls", "sync", "reboot",
                "poweroff", "readlink", "stty", "rm", "ps", "kill", "df", "free", "false", "timeout")
RUNTIME_BINARIES = ("curl", "xz", "python3", "wpa_supplicant", "ip", "rfkill",
                    "ssh-keygen", "sshd", "modprobe")
BUILD_BINARIES = ("mkinitramfs", "unshare", "mount", "modinfo", "ldd", "depmod",
                  "cpio", "gzip", "ldconfig", "chroot", "lsinitramfs")
ENV = {"PATH": SEARCH_PATH, "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1"}
MULTIARCH = {"arm32": "arm-linux-gnueabihf", "arm64": "aarch64-linux-gnu", "riscv64": "riscv64-linux-gnu"}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def validate_profile(value):
    require(type(value) is dict and set(value) == {
        "schema", "board", "architecture", "multiarch", "dt_compatible", "modules", "firmware", "wireless"},
        "救援板級配置欄位不完整或含未知欄位")
    require(value["schema"] == "bpi-lab-rescue-build-profile-v1"
            and type(value["board"]) is str and re.fullmatch(r"bpi-[a-z0-9-]{1,32}", value["board"]),
            "救援板型或配置版本不符")
    require(type(value["architecture"]) is str and value["architecture"] in MULTIARCH
            and value["multiarch"] == MULTIARCH[value["architecture"]], "救援架構與函式庫 ABI 不符")
    for key, pattern, minimum in (("modules", r"[A-Za-z0-9_+-]{1,128}", 1),
                                 ("dt_compatible", r"[A-Za-z0-9,._+-]{1,128}", 1)):
        items = value[key]
        require(type(items) is list and minimum <= len(items) <= 128
                and all(type(item) is str and re.fullmatch(pattern, item) for item in items)
                and len(set(items)) == len(items), "救援模組或 DT 身分清單不明確")
    firmware = value["firmware"]
    require(type(firmware) is list and len(firmware) <= 256 and type(value["wireless"]) is bool,
            "救援韌體清單或無線模式型別不符")
    seen = set()
    for item in firmware:
        require(type(item) is dict and set(item) == {"path", "sha256"}
                and type(item["path"]) is str and type(item["sha256"]) is str,
                "韌體欄位無效")
        path = item["path"]
        require(re.fullmatch(r"[A-Za-z0-9_.+/-]{1,240}", path)
                and all(part not in ("", ".", "..") for part in path.split("/")) and path not in seen
                and re.fullmatch(r"[0-9a-f]{64}", item["sha256"]), "韌體路徑、摘要或唯一性不符")
        seen.add(path)
    require(not value["wireless"] or {"regulatory.db", "regulatory.db.p7s"} <= seen,
            "無線救援必須固定法規資料及其簽章摘要")
    return value


def sha256(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def regular(path, label, executable=False):
    require(path.is_file(), f"{label}必須是存在的一般檔案：{path}")
    require(stat.S_ISREG(path.stat().st_mode), f"{label}型別不符：{path}")
    require(not executable or os.access(path, os.X_OK), f"{label}不可執行：{path}")


def verified_firmware(path, expected, target=None):
    """以同一一般檔案描述元核對內容；複製只使用已核對的有界位元組。"""
    with safe.open_root(path.parent) as root:
        metadata, blob = safe.fingerprint(root, path.name, limit=64 * 1024**2, keep=target is not None)
    require(metadata["sha256"] == expected, "板級韌體摘要不符或前檢後改變")
    if target is not None:
        with target.open("xb") as stream:
            stream.write(blob)
    return metadata


def elf_native(path, architecture="arm64", static=False):
    regular(path, "執行檔", executable=True)
    layouts = {"arm64": (2, 183, 64, 56, 32, 54, "<Q"),
               "arm32": (1, 40, 52, 32, 28, 42, "<I"),
               "riscv64": (2, 243, 64, 56, 32, 54, "<Q")}
    require(architecture in layouts, "不支援此救援執行檔架構")
    elf_class, machine, header_size, entry_size, offset_pos, count_pos, offset_fmt = layouts[architecture]
    with path.open("rb") as stream:
        header = stream.read(header_size)
        require(len(header) == header_size and header[:6] == b"\x7fELF" + bytes((elf_class, 1))
                and struct.unpack_from("<H", header, 18)[0] == machine,
                f"必須是 {architecture} 小端序 ELF：{path}")
        require(struct.unpack_from("<H", header, 16)[0] in (2, 3), f"ELF 型別不符：{path}")
        offset = struct.unpack_from(offset_fmt, header, offset_pos)[0]
        size, count = struct.unpack_from("<HH", header, count_pos)
        require(size == entry_size and 0 < count < 1024 and offset >= header_size
                and offset + size * count <= path.stat().st_size, f"ELF 標頭截斷：{path}")
        stream.seek(offset)
        kinds = [struct.unpack_from("<I", stream.read(size))[0] for _ in range(count)]
        require(not static or (2 not in kinds and 3 not in kinds),
                "--busybox 必須靜態連結，不得含 PT_DYNAMIC 或 PT_INTERP")


def elf_aarch64(path, static=False):
    return elf_native(path, static=static)


def checked(args, env=None, timeout=30, **kwargs):
    try:
        result = subprocess.run([str(arg) for arg in args], env=ENV if env is None else env, check=False,
                                capture_output=True, text=True, timeout=timeout, **kwargs)
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"命令逾時（{timeout} 秒）：{args[0]}") from error
    require(result.returncode == 0, f"命令失敗：{args[0]}\n{result.stderr.strip()}")
    return result.stdout


def run_build(command, output, log, timeout=600):
    with subprocess.Popen(command, env=dict(ENV, TMPDIR=str(output / "tmp"), HOME=str(output)),
                          cwd=output, stdout=log, stderr=subprocess.STDOUT, start_new_session=True) as process:
        try:
            return process.wait(timeout=timeout)
        except BaseException:
            # unshare、mkinitramfs 與 hook 共用獨立程序群組，逾時不能只停止外層。
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise


def authorized_key(path):
    regular(path, "公鑰")
    require(path.stat().st_size <= 16384, "公鑰過大")
    lines = path.read_text(encoding="utf-8").splitlines()
    require(len(lines) == 1, "只接受一行、無選項的專用公鑰")
    parts = lines[0].split()
    require(len(parts) >= 2 and parts[0] in ("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256"),
            "只接受 Ed25519、RSA 或 NIST P-256 公鑰，不接受私鑰或 authorized_keys 選項")
    try:
        blob = base64.b64decode(parts[1], validate=True)
        size = struct.unpack_from(">I", blob)[0]
        require(blob[4:4 + size] == parts[0].encode(), "公鑰型別與內容不符")
    except (ValueError, struct.error) as error:
        raise ValueError("公鑰內容無效") from error
    checked(["ssh-keygen", "-l", "-f", path])
    # 不帶入原公鑰註解，避免包入帳號、主機名稱或其他非必要資訊。
    return " ".join(parts[:2]) + "\n"


def output_path(value):
    path = Path(value)
    require(path.is_absolute() and ".." not in path.parts, "--output 必須是無 .. 的絕對新目錄")
    require(not os.path.lexists(path), "工作輸出必須是新目錄，拒絕覆寫")
    require(path.parent.is_dir(), "工作輸出的父目錄必須已存在")
    resolved = path.parent.resolve() / path.name
    require(path == resolved, "工作輸出不得經過符號連結")
    require(not any(resolved.is_relative_to(root) for root in
                    map(Path, ("/boot", "/dev", "/proc", "/sys", "/etc", "/usr", "/lib", "/sbin", "/bin"))),
            "工作輸出位於禁止的系統路徑")
    return resolved


def preflight(args, *, profile=None):
    if profile is not None:
        validate_profile(profile)
    output = output_path(args.output)
    require(os.geteuid() == 0, "須由 root 在板上原 Linux 建置")
    architecture = profile["architecture"] if profile is not None else "arm64"
    machines = {"arm64": ("aarch64",), "arm32": ("armv7l", "armv8l"), "riscv64": ("riscv64",)}
    require(platform.machine() in machines[architecture], "需要相同架構原生 Linux；預設 AArch64，不支援交叉建置")
    if profile is not None:
        compatible = Path("/sys/firmware/devicetree/base/compatible").read_bytes().rstrip(b"\0").decode("ascii").split("\0")
        require(compatible == profile["dt_compatible"], "救援建置主機的 DT 身分與板級配置不同")
    kernel = args.kernel or platform.release()
    require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", kernel), "核心版本格式不符")
    require(kernel == platform.release(), "核心版本必須等於目前 uname -r")
    binaries = {}
    for name in RUNTIME_BINARIES + BUILD_BINARIES:
        found = shutil.which(name, path=SEARCH_PATH)
        require(found, f"缺少必要執行檔：{name}；請先由主代理備妥，不會自行安裝")
        binaries[name] = str(Path(found).absolute())
        regular(Path(found), name, executable=True)
        if name in RUNTIME_BINARIES:
            elf_native(Path(found), architecture)
            require("not found" not in checked(["ldd", found]), f"{name} 缺少動態庫")
    busybox = Path(args.busybox).resolve(strict=True)
    elf_native(busybox, architecture, static=True)
    require(re.fullmatch(r"[0-9a-f]{64}", args.busybox_sha256), "BusyBox SHA-256 必須為 64 位小寫十六進位")
    require(sha256(busybox) == args.busybox_sha256, "BusyBox SHA-256 不符")
    applets = set(checked([busybox, "--list"]).splitlines())
    require(set(APPLET_NAMES) <= applets, "BusyBox 缺少必要 applet：" + ", ".join(sorted(set(APPLET_NAMES) - applets)))
    key = authorized_key(Path(args.authorized_key)) if args.authorized_key else ""
    module_root = Path("/lib/modules") / kernel
    require(module_root.is_dir() and module_root.resolve().name == kernel,
            "核心與模組目錄不符，不接受指向另一版本的模組目錄")
    config = Path("/boot") / f"config-{kernel}"
    regular(config, "對應核心設定")
    values = dict(line.split("=", 1) for line in config.read_text().splitlines() if line.startswith("CONFIG_") and "=" in line)
    for name in ("BLK_DEV_INITRD", "RD_GZIP", "DEVTMPFS", "PROC_FS", "SYSFS", "TMPFS",
                 "UNIX", "INET", "PACKET", "MODULES"):
        require(values.get("CONFIG_" + name) == "y", f"核心缺少必要 CONFIG_{name}=y")
    for name in ("modules.dep", "modules.alias", "modules.builtin", "modules.builtin.modinfo"):
        regular(module_root / name, "現成模組索引")
    for name in MODULES if profile is None else profile["modules"]:
        checked(["modinfo", "-k", kernel, name])
        filename = checked(["modinfo", "-k", kernel, "-F", "filename", name]).strip()
        require(filename == "(builtin)" or Path(filename).resolve().is_relative_to(module_root.resolve()),
                f"{name} 來源不在指定核心模組目錄")
        if filename != "(builtin)":
            vermagic = checked(["modinfo", "-k", kernel, "-F", "vermagic", name]).split()
            require(vermagic and vermagic[0] == kernel, f"{name} 的 vermagic 與核心不符")
    if profile is None:
        firmware = sorted(Path("/lib/firmware/brcm").glob("brcmfmac43455-sdio.*"))
        for suffix in (".bin", ".txt", ".clm_blob"):
            require(any(path.name.endswith(suffix) for path in firmware), f"BCM4345/6 韌體缺少 {suffix}")
    else:
        firmware = [Path("/lib/firmware") / item["path"] for item in profile["firmware"]]
        for item, path in zip(profile["firmware"], firmware):
            resolved = path.resolve(strict=True)
            require(resolved.is_relative_to(Path("/lib/firmware").resolve()), "韌體來源越界")
            verified_firmware(resolved, item["sha256"])
    for path in firmware:
        regular(path, "韌體")
    for name in ("regulatory.db", "regulatory.db.p7s") if profile is None else ():
        path = Path("/lib/firmware") / name
        regular(path, "無線法規資料")
        firmware.append(path)
    regular(Path("/etc/ssl/certs/ca-certificates.crt"), "TLS CA 憑證")
    py = json.loads(checked([binaries["python3"], "-I", "-S", "-B", "-c",
                            "import json,sysconfig; print(json.dumps(sysconfig.get_path('stdlib')))"]))
    require(re.fullmatch(r"/usr/lib/python3\.\d+", py) and Path(py).is_dir(),
            "Python 標準函式庫必須來自 /usr/lib/python3.x")
    share = Path("/usr/share/initramfs-tools")
    for name in ("hook-functions", "scripts/functions"):
        regular(share / name, "系統 initramfs helper")
    return output, kernel, binaries, busybox, key, firmware, Path(py)


def write(path, text, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def prepare_runtime(seed, kernel, *, profile=None):
    """舊入口保留原識別；明示板級配置使用獨立識別，沿用同份唯讀執行程式。"""
    if profile is not None:
        validate_profile(profile)
    schema = SCHEMA if profile is None else LAB_SCHEMA
    write(seed / "etc/bpi-rescue.json", json.dumps({"schema": schema, "kernel": kernel}, sort_keys=True) + "\n")
    entry = seed / "usr/sbin/bpi-rescue"
    entry.parent.mkdir(parents=True, exist_ok=True)
    if profile is None:
        shutil.copyfile(ASSETS / "runtime.py", entry)
        entry.chmod(0o755)
    else:
        module = entry.with_name("bpi_rescue_runtime.py")
        shutil.copyfile(ASSETS / "runtime.py", module)
        module.chmod(0o644)
        write(entry, "#!/usr/bin/python3 -B\n"
              '"""使用獨立板級救援識別，不繼承舊 H618 配對。"""\n'
              "import bpi_rescue_runtime as runtime\n"
              f"runtime.SCHEMA = {LAB_SCHEMA!r}\n"
              "if __name__ == '__main__':\n"
              "    raise SystemExit(runtime.main())\n", 0o755)


def prepare(output, kernel, binaries, busybox, key, firmware, stdlib, *, profile=None):
    conf = output / "conf"
    share = output / "share"
    seed = output / "seed"
    for name in ("hooks", "scripts", "conf.d"):
        (conf / name).mkdir(parents=True)
    for name in ("hooks", "scripts", "conf.d", "conf-hooks.d", "modules.d"):
        (share / name).mkdir(parents=True, exist_ok=True)
    for name in ("hook-functions", "scripts/functions"):
        shutil.copyfile(Path("/usr/share/initramfs-tools") / name, share / name)
    shutil.copyfile(ASSETS / "init", share / "init")
    (share / "init").chmod(0o755)
    write(conf / "initramfs.conf", "MODULES=list\nBUSYBOX=n\nCOMPRESS=gzip\nDEVICE=\nNFSROOT=auto\nRESUME=none\nUMASK=0022\n")
    write(conf / "modules", "\n".join(MODULES if profile is None else profile["modules"]) + "\n")
    shutil.copyfile(ASSETS / "hook", conf / "hooks/zz-bpi-rescue")
    (conf / "hooks/zz-bpi-rescue").chmod(0o755)
    for directory in ("usr/bin", "usr/sbin", "etc/ssh", "root", "proc", "sys", "dev", "run", "tmp", "var"):
        (seed / directory).mkdir(parents=True, exist_ok=True)
    for path in (seed, *seed.rglob("*")):
        if path.is_dir():
            path.chmod(0o755)
    shutil.copyfile(busybox, seed / "usr/bin/busybox")
    (seed / "usr/bin/busybox").chmod(0o755)
    require(sha256(seed / "usr/bin/busybox") == sha256(busybox), "BusyBox 複製核對失敗")
    prepare_runtime(seed, kernel, profile=profile)
    write(seed / "etc/ssh/rescue_authorized_keys", key, 0o600)
    shutil.copyfile(ASSETS / "sshd_config", seed / "etc/ssh/sshd_config")
    for name, target in (("ssh-start", "usr/sbin/bpi-rescue-ssh"),
                         ("udhcpc-script", "usr/sbin/bpi-rescue-udhcpc"),
                         ("bpi_rescue_cli.py", "usr/sbin/bpi_rescue_cli.py")):
        shutil.copyfile(ASSETS / name, seed / target)
        (seed / target).chmod(0o755)
    write(seed / "etc/passwd", "root:x:0:0:root:/root:/bin/sh\nsshd:x:104:65534:sshd:/run/sshd:/bin/false\n")
    write(seed / "etc/group", "root:x:0:\nnogroup:x:65534:\n")
    # NP 不是可用密碼雜湊，也不是 Linux 鎖帳號前綴；公鑰登入不會被鎖帳號檢查擋下。
    write(seed / "etc/shadow", "root:NP:19000:0:99999:7:::\nsshd:!:19000:0:99999:7:::\n", 0o600)
    write(seed / "etc/nsswitch.conf", "passwd: files\ngroup: files\nshadow: files\nhosts: files dns\n")
    write(seed / "etc/hosts", "127.0.0.1 localhost\n::1 localhost\n")
    (seed / "etc/resolv.conf").symlink_to("/run/resolv.conf")
    (seed / "var/run").symlink_to("/run")
    (seed / "var/empty").mkdir()
    write(seed / "run/resolv.conf", "")
    (seed / "root").chmod(0o700)
    (seed / "tmp").chmod(0o1777)
    for path in firmware:
        target = seed / "usr/lib/firmware" / path.relative_to("/lib/firmware")
        target.parent.mkdir(parents=True, exist_ok=True)
        if profile is None:
            shutil.copyfile(path, target)
        else:
            name = path.relative_to("/lib/firmware").as_posix()
            expected = {item["path"]: item["sha256"] for item in profile["firmware"]}
            require(name in expected, "複製韌體不在板級配置")
            resolved = path.resolve(strict=True)
            require(resolved.is_relative_to(Path("/lib/firmware").resolve()), "複製韌體來源越界")
            verified_firmware(resolved, expected[name], target)
    py_target = seed / stdlib.relative_to("/")
    shutil.copytree(stdlib, py_target, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", "test", "tests", "idlelib", "tkinter", "turtledemo",
        "ensurepip", "site-packages", "dist-packages"))
    py_bytes = sum(p.stat().st_size for p in py_target.rglob("*") if p.is_file())
    write(output / "binaries.json", json.dumps(binaries, sort_keys=True) + "\n")
    # hook 使用逐行兩欄資料，不以 shell eval 解讀來源路徑。
    pairs = [(source, ("/usr/sbin/" if name in ("sshd", "wpa_supplicant", "modprobe") else "/usr/bin/") + name)
             for name, source in binaries.items() if name in RUNTIME_BINARIES]
    pairs += [(str(p), str(p)) for p in sorted(stdlib.rglob("*.so"))
              if (py_target / p.relative_to(stdlib)).is_file()]
    multiarch = "aarch64-linux-gnu" if profile is None else profile["multiarch"]
    for pattern in ("libnss_files.so.*", "libnss_dns.so.*", "libresolv.so.*"):
        pairs += [(str(p), str(p)) for p in sorted(Path("/lib").joinpath(multiarch).glob(pattern))]
    require(all(not any(c.isspace() for c in source + target) for source, target in pairs), "執行檔路徑不能含空白")
    write(output / "copy-exec.list", "".join(f"{source} {target}\n" for source, target in pairs))
    write(output / "applets", "\n".join(APPLET_NAMES) + "\n")
    return py_bytes


def build(args, *, profile=None):
    output, kernel, binaries, busybox, key, firmware, stdlib = preflight(args, profile=profile)
    output.mkdir(mode=0o700, exist_ok=False)
    report = {"schema": SCHEMA if profile is None else LAB_SCHEMA, "kernel": kernel, "status": "未完成", "hardware_tested": False,
              "busybox_sha256": args.busybox_sha256, "authorized_key": bool(key)}
    report_path = output / "build-report.json"
    if profile is not None:
        report["board_profile"] = profile
    try:
        report["python_stdlib_bytes"] = prepare(output, kernel, binaries, busybox, key, firmware, stdlib, profile=profile)
        identity = output / "seed/etc/bpi-rescue.json"
        report["rescue_identity"] = {"schema": report["schema"], "kernel": kernel, "identity_sha256": sha256(identity)}
        report["runtime_entry_sha256"] = sha256(output / "seed/usr/sbin/bpi-rescue")
        (output / "tmp").mkdir()
        image = output / "rescue-initramfs.img"
        command = [binaries["unshare"], "--mount", "--fork", "--propagation", "private",
                   "/bin/sh", str(ASSETS / "build-namespace"), str(output), kernel, binaries["mkinitramfs"]]
        report["command"] = command
        with (output / "mkinitramfs.log").open("w") as log:
            try:
                code = run_build(command, output, log)
            except subprocess.TimeoutExpired as error:
                raise ValueError("建置逾時（600 秒），已終止建置程序群組") from error
        require(code == 0, "建置失敗，請查閱新目錄內 mkinitramfs.log；不會自動重試或覆寫")
        regular(image, "救援 initramfs")
        require(image.stat().st_size > 0, "mkinitramfs 輸出空檔")
        listing = checked([binaries["lsinitramfs"], image], env=dict(ENV, TMPDIR=str(output / "tmp")))
        write(output / "archive-files.txt", listing)
        files = {line.removeprefix("./") for line in listing.splitlines()}
        require({"init", "usr/bin/busybox", "usr/bin/curl", "usr/bin/python3", "usr/sbin/sshd",
                 "usr/sbin/bpi-rescue", "etc/bpi-rescue.json"} <= files, "initramfs 缺少必要內容")
        if profile is not None:
            require("usr/sbin/bpi_rescue_runtime.py" in files, "板級救援缺少共用執行模組")
        require(not any(re.search(r"(^|/)(boot|NetworkManager|machine-id)(/|$)|ssh_host_", p) for p in files),
                "initramfs 含禁止內容")
        report.update(status="建置完成，尚未實板驗證", image_bytes=image.stat().st_size,
                      image_sha256=sha256(image), firmware_bytes=sum(
                          p.stat().st_size for p in (output / "seed/usr/lib/firmware").rglob("*") if p.is_file()),
                      sources={p.name: sha256(p) for p in ASSETS.iterdir() if p.is_file()})
        write(output / "SHA256SUMS", f"{report['image_sha256']}  {image.name}\n")
    except Exception as error:
        report["error"] = str(error)
        raise
    finally:
        write(report_path, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return report


def parser():
    result = ChineseArgumentParser(description=__doc__)
    result.add_argument("--output", required=True, help="只寫入這個尚不存在的絕對工作目錄")
    result.add_argument("--busybox", required=True, help="已核對並解包的靜態 AArch64 BusyBox")
    result.add_argument("--busybox-sha256", required=True, help="可信來源核對後的 BusyBox SHA-256")
    result.add_argument("--authorized-key", help="可選的單一專用 SSH 公鑰，不接受私鑰")
    result.add_argument("--kernel", help="預設 uname -r；指定值亦必須與執行核心相同")
    return result


def main():
    try:
        report = build(parser().parse_args())
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"拒絕或失敗：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
