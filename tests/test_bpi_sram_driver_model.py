#!/usr/bin/env python3
"""抽取指定建置的原始 C 函式測試；只替代 MMIO／時基，不驗證硬體或 AArch64 ABI。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest


def c_definition(path: Path, name: str, *, structure=False, occurrence=0) -> str:
    """保留完整定義與來源行號；詞法掃描忽略註解、字串中的大括號。"""
    source = path.read_text(encoding="utf-8")
    prefix = rf"^struct\s+{re.escape(name)}\s*\{{" if structure else (
        rf"^(?:static\s+)?(?:inline\s+)?(?:int|void|u32)\s+"
        rf"{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{"
    )
    matches = list(re.finditer(prefix, source, re.MULTILINE))
    if not matches or not -len(matches) <= occurrence < len(matches):
        raise AssertionError(f"找不到原始 C 定義：{path}:{name}")
    match = matches[occurrence]
    depth = 1
    tokens = re.compile(r'/\*[\s\S]*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[{}]')
    for token in tokens.finditer(source, match.end()):
        depth += (token.group() == "{") - (token.group() == "}")
        if depth == 0:
            end = token.end()
            if structure:
                if source[end:end + 1] != ";":
                    raise AssertionError(f"C 結構沒有完整結尾：{path}:{name}")
                end += 1
            line = source.count("\n", 0, match.start()) + 1
            return f"#line {line} {json.dumps(str(path))}\n{source[match.start():end]}\n"
    raise AssertionError(f"C 定義的大括號不完整：{path}:{name}")


def c_macros(path: Path, pattern: str) -> str:
    """沿用原始常數及續行，不在模型另抄寄存器或協定數值。"""
    lines = iter(path.read_text(encoding="utf-8").splitlines(keepends=True))
    result, seen = [], set()
    for line in lines:
        block = line
        while line.rstrip().endswith("\\"):
            line = next(lines)
            block += line
        match = re.match(r"#define\s+(\w+)\b", block)
        if match and re.fullmatch(pattern, match[1]) and match[1] not in seen:
            seen.add(match[1])
            result.append(block)
    if not result:
        raise AssertionError(f"找不到原始 C 常數：{path}:{pattern}")
    return "".join(result)


TYPES = r"""
#ifndef MODEL_TYPES_H
#define MODEL_TYPES_H
#include <stdint.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef unsigned int uint;
typedef unsigned short ushort;
typedef unsigned long ulong;
#endif
"""


PREAMBLE = r"""
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <linux/types.h>
#define CONFIG_BPI_SRAM_SUPERVISOR 1
#define CONFIG_SPL_BUILD 1
#define CONFIG_SUN50I_GEN_H6 1
#define CONFIG_SYS_NS16550_REG_SIZE (-4)
#define CONFIG_IS_ENABLED(name) CONFIG_ ## name
#define IS_ENABLED(name) (name)
#define min(a, b) ((a) < (b) ? (a) : (b))
#define debug(...) do { } while (0)
#define dmb() do { } while (0)
#include "sunxi_mmc.h"
#include "ns16550.h"

/* 只提供所測函式使用的成員，不將主機結構布局當作韌體 ABI 證據。 */
struct gpio_desc { int unused; };
struct mmc_config { int unused; };
struct mmc { void *priv; };
static struct sunxi_mmc registers;
static struct ns16550 uart;
static ulong now;
static unsigned reads, writes, fifo_reads, cmd_writes, resets, lsr_reads, fcr_writes;
static unsigned fifo_mode, irq_value, clock_stuck, card_busy, temt_after;

static void tick(void)
{
    if (++reads > 100000) {
        fputs("模型讀取超過上限，原始函式可能未有界返回\n", stderr);
        exit(90);
    }
    ++now;
}

static ulong get_timer(ulong base) { return now - base; }
static void udelay(ulong us) { now += (us + 999) / 1000; }

static u32 readl(const volatile u32 *address)
{
    tick();
    if (address == &registers.cmd)
        return clock_stuck ? SUNXI_MMC_CMD_START : 0;
    if (address == &registers.rint)
        return irq_value;
    if (address == &registers.status) {
        u32 value = card_busy ? SUNXI_MMC_STATUS_CARD_DATA_BUSY : 0;
        if (fifo_mode == 1)
            value |= SUNXI_MMC_STATUS_FIFO_EMPTY;
        else if (fifo_mode == 2)
            value |= (fifo_reads < 3 ? 3U : 63U) << 17;
        else if (fifo_mode == 3)
            value |= SUNXI_MMC_STATUS_FIFO_FULL;
        return value;
    }
    if (address == &registers.fifo)
        return 0x60000000U + fifo_reads++;
    return *address;
}

static void writel(u32 value, volatile u32 *address)
{
    ++writes;
    if (address == &registers.cmd)
        ++cmd_writes;
    if (address == &registers.gctrl) {
        if ((value & SUNXI_MMC_GCTRL_RESET) == SUNXI_MMC_GCTRL_RESET)
            ++resets;
        /* 模型中的控制器立即完成 reset，避免把自清位元當作持久狀態。 */
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

static unsigned serial_in(const volatile unsigned char *address)
{
    tick();
    if (address == &uart.lsr) {
        ++lsr_reads;
        return lsr_reads >= temt_after ? UART_LSR_TEMT : 0;
    }
    return *address;
}

static void serial_out(unsigned value, volatile unsigned char *address)
{
    ++writes;
    if (address == &uart.fcr)
        ++fcr_writes;
    *address = value;
}
"""


MAIN = r"""
int main(int argc, char **argv)
{
    const char *name = argc > 1 ? argv[1] : "";
    unsigned words = argc > 2 ? (unsigned)strtoul(argv[2], NULL, 10) : 6;
    if (!words || words > 128) {
        fputs("模型緩衝區大小不合法\n", stderr);
        return 2;
    }
    u32 *buffer = calloc(words + 2, sizeof(*buffer));
    if (!buffer)
        return 3;
    buffer[0] = buffer[words + 1] = 0xa55a1234;
    struct sunxi_mmc_priv priv = {.reg = &registers};
    struct mmc card = {.priv = &priv};
    struct mmc_data data = {
        .dest = (char *)(buffer + 1), .flags = MMC_DATA_READ,
        .blocks = 1, .blocksize = words * sizeof(*buffer)
    };
    struct mmc_cmd cmd = {.cmdidx = 7, .resp_type = MMC_RSP_R1};
    int result = 0, before_result = 0, init_result = 0;
    int sequence_ok = 1;
    sd_start = 0;
    temt_after = 1;
    irq_value = SUNXI_MMC_RINT_COMMAND_DONE;
    if (strstr(name, "deadline"))
        now = 9975;

    if (!strcmp(name, "live_before") || !strcmp(name, "live_exact") ||
        !strcmp(name, "live_wrap_before") || !strcmp(name, "live_wrap_exact")) {
        sd_start = strstr(name, "wrap") ? ULONG_MAX - 99 : 123;
        now = sd_start + (strstr(name, "before") ? 9999 : 10000);
        result = sup_sd_live();
    } else if (!strcmp(name, "expired_command")) {
        now = 10000;
        result = sunxi_mmc_send_cmd_common(&priv, &card, &cmd, NULL);
    } else if (!strcmp(name, "clock_deadline") || !strcmp(name, "clock_local")) {
        clock_stuck = 1;
        result = mmc_update_clk(&priv);
    } else if (!strncmp(name, "fifo_", 5)) {
        if (strstr(name, "empty"))
            fifo_mode = 1;
        if (strstr(name, "final"))
            fifo_mode = 2;
        if (strstr(name, "full"))
            fifo_mode = 3;
        result = mmc_trans_data_by_cpu(&priv, &card, &data);
        if (fifo_reads)
            for (unsigned i = 0; i < words; ++i)
                sequence_ok &= buffer[i + 1] == 0x60000000U + i;
    } else if (!strcmp(name, "irq_deadline") || !strcmp(name, "irq_local")) {
        irq_value = 0;
        result = mmc_rint_wait(&priv, &card, 1000, SUNXI_MMC_RINT_COMMAND_DONE, "cmd");
    } else if (!strcmp(name, "irq_cleanup_deadline") || !strcmp(name, "recover_deadline")) {
        irq_value = 0;
        clock_stuck = 1;
        result = sunxi_mmc_send_cmd_common(&priv, &card, &cmd, NULL);
        if (!strcmp(name, "recover_deadline")) {
            before_result = result;
            now += 100;
            sd_start = now;
            irq_value = SUNXI_MMC_RINT_COMMAND_DONE;
            clock_stuck = 0;
            init_result = sunxi_mmc_core_init(&card);
            result = sunxi_mmc_send_cmd_common(&priv, &card, &cmd, NULL);
        }
    } else if (!strcmp(name, "busy_deadline") || !strcmp(name, "busy_local")) {
        card_busy = clock_stuck = 1;
        cmd.resp_type = MMC_RSP_R1b;
        result = sunxi_mmc_send_cmd_common(&priv, &card, &cmd, NULL);
    } else if (!strcmp(name, "fatal_reset")) {
        priv.fatal_err = 1;
        before_result = sunxi_mmc_send_cmd_common(&priv, &card, &cmd, NULL);
        init_result = sunxi_mmc_core_init(&card);
        result = sunxi_mmc_send_cmd_common(&priv, &card, &cmd, NULL);
    } else if (!strcmp(name, "reject_write")) {
        data.flags = MMC_DATA_WRITE;
        result = sunxi_mmc_send_cmd_common(&priv, &card, &cmd, &data);
    } else if (!strncmp(name, "uart_", 5)) {
        if (!strcmp(name, "uart_stuck"))
            temt_after = UINT_MAX;
        else if (!strcmp(name, "uart_delayed"))
            temt_after = 7;
        ns16550_init(&uart, 0x1234);
    } else {
        fputs("未知的模型測例\n", stderr);
        free(buffer);
        return 2;
    }
    printf("{\"result\":%d,\"before_result\":%d,\"init_result\":%d,"
           "\"now\":%lu,\"reads\":%u,\"writes\":%u,\"fifo_reads\":%u,"
           "\"cmd_writes\":%u,\"resets\":%u,\"fatal\":%u,\"guards\":%d,"
           "\"sequence_ok\":%d,\"lsr_reads\":%u,\"fcr_writes\":%u,"
           "\"fcr\":%u,\"lcr\":%u,\"divisor\":%u}\n",
           result, before_result, init_result, now, reads, writes, fifo_reads,
           cmd_writes, resets, priv.fatal_err,
           buffer[0] == 0xa55a1234 && buffer[words + 1] == 0xa55a1234,
           sequence_ok, lsr_reads, fcr_writes, uart.fcr, uart.lcr,
           uart.dll | ((unsigned)uart.dlm << 8));
    free(buffer);
    return 0;
}
"""


class DriverModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build = os.environ.get("BPI_SRAM_BUILD")
        if not build:
            raise unittest.SkipTest("請以 BPI_SRAM_BUILD 指定已完成的建置目錄")
        cls.build = Path(build).resolve()
        source = cls.build / "source"
        driver = source / "drivers/mmc/sunxi_mmc.c"
        serial = source / "drivers/serial/ns16550.c"
        supervisor = source / "arch/arm/mach-sunxi/supervisor.c"
        mmc_header = source / "include/mmc.h"
        for path in (driver, serial, supervisor, mmc_header, cls.build / "build-report.json"):
            if not path.is_file():
                raise AssertionError(f"建置尚未完成或缺少來源：{path}")
        cls.temporary = tempfile.TemporaryDirectory(prefix="bpi-sram-driver-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.environment = dict(os.environ, TMPDIR=str(cls.directory))
        (cls.directory / "linux").mkdir()
        (cls.directory / "linux/types.h").write_text(TYPES, encoding="utf-8")
        declaration = re.search(r"^static ulong sd_start;\s*$", supervisor.read_text(encoding="utf-8"), re.MULTILINE)
        if not declaration:
            raise AssertionError("來源缺少 SD deadline 起始狀態，請核對指定建置")
        parts = [PREAMBLE, c_macros(mmc_header, r"MMC_(DATA|RSP)_\w+"),
                 c_macros(serial, r"UART_LCRVAL|UART_MCRVAL|CFG_SYS_NS16550_IER"),
                 c_definition(driver, "sunxi_mmc_priv", structure=True),
                 c_definition(mmc_header, "mmc_cmd", structure=True),
                 c_definition(mmc_header, "mmc_data", structure=True),
                 declaration.group() + "\n", c_definition(supervisor, "sup_sd_live")]
        for name in ("mmc_update_clk", "mmc_trans_data_by_cpu", "mmc_rint_wait",
                     "sunxi_mmc_send_cmd_common", "sunxi_mmc_core_init"):
            parts.append(c_definition(driver, name))
        parts += [c_definition(serial, "ns16550_getfcr", occurrence=-1),
                  c_definition(serial, "ns16550_setbrg"), c_definition(serial, "ns16550_init"), MAIN]
        cls.harness = cls.directory / "driver_model.c"
        cls.harness.write_text("\n".join(parts), encoding="utf-8")
        cls.executable = cls.directory / "driver_model"
        command = shlex.split(os.environ.get("HOSTCC", "cc")) + [
            "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-Wno-unused-but-set-variable",
            "-I", str(cls.directory), "-I", str(driver.parent), "-idirafter", str(source / "include"),
            str(cls.harness), "-o", str(cls.executable),
        ]
        try:
            result = subprocess.run(command, cwd=cls.directory, env=cls.environment,
                                    capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AssertionError("無法編譯真實驅動函式的主機模型") from exc
        if result.returncode:
            raise AssertionError(f"真實驅動函式編譯失敗：\n{result.stdout}{result.stderr}")

    def run_case(self, name, words=6):
        try:
            process = subprocess.run([str(self.executable), name, str(words)],
                                     cwd=self.directory, env=self.environment,
                                     capture_output=True, text=True, timeout=3, check=False)
        except subprocess.TimeoutExpired:
            self.fail(f"{name}：真實 C 函式未在模型執行期限內返回")
        self.assertEqual(process.returncode, 0, f"{name}：模型異常退出：{process.stderr}")
        try:
            state = json.loads(process.stdout)
        except json.JSONDecodeError:
            self.fail(f"{name}：模型輸出不是完整 JSON：{process.stdout}")
        self.assertEqual(state["guards"], 1, f"{name}：FIFO 覆蓋了前後哨兵")
        return state

    def test_deadline_exact_boundary_and_unsigned_wrap(self):
        """真正的 sup_sd_live 必須在第 10000ms 拒絕，並正確處理時基回捲。"""
        for name, expected in (("live_before", 1), ("live_exact", 0),
                               ("live_wrap_before", 1), ("live_wrap_exact", 0)):
            with self.subTest(情境=name):
                self.assertEqual(self.run_case(name)["result"], expected, "SD deadline 邊界不符")

    def test_expired_command_never_issues_mmio(self):
        """期限已到時，真實 send_cmd 不得先碰控制器。"""
        state = self.run_case("expired_command")
        self.assertLess(state["result"], 0, "期限已到卻接受命令")
        self.assertEqual((state["reads"], state["writes"]), (0, 0), "拒絕前已操作 MMIO")

    def test_all_waits_obey_global_not_only_local_deadline(self):
        """剩餘 25ms 時，五種等待與錯誤清理不能重新獲得局部秒級預算。"""
        for name in ("clock_deadline", "fifo_zero_deadline", "fifo_empty_deadline",
                     "irq_deadline", "irq_cleanup_deadline", "busy_deadline"):
            with self.subTest(情境=name):
                state = self.run_case(name)
                self.assertLess(state["result"], 0, "故障等待意外成功")
                self.assertGreaterEqual(state["now"], 10000, "未到期限就停止，未測到等待路徑")
                self.assertLessEqual(state["now"], 10005, "全域期限後仍持續等待或清理")
                self.assertEqual(state["fifo_reads"], 0, "無資料時不應讀 FIFO")
                if name in ("irq_cleanup_deadline", "busy_deadline"):
                    self.assertGreaterEqual(state["resets"], 1, "未走到真實錯誤清理路徑")

    def test_local_timeouts_still_bound_early_failures(self):
        """全域尚有時間時，既有局部期限仍能截斷故障等待。"""
        for name, limit in (("clock_local", 2010), ("fifo_zero_local", 2010),
                            ("fifo_empty_local", 2010), ("irq_local", 1010),
                            ("busy_local", 4020)):
            with self.subTest(情境=name):
                state = self.run_case(name)
                self.assertLess(state["result"], 0, "局部故障沒有回傳錯誤")
                self.assertLessEqual(state["now"], limit, "局部等待超過預算")

    def test_real_core_init_clears_fatal_and_command_recovers(self):
        """帶 fatal 的命令先失敗；只呼叫真實 core_init 後應能重新送出命令。"""
        state = self.run_case("fatal_reset")
        self.assertLess(state["before_result"], 0, "fatal 狀態未拒絕命令")
        self.assertEqual(state["init_result"], 0, "控制器重新初始化失敗")
        self.assertEqual(state["fatal"], 0, "真實 core_init 沒有清除 fatal_err")
        self.assertEqual(state["result"], 0, "清除 fatal 後仍不能送出命令")
        self.assertEqual(state["cmd_writes"], 1, "fatal 拒絕路徑不應送出額外命令")
        self.assertEqual(state["resets"], 1, "未執行真實控制器 reset")

    def test_new_deadline_and_real_init_allow_retry(self):
        """上一輪逾時後，更新期限並重新初始化即可送出下一輪命令。"""
        state = self.run_case("recover_deadline")
        self.assertLess(state["before_result"], 0, "第一輪未觸發逾時")
        self.assertEqual((state["init_result"], state["result"]), (0, 0), "下一輪無法恢復")
        self.assertLessEqual(state["now"], 10110, "清理或下一輪重試耗時異常")

    def test_fifo_last_burst_cannot_overwrite_buffer(self):
        """真實 PIO 限制最後一批 FIFO 數量，並保持資料順序與哨兵。"""
        for words in (1, 2, 3, 4, 6, 31, 32, 33, 63, 64, 65):
            for name in ("fifo_final", "fifo_full"):
                with self.subTest(情境=name, 字數=words):
                    state = self.run_case(name, words)
                    self.assertEqual(state["result"], 0, "合法 FIFO 讀取失敗")
                    self.assertEqual(state["fifo_reads"], words, "FIFO 讀取數量超出或不足")
                    self.assertEqual(state["sequence_ok"], 1, "PIO 遺漏或重複 FIFO 資料")

    def test_write_rejected_before_mmio(self):
        """唯讀驅動不得在拒絕寫入前送出命令或操作 FIFO。"""
        state = self.run_case("reject_write")
        self.assertLess(state["result"], 0, "寫入命令未被拒絕")
        self.assertEqual((state["reads"], state["writes"]), (0, 0), "拒絕寫入前已操作 MMIO")

    def test_uart_temt_timeout_still_initializes_fifo_and_divisor(self):
        """TEMT 永遠為零時，真實 UART 初始化仍在 20ms 後重設 FIFO 並返回。"""
        state = self.run_case("uart_stuck")
        self.assertGreaterEqual(state["lsr_reads"], 20, "未進入 TEMT 逾時路徑")
        self.assertLessEqual(state["now"], 23, "TEMT 等待沒有在 20ms 附近停止")
        self.assertEqual((state["fcr_writes"], state["fcr"]), (1, 7), "逾時後未重設 FIFO")
        self.assertEqual((state["lcr"], state["divisor"]), (3, 0x1234), "逾時後 UART 配置不完整")

    def test_uart_ready_and_delayed_temt(self):
        """正常或稍晚出現 TEMT 時不必用盡逾時預算。"""
        for name, reads in (("uart_ready", 1), ("uart_delayed", 7)):
            with self.subTest(情境=name):
                state = self.run_case(name)
                self.assertEqual(state["lsr_reads"], reads, "TEMT 就緒後仍在等待")
                self.assertLess(state["now"], 20, "正常 UART 初始化不應耗盡期限")
                self.assertEqual((state["fcr"], state["lcr"], state["divisor"]),
                                 (7, 3, 0x1234), "正常 UART 初始化配置不符")


if __name__ == "__main__":
    unittest.main(verbosity=2)
