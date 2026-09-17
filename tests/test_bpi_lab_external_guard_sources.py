"""對固定唯讀擷取證據重播五個發行版的 initramfs；不執行映像程式。"""

import hashlib
import json
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bpi_lab_allwinner as allwinner
from tools import bpi_lab_external_guard as guard

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "output/evidence/bpi-external-init-profiles-20260918-001"
RECORD = ROOT / "docs/evidence/bpi-multiboard-integrate-20260917/external-init-profiles.json"


def audit_source(directory):
    metadata = (directory / "extraction.json").read_bytes()
    extraction = json.loads(metadata)
    assert extraction["source_verified"] is True
    assert extraction["mounted"] is False and extraction["image_code_executed"] is False
    file = extraction["files"]["/boot/uInitrd"]
    assert file["file"] == "file-0000.bin"
    blob = (directory / file["file"]).read_bytes()
    assert file["digest"] == {"bytes": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
    payload = allwinner._legacy(blob)
    entries, compression = guard.parse_archive(payload)
    profile = {key: hashlib.sha256(entries[name]["data"]).hexdigest()
               for key, name in (("init_sha256", "init"), ("functions_sha256", "scripts/functions"))}
    guard._init_order(entries, profile)
    rebuilt = guard.archive(entries, compression)
    assert guard.parse_archive(rebuilt) == (entries, compression)
    groups = {item["hardlinks"] for item in entries.values() if "hardlinks" in item}
    return {"release": directory.name, "source": extraction["source"], "source_digest": extraction["source_digest"],
            "extraction_sha256": hashlib.sha256(metadata).hexdigest(), "uinitrd_digest": file["digest"],
            "initrd_sha256": hashlib.sha256(payload).hexdigest(), "profile": profile,
            "entries": len(entries), "hardlink_groups": len(groups), "hardlink_members": sum(map(len, groups)),
            "compression": compression, "repacked_sha256": hashlib.sha256(rebuilt).hexdigest()}


class SourceProfilesTests(unittest.TestCase):
    def test_five_fixed_source_profiles_preserve_contents_and_links(self):
        root = Path(os.environ.get("BPI_LAB_INIT_PROFILE_EVIDENCE", EVIDENCE))
        if not root.is_dir():
            self.skipTest("本機沒有固定的五發行版唯讀擷取證據")
        record = json.loads(RECORD.read_text())
        self.assertEqual(len(record["samples"]), 5)
        for sample in record["samples"]:
            with self.subTest(release=sample["release"]):
                self.assertEqual(audit_source(root / sample["release"]), sample)


if __name__ == "__main__":
    unittest.main()
