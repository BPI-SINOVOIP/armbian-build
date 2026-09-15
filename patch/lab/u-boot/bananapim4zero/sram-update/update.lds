/* SPDX-License-Identifier: GPL-2.0+ */
#include <generated/generic-asm-offsets.h>

OUTPUT_FORMAT("elf64-littleaarch64")
OUTPUT_ARCH(aarch64)
ENTRY(_start)
MEMORY {
    code (rwx) : ORIGIN = 0x30000, LENGTH = 0x10000
    state (rw) : ORIGIN = 0x40000, LENGTH = 0x3000
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
    __image_end = .;
    .bss (NOLOAD) : {
        __bss_start = .;
        *(.bss*) *(COMMON)
        . = ALIGN(16);
        __bss_end = .;
    } > state
    __guard_start = 0x43ff0;
    __guard_end = 0x44000;
    __stack_bottom = __guard_end;
    __runtime_end = 0x48000;
    __initial_sp = CONFIG_SPL_STACK;
    __malloc_end = __runtime_end;
    __malloc_start = __malloc_end - CONFIG_SPL_SYS_MALLOC_F_LEN;
    __gd_end = __malloc_start;
    __gd_start = (__gd_end - GENERATED_GBL_DATA_SIZE) & ~15;
    __stack_top = __gd_start;
    /DISCARD/ : {
        *(__u_boot_list*) *(.rela*) *(.dyn*) *(.plt*) *(.interp*)
        *(.gnu*) *(.eh_frame*) *(.comment*) *(.note*)
    }
}
ASSERT(_start == 0x30000 && __image_copy_start == _start, "更新器入口不符")
ASSERT(CONFIG_SPL_TEXT_BASE == 0x30000, "更新器組態載入位址不符")
ASSERT(_image_binary_end <= 0x40000, "更新器程式超出載入區")
ASSERT(__bss_start == 0x40000 && __bss_end <= 0x43000, "更新器 BSS 超界")
ASSERT(CONFIG_SPL_BSS_START_ADDR == __bss_start, "更新器 BSS 組態起點不符")
ASSERT(CONFIG_SPL_BSS_MAX_SIZE == 0x3000, "更新器 BSS 預算不符")
ASSERT(__bss_end <= __guard_start && __guard_end == __stack_bottom, "更新器 BSS、哨兵與堆疊重疊")
ASSERT(__stack_top - __stack_bottom >= 0x1800, "更新器堆疊不足 6 KiB")
ASSERT(GENERATED_GBL_DATA_SIZE > 0 && (__gd_start & 15) == 0, "更新器 gd 配置不符")
ASSERT(__gd_start < __gd_end && __gd_end == __malloc_start, "更新器 gd 與配置器重疊")
ASSERT(CONFIG_SPL_SYS_MALLOC_F_LEN == 0x2000, "更新器配置器預算不符")
ASSERT(CONFIG_SPL_STACK == 0x48000 && __initial_sp == __runtime_end, "更新器初期堆疊超界")
