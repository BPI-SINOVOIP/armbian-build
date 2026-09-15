/* SPDX-License-Identifier: GPL-2.0+ */
#include "payload.h"

void *memcpy(void *dest, const void *src, size_t bytes)
{
	u8 *out = dest;
	const u8 *in = src;
	while (bytes--)
		*out++ = *in++;
	return dest;
}

void *memset(void *dest, int value, size_t bytes)
{
	u8 *out = dest;
	while (bytes--)
		*out++ = value;
	return dest;
}

int strcmp(const char *a, const char *b)
{
	while (*a && *a == *b) {
		a++;
		b++;
	}
	return (u8)*a - (u8)*b;
}

char *strchr(const char *s, int c)
{
	do {
		if (*s == (char)c)
			return (char *)s;
	} while (*s++);
	return NULL;
}

static void uart_byte(char c)
{
	volatile u8 *uart = (void *)0x05000000UL;
	u32 tries = 1000000;
	while (!(uart[20] & 0x20) && --tries)
		;
	if (!tries)
		ddr_halt();
	uart[0] = c;
}

void putc(char c)
{
	if (c == '\n')
		uart_byte('\r');
	uart_byte(c);
}

void serial_putc(char c) { putc(c); }
int serial_tstc(void) { return *(volatile u8 *)0x05000014UL & 1; }
int serial_getc(void)
{
	while (!serial_tstc())
		;
	return *(volatile u8 *)0x05000000UL;
}

ulong get_tbclk(void)
{
	ulong rate;
	__asm__ volatile("mrs %0, cntfrq_el0" : "=r" (rate));
	return rate;
}

u64 get_ticks(void)
{
	ulong ticks;
	/* 沿用上游 sunxi 計數器勘誤過濾，但不存取 gd。 */
	__asm__ volatile("isb" ::: "memory");
	do {
		__asm__ volatile("mrs %0, cntpct_el0" : "=r" (ticks));
	} while (((ticks + 1) & GENMASK(10, 0)) <= 1);
	return ticks;
}

ulong timer_get_us(void) { return get_ticks() / 24; }
void udelay(ulong usec)
{
	u64 start = get_ticks();
	while (get_ticks() - start < (u64)usec * 24)
		;
}

__noreturn void ddr_halt(void)
{
	dsb();
	for (;;)
		__asm__ volatile("wfe");
}
