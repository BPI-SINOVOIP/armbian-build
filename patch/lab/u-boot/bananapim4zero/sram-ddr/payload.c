/* SPDX-License-Identifier: GPL-2.0+ */
#include "payload.h"

static struct ddr_context saved_context;
static struct ddr_request request;
static struct ddr_line line;
static char original[DDR_LINE_BYTES];
static const u64 stack_guard = 0x5352414d44445232ULL;
extern char __image_end[];

static void terminal(const char *result, const char *reason)
{
	printf("BPI-SPL2 event=ddr-result nonce_hex=%08x id=%u result=%s reason=%s ddr=off tested_bytes=0\n",
	       saved_context.nonce, request.id, result, reason);
}

__noreturn void panic(const char *format, ...)
{
	(void)format;
	printf("BPI-SPL2 event=ddr-result nonce_hex=%08x id=%u result=error reason=driver_abort ddr=unknown\n",
	       saved_context.nonce, request.id);
	ddr_halt();
}

__noreturn void ddr_exception(ulong esr, ulong far)
{
	printf("BPI-SPL2 event=ddr-result nonce_hex=%08x id=%u result=error reason=exception esr=%016lx far=%016lx ddr=unknown\n",
	       saved_context.nonce, request.id, esr, far);
	ddr_halt();
}

static __noreturn void run_one(void)
{
	struct dram_config geometry = {0};
	ulong size;
	bool passed;

	printf("BPI-SPL2 event=ddr-params %s\n", original + 2);
	if (!ddr_preflight(&geometry)) {
		terminal("blocked", "pmic_geometry_unverified");
		ddr_halt();
	}
	if (!ddr_geometry_valid(&geometry, request.window_mib, &size)) {
		terminal("blocked", "invalid_geometry");
		ddr_halt();
	}
	/* 此路徑必須等獨立前置檢查實作、審查及核准後才可到達。 */
	printf("BPI-SPL2 event=ddr-start nonce_hex=%08x id=%u size_mib=%lu\n",
	       saved_context.nonce, request.id, size >> 20);
	if (!mctl_core_init(&request.para, &geometry)) {
		printf("BPI-SPL2 event=ddr-result nonce_hex=%08x id=%u result=fail reason=init ddr=unknown\n",
		       saved_context.nonce, request.id);
		ddr_halt();
	}
	passed = ddr_lab_test(&request, &geometry);
	if (*(volatile u64 *)0x40000UL != stack_guard) {
		printf("BPI-SPL2 event=ddr-result nonce_hex=%08x id=%u result=error reason=stack_guard ddr=unknown\n",
		       saved_context.nonce, request.id);
		ddr_halt();
	}
	printf("BPI-SPL2 event=ddr-result nonce_hex=%08x id=%u result=%s ddr=on coverage=partial level=%u passes=1 window_mib=%u\n",
	       saved_context.nonce, request.id, passed ? "pass" : "fail",
	       request.level, request.window_mib);
	ddr_halt();
}

__noreturn void ddr_main(const struct ddr_context *context)
{
	ulong el, sp, sctlr;
	u64 started, last;
	u32 rejects = 0;
	int frame, parsed;
	const char *reason;

	__asm__ volatile("mrs %0, CurrentEL" : "=r" (el));
	__asm__ volatile("mov %0, sp" : "=r" (sp));
	__asm__ volatile("mrs %0, sctlr_el3" : "=r" (sctlr));
	if (!ddr_context_valid(context, (ulong)context, el, sp, sctlr,
			       (ulong)__image_end - DDR_ENTRY)) {
		terminal("blocked", "invalid_context");
		ddr_halt();
	}
	saved_context = *context;
	*(volatile u64 *)0x40000UL = stack_guard;
	if (get_tbclk() != 24000000) {
		terminal("blocked", "timer_rate");
		ddr_halt();
	}
	printf("BPI-SPL2 event=ddr-ready nonce_hex=%08x abi=2 kind=2 preflight=unverified\n",
	       saved_context.nonce);
	started = last = get_ticks();
	for (;;) {
		u64 now = get_ticks();
		bool partial = line.used || line.invalid;

		if (now - started > 30ULL * 24000000 ||
		    (partial && now - last > 5ULL * 24000000)) {
			terminal("blocked", partial ? "line_timeout" : "request_timeout");
			ddr_halt();
		}
		if (!serial_tstc())
			continue;
		last = now;
		frame = ddr_line_feed(&line, serial_getc());
		if (!frame)
			continue;
		reason = "line_encoding_or_length";
		if (frame > 0) {
			memcpy(original, line.text, sizeof(original));
			parsed = ddr_parse(line.text, saved_context.nonce, &request);
			if (!parsed)
				run_one();
			reason = parsed == DDR_PARSE_NONCE ? "nonce_mismatch" :
				 parsed == DDR_PARSE_PASSES ? "passes_not_one" : "fields_or_range";
		}
		printf("BPI-SPL2 event=ddr-reject nonce_hex=%08x reason=%s\n",
		       saved_context.nonce, reason);
		if (++rejects == 8) {
			terminal("blocked", "reject_limit");
			ddr_halt();
		}
	}
}
