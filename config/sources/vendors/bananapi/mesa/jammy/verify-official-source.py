"""核對 Jammy 官方簽章鏈、上傳者簽章與釘選完整來源。"""

import datetime
from email.utils import parsedate_to_datetime
import hashlib
import lzma
from pathlib import Path
import subprocess

from debian.deb822 import Deb822, Dsc

VERSION = "23.2.1-1ubuntu3.1~22.04.4"
ARCHIVE_FINGERPRINT = "F6ECB3762474EDA9D21B7022871920D1991BC93C"
UPLOADER_FINGERPRINT = "50C4A0DDCF31E452CEB19B516569D855A744BE93"
EXPECTED = {
    "mesa_23.2.1.orig.tar.gz": (30631591, "c1458ab511b87d60644b2c46d67f7d9d5c0e3db691db6aae11762b1cc5b43ca0"),
    "mesa_23.2.1-1ubuntu3.1~22.04.4.diff.gz": (230800, "a870171e601fc9bf99751ce315bacf68a0d57fe4f386a22626111be5dac572c0"),
    "mesa_23.2.1-1ubuntu3.1~22.04.4.dsc": (5839, "b59a9239b335cf6096a799bb5aecf5d664762882f9ada30d79ed893dbda259df"),
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
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    if (path.stat().st_size, digest.hexdigest()) != expected:
        raise ValueError("大小或 SHA 不符：" + name)


def signature(name, keyring, fingerprint, output=None):
    command = ["gpgv", "--keyring", keyring, "--status-fd", "1"]
    if output is not None:
        command.extend(["--output", output])
    result = subprocess.run(command + [name], check=True, text=True, capture_output=True)
    statuses = [line.split() for line in result.stdout.splitlines()]
    invalid = {"EXPKEYSIG", "EXPSIG", "REVKEYSIG", "BADSIG", "ERRSIG"}
    if any(len(row) > 1 and row[0] == "[GNUPG:]" and row[1] in invalid for row in statuses):
        raise ValueError("簽章已失效或不符合要求")
    valid = [row for row in statuses if len(row) >= 11 and row[:2] == ["[GNUPG:]", "VALIDSIG"]]
    if not any(row[2] == fingerprint or (len(row) == 12 and row[11] == fingerprint) for row in valid):
        raise ValueError("未取得指定指紋的有效簽章")


def main():
    signature("InRelease", "/usr/share/keyrings/ubuntu-archive-keyring.gpg", ARCHIVE_FINGERPRINT, "Release")
    release = Deb822(Path("Release").read_text())
    if (release["Codename"], release["Suite"]) != ("jammy", "jammy-security"):
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
    dsc_name = "mesa_23.2.1-1ubuntu3.1~22.04.4.dsc"
    signature(dsc_name, str(Path("mesa-uploader.gpg").resolve()), UPLOADER_FINGERPRINT)
    dsc = Dsc(Path(dsc_name).read_text())
    if (dsc["Source"], dsc["Version"], dsc["Format"]) != ("mesa", VERSION, "1.0"):
        raise ValueError("來源包識別不同")
    print("官方簽章、上傳者指紋、完整來源包與指定版本核對通過。")


if __name__ == "__main__":
    main()
