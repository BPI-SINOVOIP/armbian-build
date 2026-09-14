/* SPDX-License-Identifier: GPL-2.0+ */
OUTPUT_FORMAT("elf64-littleaarch64")
OUTPUT_ARCH(aarch64)
ENTRY(_start)
MEMORY {
    code (rwx) : ORIGIN = 0x20060, LENGTH = 0xffa0
    state (rw) : ORIGIN = 0x48010, LENGTH = 0x7fe0
}
SECTIONS {
    .text : {
        __image_copy_start = .;
        arch/arm/cpu/armv8/start.o (.text*)
        *(.text*)
    } > code
    .vectors ALIGN(2048) : { KEEP(*(.sup_vectors)) } > code
    .rodata : { . = ALIGN(16); *(.rodata*) } > code
    .data : { . = ALIGN(16); *(.data*) } > code
    .blk_drivers : {
        . = ALIGN(8);
        KEEP(*(SORT(__u_boot_list_2_blk_driver*)))
    } > code
    .image_copy_end : {
        . = ALIGN(16);
        KEEP(*(.__image_copy_end))
    } > code
    .end : { KEEP(*(.__end)) } > code
    _image_binary_end = .;
    .bss (NOLOAD) : {
        __bss_start = .;
        *(.bss*) *(COMMON)
        . = ALIGN(16);
        __bss_end = .;
    } > state
    /DISCARD/ : {
        *(__u_boot_list*) *(.rela*) *(.dyn*) *(.plt*) *(.interp*)
        *(.gnu*) *(.eh_frame*) *(.comment*) *(.note*)
    }
}
ASSERT(__image_copy_start == 0x20060, "SPL1 入口不符")
ASSERT(_image_binary_end <= 0x30000, "SPL1 超出載入區")
ASSERT(__bss_start == 0x48010, "SPL1 BSS 起點不符")
ASSERT(__bss_end <= 0x4fff0, "SPL1 BSS 覆蓋哨兵")
ASSERT(CONFIG_SPL_STACK == 0x58000, "SPL1 初期堆疊不符")
ASSERT(CONFIG_SPL_SYS_MALLOC_F_LEN <= 0x2000, "SPL1 配置器預算超限")
