#!/usr/bin/env python3
"""準備 CM6 標準 Noble 的固定藍牙與可選 GPU 配套，不讀取歷史鏡像。"""
import argparse
import json
from pathlib import Path, PurePosixPath
import subprocess
import urllib.parse

import bpi_cm6_gpio as gpio
import bpi_k1_acceleration as acceleration
import package_bpi_cm6_bluetooth as bluetooth
import prepare_bpi_k1_vendor_rootfs as common

REPO = Path(__file__).resolve().parents[1]
GPU_NAMES = {"img-gpu-powervr", "libegl-mesa0", "libgbm1", "libgl1-mesa-dri",
             "libglapi-mesa", "libglx-mesa0", "libgbm-dev"}


def prepare(cache, gpu):
    cache = cache.absolute()
    cache.mkdir(parents=True, exist_ok=True)
    build = cache / "bluetooth-build"
    package = cache / "bluetooth-package"
    if not build.exists():
        subprocess.run(["python3", str(REPO / "tools/build_bpi_cm6_bluetooth.py"),
                        "--output", str(build)], check=True, stdout=subprocess.PIPE)
    bluetooth.verify_build(build)
    if not package.exists():
        subprocess.run(["python3", str(REPO / "tools/package_bpi_cm6_bluetooth.py"),
                        "--build-root", str(build), "--output", str(package)], check=True, stdout=subprocess.PIPE)
    manifest = json.loads((package / "package-manifest.json").read_text())
    deb = package / manifest["artifact"]
    common.bluetooth_package("bpi-cm6", deb)
    gpio_cache = cache / "gpio"
    for component, (_, _, _, builder) in gpio.RECIPES.items():
        destination = gpio_cache / component
        if not destination.exists():
            subprocess.run(["python3", str(REPO / "tools" / builder), "build",
                            "--cache", str(cache / "gpio-sources" / component),
                            "--output", str(destination)], check=True, stdout=subprocess.PIPE)
    gpio.package_records(gpio_cache)
    result = {"bluetooth_package": str(deb), "gpio_cache": str(gpio_cache), "gpu": [], "hardware_validation": "pending"}
    if gpu:
        lock_path = REPO / "config/spacemit-k1-acceleration/noble.lock.json"
        lock = acceleration.load_lock(lock_path)
        ids = [key for key in lock["profiles"]["bpi-cm6"]["packages"]
               if lock["packages"][key]["Package"] in GPU_NAMES]
        acceleration.require(len(ids) == 7, "固定 CM6 GPU 套件集合不符")
        result["verified_sources"] = acceleration.verify_sources(lock, lock_path, cache / "deb-cache", ids)
        for key in ids:
            item = lock["packages"][key]
            path = cache / "deb-cache" / PurePosixPath(item["Filename"]).name
            acceleration.obtain(lock["repository"] + urllib.parse.quote(item["Filename"], safe="/~"),
                                path, item["SHA256"], item["Size"])
            result["gpu"].append({"path": str(path), "sha256": item["SHA256"]})
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--gpu", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare(args.cache, args.gpu), ensure_ascii=False))
