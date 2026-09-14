/* SPDX-License-Identifier: GPL-2.0+ */
typedef unsigned int u32;

struct context {
	u32 magic, abi, bytes, board, nonce, source, image_bytes, runtime_bytes;
	unsigned char digest[32];
};

static void putc_raw(char ch)
{
	volatile unsigned char *uart = (void *)0x05000000;
	unsigned int tries = 1000000;

	while (!(uart[20] & 0x20) && --tries)
		;
	if (tries)
		uart[0] = ch;
}

static void put_text(const char *text)
{
	while (*text)
		putc_raw(*text++);
}

static void hex(u32 value)
{
	const char *digits = "0123456789abcdef";
	int shift;

	for (shift = 28; shift >= 0; shift -= 4)
		putc_raw(digits[(value >> shift) & 15]);
}

void smoke_main(const struct context *ctx)
{
	unsigned long el, sp, sctlr;

	__asm__ volatile("mrs %0, CurrentEL" : "=r" (el));
	__asm__ volatile("mov %0, sp" : "=r" (sp));
	__asm__ volatile("mrs %0, sctlr_el3" : "=r" (sctlr));
	if ((unsigned long)ctx < 0x48010 ||
	    (unsigned long)ctx > 0x4fff0 - sizeof(*ctx) ||
	    ctx->magic != 0x31505553 || ctx->abi != 1 || ctx->bytes != 64 ||
	    ctx->board != 0x06180001 || el != 12 ||
	    sp < 0x40000 || sp >= 0x48000 || (sctlr & 0x1005)) {
		put_text("BPI-SPL2 event=halt result=invalid-context\r\n");
		return;
	}
	put_text("BPI-SPL2 event=smoke nonce_hex=");
	hex(ctx->nonce);
	put_text(" sp=");
	hex((u32)sp);
	put_text(" el=3 ddr=off result=pass\r\n");
}
