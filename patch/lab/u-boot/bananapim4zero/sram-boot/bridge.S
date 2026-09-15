/* SPDX-License-Identifier: GPL-2.0+ */
.section .text.entry,"ax"
.global _start, bridge_copy_start, bridge_handoff, bridge_stop
_start:
	msr daifset, #0xf
	mrs x1, CurrentEL
	cmp x1, #12
	b.ne bridge_stop
	mrs x1, sctlr_el3
	ldr x2, =0x1005
	tst x1, x2
	b.ne bridge_stop

	/* 讀取 SPL1 上下文前，先接管堆疊、gd 與例外向量。 */
	msr spsel, #1
	ldr x1, =__stack_top
	mov sp, x1
	mov x18, xzr
	adr x1, bridge_vectors
	msr vbar_el3, x1
	isb
	tst x0, #7
	b.ne bridge_stop
	ldr x1, =0x48010
	cmp x0, x1
	b.lo bridge_stop
	ldr x1, =0x4ffb0
	cmp x0, x1
	b.hi bridge_stop
	ldr w1, [x0]
	ldr w2, =0x31505553
	cmp w1, w2
	b.ne bridge_stop
	ldr w1, [x0, #4]
	cmp w1, #4
	b.ne bridge_stop
	ldr w1, [x0, #8]
	cmp w1, #64
	b.ne bridge_stop
	ldr w1, [x0, #12]
	ldr w2, =0x06180001
	cmp w1, w2
	b.ne bridge_stop
	ldr w1, [x0, #20]
	cmn w1, #1
	b.eq .Lsource_ok
	cmp w1, #4
	b.hi bridge_stop
.Lsource_ok:
	ldr w1, [x0, #24]
	ldr w2, =__image_bytes
	cmp w1, w2
	b.ne bridge_stop
	ldr w1, [x0, #28]
	ldr w2, =0x18000
	cmp w1, w2
	b.ne bridge_stop
	ldr x1, =bridge_context
	ldp x2, x3, [x0]
	stp x2, x3, [x1]
	ldp x2, x3, [x0, #16]
	stp x2, x3, [x1, #16]
	ldp x2, x3, [x0, #32]
	stp x2, x3, [x1, #32]
	ldp x2, x3, [x0, #48]
	stp x2, x3, [x1, #48]

	/* 封包 SHA 由固定 loader 驗證；覆寫前再核對內嵌 eGON 校驗和。 */
	ldr x0, =bridge_spl
	ldr w3, [x0, #12]
	ldr w4, =0x5f0a6c39
	sub w4, w4, w3
	ldr x2, =bridge_spl_end
.Lchecksum:
	ldr w1, [x0], #4
	add w4, w4, w1
	cmp x0, x2
	b.lo .Lchecksum
	cmp w4, w3
	b.ne bridge_stop

bridge_copy_start:
	ldr x0, =bridge_spl
	ldr x1, =0x20000
	ldr x2, =0x2a000
.Lcopy:
	ldp x3, x4, [x0], #16
	stp x3, x4, [x1], #16
	cmp x1, x2
	b.lo .Lcopy

	/* 不返回 SPL1；保留原標頭及固定 FIT 規則，不將來源槽冒充 FIT LBA。 */
	dsb sy
	ic iallu
	dsb sy
	isb
	mov x0, xzr
	mov x1, xzr
	mov x2, xzr
	mov x3, xzr
	mov x18, xzr
	adr x30, bridge_stop
	ldr x9, =0x20060
bridge_handoff:
	br x9

bridge_trap:
	msr daifset, #0xf
	msr spsel, #1
	ldr x1, =__stack_top
	mov sp, x1
	mov x18, xzr
bridge_stop:
	wfe
	b bridge_stop
	.ltorg

.section .spl,"a"
.global bridge_spl, bridge_spl_end
bridge_spl:
	.incbin "spl.bin"
bridge_spl_end:

.section .context,"aw",%nobits
.global bridge_context, bridge_context_end
bridge_context:
	.space 64
bridge_context_end:

.section .vectors,"ax"
.balign 2048
.global bridge_vectors, bridge_vectors_end
bridge_vectors:
.rept 16
	b bridge_trap
	.space 124
.endr
bridge_vectors_end:
