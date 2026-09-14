/* SPDX-License-Identifier: GPL-2.0+ */
OUTPUT_FORMAT("elf64-littleaarch64")
OUTPUT_ARCH(aarch64)
ENTRY(_start)
SECTIONS {
    . = 0x30000;
    .text : { KEEP(*(.text.entry)) *(.text*) }
    .rodata : { *(.rodata*) }
    .data : { *(.data*) }
    .bss ALIGN(16) (NOLOAD) : {
        __bss_start = .;
        *(.bss*) *(COMMON)
        . = ALIGN(16);
        __bss_end = .;
    }
    /DISCARD/ : { *(.note*) *(.comment*) *(.eh_frame*) }
}
ASSERT(_start == 0x30000, "SPL2 入口不符")
ASSERT(__bss_end < 0x40000, "SPL2 程式與堆疊重疊")
