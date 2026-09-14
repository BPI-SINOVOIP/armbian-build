/* SPDX-License-Identifier: GPL-2.0+ */
#include <linux/linkage.h>

ENTRY(sup_prepare)
	mrs x0, CurrentEL
	cmp x0, #12
	b.ne sup_trap
	msr daifset, #0xf
	mov x0, sp
	msr spsel, #1
	mov sp, x0
	adr x0, sup_vectors
	msr vbar_el3, x0
	isb
	ret
ENDPROC(sup_prepare)

ENTRY(sup_enter)
	msr daifset, #0xf
	dsb sy
	isb
	mov x2, x0
	mov x0, x1
	mov x18, xzr
	blr x2
1:
	wfe
	b 1b
ENDPROC(sup_enter)

.section .sup_vectors,"ax"
.align 11
sup_vectors:
.rept 16
	b sup_trap
	.space 124
.endr
sup_trap:
	msr daifset, #0xf
1:
	wfe
	b 1b
