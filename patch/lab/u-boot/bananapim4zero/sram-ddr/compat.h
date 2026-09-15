/* SPDX-License-Identifier: GPL-2.0+ */
#ifndef BPI_DDR_COMPAT_H
#define BPI_DDR_COMPAT_H
#include <stddef.h>
#include <stdbool.h>
#include <stdarg.h>

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned int uint32_t;
typedef unsigned long long u64;
typedef unsigned long ulong;
#define __noreturn __attribute__((noreturn))
#define __section(name) __attribute__((section(name)))
#define __maybe_unused __attribute__((unused))
#define BIT(n) (1UL << (n))
#define GENMASK(h, l) ((~0UL << (l)) & (~0UL >> (63 - (h))))
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))
#define DIV_ROUND_UP(n, d) (((n) + (d) - 1) / (d))
#define min(a, b) ((a) < (b) ? (a) : (b))
#define max(a, b) ((a) > (b) ? (a) : (b))
#define check_member(s, m, o) _Static_assert(offsetof(struct s, m) == (o), "暫存器配置不符")
#define debug(...) ((void)0)
#define IS_ENABLED(x) (x)
#define CONFIG_IS_ENABLED(x) CONFIG_##x
#define CONFIG_PRINTF 1
#define CONFIG_DRAM_SUNXI_H616_LAB 1
#define CONFIG_DRAM_SUNXI_H616_DIAGNOSTICS 0
#define CONFIG_MACH_SUN50I_H616 1
#define CONFIG_SUN50I_GEN_H6 1
#define CONFIG_SUNXI_DRAM_H616_LPDDR4 1
#define CONFIG_DRAM_SUNXI_PHY_ADDR_MAP_0 1
#define CONFIG_DRAM_CLK 792
#define CONFIG_DRAM_SUNXI_DX_ODT 0x07070707
#define CONFIG_DRAM_SUNXI_DX_DRI 0x0e0e0e0e
#define CONFIG_DRAM_SUNXI_CA_DRI 0x0d0d
#define CONFIG_DRAM_SUNXI_ODT_EN 0xaaaaeeee
#define CONFIG_DRAM_SUNXI_TPR0 0
#define CONFIG_DRAM_SUNXI_TPR2 0
#define CONFIG_DRAM_SUNXI_TPR6 0x3a808080
#define CONFIG_DRAM_SUNXI_TPR10 0x402f6663
#define CONFIG_DRAM_SUNXI_TPR11 0x25252523
#define CONFIG_DRAM_SUNXI_TPR12 0x110f0f10
#define CFG_SYS_SDRAM_BASE 0x40000000UL

static inline void dsb(void) { __asm__ volatile("dsb sy" ::: "memory"); }
static inline void dmb(void) { __asm__ volatile("dmb sy" ::: "memory"); }
static inline u32 ddr_readl(ulong p) { return *(volatile u32 *)p; }
static inline void ddr_writel(u32 v, ulong p) { *(volatile u32 *)p = v; }
#define readl(p) ddr_readl((ulong)(p))
#define writel(v, p) ddr_writel((v), (ulong)(p))
#define writel_relaxed(v, p) writel(v, p)
#define setbits_le32(p, v) writel(readl(p) | (v), (p))
#define clrbits_le32(p, v) writel(readl(p) & ~(v), (p))
#define clrsetbits_le32(p, c, s) writel((readl(p) & ~(c)) | (s), (p))

void *memcpy(void *dest, const void *src, size_t bytes);
void *memset(void *dest, int value, size_t bytes);
int strcmp(const char *a, const char *b);
char *strchr(const char *s, int c);
int printf(const char *format, ...);
int vprintf(const char *format, va_list args);
void putc(char c);
void serial_putc(char c);
int serial_getc(void);
int serial_tstc(void);
ulong get_tbclk(void);
u64 get_ticks(void);
ulong timer_get_us(void);
void udelay(ulong usec);
__noreturn void panic(const char *format, ...);
#endif
