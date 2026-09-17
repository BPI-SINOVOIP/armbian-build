/* 主機匿名 RAM、SD 與原廠交接替身；不是 BSP 建置或實板資格。 */
#define _GNU_SOURCE
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/mman.h>
#include <openssl/sha.h>
#define CONFIG_SHA256 1
#define CONFIG_PARTITION_UUIDS 1
#define CONFIG_GENERIC_MMC 1
#define CONFIG_RTD1395 1
#define CONFIG_RTD1295 1
#define CONFIG_RTK_SD_DRIVER 1
#define CONFIG_CMD_FAT 1
#define CONFIG_NR_DRAM_BANKS 1
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define SWAPEND32(a) __builtin_bswap32(a)
#define MIPS_KSEG0BASE 0x80000000U
#define CLOCK_ENABLE2_reg 0x98000010UL
#define OTP_REG_BASE 0x98017000UL
#define OTP_BIT_SECUREBOOT 3494
#define _BIT4 0x10U
#define IS_SD(m) ((m)->version)
#define NONE_SECURE_BOOT 0
#define IF_TYPE_SD 7
#define FS_TYPE_FAT 1
#define CMD_RET_FAILURE 1
#define CMD_RET_USAGE 2
#define CHUNKSZ_SHA256 65536
#define BOOT_RESCUE_MODE 3
#define ARCH_DMA_MINALIGN 64
#define FDT_ERR_NOTFOUND 1
#define U_BOOT_CMD(...)
#define setenv lab_setenv
#define free lab_free
typedef unsigned long ulong;
typedef int cmd_tbl_t;
typedef SHA256_CTX sha256_context;
typedef struct { char uuid[37]; } disk_partition_t;
typedef struct { int if_type, dev; ulong blksz, lba; ulong (*block_read)(int, ulong, ulong, void *); } block_dev_desc_t;
struct mmc { unsigned int cid[4]; int version, part_num; block_dev_desc_t block_dev; };
static ulong read_sd(int, ulong, ulong, void *);
static struct mmc sd = {{0xffffffff,0xffffffff,0xffffffff,0xffffffff},1,0,{IF_TYPE_SD,0,512,32768,read_sd}};
static struct mmc emmc = {{0x11223344,0x55667788,0x99aabbcc,0xddeeff00},0,0,{6,0,512,32768,NULL}};
static struct { struct { ulong start, size; } bi_dram[1]; } bd = {{{0,0x80000000}}};
static struct { typeof(bd) *bd; ulong relocaddr, start_addr_sp, irq_sp, mon_len;
    struct { ulong tlb_addr; } arch; void *fdt_blob, *new_fdt; ulong fdt_size;
} gd_value = {&bd,0x70100000,0x70200000,0,0x100000,{0},NULL,NULL,0};
static typeof(gd_value) *gd = &gd_value;
static ulong mem_malloc_start=0x70300000, mem_malloc_end=0x70400000;
static struct { unsigned int audio_fw_entry_pt; } ipc_shm;
static int audio_fw_state, ipc_ir_set, boot_mode, called, audio_called, fault;
static char bootargs[4096];
static unsigned int rtd_inl(ulong reg)
{
    if (reg==OTP_REG_BASE+(OTP_BIT_SECUREBOOT/32)*4) return 0;
    assert(reg==CLOCK_ENABLE2_reg); return 0;
}
static unsigned int rtk_get_secure_boot_type(void) { return 0; }
static struct mmc *find_mmc_device(int dev) { assert(!"SD 救援不得探測 eMMC"); return NULL; }
static struct mmc *find_sd_device(void) { return &sd; }
static int mmc_init(struct mmc *m) { assert(!"SD 救援不得初始化 eMMC"); return -1; }
static int sd_init(struct mmc *m) { return m != &sd; }
static int get_partition_info(block_dev_desc_t *device, int part, disk_partition_t *out)
{
    assert(part==1 && device==&sd.block_dev);
    strcpy(out->uuid, fault==4 ? "bad" : "abcdef12-01");
    return 0;
}
static void *memalign(size_t alignment, size_t bytes)
{ assert(alignment==64 && bytes==65536); return (void *)0x70380000; }
static void lab_free(void *p) { assert(p==(void *)0x70380000); }
static ulong read_sd(int device, ulong sector, ulong count, void *buffer)
{
    assert(device==sd.block_dev.dev && sector<8192 && count==128);
    memset(buffer,0,count*512);
    if (fault==5) ((char *)buffer)[0]=1;
    return fault==6 ? count-1 : count;
}
static void sha256_starts(sha256_context *s) { assert(SHA256_Init(s)); }
static void sha256_update(sha256_context *s, const unsigned char *p, unsigned int n) { assert(SHA256_Update(s,p,n)); }
static void sha256_finish(sha256_context *s, unsigned char *p) { assert(SHA256_Final(p,s)); }
static void sha256_csum_wd(const unsigned char *p, unsigned int n, unsigned char *out, unsigned int chunk)
{ assert(SHA256(p,n,out)); }
static int lab_setenv(const char *name, const char *value)
{
    assert(strcmp(name,"bootcmd") && strcmp(name,"boot_targets"));
    if (!strcmp(name,"bootargs")) { assert(value && strlen(value)<sizeof(bootargs)); strcpy(bootargs,value); }
    return fault==13;
}
static int setenv_hex(const char *name, ulong value) { assert(!strcmp(name,"filesize")); return 0; }
static int fs_set_blk_dev(const char *device, const char *part, int type)
{ assert(!strcmp(device,"sd") && !strcmp(part,"0:1") && type==FS_TYPE_FAT); return 0; }
static int fs_read(const char *, ulong, loff_t, loff_t, loff_t *);
static int fdt_open_into(void *a, void *b, int capacity) { assert(a==b && capacity>=65536); return fault==12 ? -1 : 0; }
static int fdt_path_offset(void *fdt, const char *path) { assert(!strcmp(path,"/chosen")); return -FDT_ERR_NOTFOUND; }
static int fdt_add_subnode(void *fdt, int parent, const char *name) { assert(parent==0 && !strcmp(name,"chosen")); return 1; }
static int fdt_setprop_string(void *fdt, int node, const char *name, const char *value)
{ assert(node==1 && !strcmp(name,"bootargs") && !strcmp(value,bootargs)); return 0; }
static int do_go_all_fw(void) { assert(0); return 1; }
static int do_go_audio_fw(void) { ++audio_called; return 0; }
static int rtk_call_booti(void)
{
    assert(boot_mode==BOOT_RESCUE_MODE && audio_called==1 && strstr(bootargs,"root=/dev/ram0"));
    assert(strstr(bootargs,"rdinit=/init") && !strstr(bootargs,"LABEL=") && !strstr(bootargs,"root=UUID"));
    ++called;
    return 0;
}

/* 測試將真正產生的合併來源插在本檔與尾段之間。 */
