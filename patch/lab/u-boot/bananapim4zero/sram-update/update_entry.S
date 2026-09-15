/* SPDX-License-Identifier: GPL-2.0+ */
#include <config.h>
#include <linux/linkage.h>

.text
ENTRY(_start)
	msr daifset, #0xf
	mov x19, x0
	mrs x1, CurrentEL
	cmp x1, #12
	b.ne update_halt
	mrs x1, sctlr_el3
	ldr x2, =0x1005
	tst x1, x2
	b.ne update_halt
	msr spsel, #1
	ldr x0, =CONFIG_SPL_STACK
	bic sp, x0, #0xf
	bl sup_prepare

	/* 與 crt0 共用配置流程，但保留載入器傳入的上下文。 */
	mov x0, sp
	bl board_init_f_alloc_reserve
	ldr x1, =__stack_top
	cmp x0, x1
	b.ne update_halt
	mov sp, x0
	mov x18, x0
	bl board_init_f_init_reserve
	mov x0, x19
	bl board_init_f
ENDPROC(_start)

ENTRY(update_halt)
	msr daifset, #0xf
1:
	wfe
	b 1b
ENDPROC(update_halt)
