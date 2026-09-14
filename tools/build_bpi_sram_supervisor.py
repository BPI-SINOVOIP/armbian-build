#!/usr/bin/env python3
"""從固定 U-Boot 原版離線建置 SRAM 管理器與 smoke 負載，不操作硬體。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
from typing import Sequence


COMMIT = "25049ad560826f7dc1c4740883b0016014a59789"
TREE = "2ccf5ff0294135c081c77f6fd9e1a6e697cd527b"
ARCHIVE_SHA256 = "d4c25dae69c1d796f5dd20d2e3d8a41198483f389f5453f7dfbc68ba897f8226"
SOURCE_DATE_EPOCH = "1789401600"
REPO = Path(__file__).resolve().parents[1]
OVERLAY = REPO / "patch/lab/u-boot/bananapim4zero/sram-supervisor"
DEFCONFIG = "bananapi_m4zero_supervisor_defconfig"
SOURCE_FILES = (
    "supervisor.h", "supervisor_core.c", "supervisor.c", "supervisor_entry.S",
    "supervisor.lds",
)
INPUT_FILES = ("overlay.patch", *SOURCE_FILES, DEFCONFIG,
               "smoke_entry.S", "smoke.c", "smoke.lds")


class BuildError(ValueError):
    """來源、路徑或建置結果不符合固定建置契約。"""


class ChineseArgumentParser(argparse.ArgumentParser):
    """沿用本專案主機工具的中文參數介面。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, add_help=False, allow_abbrev=False, **kwargs)
        self._positionals.title = "位置參數"
        self._optionals.title = "選項"
        self.add_argument("-h", "--help", action="help", help="顯示說明並離開")

    def format_usage(self):
        return super().format_usage().replace("usage: ", "用法：")

    def format_help(self):
        return super().format_help().replace("usage: ", "用法：")

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "參數錯誤：必填參數缺漏、格式無效或選項不受支援；"
                  "--jobs 須為 1 至 16，請以 --help 查看說明。\n")


def jobs_value(value: str) -> int:
    if not re.fullmatch(r"[0-9]+", value) or not 1 <= int(value) <= 16:
        raise argparse.ArgumentTypeError("平行工作數必須是 1 至 16 的整數")
    return int(value)


def make_word(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_./+\-]+", value):
        raise BuildError("交給 make 的路徑或工具前綴不得含空白、非 ASCII 或命令特殊字元")
    return value


def cross_value(value: str) -> str:
    try:
        make_word(value)
        if not value.endswith("-"):
            raise BuildError("交叉編譯工具前綴必須以 - 結尾")
    except BuildError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = ChineseArgumentParser(description=__doc__)
    parser.add_argument("--source-git", type=Path, required=True, metavar="目錄",
                        help="含固定提交的本機 Git 資料目錄；禁止下載與來源寫入")
    parser.add_argument("--output", type=Path, required=True, metavar="新目錄",
                        help="父目錄須存在；拒絕既存輸出及路徑中的符號連結")
    parser.add_argument("--cross-compile", type=cross_value,
                        default="aarch64-linux-gnu-", metavar="前綴",
                        help="交叉工具前綴，預設 aarch64-linux-gnu-")
    parser.add_argument("--jobs", type=jobs_value, default=4, metavar="數量",
                        help="平行工作數，預設 4，上限 16")
    return parser


def new_output_path(path: Path, source_git: Path) -> Path:
    path = path.expanduser().absolute()
    if ".." in path.parts:
        raise BuildError("輸出路徑不得含 ..")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise BuildError(f"輸出路徑不得經過符號連結：{part}")
    if path.exists():
        raise BuildError(f"輸出已存在，拒絕覆寫：{path}")
    if not path.parent.is_dir():
        raise BuildError("輸出父目錄必須已存在")
    if path.is_relative_to(source_git) or path.is_relative_to(OVERLAY):
        raise BuildError("輸出不得位於來源 Git 或 overlay 目錄內")
    make_word(str(path))
    return path


def read_input(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > 8 * 1024 * 1024:
            raise BuildError(f"overlay 輸入必須是小於等於 8 MiB 的一般檔案：{path}")
        data = stream.read(8 * 1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
        if (len(data) != before.st_size or
                (before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
            raise BuildError(f"讀取期間 overlay 輸入已改變：{path}")
        return data


def file_record(path: Path) -> dict:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise BuildError(f"預期一般檔案：{path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"bytes": path.stat().st_size, "sha256": digest.hexdigest()}


def validate_members(members: list[tarfile.TarInfo]) -> None:
    """先驗證所有名稱與連結，再解包；不依賴不同 Python 版本的預設過濾器。"""
    entries = {}
    for member in members:
        name = member.name.rstrip("/") if member.isdir() else member.name
        parts = name.split("/")
        if (not name or any(part in ("", ".", "..", ".git") for part in parts)
                or "\\" in name or "\x00" in name):
            raise BuildError(f"封存成員路徑不安全：{member.name}")
        if name in entries:
            raise BuildError(f"封存成員重複：{name}")
        if (not (member.isdir() or member.isreg() or member.issym())
                or member.issparse() or member.type == tarfile.GNUTYPE_SPARSE):
            raise BuildError(f"封存包含不允許的類型：{name}")
        entries[name] = member
    for name, member in entries.items():
        for parent in PurePosixPath(name).parents:
            if str(parent) in entries and not entries[str(parent)].isdir():
                raise BuildError(f"封存成員穿越非目錄或符號連結：{name}")
        if member.issym():
            link = member.linkname
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), link))
            if (not link or link.startswith("/") or "\\" in link or "\x00" in link
                    or target == ".." or target.startswith("../")):
                raise BuildError(f"封存符號連結越界：{name}")
            # 固定原版不需要鏈式連結，拒絕它可避免逐層解析的歧義。
            cursor = posixpath.dirname(name)
            for part in link.split("/"):
                cursor = posixpath.normpath(posixpath.join(cursor, part))
                if cursor in entries and entries[cursor].issym():
                    raise BuildError(f"封存不允許鏈式符號連結：{name}")
            for parent in (PurePosixPath(target), *PurePosixPath(target).parents):
                if str(parent) in entries and entries[str(parent)].issym():
                    raise BuildError(f"封存不允許鏈式符號連結：{name}")
            if target not in entries:
                raise BuildError(f"封存符號連結目標不存在：{name}")


def extract_archive(archive: Path, destination: Path) -> None:
    if file_record(archive)["sha256"] != ARCHIVE_SHA256:
        raise BuildError("U-Boot 原版封存 SHA-256 不符，拒絕解包")
    with tarfile.open(archive, "r:") as bundle:
        members = bundle.getmembers()
        validate_members(members)
        destination.mkdir(mode=0o700)
        for member in members:
            target = destination / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                target.mkdir(exist_ok=True)
            elif member.isreg():
                source = bundle.extractfile(member)
                if source is None:
                    raise BuildError(f"無法讀取封存成員：{member.name}")
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o755 if member.mode & 0o111 else 0o644)
                os.utime(target, (member.mtime, member.mtime))
        # 所有一般檔案完成後才建立已驗證的內部連結。
        for member in members:
            if member.issym():
                (destination / member.name).symlink_to(member.linkname)


def build_environment(output: Path) -> dict[str, str]:
    # 不繼承 MAKEFLAGS、編譯旗標或 Git 目錄覆寫，避免污染建置與寫回原倉。
    return {
        "PATH": os.environ.get("PATH", os.defpath), "HOME": str(output),
        "TMPDIR": str(output / "tmp"), "ARCH": "arm", "LC_ALL": "C", "TZ": "UTC",
        "SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH,
        "KBUILD_BUILD_USER": "bpi", "KBUILD_BUILD_HOST": "bpi",
        "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1",
        "KCONFIG_NOSILENTUPDATE": "1",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1",
        "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CEILING_DIRECTORIES": str(output),
    }


class Runner:
    def __init__(self, output: Path, environment: dict, report: dict, log):
        self.output, self.environment, self.report, self.log = output, environment, report, log

    def run(self, argv: Sequence[str], *, cwd: Path | None = None,
            capture: bool = False, stdout: Path | None = None) -> str:
        if capture and stdout is not None:
            raise BuildError("命令輸出模式互斥")
        record = {"argv": list(argv), "cwd": str(cwd or self.output), "returncode": None,
                  "stdout": str(stdout.relative_to(self.output)) if stdout else "build.log"}
        self.report["commands"].append(record)
        self.log.write(("\n執行命令：" + json.dumps(record, ensure_ascii=False) + "\n").encode())
        self.log.flush()
        destination = stdout.open("xb") if stdout else None
        try:
            result = subprocess.run(
                list(argv), cwd=cwd or self.output, env=self.environment,
                stdin=subprocess.DEVNULL,
                stdout=destination if destination else subprocess.PIPE if capture else self.log,
                stderr=self.log, check=False,
            )
        except OSError as exc:
            raise BuildError(f"命令無法執行：{argv[0]}（errno={exc.errno}）") from exc
        finally:
            if destination:
                destination.close()
        record["returncode"] = result.returncode
        if capture:
            self.log.write(result.stdout)
        self.log.flush()
        if result.returncode:
            raise BuildError(f"命令失敗，結束碼 {result.returncode}：{argv[0]}；請查看 build.log")
        return result.stdout.decode("utf-8", errors="replace").strip() if capture else ""


def find_tool(name: str) -> str:
    found = shutil.which(name)
    if found is None:
        raise BuildError(f"找不到必要工具：{name}")
    return make_word(str(Path(found).absolute()))


def record_tool(runner: Runner, name: str, path: str) -> None:
    real = Path(path).resolve(strict=True)
    runner.report["tools"][name] = {
        "path": path, "resolved_path": str(real), **file_record(real),
        "version": runner.run([path, "--version"], capture=True),
    }


def source_identity(runner: Runner, git: list[str]) -> dict:
    if runner.run([*git, "rev-parse", "--is-bare-repository"], capture=True) not in ("true", "false"):
        raise BuildError("--source-git 必須指向有效的 Git 資料目錄")
    commit = runner.run([*git, "rev-parse", "--verify", COMMIT + "^{commit}"], capture=True)
    tree = runner.run([*git, "rev-parse", "--verify", COMMIT + "^{tree}"], capture=True)
    if (commit, tree) != (COMMIT, TREE):
        raise BuildError("U-Boot 提交或來源樹雜湊不符固定基準")
    return {"commit": commit, "tree": tree}


def copy_artifact(runner: Runner, source: Path, destination: Path) -> None:
    expected = file_record(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as src, destination.open("xb") as dst:
        shutil.copyfileobj(src, dst)
    if file_record(destination) != expected:
        raise BuildError(f"產物複製後雜湊不符：{destination}")
    runner.report["artifacts"][str(destination.relative_to(runner.output))] = expected


def audit_elf(runner: Runner, tools: dict, elf: Path, stem: str) -> None:
    for tool, options in (("objdump", ["-drwC"]), ("readelf", ["-aW"]), ("nm", ["-n", "-S"])):
        destination = runner.output / f"{stem}.{tool}.txt"
        runner.run([tools[tool], *options, str(elf)], stdout=destination)
        runner.report["artifacts"][destination.name] = file_record(destination)


def execute_build(runner: Runner, args, source_git: Path, inputs: dict[str, bytes]) -> None:
    output = runner.output
    snapshot = output / "inputs"
    snapshot.mkdir()
    for name, data in inputs.items():
        path = snapshot / name
        with path.open("xb") as stream:
            stream.write(data)
        os.utime(path, (int(SOURCE_DATE_EPOCH), int(SOURCE_DATE_EPOCH)))
        runner.report["inputs"][name] = {"source": str(OVERLAY / name), **file_record(path)}

    tools = {name: find_tool(name) for name in ("git", "make", "cc")}
    tools.update({name: find_tool(args.cross_compile + name)
                  for name in ("gcc", "ld", "as", "ar", "strip", "objcopy", "objdump", "readelf", "nm")})
    cross = tools["gcc"][:-3]
    if shutil.which(cross + "ld.bfd"):
        tools["ld.bfd"] = find_tool(cross + "ld.bfd")
    for name, path in tools.items():
        record_tool(runner, name, path)
    record_tool(runner, "python", sys.executable)
    machine = runner.run([tools["gcc"], "-dumpmachine"], capture=True)
    if not machine.startswith("aarch64"):
        raise BuildError("交叉編譯器目標不是 aarch64")
    runner.report["compiler_target"] = machine
    runner.report["compiler_version"] = runner.run([tools["gcc"], "-dumpfullversion"], capture=True)
    driver_ld = runner.run([tools["gcc"], "-print-prog-name=ld"], capture=True)
    record_tool(runner, "gcc_driver_ld", find_tool(driver_ld))
    git_options = [tools["git"], "-c", "protocol.allow=never", "-c", "gc.auto=0",
                   "-c", "maintenance.auto=false", "-c", "core.hooksPath=/dev/null",
                   "-c", "core.attributesFile=/dev/null"]
    git = [*git_options, "-c", f"safe.directory={source_git}", f"--git-dir={source_git}"]
    runner.report["source"].update(source_identity(runner, git))
    archive = output / "u-boot-original.tar"
    runner.run([*git, "archive", "--format=tar", COMMIT], stdout=archive)
    runner.report["source"]["archive"] = file_record(archive)
    source = output / "source"
    extract_archive(archive, source)
    archive.unlink()
    runner.report["source"]["archive_retained"] = False

    patch = snapshot / "overlay.patch"
    runner.run([*git_options, "apply", "--check", str(patch)], cwd=source)
    runner.run([*git_options, "apply", str(patch)], cwd=source)
    destinations = {name: source / "arch/arm/mach-sunxi" / name for name in SOURCE_FILES}
    destinations[DEFCONFIG] = source / "configs" / DEFCONFIG
    for name, destination in destinations.items():
        with destination.open("xb") as stream:
            stream.write(inputs[name])
        os.utime(destination, (int(SOURCE_DATE_EPOCH), int(SOURCE_DATE_EPOCH)))

    build = output / "build"
    build.mkdir()
    make = [tools["make"], "-C", str(source), f"O={build}", "ARCH=arm",
            f"CROSS_COMPILE={cross}", f"HOSTCC={tools['cc']}",
            f"LD={tools.get('ld.bfd', tools['ld'])}", "KCFLAGS=-fstack-usage", "V=1"]
    runner.run([*make, DEFCONFIG])
    config = build / ".config"
    runner.report["config"] = {**file_record(config), "text": config.read_text(encoding="utf-8")}
    runner.run([*make, f"-j{args.jobs}", "spl/sunxi-spl.bin"])
    runner.report["config"] = {**file_record(config), "text": config.read_text(encoding="utf-8")}
    for name, destination in (("sunxi-spl.bin", "spl1-egon.bin"), ("u-boot-spl", "spl1.elf"),
                              ("u-boot-spl.map", "spl1.map"), ("u-boot-spl.bin", "spl1.bin")):
        copy_artifact(runner, build / "spl" / name, output / destination)
    copy_artifact(runner, config, output / "spl1.config")
    audit_elf(runner, tools, output / "spl1.elf", "spl1")

    smoke = output / "smoke-build"
    smoke.mkdir()
    flags = ["-Os", "-fno-stack-protector", "-mgeneral-regs-only", "-ffreestanding",
             "-fno-builtin", "-fno-pie", "-fno-unwind-tables", "-fno-asynchronous-unwind-tables",
             "-fstack-usage", "-I", str(snapshot)]
    objects = []
    for name in ("smoke_entry.S", "smoke.c"):
        obj = smoke / (Path(name).stem + ".o")
        runner.run([tools["gcc"], *flags, "-c", str(snapshot / name), "-o", str(obj)])
        objects.append(str(obj))
    elf = output / "spl2-smoke.elf"
    runner.run([tools["gcc"], *flags, "-nostdlib", "-static", "-no-pie", "-Wl,--build-id=none",
                f"-Wl,-T,{snapshot / 'smoke.lds'}", f"-Wl,-Map,{output / 'spl2-smoke.map'}",
                *objects, "-o", str(elf)])
    runner.run([tools["objcopy"], "-O", "binary", str(elf), str(output / "spl2-smoke.bin")])
    for name in ("spl2-smoke.elf", "spl2-smoke.map", "spl2-smoke.bin"):
        runner.report["artifacts"][name] = file_record(output / name)
    audit_elf(runner, tools, elf, "spl2-smoke")
    for label, directory in (("spl1", build), ("spl2-smoke", smoke)):
        usage = sorted(directory.rglob("*.su"))
        if not usage:
            raise BuildError(f"未產生堆疊用量紀錄：{label}")
        for path in usage:
            copy_artifact(runner, path, output / "stack-usage" / label / path.relative_to(directory))
    if source_identity(runner, git) != {"commit": COMMIT, "tree": TREE}:
        raise BuildError("來源 Git 基準在建置期間已改變")
    for name, data in inputs.items():
        if read_input(OVERLAY / name) != data:
            raise BuildError(f"建置期間 overlay 已改變；產物未列為成功：{name}")
    runner.report["inputs_unchanged"] = True


def build(args, invocation: Sequence[str]) -> Path:
    source_git = args.source_git.expanduser().resolve(strict=True)
    if not source_git.is_dir():
        raise BuildError("--source-git 必須是 Git 資料目錄")
    output = new_output_path(args.output, source_git)
    inputs = {name: read_input(OVERLAY / name) for name in INPUT_FILES}
    # mkdir 排他建立，拒絕檢查後才出現的既存檔案或符號連結。
    output.mkdir(mode=0o700)
    report = {
        "status": "失敗", "hardware_validation": "尚未執行", "payload_packaged": False,
        "builder": {"path": str(Path(__file__).resolve()), **file_record(Path(__file__))},
        "invocation": list(invocation), "commands": [], "tools": {}, "inputs": {}, "artifacts": {},
        "source": {"git_dir": str(source_git), "expected_commit": COMMIT, "expected_tree": TREE,
                   "expected_archive_sha256": ARCHIVE_SHA256},
        "environment": build_environment(output),
    }
    try:
        (output / "tmp").mkdir()
        with (output / "build.log").open("xb") as log:
            runner = Runner(output, report["environment"], report, log)
            execute_build(runner, args, source_git, inputs)
        report["status"] = "離線建置完成，尚未實板驗證"
    except BaseException as exc:
        report["error"] = str(exc) if isinstance(exc, BuildError) else (
            f"建置中止（類型={type(exc).__name__}，errno={getattr(exc, 'errno', None)}）"
        )
        raise
    finally:
        if (output / "build.log").is_file():
            report["artifacts"]["build.log"] = file_record(output / "build.log")
        with (output / "build-report.json").open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
    return output


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(arguments)
    try:
        output = build(args, [sys.executable, str(Path(__file__).resolve()), *arguments])
    except BuildError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"錯誤：檔案或路徑操作失敗（errno={exc.errno}，路徑={exc.filename}）", file=sys.stderr)
        return 1
    except tarfile.TarError:
        print("錯誤：原版封存格式損壞，拒絕解包", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("建置已中止；既有輸出保留供審查，不可原地重跑。", file=sys.stderr)
        return 130
    print(f"離線建置完成：{output / 'build-report.json'}；尚未實板驗證，未封裝 payload。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
