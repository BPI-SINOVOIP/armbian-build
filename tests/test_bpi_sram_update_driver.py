#!/usr/bin/env python3
"""抽取更新器真實 MMC 函式驗證 PIO 與截止時間；僅替代 MMIO、時基及非 DM 接口。"""

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest

from test_bpi_sram_driver_model import TYPES, c_definition, c_macros


PREAMBLE = r"""
#include <errno.h>
#include <limits.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <linux/types.h>
#define CONFIG_BPI_SRAM_SUPERVISOR 1
#define CONFIG_BPI_SRAM_UPDATE 1
#define CONFIG_SPL_BUILD 1
#define CONFIG_SUN50I_GEN_H6 1
#define CONFIG_IS_ENABLED(name) CONFIG_ ## name
#define IS_ENABLED(name) (name)
#define min(a, b) ((a) < (b) ? (a) : (b))
#define debug(...) do { } while (0)
#define dmb() do { } while (0)
#define mmc_trace_state(mmc, cmd) do { } while (0)
#define pr_err(...) do { } while (0)
#include "sunxi_mmc.h"

/* 主機只保留所測函式需要的成員，不宣稱與 AArch64 結構布局相同。 */
struct gpio_desc { int unused; };
struct mmc_config { int unused; };
struct mmc { void *priv; unsigned int rca; };
static struct sunxi_mmc registers;
static ulong now, sd_start, command_start;
static unsigned reads, writes, fifo_reads, fifo_writes, cmd_writes, resets;
static unsigned fifo_full, read_empty, read_zero, irq_stuck, clock_stuck, card_busy;
static unsigned cmd13_count, cmd13_delay, cmd13_error, ready_after = 1;
static unsigned dma_writes, dma_enabled, ahb_seen, data_command, sequence_ok = 1;

static void tick(void)
{
    if (++reads > 100000) {
        fputs("模型讀取超過上限，真實函式可能未有界返回\n", stderr);
        exit(90);
    }
    now++;
}

static ulong get_timer(ulong base) { return now - base; }
static void udelay(ulong us) { now += (us + 999) / 1000; }
static int mmc_host_is_spi(struct mmc *mmc) { return 0; }
static int mmc_wait_dat0(struct mmc *mmc, int state, int timeout_us) { return -ENOSYS; }
"""


MMIO = r"""
static u32 readl(const volatile u32 *address)
{
    tick();
    if (address == &registers.cmd)
        return clock_stuck ? SUNXI_MMC_CMD_START : 0;
    if (address == &registers.rint) {
        if (irq_stuck)
            return 0;
        if ((registers.cmd & 63) == MMC_CMD_SEND_STATUS) {
            if (cmd13_error)
                return SUNXI_MMC_RINT_RESP_TIMEOUT;
            if (get_timer(command_start) < cmd13_delay)
                return 0;
        }
        return SUNXI_MMC_RINT_COMMAND_DONE | SUNXI_MMC_RINT_DATA_OVER |
               SUNXI_MMC_RINT_AUTO_COMMAND_DONE;
    }
    if (address == &registers.resp0)
        return cmd13_count >= ready_after ? MMC_STATUS_RDY_FOR_DATA : MMC_STATE_PRG;
    if (address == &registers.status) {
        u32 value = card_busy ? SUNXI_MMC_STATUS_CARD_DATA_BUSY : 0;
        if (fifo_full)
            value |= SUNXI_MMC_STATUS_FIFO_FULL;
        if (read_empty)
            value |= SUNXI_MMC_STATUS_FIFO_EMPTY;
        if (!read_zero && !read_empty)
            value |= 63U << 17;
        return value;
    }
    if (address == &registers.fifo)
        return 0x60000000U + fifo_reads++;
    return *address;
}

static void writel(u32 value, volatile u32 *address)
{
    writes++;
    if (address == &registers.cmd) {
        cmd_writes++;
        if (!(value & SUNXI_MMC_CMD_UPCLK_ONLY)) {
            command_start = now;
            if ((value & 63) == MMC_CMD_SEND_STATUS)
                cmd13_count++;
            if (value & SUNXI_MMC_CMD_DATA_EXPIRE)
                data_command = value;
        }
    }
    if (address == &registers.fifo) {
        sequence_ok &= value == 0x60000000U + fifo_writes;
        fifo_writes++;
    }
    if (address == &registers.dmac || address == &registers.dlba ||
        address == &registers.idst || address == &registers.idie)
        dma_writes++;
    if (address == &registers.gctrl) {
        dma_enabled |= !!(value & SUNXI_MMC_GCTRL_DMA_ENABLE);
        ahb_seen |= !!(value & SUNXI_MMC_GCTRL_ACCESS_BY_AHB);
        if ((value & SUNXI_MMC_GCTRL_RESET) == SUNXI_MMC_GCTRL_RESET)
            resets++;
        *address = value & ~SUNXI_MMC_GCTRL_RESET;
        return;
    }
    *address = value;
}

static void setbits_le32(volatile u32 *address, u32 bits)
{
    writel(readl(address) | bits, address);
}
#define readl_relaxed(address) readl(address)
"""


BRIDGE = r"""
static int mmc_send_cmd(struct mmc *mmc, struct mmc_cmd *cmd, struct mmc_data *data)
{
    return sunxi_mmc_send_cmd_common(mmc->priv, mmc, cmd, data);
}
"""


MAIN = r"""
int main(int argc, char **argv)
{
    const char *name = argc > 1 ? argv[1] : "";
    unsigned words = argc > 2 ? (unsigned)strtoul(argv[2], NULL, 10) : 128;
    if (!words || words > 256)
        return 2;
    u32 *buffer = calloc(words + 2, sizeof(*buffer));
    if (!buffer)
        return 3;
    buffer[0] = buffer[words + 1] = 0xa55a1234;
    for (unsigned i = 0; i < words; i++)
        buffer[i + 1] = 0x60000000U + i;
    struct sunxi_mmc_priv priv = {.reg = &registers};
    struct mmc card = {.priv = &priv, .rca = 1};
    struct mmc_data data = {
        .src = (const char *)(buffer + 1), .flags = MMC_DATA_WRITE,
        .blocks = words == 256 ? 2 : 1,
        .blocksize = words == 256 ? 512 : words * sizeof(*buffer),
    };
    struct mmc_cmd cmd = {
        .cmdidx = words == 256 ? MMC_CMD_WRITE_MULTIPLE_BLOCK : MMC_CMD_WRITE_SINGLE_BLOCK,
        .resp_type = MMC_RSP_R1,
    };
    int result, before_result = 0;
    if (strstr(name, "deadline"))
        now = 9975;
    if (strstr(name, "wrap"))
        now = sd_start = ULONG_MAX - 15;
    ulong start = now;
    if (!strcmp(name, "pio_write")) {
        result = mmc_trans_data_by_cpu(&priv, &card, &data);
    } else if (!strcmp(name, "write_command")) {
        result = sunxi_mmc_send_cmd_common(&priv, &card, &cmd, &data);
    } else if (!strcmp(name, "pio_read")) {
        data.flags = MMC_DATA_READ;
        result = mmc_trans_data_by_cpu(&priv, &card, &data);
        for (unsigned i = 0; i < words; i++)
            sequence_ok &= buffer[i + 1] == 0x60000000U + i;
    } else if (!strncmp(name, "fifo_", 5)) {
        if (strstr(name, "write_full"))
            fifo_full = 1;
        else {
            data.flags = MMC_DATA_READ;
            read_empty = !!strstr(name, "empty");
            read_zero = !read_empty;
        }
        result = sunxi_mmc_send_cmd_common(&priv, &card, &cmd, &data);
    } else if (!strncmp(name, "busy_", 5)) {
        card_busy = clock_stuck = 1;
        cmd.resp_type = MMC_RSP_R1b;
        result = sunxi_mmc_send_cmd_common(&priv, &card, &cmd, NULL);
    } else if (!strncmp(name, "cmd13_", 6)) {
        ready_after = UINT_MAX;
        if (strstr(name, "irq") || strstr(name, "retry"))
            irq_stuck = clock_stuck = 1;
        if (strstr(name, "slow")) {
            cmd13_delay = 900;
            ready_after = 2;
        }
        if (strstr(name, "errors"))
            cmd13_error = 1;
        result = mmc_poll_for_busy(&card, strstr(name, "invalid") ? 0 : 1000);
        if (strstr(name, "retry")) {
            before_result = result;
            irq_stuck = clock_stuck = 0;
            ready_after = 1;
            sd_start = now;
            result = mmc_poll_for_busy(&card, 1000);
        }
    } else if (!strcmp(name, "expired_write")) {
        now = 10000;
        result = sunxi_mmc_send_cmd_common(&priv, &card, &cmd, &data);
    } else {
        fputs("未知的更新器驅動測例\n", stderr);
        free(buffer);
        return 2;
    }
    printf("{\"result\":%d,\"before_result\":%d,\"elapsed\":%lu,"
           "\"reads\":%u,\"writes\":%u,\"fifo_reads\":%u,\"fifo_writes\":%u,"
           "\"cmd13_count\":%u,\"resets\":%u,\"sequence_ok\":%u,\"guards\":%d,"
           "\"dma_writes\":%u,\"dma_enabled\":%u,\"ahb_seen\":%u,"
           "\"write_bit\":%u,\"auto_stop\":%u,\"poll_active\":%u}\n",
           result, before_result, get_timer(start), reads, writes, fifo_reads, fifo_writes,
           cmd13_count, resets, sequence_ok,
           buffer[0] == 0xa55a1234 && buffer[words + 1] == 0xa55a1234,
           dma_writes, dma_enabled, ahb_seen,
           !!(data_command & SUNXI_MMC_CMD_WRITE), !!(data_command & SUNXI_MMC_CMD_AUTO_STOP),
           update_poll_active);
    free(buffer);
    return 0;
}
"""


class UpdateDriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build = os.environ.get("BPI_UPDATE_BUILD")
        if not build:
            raise unittest.SkipTest("請以 BPI_UPDATE_BUILD 指定已完成的更新器建置")
        cls.build = Path(build).resolve()
        report = json.loads((cls.build / "build-report.json").read_text(encoding="utf-8"))
        if not report.get("inputs_unchanged") or not report.get("payload_packaged") or report.get("kind") != 3:
            raise AssertionError("指定目錄不是已完成的第三版更新器建置")
        source = cls.build / "source"
        driver = source / "drivers/mmc/sunxi_mmc.c"
        core = source / "drivers/mmc/mmc.c"
        updater = source / "arch/arm/mach-sunxi/supervisor.c"
        header = source / "include/mmc.h"
        for path in (driver, core, updater):
            data = path.read_bytes()
            expected = report["adapted_sources"][str(path.relative_to(source))]
            if expected != {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}:
                raise AssertionError(f"所測來源與建置報告不符：{path}")
        for flag in ("BPI_SRAM_UPDATE", "BPI_SRAM_LAB_V3", "SPL_MMC_WRITE"):
            if f"CONFIG_{flag}=y" not in report["config"]["text"].splitlines():
                raise AssertionError(f"所測建置未啟用 CONFIG_{flag}")
        cls.temporary = tempfile.TemporaryDirectory(prefix="bpi-sram-update-driver-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.environment = dict(os.environ, TMPDIR=str(cls.directory))
        (cls.directory / "linux").mkdir()
        (cls.directory / "linux/types.h").write_text(TYPES, encoding="utf-8")
        declarations = re.findall(
            r"^static (?:ulong|unsigned int|bool) update_poll_\w+;", core.read_text(encoding="utf-8"), re.MULTILINE,
        )
        if len(declarations) != 3:
            raise AssertionError("真實 generic MMC 缺少完整寫後輪詢狀態")
        parts = [PREAMBLE, c_macros(header, r"MMC_(DATA_\w+|RSP_\w+|STATUS_\w+|STATE_PRG|"
                                              r"CMD_SEND_STATUS|CMD_WRITE_SINGLE_BLOCK|CMD_WRITE_MULTIPLE_BLOCK)"),
                 c_definition(driver, "sunxi_mmc_priv", structure=True),
                 c_definition(header, "mmc_cmd", structure=True),
                 c_definition(header, "mmc_data", structure=True), MMIO,
                 "\n".join(declarations), c_definition(updater, "sup_sd_live"),
                 c_definition(core, "bpi_update_mmc_poll_live")]
        # 原始碼位於不同編譯單元；只更名同名 wrapper，保留兩層真實期限函式。
        parts += ["#define sup_sd_live driver_sup_sd_live", c_definition(driver, "sup_sd_live")]
        for name in ("mmc_update_clk", "mmc_trans_data_by_cpu", "mmc_rint_wait", "sunxi_mmc_send_cmd_common"):
            parts.append(c_definition(driver, name))
        parts += ["#undef sup_sd_live", BRIDGE]
        for name in ("mmc_send_cmd_retry", "mmc_send_status", "mmc_poll_for_busy_impl", "mmc_poll_for_busy"):
            parts.append(c_definition(core, name))
        parts.append(MAIN)
        harness = cls.directory / "update_driver_model.c"
        harness.write_text("\n".join(parts), encoding="utf-8")
        cls.executable = cls.directory / "update_driver_model"
        command = shlex.split(os.environ.get("HOSTCC", "cc")) + [
            "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-unused-but-set-variable",
            "-I", str(cls.directory), "-I", str(driver.parent), "-idirafter", str(source / "include"),
            str(harness), "-o", str(cls.executable),
        ]
        try:
            result = subprocess.run(command, cwd=cls.directory, env=cls.environment,
                                    capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AssertionError("無法編譯更新器真實驅動模型") from exc
        if result.returncode:
            raise AssertionError(f"更新器真實驅動模型編譯失敗：\n{result.stdout}{result.stderr}")

    def run_case(self, name, words=128):
        try:
            process = subprocess.run([str(self.executable), name, str(words)],
                                     cwd=self.directory, env=self.environment,
                                     capture_output=True, text=True, timeout=3, check=False)
        except subprocess.TimeoutExpired:
            self.fail(f"{name}：真實函式未在模型期限內返回")
        self.assertEqual(process.returncode, 0, f"{name}：模型異常退出：{process.stderr}")
        state = json.loads(process.stdout)
        self.assertEqual(state["guards"], 1, "PIO 覆蓋緩衝區哨兵")
        self.assertEqual((state["dma_writes"], state["dma_enabled"]), (0, 0), "PIO 意外啟用 DMA")
        self.assertEqual(state["poll_active"], 0, "寫後輪詢期限狀態未解除")
        return state

    def test_real_pio_write_and_command_flags(self):
        """真實寫入分支必須送出每個字，且單塊／多塊命令不走 DMA。"""
        for name in ("pio_write", "write_command"):
            for words in (1, 31, 32, 33, 128, 256):
                with self.subTest(情境=name, 字數=words):
                    state = self.run_case(name, words)
                    self.assertEqual(state["result"], 0, "合法寫入遭拒絕")
                    self.assertEqual((state["fifo_writes"], state["fifo_reads"]), (words, 0))
                    self.assertEqual((state["sequence_ok"], state["ahb_seen"]), (1, 1))
                    if name == "write_command":
                        self.assertEqual(state["write_bit"], 1)
                        self.assertEqual(state["auto_stop"], int(words == 256))

    def test_read_pio_keeps_final_burst_limit(self):
        """啟用寫入後，原讀取末批數量裁限仍須保留。"""
        for words in (1, 31, 32, 33, 63, 64, 65, 128):
            with self.subTest(字數=words):
                state = self.run_case("pio_read", words)
                self.assertEqual((state["result"], state["fifo_reads"], state["sequence_ok"]), (0, words, 1))

    def test_expired_write_has_no_mmio(self):
        state = self.run_case("expired_write")
        self.assertLess(state["result"], 0)
        self.assertEqual((state["reads"], state["writes"]), (0, 0))

    def test_fifo_and_card_busy_local_deadlines(self):
        for name, lower, upper in (("fifo_write_full_local", 2000, 2010),
                                   ("fifo_read_empty_local", 2000, 2010),
                                   ("fifo_read_zero_local", 2000, 2010),
                                   ("busy_local", 2000, 4020)):
            with self.subTest(情境=name):
                state = self.run_case(name)
                self.assertLess(state["result"], 0)
                self.assertGreaterEqual(state["elapsed"], lower)
                self.assertLessEqual(state["elapsed"], upper)
                self.assertGreaterEqual(state["resets"], 1, "未經真實驅動錯誤清理")
                self.assertEqual((state["fifo_writes"], state["fifo_reads"]), (0, 0))

    def test_global_deadline_covers_waits_and_cleanup(self):
        for name in ("fifo_write_full_deadline", "fifo_read_empty_deadline", "fifo_read_zero_deadline",
                     "busy_deadline", "cmd13_busy_deadline", "cmd13_irq_deadline"):
            with self.subTest(情境=name):
                state = self.run_case(name)
                self.assertLess(state["result"], 0)
                self.assertGreaterEqual(state["elapsed"], 25)
                self.assertLessEqual(state["elapsed"], 30, "剩餘 25ms 耗盡後仍在等待或清理")

    def test_cmd13_busy_irq_and_slow_ready_share_one_second(self):
        for name in ("cmd13_busy", "cmd13_irq", "cmd13_slow", "cmd13_busy_wrap"):
            with self.subTest(情境=name):
                state = self.run_case(name)
                self.assertLess(state["result"], 0)
                self.assertGreaterEqual(state["elapsed"], 1000)
                self.assertLessEqual(state["elapsed"], 1005, "CMD13 或其清理取得額外期限")
                self.assertGreaterEqual(state["cmd13_count"], 1)
                if name == "cmd13_slow":
                    self.assertEqual(state["cmd13_count"], 2)
                if name == "cmd13_irq":
                    self.assertGreaterEqual(state["resets"], 1)

    def test_cmd13_retry_count_and_invalid_timeout(self):
        state = self.run_case("cmd13_errors")
        self.assertLess(state["result"], 0)
        self.assertEqual(state["cmd13_count"], 5)
        self.assertLess(state["elapsed"], 1000)
        state = self.run_case("cmd13_invalid")
        self.assertLess(state["result"], 0)
        self.assertEqual((state["reads"], state["writes"]), (0, 0))

    def test_poll_deadline_is_released_after_failure(self):
        state = self.run_case("cmd13_retry")
        self.assertLess(state["before_result"], 0)
        self.assertEqual(state["result"], 0, "上一輪超時污染下一輪輪詢")
        self.assertLessEqual(state["elapsed"], 1010)


if __name__ == "__main__":
    unittest.main(verbosity=2)
