#!/usr/bin/env python3
"""以記憶體內 ELF／eGON 反例驗證離線稽核器，不執行韌體或寫入產物。"""

from __future__ import annotations

from contextlib import redirect_stderr
import hashlib
import importlib.util
import io
from pathlib import Path
import struct
import unittest


MODULE = Path(__file__).resolve().parents[1] / "tools/audit_bpi_sram_build.py"
SPEC = importlib.util.spec_from_file_location("bpi_sram_audit", MODULE)
assert SPEC is not None and SPEC.loader is not None
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def config_fixture(**updates) -> bytes:
    values = {"CONFIG_" + key: "y" for key in audit.REQUIRED_Y}
    values.update({"CONFIG_" + key: "n" for key in audit.REQUIRED_N})
    values.update({
        "CONFIG_SPL_TEXT_BASE": "0x20060", "CONFIG_SPL_STACK": "0x58000",
        "CONFIG_SPL_BSS_START_ADDR": "0x48010", "CONFIG_SPL_BSS_MAX_SIZE": "0x7fe0",
        "CONFIG_SPL_MAX_SIZE": "0xffa0", "CONFIG_SPL_SYS_MALLOC_F_LEN": "0x2000",
        "CONFIG_SYS_MALLOC_F_LEN": "0x2000",
        "CONFIG_SPL_LDSCRIPT": '"arch/arm/mach-sunxi/supervisor.lds"',
        "CONFIG_DEFAULT_DEVICE_TREE": '"sun50i-h616-orangepi-zero2"', "CONFIG_FIT": "y",
    })
    values.update(updates)
    return ("\n".join(f"# {key} is not set" if value == "n" else f"{key}={value}"
                      for key, value in values.items()) + "\n").encode()


def elf_fixture(stage="spl1", *, machine=183, extra_symbol=None, text_address=None,
                bss_address=None, entry_address=None, elf_type=2, bss_size=32, segment_address=None,
                raw_size=16, bss_padding=0, text_name=".text"):
    """建立最小 ELF64 固定結構供真正的 pyelftools 解析，不替代解析器。"""
    base = 0x20060 if stage == "spl1" else 0x30000
    text_address = base if text_address is None else text_address
    raw = (struct.pack("<I", 0xD503201F) * ((raw_size + 3) // 4))[:raw_size]
    bss_address = (0x48010 if stage == "spl1" else (text_address + len(raw) + 15) & ~15) if bss_address is None else bss_address
    section_bss_address = bss_address - bss_padding
    symbols = [("_start", base, 0x12, 1), ("__bss_start", bss_address, 0x10, 2),
               ("__bss_end", bss_address + bss_size, 0x10, 2)]
    if stage == "spl1":
        symbols += [("__image_copy_start", text_address, 0x10, 1),
                    ("_image_binary_end", text_address + len(raw), 0x10, 1)]
    if extra_symbol:
        symbols.append((extra_symbol, text_address, 0x12, 1))
    names = bytearray(b"\0")
    table = bytearray(24)
    for name, value, info, index in symbols:
        offset = len(names)
        names.extend(name.encode() + b"\0")
        table.extend(struct.pack("<IBBHQQ", offset, info, 0, index, value, 0))
    section_names = bytearray(b"\0")
    name_offsets = {}
    for name in (text_name, ".bss", ".symtab", ".strtab", ".shstrtab"):
        name_offsets[name] = len(section_names)
        section_names.extend(name.encode() + b"\0")
    data = bytearray(0x100)
    text_offset = len(data)
    data.extend(raw)
    symbol_offset = len(data)
    data.extend(table)
    string_offset = len(data)
    data.extend(names)
    section_string_offset = len(data)
    data.extend(section_names)
    while len(data) % 8:
        data.append(0)
    section_offset = len(data)
    data.extend(bytes(64))
    sections = [
        (text_name, 1, 6, text_address, text_offset, len(raw), 0, 0, 4, 0),
        (".bss", 8, 3, section_bss_address, text_offset + len(raw), bss_size + bss_padding, 0, 0, 1, 0),
        (".symtab", 2, 0, 0, symbol_offset, len(table), 4, 1, 8, 24),
        (".strtab", 3, 0, 0, string_offset, len(names), 0, 0, 1, 0),
        (".shstrtab", 3, 0, 0, section_string_offset, len(section_names), 0, 0, 1, 0),
    ]
    for name, *fields in sections:
        data.extend(struct.pack("<IIQQQQIIQQ", name_offsets[name], *fields))
    ident = b"\x7fELF" + bytes((2, 1, 1, 0)) + bytes(8)
    struct.pack_into("<16sHHIQQQIHHHHHH", data, 0, ident, elf_type, machine, 1,
                     base if entry_address is None else entry_address, 64, section_offset, 0,
                     64, 56, 2, 64, 6, 5)
    address = text_address if segment_address is None else segment_address
    struct.pack_into("<IIQQQQQQ", data, 64, 1, 5, text_offset, address, address,
                     len(raw), len(raw), 16)
    struct.pack_into("<IIQQQQQQ", data, 120, 1, 6, text_offset + len(raw), section_bss_address,
                     section_bss_address, 0, bss_size + bss_padding, 1)
    return bytes(data), raw


def stamp_image(blob: bytearray) -> bytes:
    struct.pack_into("<I", blob, 12, audit.STAMP)
    checksum = sum(word[0] for word in struct.iter_unpack("<I", blob)) & 0xFFFFFFFF
    struct.pack_into("<I", blob, 12, checksum)
    return bytes(blob)


def egon_fixture(raw: bytes) -> bytes:
    size = (96 + len(raw) + 511) // 512 * 512
    image = bytearray(size)
    struct.pack_into("<I8sII4s", image, 0, 0xEA000016, b"eGON.BT0", 0, size, b"SPL\x01")
    image[96:96 + len(raw)] = raw
    return stamp_image(image)


class ConfigTests(unittest.TestCase):
    def test_minimum_profile_allows_main_dt_name_and_main_fit(self):
        result = audit.inspect_config(config_fixture(), config_fixture())
        self.assertIn("SPL_STACK_R", result["required_disabled"])

    def test_build003_stack_r_select_regression(self):
        actual = config_fixture(CONFIG_SPL_STACK_R="y")
        with self.assertRaisesRegex(audit.AuditError, "CONFIG_SPL_STACK_R"):
            audit.inspect_config(config_fixture(), actual)
        with self.assertRaisesRegex(audit.AuditError, "CONFIG_SPL_STACK_R"):
            audit.inspect_config(actual, actual)

    def test_enabled_dram_and_normal_boot_are_rejected_even_in_snapshot(self):
        for key in ("CONFIG_DRAM_SUN50I_H616", "CONFIG_SPL_OF_CONTROL", "CONFIG_SPL_LOAD_FIT",
                    "CONFIG_SPL_MMC_WRITE", "CONFIG_SPL_SYS_MALLOC", "CONFIG_SPL_RAM_SUPPORT"):
            with self.subTest(key=key), self.assertRaises(audit.AuditError):
                config = config_fixture(**{key: "y"})
                audit.inspect_config(config, config)

    def test_sram_addresses_and_budget_are_checked(self):
        for key, value in (("CONFIG_SPL_STACK", "0x4ff80000"), ("CONFIG_SPL_BSS_START_ADDR", "0x40000000"),
                           ("CONFIG_SPL_SYS_MALLOC_F_LEN", "0x3000"), ("CONFIG_SPL_TEXT_BASE", "0x20000")):
            with self.subTest(key=key), self.assertRaises(audit.AuditError):
                config = config_fixture(**{key: value})
                audit.inspect_config(config, config)

    def test_numeric_equivalence_and_duplicate_rejection(self):
        audit.inspect_config(config_fixture(), config_fixture(CONFIG_SPL_STACK=str(0x58000)))
        with self.assertRaisesRegex(audit.AuditError, "重複"):
            audit.parse_config(b"CONFIG_A=y\nCONFIG_A=n\n")


class ElfTests(unittest.TestCase):
    def test_both_valid_elfs_are_structurally_parsed(self):
        for stage in ("spl1", "spl2"):
            with self.subTest(stage=stage):
                blob, raw = elf_fixture(stage)
                result = audit.inspect_elf(blob, raw, stage)
                self.assertEqual(result["machine"], "EM_AARCH64")
                self.assertEqual(result["raw_bytes"], len(raw))

    def test_wrong_architecture_entry_and_type_are_rejected(self):
        for kwargs in ({"machine": 40}, {"entry_address": 0x20064}, {"elf_type": 3}):
            with self.subTest(kwargs=kwargs), self.assertRaises(audit.AuditError):
                audit.inspect_elf(*elf_fixture(**kwargs), "spl1")

    def test_bss_and_load_extent_outside_sram_are_rejected(self):
        for kwargs in ({"bss_address": 0x40000000}, {"bss_address": 0x4FFF0},
                       {"text_address": 0x30000}, {"segment_address": 0x40000000}):
            with self.subTest(kwargs=kwargs), self.assertRaises(audit.AuditError):
                audit.inspect_elf(*elf_fixture(**kwargs), "spl1")
        with self.assertRaises(audit.AuditError):
            audit.inspect_elf(*elf_fixture("spl2", bss_address=0x3FFF0), "spl2")

    def test_raw_file_tampering_and_extra_bytes_are_rejected(self):
        blob, raw = elf_fixture()
        for changed in (raw[:-1], raw + b"\0", bytes([raw[0] ^ 1]) + raw[1:]):
            with self.subTest(raw=changed), self.assertRaisesRegex(audit.AuditError, "裸二進位"):
                audit.inspect_elf(blob, changed, "spl1")

    def test_forbidden_symbols_with_optimizer_suffix_are_rejected(self):
        for name in ("mctl_init", "sunxi_dram_init", "board_init_r", "clock_init_safe",
                     "spl_mmc_load_image", "spl_ymodem_load_image", "dram_init.isra.0",
                     "_u_boot_list_2_spl_image_loader_2_mmc"):
            with self.subTest(name=name), self.assertRaisesRegex(audit.AuditError, "禁止"):
                audit.inspect_elf(*elf_fixture(extra_symbol=name), "spl1")
        audit.inspect_elf(*elf_fixture(extra_symbol="_u_boot_list_2_blk_driver_2_mmc"), "spl1")

    def test_missing_symbols_are_not_treated_as_absence_of_dram(self):
        blob, raw = elf_fixture()
        changed = bytearray(blob)
        offset = struct.unpack_from("<Q", changed, 40)[0]
        struct.pack_into("<I", changed, offset + 3 * 64 + 4, 1)
        with self.assertRaisesRegex(audit.AuditError, "符號表"):
            audit.inspect_elf(bytes(changed), raw, "spl1")

    def test_zero_length_smoke_bss_is_allowed(self):
        audit.inspect_elf(*elf_fixture("spl2", bss_size=0), "spl2")

    def test_bss_prefix_padding_outside_symbols_is_rejected(self):
        for size in (0, 32):
            with self.subTest(size=size), self.assertRaisesRegex(audit.AuditError, "BSS 區段與符號邊界"):
                audit.inspect_elf(*elf_fixture("spl2", bss_size=size, raw_size=23, bss_padding=9), "spl2")
        with self.assertRaises(audit.AuditError):
            audit.inspect_elf(*elf_fixture("spl2", bss_size=0, raw_size=23, bss_padding=25), "spl2")

    def test_external_bss_alignment_gap_is_allowed(self):
        for size in (0, 32):
            with self.subTest(size=size):
                audit.inspect_elf(*elf_fixture("spl2", bss_size=size, raw_size=23, bss_padding=0), "spl2")

    def test_build005_unlisted_allocated_sections_are_still_required(self):
        for name in (".vectors", ".blk_drivers"):
            blob, raw = elf_fixture(text_name=name)
            with self.subTest(name=name), self.assertRaisesRegex(audit.AuditError, name):
                audit.inspect_elf(blob, bytes(len(raw)), "spl1")


class ReportTests(unittest.TestCase):
    def fixture(self):
        return {
            "status": "離線建置完成，尚未實板驗證", "inputs_unchanged": True,
            "source": {"commit": "1" * 40, "expected_commit": "1" * 40,
                       "tree": "2" * 40, "expected_tree": "2" * 40,
                       "archive": {"sha256": "3" * 64, "bytes": 1},
                       "expected_archive_sha256": "3" * 64},
            "commands": [{"argv": ["/usr/bin/make", "spl/sunxi-spl.bin"], "returncode": 0}],
            "environment": {"SOURCE_DATE_EPOCH": "1789401600", "KBUILD_BUILD_USER": "bpi",
                            "KBUILD_BUILD_HOST": "bpi", "LC_ALL": "C", "GIT_NO_LAZY_FETCH": "1"},
        }

    def test_fixed_new_epoch_and_failed_build_rejection(self):
        report = self.fixture()
        self.assertEqual(audit.report_metadata(report)["source_date_epoch"], "1789401600")
        report["environment"]["SOURCE_DATE_EPOCH"] = "1789430400"
        with self.assertRaisesRegex(audit.AuditError, "SOURCE_DATE_EPOCH"):
            audit.report_metadata(report)
        report = self.fixture()
        report["commands"][0]["returncode"] = 1
        with self.assertRaisesRegex(audit.AuditError, "失敗"):
            audit.report_metadata(report)

    def test_main_uboot_target_is_rejected(self):
        report = self.fixture()
        report["commands"].append({"argv": ["/usr/bin/make", "all"], "returncode": 0})
        with self.assertRaisesRegex(audit.AuditError, "正常 U-Boot"):
            audit.report_metadata(report)


class EgonTests(unittest.TestCase):
    def setUp(self):
        _, self.raw = elf_fixture()
        self.image = egon_fixture(self.raw)
        self.config = audit.parse_config(config_fixture())

    def test_header_branch_checksum_and_payload(self):
        result = audit.inspect_egon(self.image, self.raw, self.config)
        self.assertEqual(result["header_bytes"], 96)
        self.assertEqual(result["branch_target"], "0x20060")
        self.assertEqual(result["image_bytes"], 512)

    def test_checksum_magic_branch_and_length_errors(self):
        for offset, value in ((12, 0), (4, 0), (0, 0xEA000015), (16, 1024), (0, 0x14000018)):
            with self.subTest(offset=offset, value=value), self.assertRaises(audit.AuditError):
                image = bytearray(self.image)
                struct.pack_into("<I", image, offset, value)
                audit.inspect_egon(bytes(image), self.raw, self.config)

    def test_correct_checksum_does_not_hide_bad_body_or_padding(self):
        for offset in (96, len(self.image) - 1):
            with self.subTest(offset=offset), self.assertRaises(audit.AuditError):
                image = bytearray(self.image)
                image[offset] ^= 1
                audit.inspect_egon(stamp_image(image), self.raw, self.config)

    def test_appended_payload_and_sram_overflow_are_rejected(self):
        with self.assertRaises(audit.AuditError):
            audit.inspect_egon(self.image + bytes(512), self.raw, self.config)
        raw = bytes(0x10000)
        with self.assertRaisesRegex(audit.AuditError, "超出"):
            audit.inspect_egon(egon_fixture(raw), raw, self.config)

    def test_header_dt_name_does_not_mean_dtb(self):
        image = bytearray(self.image)
        name = b"sun50i-h616-orangepi-zero2"
        image[23] = 2
        struct.pack_into("<I", image, 32, 44)
        image[44:44 + len(name)] = name
        result = audit.inspect_egon(stamp_image(image), self.raw, self.config)
        self.assertEqual(result["dt_name"], name.decode())


class EvidenceTests(unittest.TestCase):
    def test_hash_is_computed_from_content_not_a_known_good_artifact(self):
        data = b"arbitrary-test-data"
        record = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        audit.check_digest(record, hashlib.sha256(data).hexdigest(), len(data), "測試")
        with self.assertRaisesRegex(audit.AuditError, "不符"):
            audit.check_digest(record, hashlib.sha256(data + b"x").hexdigest(), len(data), "測試")

    def test_absolute_and_traversing_report_paths_are_rejected(self):
        for name in ("/etc/passwd", "../outside", "a/../../outside", "a//b", "", "a\\b"):
            with self.subTest(name=name), self.assertRaises(audit.AuditError):
                audit.relative_name(name)

    def test_readonly_evidence_rejects_toolchain_symlink(self):
        link = Path("/usr/bin/aarch64-linux-gnu-gcc")
        if not link.is_symlink():
            self.skipTest("此環境未提供既存符號連結")
        evidence = audit.Evidence(Path("/usr/bin"))
        with self.assertRaises(OSError):
            evidence.read(link.name)

    def test_chinese_cli_and_required_directory(self):
        parser = audit.build_parser()
        self.assertIn("用法：", parser.format_help())
        self.assertIn("選項", parser.format_help())
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args([])


if __name__ == "__main__":
    unittest.main()
