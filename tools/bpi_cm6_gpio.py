#!/usr/bin/env python3
"""核對 CM6 GPIO 來源套件及安裝內容；只讀檔案，不接觸 GPIO。"""
from pathlib import Path, PurePosixPath
import hashlib
import io
import json
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
RECIPES = {
    "wiringpi": ("bpi-cm6-wiringpi", "3.19+gitda58b589.cm6.1", "da58b589a3ca3e44f569850f07ee17de2e294b5f", "build_bpi_cm6_wiringpi.py"),
    "rpi-gpio": ("python3-bpi-cm6-gpio", "0.7.1+cm6.1", "c04d27c86f65ed824921a457455a09d6820b9e1d", "build_bpi_cm6_rpi_gpio.py"),
}


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def regular(path):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "GPIO 輸入須為一般檔案：" + str(path))
    return path


def payload_inventory(package):
    raw = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", str(package)])
    records = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for item in archive:
            name = PurePosixPath(item.name)
            require(not name.is_absolute() and ".." not in name.parts, "GPIO 套件含越界路徑")
            relative = name.as_posix()
            if relative == "." or item.isdir():
                continue
            require(relative not in records and relative.startswith("usr/"), "GPIO 套件覆寫範圍或重複路徑不符")
            if item.isfile():
                data = archive.extractfile(item).read()
                records[relative] = {"type": "file", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                if data.startswith(b"\x7fELF"):
                    require(data[4:6] == b"\x02\x01" and int.from_bytes(data[18:20], "little") == 243,
                            "GPIO 套件包含非 riscv64 ELF：" + relative)
            elif item.issym():
                records[relative] = {"type": "symlink", "target": item.linkname}
            else:
                raise ValueError("GPIO 套件含不允許的特殊檔案：" + relative)
    require(records, "GPIO 套件內容為空")
    return records


def package_records(cache):
    cache = Path(cache)
    require(cache.is_dir() and not cache.is_symlink(), "GPIO 套件目錄不符")
    records = {}
    for directory, (name, version, commit, builder) in RECIPES.items():
        manifest_path = regular(cache / directory / "package-manifest.json")
        manifest = json.loads(manifest_path.read_text())
        require(manifest.get("schema_version") == 1 and manifest.get("package") == name and
                manifest.get("version") == version and manifest.get("architecture") == "riscv64",
                "GPIO 套件 manifest 欄位不符：" + name)
        lock_path = regular(ROOT / "config/spacemit-k1-gpio" / directory / "source-lock.json")
        lock = json.loads(lock_path.read_text())
        require(lock.get("package") == name and lock.get("version") == version and
                lock.get("architecture") == "riscv64" and lock["source"]["commit"] == commit,
                "GPIO 來源鎖身分不符：" + name)
        require(manifest.get("builder_sha256") == digest(regular(ROOT / "tools" / builder)),
                "GPIO 套件由不同版本建置工具產生，必須重新建置：" + name)
        require(manifest.get("lock_sha256") == digest(lock_path),
                "GPIO 套件與本次來源鎖不同，必須重新建置：" + name)
        expected_source = {**lock["source"], "patches": lock.get("patches", lock["source"].get("patches", []))}
        require(manifest.get("source") == expected_source,
                "GPIO 套件的來源／壓縮檔／補丁順序不符：" + name)
        for patch in expected_source["patches"]:
            relative = PurePosixPath(patch["path"])
            require(not relative.is_absolute() and ".." not in relative.parts, "GPIO 補丁路徑越界")
            require(digest(regular(lock_path.parent / relative)) == patch["sha256"],
                    "GPIO 補丁實檔已變更，拒絕沿用舊套件：" + name)
        filename = manifest["artifact"]
        require(isinstance(filename, str) and Path(filename).name == filename and filename.endswith(".deb"),
                "GPIO DEB 檔名不合法")
        package = regular(cache / directory / filename)
        require(package.stat().st_size == manifest["bytes"] and digest(package) == manifest["sha256"],
                "GPIO DEB 大小或 SHA 不符：" + name)
        fields = subprocess.check_output(["dpkg-deb", "--show", "--showformat=${Package}\t${Version}\t${Architecture}", str(package)], text=True)
        require(fields == "\t".join((name, version, "riscv64")), "GPIO DEB 控制欄位不符：" + name)
        records[name] = {"package": name, "version": version, "architecture": "riscv64", "filename": filename,
                         "bytes": package.stat().st_size, "sha256": digest(package),
                         "manifest_sha256": digest(manifest_path), "source": manifest["source"],
                         "builder_sha256": manifest["builder_sha256"], "lock_sha256": manifest["lock_sha256"],
                         "payload": payload_inventory(package)}
    return records


def paths(cache):
    return [Path(cache) / directory / json.loads(regular(Path(cache) / directory / "package-manifest.json").read_text())["artifact"]
            for directory in RECIPES]


def verify_installed(root, records):
    root = Path(root).resolve()
    for record in records.values():
        for relative, item in record["payload"].items():
            path = root / relative
            require(path.parent.resolve().is_relative_to(root), "GPIO 安裝路徑離開根系統")
            if item["type"] == "file":
                require(regular(path).stat().st_size == item["bytes"] and digest(path) == item["sha256"],
                        "GPIO 安裝後檔案不符：" + relative)
            else:
                require(path.is_symlink() and str(path.readlink()) == item["target"],
                        "GPIO 安裝後連結不符：" + relative)
    return {"packages": sorted(records), "payload_verified": True,
            "scope": "套件檔案與架構核對；未執行 GPIO 或電氣腳位驗證"}
