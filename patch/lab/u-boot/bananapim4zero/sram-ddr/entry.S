/* SPDX-License-Identifier: GPL-2.0+ */
.section .text.entry,"ax"
.global _start
_start:
	msr daifset, #0xf
	mrs x1, CurrentEL
	cmp x1, #12
	b.ne .Lstop
	mrs x1, sctlr_el3
	ldr x2, =0x1005
	tst x1, x2
	b.ne .Lstop
	mov x19, x0
	msr spsel, #1
	ldr x1, =0x477f0
	mov sp, x1
	mov x18, xzr
	adr x1, ddr_vectors
	msr vbar_el3, x1
	isb
	ldr x1, =__bss_start
	ldr x2, =__bss_end
.Lzero:
	cmp x1, x2
	b.hs .Lmain
	str xzr, [x1], #8
	b .Lzero
.Lmain:
	mov x0, x19
	bl ddr_main
.Lstop:
	wfe
	b .Lstop

.section .vectors,"ax"
.balign 2048
ddr_vectors:
.rept 16
	b ddr_trap
	.space 124
.endr
ddr_trap:
	msr daifset, #0xf
	msr spsel, #1
	ldr x1, =0x47ff0
	mov sp, x1
	mrs x0, esr_el3
	mrs x1, far_el3
	bl ddr_exception
	b .Lstop
