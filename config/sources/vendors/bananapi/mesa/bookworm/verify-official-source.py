"""核定官方簽章、來源索引與完整來源包；不接受忽略驗簽選項。"""

import datetime
from email.utils import parsedate_to_datetime
import hashlib
import lzma
from pathlib import Path
import subprocess

from debian.deb822 import Deb822, Dsc

VERSION = "22.3.6-1+deb12u2"
EXPECTED = {
    "mesa_22.3.6-1+deb12u2.dsc": (5888, "bd1034d3d29ef9e1e638bb5645a0ca31426bc2bcbb60842cbad2adcccffb4a00"),
    "mesa_22.3.6.orig.tar.gz": (27497916, "737cb8e5d380ce1a0ad01fb8b74db7f27303435c353e753d2302e8cf3927bad2"),
    "mesa_22.3.6-1+deb12u2.diff.gz": (116941, "a6045acd53bc400eafcf8b2aba94df5728378df27b6e61154784b00eaec2ec1e"),
}


def checksums(value):
    result = {}
    for line in value.splitlines():
        if not line.strip():
            continue
        sha, size, name = line.split()
        if name in result:
            raise ValueError("重複校驗欄位")
        result[name] = (int(size), sha)
    return result


def verify(name, expected):
    path = Path(name)
    with path.open("rb") as stream:
        actual = (path.stat().st_size, hashlib.file_digest(stream, "sha256").hexdigest())
    if actual != expected:
        raise ValueError("大小或 SHA 不符：" + name)


def main():
    result = subprocess.run(["gpgv", "--keyring", "/usr/share/keyrings/debian-archive-keyring.gpg",
        "--status-fd", "1", "--output", "Release", "InRelease"], check=True, text=True, capture_output=True)
    if "[GNUPG:] VALIDSIG " not in result.stdout or any(flag in result.stdout for flag in ("EXPKEYSIG", "REVKEYSIG", "BADSIG", "ERRSIG")):
        raise ValueError("封存庫簽章不符合要求")
    release = Deb822(Path("Release").read_text())
    if release["Codename"] != "bookworm":
        raise ValueError("封存庫版本不同")
    now = datetime.datetime.now(datetime.timezone.utc)
    if parsedate_to_datetime(release["Date"]) > now + datetime.timedelta(minutes=10):
        raise ValueError("封存庫日期位於未來")
    if release.get("Valid-Until") and parsedate_to_datetime(release["Valid-Until"]) < now:
        raise ValueError("封存庫索引已過有效期限")
    verify("Sources.xz", checksums(release["SHA256"])["main/source/Sources.xz"])
    with lzma.open("Sources.xz", "rt") as stream:
        matches = [item for item in Deb822.iter_paragraphs(stream) if item.get("Package") == "mesa" and item.get("Version") == VERSION]
    if len(matches) != 1 or matches[0]["Directory"] != "pool/main/m/mesa":
        raise ValueError("有效簽章索引未包含唯一指定來源，停止而不放寬版本")
    if checksums(matches[0]["Checksums-Sha256"]) != EXPECTED:
        raise ValueError("索引與核定來源包集合不同")
    for name, expected in EXPECTED.items():
        verify(name, expected)
    dsc = Dsc(Path("mesa_22.3.6-1+deb12u2.dsc").read_text())
    if (dsc["Source"], dsc["Version"], dsc["Format"]) != ("mesa", VERSION, "1.0"):
        raise ValueError("來源包識別不同")
    print("官方簽章至索引、完整來源包與指定版本核對通過。")


if __name__ == "__main__":
    main()
