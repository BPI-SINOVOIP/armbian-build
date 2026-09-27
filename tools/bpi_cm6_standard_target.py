#!/usr/bin/env python3
"""只在新 CM6 標準 rootfs 內編譯及安裝受控套件；不讀 GPIO 裝置。"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig

RPI_SOURCES = ("py_gpio.c", "c_gpio.c", "cpuinfo.c", "event_gpio.c", "soft_pwm.c",
               "py_pwm.c", "common.c", "constants.c", "c_gpio_bpi.c")
BT_SOURCES = ("hciattach.c", "hciattach_rtk.c", "hciattach_h4.c", "rtb_fwc.c")
APT = ["apt-get", "-y", "-o", "APT::Get::AllowUnauthenticated=false",
       "-o", "Acquire::AllowInsecureRepositories=false",
       "-o", "Acquire::AllowDowngradeToInsecureRepositories=false"]
COMMANDS = []


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def run(argv, cwd=None):
    result = subprocess.run([str(v) for v in argv], cwd=cwd, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env={**os.environ, "LC_ALL": "C", "DEBIAN_FRONTEND": "noninteractive",
                                 "SOURCE_DATE_EPOCH": "1784421268", "TZ": "UTC"})
    COMMANDS.append({"argv": [str(v) for v in argv], "exit": result.returncode,
                     "stdout": result.stdout, "stderr": result.stderr})
    require(result.returncode == 0, "命令未完成：" + " ".join(map(str, argv)))
    return result.stdout


def installed():
    text = run(["dpkg-query", "-W", "-f=${binary:Package}\t${Version}\t${Architecture}\t${db:Status-Abbrev}\n"])
    return {p: {"version": v, "architecture": a} for p, v, a, status in
            (line.split("\t") for line in text.splitlines()) if status.startswith("ii")}


def parse_install_plan(text):
    require(not any(line.startswith(("Remv ", "Purg ")) for line in text.splitlines()), "APT 計畫包含移除")
    result = []
    for line in text.splitlines():
        if not line.startswith("Inst "):
            continue
        match = re.match(r"Inst (\S+) \((\S+) ", line)
        require(match is not None, "APT 計畫包含既有套件升降級或未知格式")
        result.append(match.group(1) + "=" + match.group(2))
    return result


def removal_plan(text, packages, added_build):
    """將 APT 套件名解析回唯一 dpkg 身分，只接受本輪新增建置相依。"""
    require(not any(line.startswith("Inst ") for line in text.splitlines()),
            "移除建置相依不可安裝或升降級套件")
    resolved = {}
    for line in text.splitlines():
        if not line.startswith(("Remv ", "Purg ")):
            continue
        fields = line.split()
        require(len(fields) >= 2, "APT 移除計畫格式不符")
        token = fields[1]
        match = re.fullmatch(r"([a-z0-9][a-z0-9+.-]+)(?::([a-z0-9][a-z0-9-]*))?", token)
        require(match is not None, "APT 移除套件名稱不合法")
        name, architecture = match.groups()
        matches = [key for key, item in packages.items()
                   if key.split(":", 1)[0] == name
                   and (architecture is None or item["architecture"] == architecture)]
        require(len(matches) == 1, "APT 移除套件身分未知或架構不唯一：" + token)
        key = matches[0]
        require(key in added_build, "移除建置相依會更動原套件或執行期套件：" + key)
        resolved[token] = key
    return {"plan": text, "resolved_packages": resolved,
            "removed_build_packages": sorted(set(resolved.values()))}


def apt_install(names, archive_dir, receipts):
    before = installed()
    plan = run(APT + ["--simulate", "--no-remove", "--no-upgrade", "--no-install-recommends", "install", *names])
    exact = parse_install_plan(plan)
    if not exact:
        return
    archive_dir.mkdir(parents=True, exist_ok=False)
    (archive_dir / "partial").mkdir()
    option = ["-o", "Dir::Cache::archives=" + str(archive_dir)]
    run(APT + option + ["--download-only", "--no-remove", "--no-upgrade", "--no-install-recommends", "install", *exact])
    packages = []
    for path in sorted(archive_dir.glob("*.deb")):
        fields = run(["dpkg-deb", "-f", path, "Package", "Version", "Architecture"])
        values = dict(line.split(": ", 1) for line in fields.splitlines())
        require(values["Architecture"] in {"riscv64", "all"}, "APT 建置相依架構錯誤")
        metadata = run(["apt-cache", "show", values["Package"] + "=" + values["Version"]])
        blocks = [dict(line.split(": ", 1) for line in block.splitlines() if ": " in line and not line.startswith(" "))
                  for block in metadata.split("\n\n")]
        actual = digest(path)
        records = [block for block in blocks if block.get("SHA256") == actual and
                   block.get("Architecture") == values["Architecture"]]
        require(records, "APT DEB 不符已驗證索引的 SHA256")
        packages.append({"package": values["Package"], "version": values["Version"],
                         "architecture": values["Architecture"], "sha256": actual,
                         "bytes": path.stat().st_size, "filename": records[0]["Filename"]})
    require(len(packages) == len(exact), "APT 下載數量與固定安裝計畫不同")
    run(APT + option + ["--no-download", "--no-remove", "--no-upgrade", "--no-install-recommends", "install", *exact])
    after = installed()
    require(all(after.get(name) == info for name, info in before.items()), "建置相依更動了既有套件")
    receipts.append({"requested": names, "exact_plan": exact, "packages": packages})


def copy(source, stage, relative, mode=0o644):
    target = stage / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(mode)
    return target


def elf_info(path):
    data = path.read_bytes()[:64]
    require(len(data) == 64 and data[:6] == b"\x7fELF\x02\x01" and int.from_bytes(data[18:20], "little") == 243,
            "成品不是 ELF64 RISC-V")
    require(int.from_bytes(data[48:52], "little") & 6 == 4, "成品不是 lp64d ABI")
    text = run(["readelf", "-W", "-h", "-d", "-V", "-A", path])
    require("(RPATH)" not in text and "(RUNPATH)" not in text, "成品含建置路徑")
    return {"path": str(path), "sha256": digest(path), "readelf": text,
            "needed": sorted(set(re.findall(r"Shared library: \[(.*?)\]", text))),
            "glibc_versions": sorted(set(re.findall(r"GLIBC_[0-9.]+", text)))}


def dependency_list(stage, binaries, name, version):
    debian = stage / "debian"
    debian.mkdir()
    (debian / "control").write_text("Source: " + name + "\nSection: misc\nPriority: optional\nMaintainer: BPI CM6 <support@banana-pi.org>\n\nPackage: " + name + "\nArchitecture: riscv64\nDescription: CM6 標準板級套件\n")
    (debian / "shlibs.local").write_text("libwiringPi 3 " + name + " (= " + version + ")\nlibwiringPiDev 3 " + name + " (= " + version + ")\n" if name == "bpi-cm6-wiringpi" else "")
    output = run(["dpkg-shlibdeps", "-O", "-x" + name,
                  "-l" + str(stage / "usr/lib/riscv64-linux-gnu"),
                  *["-e" + str(path) for path in binaries]], cwd=stage)
    shutil.rmtree(debian)
    dependencies = next(line.split("=", 1)[1] for line in output.splitlines() if line.startswith("shlibs:Depends="))
    return [part.strip() for part in dependencies.split(",") if part.strip()]


def package(bundle, output, kind, stage, binaries, state, abi, extra):
    name = {"wiringpi": "bpi-cm6-wiringpi", "rpi-gpio": "python3-bpi-cm6-gpio", "bluetooth": "bpi-cm6-bluetooth"}[kind]
    version = state["sources"][kind]["version"]
    depends = dependency_list(stage, binaries, name, version) + extra
    if kind == "rpi-gpio":
        major, minor = map(int, abi.split("."))
        depends += ["python3 (>= " + abi + "~)", "python3 (<< " + str(major) + "." + str(minor + 1) + ")"]
    fields = {"wiringpi": "Conflicts: wiringpi, libwiringpi2, libwiringpi3, libwiringpi-dev\nProvides: wiringpi, libwiringpi3, libwiringpi-dev\n",
              "rpi-gpio": "Conflicts: python3-rpi.gpio\nProvides: python3-rpi.gpio (= 0.7.1)\n", "bluetooth": ""}
    control = stage / "DEBIAN"
    control.mkdir(exist_ok=True)
    (control / "control").write_text("Package: " + name + "\nVersion: " + version + "\nArchitecture: riscv64\nSection: misc\nPriority: optional\nMaintainer: BPI CM6 <support@banana-pi.org>\nDepends: " + ", ".join(depends) + "\n" + fields[kind] + "Description: CM6 標準板級來源套件\n 依目標發行版原生編譯；軟體安裝不等於實機功能驗收。\n")
    doc = stage / "usr/share/doc" / name
    doc.mkdir(parents=True, exist_ok=True)
    copy(bundle / "source-manifest.json", stage, "usr/share/doc/" + name + "/source-manifest.json")
    copy(bundle / "native-build.lock.json", stage, "usr/share/doc/" + name + "/native-build.lock.json")
    if kind in {"wiringpi", "rpi-gpio"}:
        copy(bundle / (kind + "-source.tar.gz"), stage, "usr/share/doc/" + name + "/source.tar.gz")
        for patch in (bundle / "source-patches" / kind).glob("*.patch"):
            copy(patch, stage, "usr/share/doc/" + name + "/patches/" + patch.name)
    elfs = [elf_info(path) for path in binaries]
    for item, path in zip(elfs, binaries):
        item["path"] = "/" + path.relative_to(stage).as_posix()
    payload = []
    for path in sorted(stage.rglob("*")):
        if control in path.parents or path == control or path.is_dir():
            continue
        item = {"path": "/" + path.relative_to(stage).as_posix(), "mode": oct(path.lstat().st_mode & 0o777)}
        if path.is_symlink():
            target = os.readlink(path)
            require(not os.path.isabs(target) and ".." not in Path(target).parts, "套件連結越界")
            item.update(type="symlink", target=target, sha256=hashlib.sha256(target.encode()).hexdigest())
        else:
            item.update(type="file", bytes=path.stat().st_size, sha256=digest(path))
        payload.append(item)
    artifact = name + "_" + version + "_riscv64.deb"
    for path in sorted(stage.rglob("*"), reverse=True):
        if not path.is_symlink():
            os.utime(path, (1784421268, 1784421268))
    run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", stage, output / artifact])
    record = {"schema_version": 1, "package": name, "version": version, "architecture": "riscv64",
              "release": state["release"], "python_abi": abi, "artifact": artifact,
              "bytes": (output / artifact).stat().st_size, "sha256": digest(output / artifact),
              "builder_sha256": state["builder_sha256"], "lock_sha256": state["lock_sha256"],
              "source": state["sources"][kind], "runtime_dependencies": depends, "elf": elfs,
              "payload_files": payload, "hardware_validation": "pending"}
    write_json(output / (kind + "-package-manifest.json"), record)
    return record


def compile_all(bundle, output, state, abi):
    flags = ["-march=rv64gc", "-mabi=lp64d", "-O2", "-g0", "-fstack-protector-strong",
             "-D_FORTIFY_SOURCE=2", "-ffile-prefix-map=" + str(bundle) + "=/usr/src/bpi-cm6-standard"]
    harden = ["-Wl,-z,relro,-z,now", "-Wl,-z,noexecstack"]
    records = []
    source = bundle / "wiringpi"
    for directory in ("wiringPi", "devLib", "gpio"):
        for path in (source / directory).iterdir():
            if path.is_file() and (path.suffix == ".o" or ".so" in path.name or path.name == "gpio"):
                path.unlink()
    make = ["make", "-j2", "V=1", "CC=gcc", "WIRINGPI_SONAME_SUFFIX=.3", "EXTRA_CFLAGS=" + " ".join(flags)]
    wpi, dev = source / "wiringPi", source / "devLib"
    run(make + ["INCLUDE=-I. -I./board", "LIBS=" + " ".join(harden) + " -lm -lpthread -lrt -lcrypt"], cwd=wpi)
    (wpi / "libwiringPi.so").symlink_to("libwiringPi.so.3.19")
    run(make + ["INCLUDE=-I. -I" + str(wpi), "LIBS=-L" + str(wpi) + " -Wl,--no-undefined -lwiringPi " + " ".join(harden)], cwd=dev)
    (dev / "libwiringPiDev.so").symlink_to("libwiringPiDev.so.3.19")
    run(make + ["INCLUDE=-I" + str(wpi) + " -I" + str(dev), "LDFLAGS=-L" + str(wpi) + " -L" + str(dev) + " " + " ".join(harden)], cwd=source / "gpio")
    stage = bundle / "stage-wiringpi"
    stage.mkdir()
    binaries = [copy(source / "gpio/gpio", stage, "usr/bin/gpio", 0o755)]
    for name, directory in (("libwiringPi", wpi), ("libwiringPiDev", dev)):
        path = copy(directory / (name + ".so.3.19"), stage, "usr/lib/riscv64-linux-gnu/" + name + ".so.3.19")
        binaries.append(path)
        path.with_name(name + ".so.3").symlink_to(path.name)
        path.with_name(name + ".so").symlink_to(path.name)
    for path in wpi.glob("*.h"):
        copy(path, stage, "usr/include/" + path.name)
    for name in ("ds1302.h", "gertboard.h", "lcd128x64.h", "lcd.h", "maxdetect.h", "piFace.h", "piGlow.h", "piNes.h", "scrollPhat.h"):
        copy(dev / name, stage, "usr/include/" + name)
    copy(source / "COPYING.LESSER", stage, "usr/share/doc/bpi-cm6-wiringpi/COPYING.LESSER")
    copy(source / "debian/copyright", stage, "usr/share/doc/bpi-cm6-wiringpi/copyright")
    copy(source / "gpio/gpio.1", stage, "usr/share/man/man1/gpio.1")
    (stage / "DEBIAN").mkdir()
    (stage / "DEBIAN/triggers").write_text("activate-noawait ldconfig\n")
    records.append(package(bundle, output, "wiringpi", stage, binaries, state, abi, []))
    source = bundle / "rpi-gpio"
    stage = bundle / "stage-rpi-gpio"
    stage.mkdir()
    for name in ("RPi/__init__.py", "RPi/GPIO/__init__.py"):
        copy(source / name, stage, "usr/lib/python3/dist-packages/" + name)
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    require(suffix == ".cpython-" + abi.replace(".", "") + "-riscv64-linux-gnu.so", "Python 真實擴充 ABI 不符")
    binary = stage / "usr/lib/python3/dist-packages/RPi" / ("_GPIO" + suffix)
    includes = sorted(set(sysconfig.get_paths()[name] for name in ("include", "platinclude")))
    run(["gcc", *flags, *harden, "-shared", "-fPIC", "-pthread", *["-I" + p for p in includes],
         "-o", binary, *[source / "source" / name for name in RPI_SOURCES]])
    copy(source / "LICENCE.txt", stage, "usr/share/doc/python3-bpi-cm6-gpio/copyright")
    records.append(package(bundle, output, "rpi-gpio", stage, [binary], state, abi, []))
    source = bundle / "bluetooth"
    stage = bundle / "stage-bluetooth"
    binary = stage / "usr/lib/bpi-cm6-bluetooth/rtk_hciattach"
    binary.parent.mkdir(parents=True)
    run(["gcc", "-std=gnu11", *flags, *harden, "-fPIE", "-pie", *[source / name for name in BT_SOURCES], "-o", binary])
    copy(bundle / "bpi-cm6-bluetooth", stage, "usr/sbin/bpi-cm6-bluetooth", 0o755)
    copy(bundle / "bpi-cm6-bluetooth.service", stage, "usr/lib/systemd/system/bpi-cm6-bluetooth.service")
    copy(bundle / "bluetooth-source-lock.json", stage, "usr/share/bpi-cm6-bluetooth/source-lock.json")
    for path in (bundle / "firmware").iterdir():
        relative = "usr/share/doc/bpi-cm6-bluetooth/firmware-copyright" if path.name == "copyright" else "usr/lib/bpi-cm6-bluetooth/firmware/" + path.name
        copy(path, stage, relative)
    for name in ("bluetooth-source.tar.gz", "firmware-source.tar.xz"):
        copy(bundle / name, stage, "usr/share/doc/bpi-cm6-bluetooth/" + name)
    for path in source.iterdir():
        copy(path, stage, "usr/share/doc/bpi-cm6-bluetooth/source/" + path.name)
    for path in (bundle / "patches").glob("*.patch"):
        copy(path, stage, "usr/share/doc/bpi-cm6-bluetooth/patches/" + path.name)
    for path in (bundle / "bluetooth-maintainer").iterdir():
        target = copy(path, stage, "DEBIAN/" + path.name, 0o755)
        run(["sh", "-n", target])
    records.append(package(bundle, output, "bluetooth", stage, [binary], state, abi,
                           ["bluez", "python3 (>= 3.10)", "kmod", "init-system-helpers"]))
    return records


def main(bundle):
    require(os.geteuid() == 0, "須由建置框架在新 rootfs 內執行")
    require(not Path("/run/systemd/system").exists(), "拒絕在已啟動系統執行映像建置流程")
    policy = Path("/usr/sbin/policy-rc.d")
    require(policy.is_file() and [line.strip() for line in policy.read_text().splitlines()
            if line.strip() and not line.startswith("#")] == ["exit 101"],
            "建置 rootfs 缺少已核對的服務啟動阻擋政策")
    require(bundle.is_absolute() and bundle.parent == Path("/var/tmp") and bundle.name.startswith("cm6-standard-"),
            "僅接受建置框架建立的 rootfs 暫存來源")
    state = json.loads((bundle / "source-manifest.json").read_text())
    require(state["release"] in {"jammy", "trixie"}, "目標內建置僅支援 Jammy／Trixie")
    require(run(["dpkg", "--print-architecture"]).strip() == "riscv64", "禁止在建置主機直接執行")
    require(re.search(r"^BOARD=bananapicm6$", Path("/etc/armbian-release").read_text(), re.M), "目標板名不符")
    require(re.search(r"^VERSION_CODENAME=" + state["release"] + r"$", Path("/etc/os-release").read_text(), re.M), "目標發行版不符")
    abi = str(sys.version_info.major) + "." + str(sys.version_info.minor)
    require(abi == state["python_abi"] and digest(__file__) == state["builder_sha256"], "Python ABI 或建置工具不符")
    for relative, item in state["inputs"].items():
        path = bundle / relative
        require(path.resolve().is_relative_to(bundle) and path.is_file() and not path.is_symlink() and
                path.stat().st_size == item["bytes"] and digest(path) == item["sha256"], "固定來源內容遭變更")
    output = bundle / "output"
    output.mkdir()
    before = installed()
    manual = set(run(["apt-mark", "showmanual"]).splitlines())
    auto = set(run(["apt-mark", "showauto"]).splitlines())
    receipts = []
    try:
        for path in [Path("/etc/apt/sources.list"), *Path("/etc/apt/sources.list.d").glob("*")]:
            if path.is_file():
                text = path.read_text()
                require(not re.search(r"trusted\s*=\s*yes|Trusted:\s*yes|allow-insecure\s*=\s*yes", text, re.I),
                        "APT 來源不可略過簽章")
        run(APT + ["-o", "APT::Update::Error-Mode=any", "update"])
        indexes = [{"name": p.name, "sha256": digest(p)} for p in sorted(Path("/var/lib/apt/lists").glob("*InRelease"))]
        require(indexes, "缺少 APT 簽章索引證據")
        apt_install(["bluez", "python3", "kmod", "init-system-helpers", "libcrypt1"], bundle / "runtime-debs", receipts)
        baseline = installed()
        apt_install(["build-essential", "python3-dev", "libcrypt-dev"], bundle / "build-debs", receipts)
        with_build = installed()
        added_build = set(with_build) - set(baseline)
        require(run(["gcc", "-dumpmachine"]).strip() == "riscv64-linux-gnu", "目標編譯器架構不符")
        toolchain = {"gcc": run(["gcc", "--version"]), "gcc_sha256": digest(Path(shutil.which("gcc")).resolve()),
                     "packages": with_build, "python": sys.version, "sysconfig": {k: sysconfig.get_config_var(k) for k in ("SOABI", "EXT_SUFFIX", "MULTIARCH")}}
        packages = compile_all(bundle, output, state, abi)
        paths = [str(output / p["artifact"]) for p in packages]
        run(APT + ["--no-remove", "--no-install-recommends", "install", *paths])
        cleanup = {"plan": "", "resolved_packages": {}, "removed_build_packages": []}
        if added_build:
            plan = run(APT + ["--simulate", "purge", *sorted(added_build)])
            cleanup_state = {"plan": plan, "added_build_packages": sorted(added_build),
                             "installed_packages": installed()}
            write_json(output / "cleanup-plan.json", cleanup_state)
            try:
                cleanup = removal_plan(plan, cleanup_state["installed_packages"], added_build)
            except ValueError:
                # rootfs 清理前仍在建置日誌保留真正拒絕的計畫；不繼續 purge。
                print(json.dumps({"cleanup_rejected": cleanup_state}, ensure_ascii=False), file=sys.stderr)
                raise
            run(APT + ["purge", *sorted(added_build)])
        after = installed()
        require(all(after.get(name) == info for name, info in before.items()), "建置流程更動了原已安裝套件")
        require(not (set(after) & added_build), "新增建置相依尚未移除")
        if manual:
            run(["apt-mark", "manual", *sorted(manual)])
        if auto:
            run(["apt-mark", "auto", *sorted(auto & set(after))])
        audit = run(["dpkg", "--audit"])
        require(not audit.strip(), "dpkg 稽核未通過")
        for record in packages:
            record["toolchain"] = toolchain
            record["dependency_receipts"] = receipts
            record["apt_inrelease"] = indexes
            kind = {"bpi-cm6-wiringpi": "wiringpi", "python3-bpi-cm6-gpio": "rpi-gpio", "bpi-cm6-bluetooth": "bluetooth"}[record["package"]]
            write_json(output / (kind + "-package-manifest.json"), record)
        write_json(output / "result.json", {"schema_version": 1, "status": "complete", "release": state["release"],
                   "source_manifest_sha256": digest(bundle / "source-manifest.json"), "packages": packages,
                   "builddeps_added_and_removed": sorted(added_build), "builddeps_cleanup": cleanup,
                   "initial_packages": before,
                   "final_packages": after, "hardware_validation": "pending"})
    finally:
        write_json(output / "commands.json", COMMANDS)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
