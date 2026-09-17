#!/usr/bin/env python3
"""CMA 核心配置、cells 與連續空間負例；不使用實板資格。"""

import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import bpi_lab_cma as cma


CONFIG = b'''CONFIG_ARM=y
CONFIG_CMA=y
CONFIG_DMA_CMA=y
CONFIG_OF_RESERVED_MEM=y
CONFIG_CMDLINE=""
CONFIG_PAGE_SHIFT=12
CONFIG_PAGE_BLOCK_MAX_ORDER=11
CONFIG_ARCH_FORCE_MAX_ORDER=11
CONFIG_CMA_AREAS=7
CONFIG_CMA_ALIGNMENT=8
'''


class KernelPolicyTests(unittest.TestCase):
    def policy(self, blob=CONFIG, release="6.18.49-current-sunxi"):
        evidence = cma.config_evidence(blob, "arm32")
        return cma.kernel_policy(evidence["values"], release, evidence["sha256"])

    def test_pageblock_not_cma_allocation_order(self):
        self.assertEqual(self.policy()["minimum_alignment"], 0x800000)
        self.assertEqual(self.policy(CONFIG.replace(b"CMA_ALIGNMENT=8", b"CMA_ALIGNMENT=4"))["minimum_alignment"], 0x800000)
        self.assertEqual(self.policy(CONFIG.replace(b"PAGE_BLOCK_MAX_ORDER=11", b"PAGE_BLOCK_MAX_ORDER=9"))["minimum_alignment"], 0x200000)

    def test_unknown_source_version_rejected(self):
        for version in ("6.12.49-current-sunxi", "6.18.47-current-sunxi", "6.18.48-current-sunxi",
                        "6.18.50-current-sunxi", "7.1.0", "6.18.490", "6.18.460"):
            with self.subTest(version=version), self.assertRaisesRegex(cma.CMAError, "尚未審閱"):
                self.policy(release=version)

    def test_reviewed_61846_matches_61849_semantics(self):
        self.assertEqual(self.policy(release="6.18.46-current-sunxi"), self.policy())

    def test_unknown_hugepage_numa_and_cmdline_branches(self):
        for key in ("HUGETLB_PAGE", "TRANSPARENT_HUGEPAGE", "HUGETLB_PAGE_SIZE_VARIABLE", "NUMA", "DMA_NUMA_CMA", "CMDLINE_FORCE"):
            with self.subTest(key=key), self.assertRaises(cma.CMAError):
                self.policy(CONFIG + f"CONFIG_{key}=y\n".encode())

    def test_invalid_config_numbers_and_duplicates(self):
        for blob in (CONFIG + b"CONFIG_CMA=y\n", CONFIG.replace(b"CMA=y", b"CMA=m"),
                     CONFIG.replace(b"PAGE_SHIFT=12", b"PAGE_SHIFT=16"),
                     CONFIG.replace(b"PAGE_BLOCK_MAX_ORDER=11", b"PAGE_BLOCK_MAX_ORDER=12"),
                     CONFIG.replace(b"CMA_AREAS=7", b"CMA_AREAS=0"), b"", CONFIG + b"x=$(id)\n"):
            with self.subTest(blob=blob), self.assertRaises(cma.CMAError):
                self.policy(blob)

    def test_config_architecture_and_unset_conflicts(self):
        for blob in (CONFIG.replace(b"CONFIG_ARM=y", b"CONFIG_ARM64=y"),
                     CONFIG + b"# CONFIG_CMA is not set\n", CONFIG + b"CONFIG_ARM64=y\n"):
            with self.subTest(blob=blob), self.assertRaises(cma.CMAError):
                cma.parse_config(blob, "arm32")


class ReservedMemoryTests(unittest.TestCase):
    def setUp(self):
        self.policy = KernelPolicyTests().policy()
        self.node = "/reserved-memory/default-pool"
        self.props = {"/": {"#address-cells": self.cell(2), "#size-cells": self.cell(1)},
                      "/reserved-memory": {"#address-cells": self.cell(2), "#size-cells": self.cell(1), "ranges": b""},
                      self.node: {"compatible": b"shared-dma-pool\0", "reusable": b"", "linux,cma-default": b"",
                                  "size": self.cell(0x6000000), "alignment": self.cell(0x1000000, 2),
                                  "alloc-ranges": self.cell(0x100000000, 2) + self.cell(0x10000000)}}

    @staticmethod
    def cell(value, count=1):
        return value.to_bytes(4 * count, "big")

    def parse(self):
        def get(node, prop=None, *, mode=None):
            if mode == "p":
                return list(self.props[node])
            if mode == "l":
                return ["default-pool"] if node == "/reserved-memory" else []
            return self.props[node][prop]
        return cma.reserved_memory(get, ["reserved-memory"], lambda: self.policy)

    def test_alignment_uses_address_cells_and_size_uses_size_cells(self):
        requirement, = self.parse()["dynamic_cma"]
        self.assertEqual(requirement["alignment"], 0x1000000)
        self.assertEqual(requirement["alloc_ranges"], [{"start": 0x100000000, "size": 0x10000000}])
        self.props[self.node]["alignment"] = self.cell(0x1000000)
        with self.assertRaisesRegex(cma.CMAError, "cells"):
            self.parse()

    def test_size_needs_minimum_not_declared_alignment_multiple(self):
        self.props[self.node]["alignment"] = self.cell(0x8000000, 2)
        self.assertEqual(self.parse()["dynamic_cma"][0]["size"], 0x6000000)

    def test_zero_small_and_absent_alignment_use_kernel_minimum(self):
        for value in (None, 0, 0x1000):
            with self.subTest(value=value):
                if value is None:
                    self.props[self.node].pop("alignment", None)
                else:
                    self.props[self.node]["alignment"] = self.cell(value, 2)
                self.assertEqual(self.parse()["dynamic_cma"][0]["alignment"], 0x800000)

    def test_bad_ranges_unknown_properties_and_no_map(self):
        original = copy.deepcopy(self.props[self.node])
        for key, value in (("alloc-ranges", b""), ("alloc-ranges", b"\0"),
                           ("alloc-ranges", self.cell((1 << 64) - 1, 2) + self.cell(8)),
                           ("no-map", b""), ("vendor,unknown", b""), ("reusable", self.cell(1))):
            with self.subTest(key=key, value=value):
                self.props[self.node] = {**original, key: value}
                with self.assertRaises(cma.CMAError):
                    self.parse()


class CMASpaceTests(unittest.TestCase):
    def setUp(self):
        self.requirements = [{"size": 0x6000000, "alignment": 0x800000,
                              "alloc_ranges": [{"start": 0x40000000, "size": 0x10000000}]}]
        self.config = {"ram": {"banks": [{"start": 0x40000000, "size": 0x10000000}],
                               "reserved": [], "kernel_work": {"start": 0x40000000, "size": 0x1000000}},
                       "files": {}, "bootargs": ["rootwait"]}

    def validate(self):
        approval = {"requirements": copy.deepcopy(self.requirements), "qualification_sha256": "a" * 64,
                    "kernel_config_sha256": "b" * 64, "effective_dtb_sha256": "c" * 64}
        cma.validate(self.config, self.requirements, approval, config_sha256="b" * 64, dtb_sha256="c" * 64)

    def test_requirement_range_is_not_reserved_or_assigned(self):
        before = copy.deepcopy(self.config)
        self.validate()
        self.assertEqual(self.config, before)

    def test_fragmentation_not_total_free_bytes(self):
        self.config["ram"]["reserved"] = [{"start": base, "size": 0x1000000} for base in (0x44000000, 0x48000000, 0x4c000000)]
        with self.assertRaisesRegex(cma.CMAError, "連續"):
            self.validate()

    def test_alignment_rounding_and_boundary(self):
        self.config["ram"]["banks"] = [{"start": 0x42000001, "size": 0x6000000}]
        with self.assertRaises(cma.CMAError):
            self.validate()
        self.config["ram"]["banks"] = [{"start": 0x42000000, "size": 0x6000000}]
        self.validate()

    def test_all_component_capacities_excluded(self):
        self.config["ram"]["banks"] = [{"start": 0x42000000, "size": 0x6000000}]
        for role in ("kernel", "initrd", "dtb"):
            with self.subTest(role=role):
                self.config["files"] = {role: {"address": 0x42000000, "bytes": 1, "capacity": 0x800000}}
                with self.assertRaises(cma.CMAError):
                    self.validate()

    def test_kernel_work_excluded(self):
        self.config["ram"]["kernel_work"] = {"start": 0x40000000, "size": 0xc000000}
        with self.assertRaises(cma.CMAError):
            self.validate()

    def test_disjoint_banks_or_ranges_must_not_be_summed(self):
        self.config["ram"]["banks"] = [{"start": 0x42000000, "size": 0x4000000},
                                         {"start": 0x48000000, "size": 0x4000000}]
        with self.assertRaises(cma.CMAError):
            self.validate()
        self.config["ram"]["banks"] = [{"start": 0x40000000, "size": 0x10000000}]
        self.requirements[0]["alloc_ranges"] = [{"start": 0x42000000, "size": 0x4000000},
                                                {"start": 0x48000000, "size": 0x4000000}]
        with self.assertRaises(cma.CMAError):
            self.validate()

    def test_multiple_ranges_try_later_valid_range(self):
        self.requirements[0]["alloc_ranges"].insert(0, {"start": 0x20000000, "size": 0x1000000})
        self.validate()

    def test_without_alloc_ranges_uses_approved_banks(self):
        self.requirements[0]["alloc_ranges"] = []
        self.validate()

    def test_bootargs_changes_and_hyphen_alias_rejected(self):
        for arg in ("cma=0", "numa_cma=16M", "numa-cma=16M", "mem=512M", "memmap=1M@0x40000000",
                    "kernelcore=128M", "movable_node", "vmalloc=128M", "reserve_mem=16M", "hugepages=4"):
            with self.subTest(arg=arg):
                self.config["bootargs"] = [arg]
                with self.assertRaises(cma.CMAError):
                    self.validate()


if __name__ == "__main__":
    unittest.main()
