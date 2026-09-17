#!/usr/bin/env python3
"""從可信救援封裝準備目標 Python，相依衝突與未知來源一律拒絕。"""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat

if __package__:
    from . import bpi_lab_deploy as deploy
    from . import bpi_lab_external_guard as guard
else:
    import bpi_lab_deploy as deploy
    import bpi_lab_external_guard as guard

require = guard.require


def select(original, rescue, *, architecture, python, stdlib, library_dirs):
    require(re.fullmatch(r"/usr/bin/python3(?:\.\d+)?", python or ""), "只接受明示的目標 Python 路徑")
    require(re.fullmatch(r"/usr/lib/python3\.\d+", stdlib or ""), "標準函式庫路徑無效")
    profile = {key: hashlib.sha256(guard.resolve_entry(original, path)[1]["data"]).hexdigest()
               for key, path in (("init_sha256", "/init"), ("functions_sha256", "/scripts/functions"))}
    bundle = guard._bundle({"schema": guard.BUNDLE_SCHEMA, "architecture": architecture,
                            "archive": {"path": "/pending", "sha256": "0" * 64}, "python": python,
                            "blkid": guard.linux.BLKID, "library_dirs": library_dirs,
                            "stdlib_dirs": [stdlib], "init_profile": profile})
    guard._init_order(original, profile)
    resolved_python, executable = guard.resolve_entry(rescue, python)
    require(re.fullmatch(r"usr/bin/python3\.\d+", resolved_python), "救援 Python 連結目標不符")
    require(resolved_python.split("/")[-1] == stdlib.split("/")[-1], "Python 與標準函式庫版本不同")
    guard.elf_info(executable["data"], architecture)
    require(stat.S_ISDIR(guard.resolve_entry(rescue, stdlib)[1]["mode"]), "救援標準函式庫缺少")
    selected = {python.lstrip("/"), resolved_python}
    selected.update(name for name in rescue if name == stdlib.lstrip("/") or name.startswith(stdlib.lstrip("/") + "/"))
    python_files = set(selected)
    for directory in library_dirs:
        require(re.fullmatch(r"/(?:usr/)?lib(?:/[A-Za-z0-9_.+-]+)?", directory), "相依目錄不在受限函式庫範圍")
        directory, item = guard.resolve_entry(rescue, directory)
        require(stat.S_ISDIR(item["mode"]), "救援相依目錄不是目錄")
        for name, item in rescue.items():
            if str(PurePosixPath(name).parent) != directory or stat.S_IFMT(item["mode"]) not in (stat.S_IFREG, stat.S_IFLNK):
                continue
            _, value = guard.resolve_entry(rescue, name)
            if stat.S_ISREG(value["mode"]) and value["data"].startswith(b"\x7fELF"):
                selected.add(name)
    merged, additions = dict(original), {}
    for name in sorted(selected):
        item = rescue[name]
        require(stat.S_IFMT(item["mode"]) in (stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK), "執行環境含特殊節點")
        if name in original:
            if name in python_files:
                require(all(original[name][key] == item[key] for key in ("mode", "data", "uid", "gid")),
                        "原 initramfs 的 Python 內容衝突")
            continue
        for parent in reversed(PurePosixPath(name).parents):
            if str(parent) == ".":
                continue
            parent = str(parent)
            value = merged.get(parent, rescue.get(parent))
            require(value is not None and stat.S_ISDIR(value["mode"]), "執行環境父路徑不是明確目錄")
            merged[parent] = value
            additions.setdefault(parent, value)
        merged[name] = item
        additions[name] = item
    roots = [python, guard.linux.BLKID, "/bin/sh"]
    roots.extend("/" + name for name, item in additions.items()
                 if stat.S_ISREG(item["mode"]) and item["data"].startswith(b"\x7fELF"))
    dependencies = guard.executable_closure(merged, roots, architecture, library_dirs)
    for path in (python, guard.linux.BLKID, "/bin/sh"):
        _, item = guard.resolve_entry(merged, path)
        require(item["mode"] & 0o111, "執行環境工具缺少執行權限")
    blob = guard.archive(additions)
    require(guard.parse_archive(blob)[0] == additions, "執行環境封裝不能完整重播")
    return blob, bundle, dependencies


def build(original_initrd, rescue_archive, output, *, architecture, python, stdlib, library_dirs):
    original, _ = guard.parse_archive(deploy.checked_bytes(original_initrd, guard.MAX_ARCHIVE))
    rescue, _ = guard.parse_archive(deploy.checked_bytes(rescue_archive, guard.MAX_ARCHIVE))
    blob, bundle, dependencies = select(original, rescue, architecture=architecture, python=python,
                                         stdlib=stdlib, library_dirs=library_dirs)
    output = deploy.new_directory(output)
    deploy.save(output, "runtime.cpio", blob)
    bundle["archive"] = {"path": str(output / "runtime.cpio"), "sha256": hashlib.sha256(blob).hexdigest()}
    deploy.save(output, "runtime-bundle.json", guard.linux.encoded(bundle))
    report = {"schema": "bpi-lab-external-bundle-build-v1", "hardware_validated": False,
              "runtime_executed": False, "original_initrd": original_initrd, "rescue_archive": rescue_archive,
              "bundle": {"path": str(output / "runtime-bundle.json"), "sha256": guard.linux.digest(bundle)},
              "archive_bytes": len(blob), "dependencies": dependencies,
              "retained_original_dependencies": [item["path"] for item in dependencies if item["path"].lstrip("/") in original],
              "builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    deploy.save(output, "bundle-build.json", guard.linux.encoded(report))
    return report


def probe(report, emulator, *, timeout=120):
    guard.fields(report, "schema hardware_validated runtime_executed original_initrd rescue_archive bundle archive_bytes "
                        "dependencies retained_original_dependencies builder_sha256")
    require(type(timeout) in (int, float) and 0 < timeout <= 600, "目標執行探測期限無效")
    require(report["schema"] == "bpi-lab-external-bundle-build-v1" and report["runtime_executed"] is False
            and report["hardware_validated"] is False, "不是未執行的救援封裝報告")
    require(report["builder_sha256"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "封裝器來源已改變")
    original, _ = guard.parse_archive(deploy.checked_bytes(report["original_initrd"], guard.MAX_ARCHIVE))
    rescue, _ = guard.parse_archive(deploy.checked_bytes(report["rescue_archive"], guard.MAX_ARCHIVE))
    bundle = guard._bundle(deploy.load(report["bundle"]))
    require(len(bundle["stdlib_dirs"]) == 1, "封裝報告不是單一明示 Python 環境")
    blob, rebuilt, dependencies = select(original, rescue, architecture=bundle["architecture"], python=bundle["python"],
                                         stdlib=bundle["stdlib_dirs"][0], library_dirs=bundle["library_dirs"])
    rebuilt["archive"] = bundle["archive"]
    require(rebuilt == bundle and dependencies == report["dependencies"] and len(blob) == report["archive_bytes"]
            and blob == deploy.checked_bytes(bundle["archive"], guard.MAX_ARCHIVE), "執行環境不能從固定來源重建")
    require(report["retained_original_dependencies"] == [item["path"] for item in dependencies
                                                         if item["path"].lstrip("/") in original], "保留原配相依的清單不符")
    additions, _ = guard.parse_archive(blob)
    entries = {**original, **additions}
    for module in (guard, guard.linux, guard.media):
        guard._put(entries, guard.PREFIX + "/" + Path(module.__file__).name, guard.entry(Path(module.__file__).read_bytes()))
    result = guard._probe(entries, bundle, emulator, timeout)
    guard._validate_probe(result, bundle)
    return {"schema": "bpi-lab-external-bundle-probe-v1", "hardware_validated": False, "runtime_executed": True,
            "build_sha256": guard.linux.digest(report), "probe": result,
            "sources": {Path(module.__file__).name: hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest()
                        for module in (guard, guard.linux, guard.media)}}


def main(argv=None):
    parser = argparse.ArgumentParser(description="從可信救援封裝擷取目標 Python 與相依項")
    for name in ("original-initrd", "rescue-archive"):
        parser.add_argument("--" + name, type=Path, required=True)
        parser.add_argument("--" + name + "-sha256", required=True)
    parser.add_argument("--architecture", choices=sorted(guard.ELF_ARCH), required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--stdlib", required=True)
    parser.add_argument("--library-dir", action="append", required=True)
    parser.add_argument("--emulator", type=Path)
    parser.add_argument("--emulator-sha256")
    parser.add_argument("--emulator-guest-base", type=lambda value: int(value, 0))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        require(args.emulator is not None or (args.emulator_sha256 is None and args.emulator_guest_base is None),
                "沒有模擬器時不得指定模擬器摘要或偏移")
        result = build({"path": str(args.original_initrd.absolute()), "sha256": args.original_initrd_sha256},
                       {"path": str(args.rescue_archive.absolute()), "sha256": args.rescue_archive_sha256},
                       args.output.absolute(), architecture=args.architecture, python=args.python,
                       stdlib=args.stdlib, library_dirs=args.library_dir)
        if args.emulator:
            emulator = {"path": str(args.emulator.absolute()), "sha256": args.emulator_sha256}
            if args.emulator_guest_base is not None:
                emulator["guest_base"] = args.emulator_guest_base
            result = probe(result, emulator)
            deploy.save(args.output.absolute(), "runtime-probe.json", guard.linux.encoded(result))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, guard.subprocess.SubprocessError):
        print(json.dumps({"status": "blocked", "hardware_validated": False,
                          "reason": "執行環境來源、相依或封裝檢查失敗"}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
