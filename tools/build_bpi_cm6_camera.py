#!/usr/bin/env python3
"""以固定來源重編 CM6 雙相機模式與停止候選；正式套件硬體驗證獨立記錄。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import shutil
import stat
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/spacemit-k1-camera"
LOCK = CONFIG / "source-lock.json"
PACKAGE_MANIFEST = "camera-build-manifest.json"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def regular(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "必須為一般檔案：" + str(path))
    return path


def child(root, name):
    relative = PurePosixPath(name)
    require(not relative.is_absolute() and ".." not in relative.parts and bool(relative.parts),
            "來源清單路徑不合法")
    path = Path(root) / name
    require(path.resolve().is_relative_to(Path(root).resolve()), "來源離開限定目錄")
    return regular(path)


def verify_file(path, record):
    path = regular(path)
    require(path.stat().st_size == record["bytes"] and digest(path) == record["sha256"],
            "固定檔案容量或 SHA-256 不符：" + path.name)
    return path


def mode2_config(value):
    require(value.get("auto_run") == 1 and value.get("auto_detect") == 0 and
            value.get("test_frame") == 300 and value.get("dump_one_frame") == 150,
            "CM6 配置必須為固定的有界雙路實拍")
    nodes = value.get("isp_node", [])
    require(len(nodes) == 2, "CM6 配置必須恰有兩路")
    for index, sensor_id in enumerate((0, 2)):
        expected = {"name": "isp" + str(index), "enable": 1, "work_mode": "online",
                    "sensor_name": "imx415_spm", "sensor_id": sensor_id,
                    "sensor_work_mode": 2, "fps": 30, "format": "NV12",
                    "in_width": 3840, "in_height": 2160, "bit_depth": 10,
                    "out_width": 1920, "out_height": 1080}
        require(all(nodes[index].get(k) == v for k, v in expected.items()), "CM6 感測器模式或尺寸不符")
    cpp = value.get("cpp_node", [])
    require(len(cpp) == 2 and all(item.get("enable") == 1 and item.get("format") == "NV12" and
            item.get("size_width") == 1920 and item.get("size_height") == 1080 for item in cpp),
            "CM6 CPP 輸出配置不符")


def load_lock():
    lock = json.loads(regular(LOCK).read_text())
    require(lock.get("schema_version") == 1 and lock.get("board") == "bpi-cm6" and
            lock.get("camera_profile") == "dual-imx415" and lock.get("package") == "k1x-cam" and
            lock.get("version") == "0.2.34+cm6.2" and lock.get("architecture") == "riscv64",
            "只接受固定的 CM6 相機修正版")
    validate_patch_series(lock)
    config = child(CONFIG, lock["config"]["path"])
    require(digest(config) == lock["config"]["sha256"], "相機配置雜湊不符")
    mode2_config(json.loads(config.read_text()))
    names = [record["path"] for record in lock["inputs"]]
    require(len(names) == len(set(names)), "固定輸入名稱重複")
    return lock


def validate_patch_series(lock):
    patches = lock["patches"]
    require([item["path"] for item in patches] == ["0001-dual-sensor-work-mode.patch", "0002-dual-vi-stop-before-isp.patch"],
            "相機補丁順序必須為模式參數、雙路停止")
    for index, item in enumerate(patches):
        require(item["target"] == "demo/online_pipeline_test.c", "相機補丁目標超出限定範圍")
        require(digest(child(CONFIG, item["path"])) == item["sha256"], "相機補丁雜湊不符")
        if index:
            require(item["before_sha256"] == patches[index - 1]["after_sha256"], "相機補丁來源雜湊鏈中斷")


def verify_toolchain(lock):
    """工具鏈本體來自固定 DEB；只檢查標準主機執行需求並記錄版本。"""
    require(platform.machine() == "x86_64", "此交叉工具鏈目前支援 Armbian Noble amd64 建置主機")
    versions = {}
    for name, minimum in lock["toolchain"]["host_requirements"].items():
        result = subprocess.run(["dpkg-query", "-W", "-f=${Version}", name], text=True, capture_output=True)
        require(result.returncode == 0, "缺少主機執行依賴：" + name)
        version = result.stdout.strip()
        require(subprocess.run(["dpkg", "--compare-versions", version, "ge", minimum]).returncode == 0,
                "主機執行依賴過舊：" + name + "，最低 " + minimum)
        versions[name] = version
    for name in ("dpkg-deb", "patch", "tar", "xz"):
        require(shutil.which(name), "缺少標準 Armbian 主機工具：" + name)
    return versions


def verify_inputs(inputs, lock):
    inputs = Path(inputs)
    require(inputs.is_dir() and not inputs.is_symlink(), "固定輸入必須為一般目錄")
    actual = set()
    for path in inputs.rglob("*"):
        require(not path.is_symlink() and (path.is_file() or path.is_dir()), "輸入快取不得含連結或特殊檔案")
        if path.is_file():
            actual.add(path.relative_to(inputs).as_posix())
    require(actual == {item["path"] for item in lock["inputs"]}, "固定輸入缺件或含未鎖定檔案")
    for record in lock["inputs"]:
        verify_file(child(inputs, record["path"]), record)
    return {record["path"]: record["sha256"] for record in lock["inputs"]}


def obtain_inputs(output, lock):
    """取得固定官方壓縮來源；只下載與核 SHA，不安裝或執行 DEB。"""
    output = Path(output)
    require(not output.is_symlink(), "輸入快取不能是連結")
    output.mkdir(parents=True, exist_ok=True)
    for item in lock["inputs"]:
        require(Path(item["path"]).name == item["path"] and item["url"].startswith("https://"), "固定下載項目不合法")
        destination = output / item["path"]
        if destination.exists():
            verify_file(destination, item)
            continue
        temporary = destination.with_name(destination.name + ".partial")
        require(not os.path.lexists(temporary), "發現未完成下載，請先核對後移除：" + temporary.name)
        try:
            with urllib.request.urlopen(item["url"], timeout=90) as response, temporary.open("xb") as stream:
                total = 0
                while block := response.read(1024 * 1024):
                    total += len(block)
                    require(total <= item["bytes"], "下載容量超過固定契約")
                    stream.write(block)
            verify_file(temporary, item)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
    return verify_inputs(output, lock)


def prepare_toolchain(inputs, work, lock, run):
    """把已驗證 DEB 解到私有工具根及目標 sysroot；不修改主機套件。"""
    tool_root = Path(work) / "toolchain"
    target_root = Path(work) / "target"
    for item in lock["inputs"]:
        if not item["path"].endswith(".deb"):
            continue
        destination = tool_root if item["kind"] == "toolchain" else target_root
        run(["dpkg-deb", "--extract", Path(inputs) / item["path"], destination])
    include = target_root / "usr/include"
    for name, target in lock["header_aliases"].items():
        require(Path(name).name == name and Path(target).name == target, "標頭別名不得含路徑")
        require(not os.path.lexists(include / name) and (include / target).is_dir(), "固定 DRM 標頭布局不符")
        (include / name).symlink_to(target)
    commands = {}
    for name, relative in lock["toolchain"]["commands"].items():
        path = tool_root / relative
        require(path.resolve().is_relative_to(tool_root.resolve()) and path.is_file(), "交叉工具鏈路徑離開解壓目錄")
        commands[name] = {"path": str(path)}
    commands.update({name: {"path": shutil.which(name)} for name in ("patch", "dpkg-deb")})
    return tool_root, target_root, commands


def extract_source(archive_path, destination, lock):
    verify_file(archive_path, lock["source"])
    before = {}
    with tarfile.open(archive_path, "r:xz") as archive:
        seen = set()
        for member in archive:
            path = PurePosixPath(member.name)
            require(not path.is_absolute() and ".." not in path.parts and path.parts[0] == "k1x-cam",
                    "相機來源含不安全路徑")
            require(member.isdir() or member.isfile(), "相機來源不得包含連結或特殊檔案")
            relative = PurePosixPath(*path.parts[1:]).as_posix()
            if member.isdir():
                continue
            require(relative not in seen and member.size <= 32 * 1024 * 1024, "相機來源重複或容量異常")
            seen.add(relative)
            output = Path(destination) / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            data = archive.extractfile(member).read()
            output.write_bytes(data)
            output.chmod(member.mode & 0o777)
            before[relative] = hashlib.sha256(data).hexdigest()
    first = lock["patches"][0]
    require(before.get(first["target"]) == first["before_sha256"], "補丁來源版本不符")
    return before


def apply_patch(source, before, lock, run):
    current = before
    for item in lock["patches"]:
        target = item["target"]
        require(current[target] == item["before_sha256"], "套用前的補丁來源雜湊不符")
        run(["patch", "--batch", "--fuzz=0", "-p1", "-i", str(CONFIG / item["path"])], cwd=source)
        after = {path.relative_to(source).as_posix(): digest(path) for path in sorted(Path(source).rglob("*"))
                 if path.is_file()}
        require(set(current) == set(after) and after[target] == item["after_sha256"], "補丁輸出內容不符")
        require([name for name in current if current[name] != after[name]] == [target], "補丁改動超出指定來源檔案")
        current = after
    return current


def elf_contract(path, readelf, run):
    header = Path(path).read_bytes()[:64]
    require(header[:6] == b"\x7fELF\x02\x01" and int.from_bytes(header[18:20], "little") == 243,
            "相機程式庫必須為小端序 ELF64 RISC-V")
    dynamic = run([readelf, "-dW", str(path)]).stdout
    require("(RPATH)" not in dynamic and "(RUNPATH)" not in dynamic, "不得嵌入主機函式庫路徑")
    needed = sorted(re.findall(r"\(NEEDED\).*\[(.*?)\]", dynamic))
    soname = re.findall(r"\(SONAME\).*\[(.*?)\]", dynamic)
    symbols = run([readelf, "--dyn-syms", "--wide", str(path)]).stdout
    exports = []
    for line in symbols.splitlines():
        fields = line.split()
        if len(fields) >= 8 and fields[4] in ("GLOBAL", "WEAK") and fields[6] != "UND":
            exports.append(fields[7])
    return {"needed": needed, "soname": soname, "exports": sorted(exports)}


def payload_inventory(root):
    result = {}
    for path in sorted(Path(root).rglob("*")):
        name = path.relative_to(root).as_posix()
        if name == "DEBIAN" or name.startswith("DEBIAN/"):
            continue
        if path.is_symlink():
            result[name] = {"link": str(path.readlink())}
        elif path.is_file():
            result[name] = {"sha256": digest(path), "mode": stat.S_IMODE(path.stat().st_mode)}
        else:
            require(path.is_dir(), "DEB 載荷含特殊檔案")
    return result


def validate_payload_changes(before, after, lock):
    allowed = {"usr/lib/libsdkcam.so", lock["config"]["installed_path"],
               "usr/share/doc/k1x-cam/cm6-source.json"}
    changed = {name for name in set(before) | set(after) if before.get(name) != after.get(name)}
    require(changed == allowed, "套件載荷改動超出 SDK、CM6 配置與來源紀錄")
    return sorted(changed)


def verify_dependencies(paths, source, target_root, tool_root, cwd):
    """確認編譯器沒有讀取來源鎖以外的標頭。"""
    roots = [Path(source).resolve(), Path(target_root).resolve(), Path(tool_root).resolve()]
    records = {}
    for dependency_file in paths:
        tokens = shlex.split(Path(dependency_file).read_text().replace("\\\n", " "))
        require(tokens and tokens[0].endswith(":"), "編譯器相依清單格式不符")
        for token in tokens[1:]:
            path = Path(token)
            path = (Path(cwd) / path).resolve() if not path.is_absolute() else path.resolve()
            require(any(path.is_relative_to(root) for root in roots), "編譯器讀取了來源鎖以外的標頭")
            records[str(path)] = digest(regular(path))
    return records


def package_records(cache):
    """核對本機來源建置紀錄；不是對外簽章或硬體通過宣告。"""
    lock = load_lock()
    cache = Path(cache)
    record = json.loads(child(cache, PACKAGE_MANIFEST).read_text())
    require(record.get("schema_version") == 1 and record.get("board") == "bpi-cm6" and
            record.get("status") == "complete" and record.get("source_lock_sha256") == digest(LOCK) and
            record.get("builder_sha256") == digest(__file__) and record.get("patches") == lock["patches"] and
            record.get("inputs") == {item["path"]: item["sha256"] for item in lock["inputs"]},
            "相機衍生套件缺少相符的固定來源建置證據")
    require(record.get("streamoff_fixed") is False and record.get("stop_order_patch_applied") is True and
            record.get("hardware_validation") == "pending", "正式套件須保留獨立的硬體驗證待辦")
    result = record["packages"]
    require(set(result) == {"k1x-cam", "k1x-cam-lib"}, "相機套件集合不符")
    for name, item in result.items():
        version = lock["version"] if name == "k1x-cam" else "0.1.8"
        require(item["version"] == version and item["filename"] == f"{name}_{version}_riscv64.deb",
                "相機衍生套件版本或檔名不符")
        verify_file(child(cache, item["filename"]), {"bytes": item["size"], "sha256": item["sha256"]})
        if name == "k1x-cam-lib":
            require(item["sha256"] == lock["upstream_packages"][name]["sha256"], "閉源 SDK 不得更動")
    return result


def build(inputs, output, board="bpi-cm6", profile="dual-imx415"):
    require(board == "bpi-cm6" and profile == "dual-imx415", "相機修正僅適用 CM6 雙 IMX415 配置")
    lock = load_lock()
    host_versions = verify_toolchain(lock)
    source_inputs = verify_inputs(inputs, lock)
    output = Path(output).absolute()
    require(not os.path.lexists(output), "相機建置目錄已存在，拒絕覆寫")
    output.mkdir(parents=True)
    inputs = Path(inputs).absolute()
    work = output / "work"
    work.mkdir()
    commands = []
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C", "TZ": "UTC",
           "SOURCE_DATE_EPOCH": str(lock["source_date_epoch"]),
           "ZERO_AR_DATE": "1"}

    def run(argv, cwd=work):
        process = subprocess.run(list(map(str, argv)), cwd=cwd, env=env, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        commands.append({"argv": list(map(str, argv)), "cwd": str(cwd), "returncode": process.returncode,
                         "stdout": process.stdout, "stderr": process.stderr})
        save(output / "commands.json", commands)
        require(process.returncode == 0, "相機建置命令失敗，請查看 commands.json：" + str(argv[0]))
        return process

    tool_root, target_root, tools = prepare_toolchain(inputs, work, lock, run)
    env["LD_LIBRARY_PATH"] = str(tool_root / "usr/lib/x86_64-linux-gnu")
    source = work / "source"
    before = extract_source(inputs / lock["source"]["path"], source, lock)
    after = apply_patch(source, before, lock, run)
    shutil.copyfile(LOCK, output / "source-lock.json")
    for item in lock["patches"]:
        shutil.copyfile(CONFIG / item["path"], output / item["path"])
    save(output / "source-files.json", {"before": before, "after": after})
    package = work / "package"
    original = inputs / lock["upstream_packages"]["k1x-cam"]["path"]
    run([tools["dpkg-deb"]["path"], "--raw-extract", original, package])
    old_inventory = payload_inventory(package)
    old_contract = elf_contract(package / "usr/lib/libsdkcam.so", tools["riscv64-linux-gnu-readelf"]["path"], run)
    demo = source / "demo"
    include_dirs = [target_root / "usr/include", target_root / "usr/include/libdrm", demo, *(demo / name for name in
                    ("include", "include/dmabufheap", "include/opengles", "utils", "extern", "gst_api", "v4l2_test")),
                    source / "libs/include", source / "sensors", source / "sensors/include"]
    objects = []
    for path in sorted(demo.rglob("*")):
        if path.suffix not in (".c", ".cpp"):
            continue
        relative = path.relative_to(source).as_posix()
        target = work / ("_".join(path.relative_to(demo).parts) + ".o")
        compiler = "riscv64-linux-gnu-g++" if path.suffix == ".cpp" else "riscv64-linux-gnu-gcc"
        run([tools[compiler]["path"], "--sysroot=" + str(tool_root), "-c", "-O2", "-g", "-fPIC", "-DNDEBUG", "-D_GNU_SOURCE",
             "-DSTB_IMAGE_IMPLEMENTATION", "-Wall", "-Wno-unused", "-Wno-implicit-fallthrough",
             "-ffile-prefix-map=" + str(work) + "=/usr/src/bpi-cm6-camera",
             "-ffile-prefix-map=" + str(inputs) + "=/usr/src/bpi-cm6-camera-inputs",
             "-frandom-seed=" + relative, "-MD", "-MF", str(target) + ".d",
             *(value for directory in include_dirs for value in ("-I", str(directory))), path, "-o", target])
        objects.append(target)
    dependencies = verify_dependencies([str(path) + ".d" for path in objects], source, target_root, tool_root, work)
    save(output / "compile-dependencies.json", dependencies)
    libraries = [target_root / name for name in lock["target_libraries"]]
    require(all(path.is_file() and path.resolve().is_relative_to(target_root.resolve()) for path in libraries),
            "固定目標鏈結函式庫缺件或離開 sysroot")
    library = work / "libsdkcam.so"
    run([tools["riscv64-linux-gnu-g++"]["path"], "--sysroot=" + str(tool_root), "-shared", "-Wl,-soname,libsdkcam.so", "-Wl,--no-undefined",
         "-o", library, *objects, *libraries, "-lm", "-pthread"])
    require(elf_contract(library, tools["riscv64-linux-gnu-readelf"]["path"], run) == old_contract,
            "修正版 ELF 相依或匯出介面與固定官方版本不同")
    require(digest(library) != old_inventory["usr/lib/libsdkcam.so"]["sha256"], "未產生可辨識的來源修正版")
    shutil.copyfile(library, package / "usr/lib/libsdkcam.so")
    installed_config = package / lock["config"]["installed_path"]
    installed_config.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(CONFIG / lock["config"]["path"], installed_config)
    installed_config.chmod(0o644)
    provenance = {"source_lock_sha256": digest(LOCK), "source_sha256": lock["source"]["sha256"],
                  "patches": lock["patches"], "builder_sha256": digest(__file__),
                  "library_sha256": digest(library), "streamoff_fixed": False, "stop_order_patch_applied": True,
                  "hardware_validation": "pending",
                  "scope": lock["scope"]}
    provenance_path = "usr/share/doc/k1x-cam/cm6-source.json"
    save(package / provenance_path, provenance)
    (package / provenance_path).chmod(0o644)
    current = payload_inventory(package)
    changed = validate_payload_changes(old_inventory, current, lock)
    control = package / "DEBIAN/control"
    text = control.read_text()
    require(re.search(r"(?m)^Version: 0\.2\.34$", text), "官方 DEB 版本不符")
    control.write_text(re.sub(r"(?m)^Version: 0\.2\.34$", "Version: " + lock["version"], text))
    md5sums = []
    for path in sorted(package.rglob("*")):
        relative = path.relative_to(package).as_posix()
        if path.is_file() and not path.is_symlink() and not relative.startswith("DEBIAN/"):
            md5sums.append(hashlib.md5(path.read_bytes()).hexdigest() + "  " + relative)
    (package / "DEBIAN/md5sums").write_text("\n".join(md5sums) + "\n")
    for path in sorted(package.rglob("*"), reverse=True):
        os.utime(path, (lock["source_date_epoch"],) * 2, follow_symlinks=False)
    os.utime(package, (lock["source_date_epoch"],) * 2)
    derived = output / f"k1x-cam_{lock['version']}_riscv64.deb"
    run([tools["dpkg-deb"]["path"], "--root-owner-group", "-Zxz", "-z6", "--threads-max=1", "--build", package, derived])
    other = lock["upstream_packages"]["k1x-cam-lib"]
    shutil.copyfile(inputs / other["path"], output / other["path"])
    require(verify_toolchain(lock) == host_versions, "建置期間主機執行依賴版本變更")
    require(verify_inputs(inputs, lock) == source_inputs, "建置期間固定輸入變更")
    actual_source = {path.relative_to(source).as_posix(): digest(path) for path in source.rglob("*") if path.is_file()}
    require(actual_source == after, "建置期間來源變更")
    packages = {}
    for name, version in (("k1x-cam", lock["version"]), ("k1x-cam-lib", "0.1.8")):
        path = output / f"{name}_{version}_riscv64.deb"
        packages[name] = {"version": version, "filename": path.name, "size": path.stat().st_size,
                          "sha256": digest(path), "provenance": provenance if name == "k1x-cam" else
                          {"signature_verified": False, "scope": "保持固定官方閉源 SDK，未變更內容。"}}
    record = {"schema_version": 1, "board": "bpi-cm6", "status": "complete", "packages": packages,
              "source_lock_sha256": digest(LOCK), "builder_sha256": digest(__file__), "patches": lock["patches"],
              "inputs": source_inputs, "library_sha256": digest(library), "elf_contract": old_contract,
              "streamoff_fixed": False, "stop_order_patch_applied": True, "hardware_validation": "pending",
              "reproducibility_validation": "pending", "changed_payload_paths": sorted(changed)}
    record["host_runtime_versions"] = host_versions
    save(output / PACKAGE_MANIFEST, record)
    package_records(output)
    shutil.copyfile(inputs / lock["source"]["path"], output / lock["source"]["path"])
    shutil.copyfile(library, output / "libsdkcam.so")
    shutil.rmtree(work)
    return record


class ChineseArgumentParser(argparse.ArgumentParser):
    def __init__(self, **kwargs):
        super().__init__(add_help=False, **kwargs)
        self._positionals.title = "位置參數"
        self._optionals.title = "選項"
        self.add_argument("-h", "--help", action="help", help="顯示說明後結束")

    def format_usage(self):
        return super().format_usage().replace("usage: ", "用法：", 1)

    def format_help(self):
        return super().format_help().replace("usage: ", "用法：", 1)

    def error(self, message):
        self.exit(2, "參數不完整或無法辨識，請使用 --help。\n")


def main():
    parser = ChineseArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("fetch", "check", "build"))
    parser.add_argument("--inputs", type=Path, required=True, help="固定相機輸入快取")
    parser.add_argument("--output", type=Path, help="全新衍生套件輸出目錄")
    parser.add_argument("--board", default="bpi-cm6")
    parser.add_argument("--profile", default="dual-imx415")
    args = parser.parse_args()
    require(args.board == "bpi-cm6" and args.profile == "dual-imx415", "只接受 CM6 雙 IMX415")
    lock = load_lock()
    if args.action == "fetch":
        obtain_inputs(args.inputs, lock)
    elif args.action == "check":
        verify_toolchain(lock)
        verify_inputs(args.inputs, lock)
    else:
        require(args.output is not None, "build 需要 --output")
        build(args.inputs, args.output, args.board, args.profile)
    print(json.dumps({"action": args.action, "status": "完成", "streamoff_fixed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()
