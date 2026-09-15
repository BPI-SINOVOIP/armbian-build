#!/usr/bin/env python3
"""依序執行 SRAM 離線稽核與回歸，將命令、輸出及雜湊封存於新目錄。"""

import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import bpi_sram_package as package


ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    "test_bpi_sram_build.py", "test_bpi_sram_audit.py", "test_bpi_sram_package.py",
    "test_bpi_sram_core.py", "test_bpi_sram_uart.py", "test_bpi_sram_execution.py",
    "test_bpi_sram_driver_model.py", "test_bpi_sram_sd_minimal.py",
    "test_bpi_sram_spl1_upgrade.py",
)


def digest(path):
    return hashlib.sha256(package.read_regular_file(path, 16 * 1024 * 1024)).hexdigest()


def main():
    parser = package.ChineseArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True, help="既有建置目錄")
    parser.add_argument("--output-dir", type=Path, required=True, help="尚不存在的驗證輸出目錄")
    parser.add_argument("--ddr-build-dir", type=Path, help="新版 DDR 契約必填的獨立負載建置目錄")
    args = parser.parse_args()
    report = {"status": "未通過", "hardware_validation": "未執行", "commands": []}
    output = args.output_dir.absolute()
    try:
        paths = [output, args.build_dir.absolute()]
        if args.ddr_build_dir is not None:
            paths.append(args.ddr_build_dir.absolute())
        for path in paths:
            if ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
                raise ValueError("目錄不得包含 .. 或經過符號連結")
        build = args.build_dir.absolute()
        report["build_report_sha256"] = digest(build / "build-report.json")
        metadata = json.loads(package.read_regular_file(build / "build-report.json", 16 * 1024 * 1024))
        if not isinstance(metadata, dict):
            raise ValueError("建置報告必須是 JSON 物件")
        ddr_v2 = metadata.get("ddr_v2", False)
        if type(ddr_v2) is not bool or ddr_v2 != (args.ddr_build_dir is not None):
            raise ValueError("新版 DDR 建置必須同時指定 --ddr-build-dir；舊版不得套用 DDR 執行測試")
        tests = TESTS
        ddr_build = None
        if ddr_v2:
            ddr_build = args.ddr_build_dir.absolute()
            report["ddr_build_report_sha256"] = digest(ddr_build / "build-report.json")
            tests = tuple("test_bpi_sram_v2_execution.py" if name == "test_bpi_sram_execution.py" else name
                          for name in TESTS) + (
                "test_bpi_sram_uart_faults.py", "test_bpi_sram_ddr_contract.py",
                "test_bpi_sram_ddr_uart.py", "test_bpi_sram_ddr_payload.py", "test_bpi_sram_xmodem_deadline.py",
                "test_bpi_sram_validation.py", "test_bpi_sram_v2_uart.py",
            )
        report["dependencies"] = {name: importlib.metadata.version(name)
                                  for name in ("unicorn", "pyelftools", "pyserial")}
        if report["dependencies"]["unicorn"] != "2.1.4":
            raise ValueError("本版執行模型限定 unicorn==2.1.4")
        scripts = [ROOT / "tools/audit_bpi_sram_build.py", *(ROOT / "tests" / name for name in tests)]
        sources = sorted(set(scripts + [Path(__file__).resolve(), ROOT / "tests/test_bpi_sram_core.c",
                     ROOT / "tests/test_bpi_sram_execution.py",
                     *ROOT.glob("tools/*bpi_sram*.py"),
                     *ROOT.glob("patch/lab/u-boot/bananapim4zero/sram-supervisor/*"),
                     *ROOT.glob("patch/lab/u-boot/bananapim4zero/sram-ddr/*")]))
        report["source_sha256"] = {str(p.relative_to(ROOT)): digest(p) for p in sources}
        driver_sources = ("drivers/mmc/sunxi_mmc.c", "drivers/mmc/sunxi_mmc.h",
                          "drivers/serial/ns16550.c", "include/mmc.h", "include/ns16550.h",
                          "arch/arm/mach-sunxi/supervisor.c", "common/xyzModem.c")
        report["driver_source_sha256"] = {name: digest(build / "source" / name) for name in driver_sources}
        output.mkdir(mode=0o700)
        environment = dict(os.environ, BPI_SRAM_BUILD=str(build), PYTHONDONTWRITEBYTECODE="1",
                           PYTHONHASHSEED="0", LC_ALL="C")
        if ddr_build is not None:
            environment["BPI_DDR_BUILD_DIR"] = str(ddr_build)
        success = True
        for index, script in enumerate(scripts):
            argv = [sys.executable, "-B", str(script)]
            if index == 0:
                argv.extend(["--build-dir", str(build)])
            destination = output / ("audit.json" if index == 0 else script.stem + ".log")
            started = time.monotonic()
            with destination.open("xb") as stream:
                try:
                    result = subprocess.run(argv, cwd=ROOT, env=environment,
                                            stdin=subprocess.DEVNULL, stdout=stream,
                                            stderr=subprocess.STDOUT, timeout=180, check=False)
                    code = result.returncode
                except subprocess.TimeoutExpired:
                    code = 124
            record = {"argv": argv, "exit_code": code, "log": destination.name,
                      "sha256": digest(destination), "elapsed_seconds": round(time.monotonic() - started, 3)}
            report["commands"].append(record)
            print(f"{script.name}：{'通過' if code == 0 else '未通過'}", flush=True)
            success &= code == 0
            if index == 0 and code:
                break
        stable = all(digest(ROOT / name) == sha for name, sha in report["source_sha256"].items())
        stable &= digest(build / "build-report.json") == report["build_report_sha256"]
        if ddr_build is not None:
            stable &= digest(ddr_build / "build-report.json") == report["ddr_build_report_sha256"]
        stable &= all(digest(build / "source" / name) == sha
                      for name, sha in report["driver_source_sha256"].items())
        report["inputs_unchanged"] = stable
        report["status"] = "離線驗證通過，尚未實板驗證" if success and stable else "未通過"
        package.write_new_regular_file(output / "validation-report.json",
                                       (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode())
        return 0 if success and stable else 1
    except (OSError, ValueError, package.PackageError, importlib.metadata.PackageNotFoundError) as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
