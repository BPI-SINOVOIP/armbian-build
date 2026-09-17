#!/usr/bin/env python3
"""動態 CMA 的離線語意與空間核對；不配置實體位址、不改寫 DTB。"""

from __future__ import annotations

import hashlib
import re


class CMAError(ValueError):
    """核心、裝置樹或外部核定缺少必要依據。"""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise CMAError(code, message)


def parse_config(blob, arch):
    """只解析原配完整配置，不執行文字，也不以倉庫預設補值。"""
    require(type(blob) is bytes and 0 < len(blob) <= 1024**2,
            "kernel_config", "原配核心配置為空或超界")
    values = {}
    for line in blob.decode("utf-8").splitlines():
        unset = re.fullmatch(r"# (CONFIG_[A-Za-z0-9_]+) is not set", line)
        if unset:
            key, value = unset[1], "n"
        elif not line or line.startswith("#"):
            continue
        else:
            match = re.fullmatch(r'(CONFIG_[A-Za-z0-9_]+)=(y|m|n|[0-9]+|0x[0-9a-fA-F]+|"(?:[^"\\\x00-\x1f]|\\.)*")', line)
            require(match, "kernel_config", "原配核心配置含無效賦值")
            key, value = match.groups()
        require(key not in values, "kernel_config", "原配核心配置含重複欄位：" + key)
        values[key] = value
    require(arch in ("arm32", "arm64") and values.get("CONFIG_ARM" if arch == "arm32" else "CONFIG_ARM64") == "y"
            and values.get("CONFIG_ARM64" if arch == "arm32" else "CONFIG_ARM", "n") == "n",
            "kernel_config", "原配核心配置架構錯配")
    return values


def kernel_policy(values, release, config_sha256):
    """採已比對的 6.18.46／6.18.49 頁面區塊語意；其他版本仍須審閱。"""
    require(re.fullmatch(r"6\.18\.(?:46|49)(?:-[A-Za-z0-9_.+~-]+)?", release),
            "cma_kernel", "此核心版本的 CMA 來源尚未審閱")
    require(all(values.get(key) == "y" for key in ("CONFIG_CMA", "CONFIG_DMA_CMA", "CONFIG_OF_RESERVED_MEM")),
            "cma_kernel", "原配核心未啟用 CMA／DMA_CMA／OF_RESERVED_MEM")
    require(not any(values.get(key, "n") != "n" for key in (
        "CONFIG_HUGETLB_PAGE", "CONFIG_TRANSPARENT_HUGEPAGE", "CONFIG_HUGETLB_PAGE_SIZE_VARIABLE",
        "CONFIG_NUMA", "CONFIG_DMA_NUMA_CMA")), "cma_kernel", "大頁或 NUMA 的 CMA 分支尚未實作")
    require(values.get("CONFIG_CMDLINE") == '""'
            and all(values.get(key, "n") == "n" for key in ("CONFIG_CMDLINE_FORCE", "CONFIG_CMDLINE_EXTEND")),
            "cma_bootargs", "內建核心命令列缺少證據或可能改變 RAM／CMA 語意")

    def number(key, minimum, maximum):
        value = values.get(key, "")
        require(re.fullmatch(r"[0-9]+", value) and minimum <= int(value) <= maximum,
                "cma_kernel", "核心配置缺少有效數值：" + key)
        return int(value)

    shift = number("CONFIG_PAGE_SHIFT", 12, 16)
    require(shift == 12, "cma_kernel", "目前只審閱 4 KiB 頁面的 CMA 配置")
    order = number("CONFIG_PAGE_BLOCK_MAX_ORDER", 0, 20)
    max_order = number("CONFIG_ARCH_FORCE_MAX_ORDER", 0, 20) if "CONFIG_ARCH_FORCE_MAX_ORDER" in values else 10
    require(order <= max_order, "cma_kernel", "頁面區塊階數超過核心最大配置階數")
    number("CONFIG_CMA_AREAS", 1, 1024)
    return {"semantics": "linux-6.18.49-pageblock", "config_sha256": config_sha256,
            "page_shift": shift, "pageblock_order": order, "minimum_alignment": 1 << (shift + order)}


def config_evidence(blob, arch):
    values = parse_config(blob, arch)
    return {"sha256": hashlib.sha256(blob).hexdigest(), "values": values}


def reserved_memory(get, roots, policy):
    """get 回傳原始屬性 bytes 或節點／屬性名稱清單；工具呼叫由上層留證。"""
    parents = [name for name in roots if name.split("@", 1)[0] == "reserved-memory"]
    require(not parents or parents == ["reserved-memory"], "reserved_memory", "保留節點名稱或數量未支援")
    result = {"reserved_memory": [], "dynamic_cma": []}
    if not parents:
        return result
    parent = "/reserved-memory"
    children = get(parent, mode="l")
    if not children:
        return result
    require(len(children) <= 64, "reserved_memory", "保留節點過多")
    props = set(get(parent, mode="p"))
    require({"#address-cells", "#size-cells", "ranges"} <= props
            <= {"#address-cells", "#size-cells", "ranges", "phandle", "linux,phandle"},
            "reserved_memory", "保留節點缺少 cells／ranges 或含未支援屬性")
    require(get(parent, "ranges") == b"", "reserved_memory", "保留節點位址轉換未支援")

    def number(node, prop, cells):
        raw = get(node, prop)
        require(len(raw) == 4 * cells, "reserved_memory", f"{node}：{prop} cells 長度不符")
        return int.from_bytes(raw, "big")

    ac, sc = (number(parent, key, 1) for key in ("#address-cells", "#size-cells"))
    require(ac in (1, 2) and sc in (1, 2), "reserved_memory", "保留節點 cells 未支援")
    root_props = set(get("/", mode="p"))
    require({"#address-cells", "#size-cells"} <= root_props
            and ac == number("/", "#address-cells", 1) and sc == number("/", "#size-cells", 1),
            "reserved_memory", "保留節點 cells 必須與根節點相同")

    def spans(node, prop):
        raw = get(node, prop)
        width = 4 * (ac + sc)
        require(raw and len(raw) % width == 0 and len(raw) <= width * 64,
                "reserved_memory", f"{node}：{prop} 區間長度無效")
        regions = []
        for offset in range(0, len(raw), width):
            start = int.from_bytes(raw[offset:offset + 4 * ac], "big")
            size = int.from_bytes(raw[offset + 4 * ac:offset + width], "big")
            require(size > 0 and start + size < 1 << (32 * ac),
                    "reserved_memory", f"{node}：{prop} 為空或位址溢位")
            regions.append({"start": start, "size": size})
        return regions

    common = {"status", "phandle", "linux,phandle"}
    for name in children:
        node = parent + "/" + name
        props = set(get(node, mode="p"))
        require(not get(node, mode="l"), "reserved_memory", "保留區不得含巢狀節點")
        if "status" in props:
            require(get(node, "status") in (b"ok\0", b"okay\0"), "reserved_memory", "保留區停用或未知狀態尚未支援")
        for boolean in props & {"reusable", "linux,cma-default", "no-map"}:
            require(get(node, boolean) == b"", "reserved_memory", "保留區布林屬性不得帶值")
        if "reg" in props:
            require(props <= common | {"reg", "no-map"}, "reserved_memory", "固定保留區含未支援語意或混用動態 size")
            result["reserved_memory"].extend(spans(node, "reg"))
            continue
        required = {"compatible", "reusable", "linux,cma-default", "size"}
        require(required <= props <= required | common | {"alignment", "alloc-ranges"}
                and get(node, "compatible") == b"shared-dma-pool\0",
                "reserved_memory", "只支援預設 reusable shared-dma-pool 動態 CMA")
        context = policy()
        size = number(node, "size", sc)
        declared = number(node, "alignment", ac) if "alignment" in props else None
        require(declared is None or declared == 0 or declared & (declared - 1) == 0,
                "cma_alignment", "動態 CMA alignment 必須為零或二的冪")
        alignment = max(declared or 0, context["minimum_alignment"])
        require(size > 0 and size % context["minimum_alignment"] == 0,
                "cma_alignment", "動態 CMA size 未符合核心頁面區塊對齊")
        result["dynamic_cma"].append({"node": node, "kind": "linux-cma-default", "size": size,
                                      "declared_alignment": declared, "alignment": alignment,
                                      "alloc_ranges": spans(node, "alloc-ranges") if "alloc-ranges" in props else [],
                                      "kernel": context})
        require(len(result["dynamic_cma"]) == 1, "reserved_memory", "不得指定多個預設動態 CMA")
    return result


def validate(config, requirements, approval, *, config_sha256, dtb_sha256):
    """只核對存在足夠連續空間，不模擬 Linux 的最終分配位置或保證啟動成功。"""
    if not requirements:
        require(approval is None, "cma_approval", "DTB 沒有動態 CMA，不接受無對應需求的核定")
        return
    require(type(approval) is dict and set(approval) == {
        "requirements", "qualification_sha256", "kernel_config_sha256", "effective_dtb_sha256"}
        and approval["requirements"] == requirements
        and approval["kernel_config_sha256"] == config_sha256
        and approval["effective_dtb_sha256"] == dtb_sha256
        and isinstance(approval["qualification_sha256"], str)
        and re.fullmatch(r"[0-9a-f]{64}", approval["qualification_sha256"]),
        "cma_approval", "動態 CMA 必須逐項核定需求、核心配置、有效 DTB 及資格證據 SHA-256")
    forbidden = {"cma", "numa_cma", "cma_pernuma", "mem", "memmap", "memblock", "highmem", "vmalloc",
                 "kernelcore", "movablecore", "movable_node", "reserve_mem", "crashkernel",
                 "hugepages", "hugepagesz", "default_hugepagesz"}
    require(not any(arg.split("=", 1)[0].replace("-", "_") in forbidden for arg in config["bootargs"]),
            "cma_bootargs", "核心參數會改變已核定 RAM／CMA 語意，尚未支援")
    occupied = config["ram"]["reserved"] + [config["ram"]["kernel_work"]] + [
        {"start": item["address"], "size": item["capacity"]} for item in config["files"].values()]
    for requirement in requirements:
        free = []
        for bank in config["ram"]["banks"]:
            for region in requirement["alloc_ranges"] or [bank]:
                # memblock 不使用實體位址零；ram.boot 是可載入範圍，不是整段固定占用。
                start = max(bank["start"], region["start"], 1)
                end = min(bank["start"] + bank["size"], region["start"] + region["size"])
                if start < end:
                    free.append((start, end))
        for area in occupied:
            start, end = area["start"], area["start"] + area["size"]
            remaining = []
            for left, right in free:
                if max(left, start) >= min(right, end):
                    remaining.append((left, right))
                else:
                    if left < start:
                        remaining.append((left, start))
                    if end < right:
                        remaining.append((end, right))
            free = remaining
        alignment = requirement["alignment"]
        require(any((left + alignment - 1) // alignment * alignment + requirement["size"] <= right
                    for left, right in free), "cma_space", "核定 RAM 扣除保留區、核心工作區及組件容量後無足夠連續 CMA 空間")
