/* SPDX-License-Identifier: GPL-2.0+ */
OUTPUT_FORMAT("elf64-littleaarch64")
OUTPUT_ARCH(aarch64)
ENTRY(_start)
PHDRS { code PT_LOAD FLAGS(5); state PT_LOAD FLAGS(6); }
SECTIONS {
    . = 0x30000;
    .text.entry : { KEEP(*(.text.entry)) } :code
    .vectors ALIGN(2048) : { KEEP(*(.vectors)) } :code
    .text : { *(.text*) } :code
    .rodata : { *(.rodata*) } :code
    .data ALIGN(16) : { *(.data*) . = ALIGN(16); } :state
    __image_end = .;
    .bss ALIGN(16) (NOLOAD) : {
        __bss_start = .;
        *(.bss*) *(COMMON)
        . = ALIGN(16);
        __bss_end = .;
    } :state
    .stack 0x40000 (NOLOAD) : {
        __stack_bottom = .;
        . += 0x7800;
        __exception_stack_bottom = .;
        . += 0x800;
        __runtime_end = .;
    } :state
    /DISCARD/ : { *(.note*) *(.comment*) *(.eh_frame*) }
}
ASSERT(_start == 0x30000, "DDR SPL2 入口不符")
ASSERT(__image_end < 0x40000, "DDR SPL2 映像侵入堆疊")
ASSERT(__bss_end <= 0x40000, "DDR SPL2 BSS 侵入堆疊")
ASSERT(__runtime_end == 0x48000, "DDR SPL2 執行期超界")
