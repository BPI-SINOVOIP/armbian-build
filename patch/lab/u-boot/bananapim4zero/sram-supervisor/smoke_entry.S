/* SPDX-License-Identifier: GPL-2.0+ */
.section .text.entry,"ax"
.global _start
_start:
	msr daifset, #0xf
	mov x19, x0
	msr spsel, #1
	ldr x1, =0x47ff0
	mov sp, x1
	mov x18, xzr
	ldr x1, =__bss_start
	ldr x2, =__bss_end
1:
	cmp x1, x2
	b.hs 2f
	str xzr, [x1], #8
	b 1b
2:
	mov x0, x19
	bl smoke_main
3:
	wfe
	b 3b
