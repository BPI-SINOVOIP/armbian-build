/* SPDX-License-Identifier: GPL-2.0+ */
OUTPUT_ARCH(aarch64)
ENTRY(_start)
PHDRS
{
	payload PT_LOAD FLAGS(5);
	context PT_LOAD FLAGS(6);
	vectors PT_LOAD FLAGS(5);
	stack PT_LOAD FLAGS(6);
}
SECTIONS
{
	.text 0x30000 : { KEEP(*(.text.entry)) *(.text*) *(.rodata*) } :payload
	.spl 0x32000 : { KEEP(*(.spl)) } :payload
	.context 0x3f000 (NOLOAD) : { KEEP(*(.context)) } :context
	.vectors 0x40000 : { KEEP(*(.vectors)) } :vectors
	__image_end = .;
	__image_bytes = __image_end - _start;
	.stack 0x42000 (NOLOAD) : {
		__stack_bottom = .;
		. += 0x5ff0;
		__stack_top = .;
	} :stack
	/DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) *(.ARM.attributes) }
}
ASSERT(_start == 0x30000, "橋接入口錯誤")
ASSERT(SIZEOF(.text) <= 0x2000, "橋接程式侵入 SPL 暫存區")
ASSERT(bridge_spl == 0x32000 && bridge_spl_end == 0x3c000, "SPL 必須為 40 KiB")
ASSERT(bridge_context == 0x3f000 && SIZEOF(.context) == 64, "上下文配置錯誤")
ASSERT(bridge_vectors == 0x40000 && SIZEOF(.vectors) == 0x800, "EL3 向量配置錯誤")
ASSERT(__image_end == 0x40800, "映像尾端錯誤")
ASSERT(__stack_bottom == 0x42000 && __stack_top == 0x47ff0, "獨立堆疊配置錯誤")
ASSERT(__stack_top < 0x48000, "不得覆寫 SPL1 狀態區")
