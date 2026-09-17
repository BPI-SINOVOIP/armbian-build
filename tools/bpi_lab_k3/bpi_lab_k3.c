// SPDX-License-Identifier: GPL-2.0+
/* K3 SDK 專用有界讀取與一次性交接；沒有持久寫入命令。 */
#include <common.h>
#include <command.h>
#include <dm.h>
#include <env.h>
#include <fs.h>
#include <image.h>
#include <lmb.h>
#include <malloc.h>
#include <mmc.h>
#include <part.h>
#include <fb_spacemit.h>
#include <asm/cache.h>
#include <asm/global_data.h>
#include <asm/io.h>
#include <u-boot/crc.h>
#include <u-boot/sha256.h>

DECLARE_GLOBAL_DATA_PTR;
extern int efuse_reload(struct udevice *dev);
extern int do_booti(struct cmd_tbl *, int, int, char *const []);

#define K3_ABI "spacemit-k3-lab-v1"
#define MAX_PAYLOAD (512UL * 1024 * 1024)
#define ENV_OFFSET 0xa0000
#define ENV_BYTES 0x4000

struct checked_load {
    ulong address, bytes, capacity;
    unsigned int device, partition;
    unsigned char sha[SHA256_SUM_LEN];
    char path[128];
    bool valid;
};

static struct checked_load loads[4];
static const char *const roles[] = { "kernel", "initrd", "dtb", "env" };
static bool begun, imported, cid_seen[256];
static u32 cid_words[256][4], approved_lcs;

static int number(const char *s, unsigned int base, ulong *value)
{
    ulong n = 0;
    unsigned int digit;
    if (!s || !*s)
        return -EINVAL;
    for (; *s; ++s) {
        if (*s >= '0' && *s <= '9')
            digit = *s - '0';
        else if (*s >= 'a' && *s <= 'f')
            digit = *s - 'a' + 10;
        else
            return -EINVAL;
        if (digit >= base || n > (ULONG_MAX - digit) / base)
            return -ERANGE;
        n = n * base + digit;
    }
    *value = n;
    return 0;
}

static bool overlap(ulong a, ulong n, ulong b, ulong m)
{
    return a < b + m && b < a + n;
}

static bool room(ulong address, ulong bytes)
{
    struct lmb lmb;
    unsigned int i;
    bool contained = false;
    if (!address || !bytes || bytes > ULONG_MAX - address)
        return false;
    for (i = 0; i < CONFIG_NR_DRAM_BANKS; ++i) {
        u64 start = gd->bd->bi_dram[i].start, size = gd->bd->bi_dram[i].size;
        if (size && address >= start && address - start < size && bytes <= size - (address - start))
            contained = true;
    }
    if (!contained)
        return false;
    lmb_init_and_reserve(&lmb, gd->bd, (void *)gd->fdt_blob);
    for (i = 0; i < lmb.reserved.cnt; ++i)
        if (overlap(address, bytes, lmb.reserved.region[i].base, lmb.reserved.region[i].size))
            return false;
    return true;
}

static void print_sha(const unsigned char *sha)
{
    unsigned int i;
    for (i = 0; i < SHA256_SUM_LEN; ++i)
        printf("%02x", sha[i]);
}

static int parse_sha(const char *s, unsigned char *sha)
{
    unsigned int i;
    ulong n;
    char pair[3] = {0};
    if (strlen(s) != 64)
        return -EINVAL;
    for (i = 0; i < SHA256_SUM_LEN; ++i) {
        memcpy(pair, s + i * 2, 2);
        if (number(pair, 16, &n))
            return -EINVAL;
        sha[i] = n;
    }
    return 0;
}

static int state(u32 *lcs)
{
    struct udevice *dev;
    fdt_addr_t base;
    if (IS_ENABLED(CONFIG_RSA_VERIFY) || get_boot_mode() != BOOT_MODE_SD)
        return -EPERM;
    if (uclass_get_device_by_driver(UCLASS_MISC, DM_DRIVER_GET(spacemit_k1x_efuse), &dev))
        return -ENODEV;
    base = dev_read_addr(dev);
    if (base == FDT_ADDR_T_NONE || efuse_reload(dev))
        return -EIO;
    /* SDK bank 6 的生命週期字組；只讀取 shadow，不讀密鑰或呼叫燒寫 API。 */
    *lcs = readl((void *)(ulong)(base + 0x164 + 4));
    return 0;
}

static int print_state(void)
{
    struct lmb lmb;
    ulong sp;
    unsigned int i;
    u32 lcs;
    if (state(&lcs))
        return CMD_RET_FAILURE;
    asm volatile("mv %0, sp" : "=r" (sp));
    printf("BPI_K3_STATE abi=%s boot_mode=sdcard rsa_verify=0 lcs=%08x\n", K3_ABI, lcs);
    printf("ram_base = 0x%lx\nram_top = 0x%lx\n", (ulong)gd->ram_base, (ulong)gd->ram_top);
    for (i = 0; i < CONFIG_NR_DRAM_BANKS; ++i) {
        if (!gd->bd->bi_dram[i].size)
            continue;
        printf("DRAM bank = 0x%x\n-> start = 0x%llx\n-> size = 0x%llx\n", i,
               (unsigned long long)gd->bd->bi_dram[i].start,
               (unsigned long long)gd->bd->bi_dram[i].size);
    }
    printf("relocaddr = 0x%lx\nsp start = 0x%lx\n", gd->relocaddr, sp);
    printf("fdt_blob = 0x%lx\nnew_fdt = 0x%lx\nfdt_size = 0x%lx\n",
           (ulong)gd->fdt_blob, (ulong)gd->new_fdt, gd->fdt_size);
    printf("video_bottom = 0x%lx\nvideo_top = 0x%lx\n", gd->video_bottom, gd->video_top);
    lmb_init_and_reserve(&lmb, gd->bd, (void *)gd->fdt_blob);
    printf("reserved.count = 0x%lx\n", lmb.reserved.cnt);
    for (i = 0; i < lmb.reserved.cnt; ++i) {
        struct lmb_property *p = &lmb.reserved.region[i];
        if (p->flags != LMB_NONE && p->flags != LMB_NOMAP)
            return CMD_RET_FAILURE;
        printf("reserved[%u] [0x%llx-0x%llx], 0x%llx bytes, flags: %s\n", i,
               (unsigned long long)p->base, (unsigned long long)(p->base + p->size - 1),
               (unsigned long long)p->size, p->flags == LMB_NONE ? "none" : "no-map");
    }
    return CMD_RET_SUCCESS;
}

static struct mmc *card(ulong device)
{
    struct mmc *mmc;
    if (device > 255)
        return NULL;
    mmc = find_mmc_device(device);
    if (!mmc || mmc_init(mmc) || mmc_get_blk_desc(mmc)->hwpart != 0 || mmc_get_blk_desc(mmc)->blksz != 512)
        return NULL;
    if (cid_seen[device] && memcmp(cid_words[device], mmc->cid, sizeof(mmc->cid)))
        return NULL;
    return mmc;
}

static int mmc_identity(const char *kind, const char *id)
{
    struct mmc *mmc;
    ulong device;
    unsigned int i;
    if (number(id, 10, &device) || (strcmp(kind, "sd") && strcmp(kind, "emmc")))
        return CMD_RET_USAGE;
    mmc = card(device);
    if (!mmc || !!IS_SD(mmc) != !strcmp(kind, "sd"))
        return CMD_RET_FAILURE;
    memcpy(cid_words[device], mmc->cid, sizeof(mmc->cid));
    cid_seen[device] = true;
    printf("BPI_K3_MMC kind=%s device=%lu bytes=%llx cid=", kind, device,
           (unsigned long long)mmc->capacity_user);
    for (i = 0; i < 4; ++i)
        printf("%08x", mmc->cid[i]);
    printf("\n");
    return CMD_RET_SUCCESS;
}

static int gpt(const char *id)
{
    struct mmc *mmc;
    struct blk_desc *desc;
    struct disk_partition info;
    ulong device;
    unsigned int i, j, count = 0;
    if (number(id, 10, &device) || !(mmc = card(device)) || !cid_seen[device])
        return CMD_RET_FAILURE;
    desc = mmc_get_blk_desc(mmc);
    part_init(desc);
    if (desc->part_type != PART_TYPE_EFI || !part_get_info(desc, 129, &info))
        return CMD_RET_FAILURE;
    for (i = 1; i <= 128; ++i) {
        if (part_get_info(desc, i, &info))
            continue;
        printf("BPI_K3_PART index=%u name=", i);
        for (j = 0; j < sizeof(info.name) && info.name[j]; ++j)
            printf("%02x", (unsigned char)info.name[j]);
        printf(" guid=%s start=%llx sectors=%llx\n", info.uuid,
               (unsigned long long)info.start, (unsigned long long)info.size);
        ++count;
    }
    printf("BPI_K3_GPT device=%lu count=%u\n", device, count);
    return count ? CMD_RET_SUCCESS : CMD_RET_FAILURE;
}

static int environment_sha(const char *id)
{
    struct mmc *mmc;
    unsigned char *data, sha[SHA256_SUM_LEN];
    ulong device;
    u32 expected;
    int result = CMD_RET_FAILURE;
    if (number(id, 10, &device) || !(mmc = card(device)) || IS_SD(mmc) || !cid_seen[device])
        return result;
    data = memalign(ARCH_DMA_MINALIGN, ENV_BYTES);
    if (!data)
        return result;
    if (blk_dread(mmc_get_blk_desc(mmc), ENV_OFFSET / 512, ENV_BYTES / 512, data) != ENV_BYTES / 512)
        goto out;
    memcpy(&expected, data, sizeof(expected));
    if (le32_to_cpu(expected) != crc32(0, data + 4, ENV_BYTES - 4))
        goto out;
    sha256_csum_wd(data, ENV_BYTES, sha, CHUNKSZ_SHA256);
    printf("BPI_K3_ENV_STORAGE device=%lu offset=a0000 bytes=4000 sha256=", device);
    print_sha(sha);
    printf("\n");
    result = CMD_RET_SUCCESS;
out:
    free(data);
    return result;
}

static int role_index(const char *role)
{
    int i;
    for (i = 0; i < ARRAY_SIZE(roles); ++i)
        if (!strcmp(role, roles[i]))
            return i;
    return -1;
}

static int sd_prefix(const char *id)
{
    struct mmc *mmc;
    sha256_context context;
    unsigned char *buffer, sha[SHA256_SUM_LEN];
    ulong device, block;
    int result = CMD_RET_FAILURE;
    if (number(id, 10, &device) || device != 0 || !(mmc = card(device))
        || !IS_SD(mmc) || !cid_seen[device] || mmc->capacity_user < 0x400000)
        return result;
    buffer = memalign(ARCH_DMA_MINALIGN, 32768);
    if (!buffer)
        return result;
    sha256_starts(&context);
    for (block = 0; block < 8192; block += 64) {
        if (blk_dread(mmc_get_blk_desc(mmc), block, 64, buffer) != 64)
            goto out;
        sha256_update(&context, buffer, 32768);
    }
    sha256_finish(&context, sha);
    printf("BPI_K3_PREFIX device=0 bytes=400000 sha256=");
    print_sha(sha);
    printf("\n");
    result = CMD_RET_SUCCESS;
out:
    free(buffer);
    return result;
}

static int hash_load(int role)
{
    unsigned char actual[SHA256_SUM_LEN];
    struct checked_load *item;
    if (role < 0 || role >= ARRAY_SIZE(loads))
        return CMD_RET_FAILURE;
    item = &loads[role];
    if (!item->valid || !room(item->address, item->capacity))
        return CMD_RET_FAILURE;
    sha256_csum_wd((void *)item->address, item->bytes, actual, CHUNKSZ_SHA256);
    if (memcmp(actual, item->sha, sizeof(actual)))
        return CMD_RET_FAILURE;
    printf("BPI_K3_HASH role=%s bytes=%lx sha256=", roles[role], item->bytes);
    print_sha(actual);
    printf("\n");
    return CMD_RET_SUCCESS;
}

static int load_file(char *const argv[])
{
    struct checked_load item = {0};
    char devpart[32];
    loff_t length, actual;
    ulong dev, part;
    int role = role_index(argv[0]), i;
    if (role < 0 || loads[role].valid || number(argv[1], 10, &dev) || number(argv[2], 10, &part)
        || dev > 255 || !part || part > 128 || !cid_seen[dev] || !card(dev)
        || number(argv[3], 16, &item.address) || number(argv[4], 16, &item.bytes)
        || number(argv[5], 16, &item.capacity) || !item.bytes || item.bytes > MAX_PAYLOAD
        || item.capacity <= item.bytes || item.capacity > MAX_PAYLOAD
        || !room(item.address, item.capacity) || parse_sha(argv[6], item.sha)
        || argv[7][0] != '/' || strlen(argv[7]) >= sizeof(item.path) || strstr(argv[7], ".."))
        return CMD_RET_FAILURE;
    for (i = 0; i < ARRAY_SIZE(loads); ++i)
        if (loads[i].valid && overlap(item.address, item.capacity, loads[i].address, loads[i].capacity))
            return CMD_RET_FAILURE;
    snprintf(devpart, sizeof(devpart), "%lx:%lx", dev, part);
    if (fs_set_blk_dev("mmc", devpart, FS_TYPE_ANY) || fs_size(argv[7], &length) || length != item.bytes)
        return CMD_RET_FAILURE;
    if (fs_set_blk_dev("mmc", devpart, FS_TYPE_ANY)
        || fs_read(argv[7], item.address, 0, item.bytes + 1, &actual) || actual != item.bytes)
        return CMD_RET_FAILURE;
    item.device = dev;
    item.partition = part;
    strcpy(item.path, argv[7]);
    item.valid = true;
    loads[role] = item;
    if (hash_load(role)) {
        loads[role].valid = false;
        return CMD_RET_FAILURE;
    }
    return CMD_RET_SUCCESS;
}

static int import_environment(void)
{
    struct checked_load *env = &loads[3];
    if (imported || !env->valid || env->bytes >= ENV_BYTES || hash_load(3))
        return CMD_RET_FAILURE;
    if (run_commandf("env import -t %lx %lx", env->address, env->bytes))
        return CMD_RET_FAILURE;
    imported = true;
    return CMD_RET_SUCCESS;
}

static int environment_value(const char *key)
{
    const char *value;
    unsigned char sha[SHA256_SUM_LEN];
    unsigned int length;
    if (!*key || strlen(key) > 63)
        return CMD_RET_USAGE;
    value = env_get(key);
    length = value ? strlen(value) : 0;
    if (length > ENV_BYTES)
        return CMD_RET_FAILURE;
    sha256_csum_wd((const unsigned char *)(value ? value : ""), length, sha, CHUNKSZ_SHA256);
    printf("BPI_K3_ENV key=%s present=%u bytes=%x sha256=", key, value != NULL, length);
    print_sha(sha);
    printf("\n");
    return CMD_RET_SUCCESS;
}

static int boot_kernel(struct cmd_tbl *cmdtp, const char *purpose)
{
    char kernel[24], initrd[48], dtb[24];
    char *argv[] = { "booti", kernel, initrd, dtb, NULL };
    struct checked_load *k = &loads[0], *r = &loads[1], *d = &loads[2];
    struct mmc *mmc;
    u32 lcs;
    int i;
    if (state(&lcs) || lcs != approved_lcs || (strcmp(purpose, "original") && strcmp(purpose, "sd-rescue")))
        return CMD_RET_FAILURE;
    for (i = 0; i < 3; ++i)
        if (hash_load(i))
            return CMD_RET_FAILURE;
    mmc = card(k->device);
    if (!mmc || k->device != r->device || k->device != d->device
        || k->partition != r->partition || k->partition != d->partition)
        return CMD_RET_FAILURE;
    if (!strcmp(purpose, "original")) {
        if (IS_SD(mmc) || !imported || strcmp(k->path, "/Image")
            || strcmp(r->path, "/initramfs-generic.img") || strcmp(d->path, "/dtb/spacemit/k3-bananapi-sm10.dtb"))
            return CMD_RET_FAILURE;
    } else if (!IS_SD(mmc) || imported || strcmp(k->path, "/bpi-lab/k3/Image")
               || strcmp(r->path, "/bpi-lab/k3/initrd") || strcmp(d->path, "/bpi-lab/k3/board.dtb")) {
        return CMD_RET_FAILURE;
    }
    snprintf(kernel, sizeof(kernel), "%lx", k->address);
    snprintf(initrd, sizeof(initrd), "%lx:%lx", r->address, r->bytes);
    snprintf(dtb, sizeof(dtb), "%lx", d->address);
    if (env_set("fdt_addr", dtb))
        return CMD_RET_FAILURE;
    begun = false;
    /* 呼叫原廠 booti，保留 chosen 合併、boot_mode 與 ft_board_setup。 */
    return do_booti(cmdtp, 0, 4, argv);
}

static int do_bpi_k3(struct cmd_tbl *cmdtp, int flag, int argc, char *const argv[])
{
    ulong value;
    u32 lcs;
    int result = CMD_RET_USAGE;
    if (argc == 2 && !strcmp(argv[1], "state"))
        return print_state();
    if (argc == 3 && !strcmp(argv[1], "begin")) {
        begun = false;
        if (number(argv[2], 16, &value) || value > 0xffffffffUL || state(&lcs) || lcs != value)
            return CMD_RET_FAILURE;
        memset(loads, 0, sizeof(loads));
        memset(cid_seen, 0, sizeof(cid_seen));
        approved_lcs = lcs;
        imported = false;
        begun = true;
        return CMD_RET_SUCCESS;
    }
    if (!begun)
        return CMD_RET_FAILURE;
    if (argc == 4 && !strcmp(argv[1], "mmc"))
        result = mmc_identity(argv[2], argv[3]);
    else if (argc == 3 && !strcmp(argv[1], "gpt"))
        result = gpt(argv[2]);
    else if (argc == 3 && !strcmp(argv[1], "envsha"))
        result = environment_sha(argv[2]);
    else if (argc == 3 && !strcmp(argv[1], "prefix"))
        result = sd_prefix(argv[2]);
    else if (argc == 10 && !strcmp(argv[1], "load"))
        result = load_file(argv + 2);
    else if (argc == 3 && !strcmp(argv[1], "hash"))
        result = hash_load(role_index(argv[2]));
    else if (argc == 2 && !strcmp(argv[1], "import"))
        result = import_environment();
    else if (argc == 3 && !strcmp(argv[1], "env"))
        result = environment_value(argv[2]);
    else if (argc == 3 && !strcmp(argv[1], "boot"))
        result = boot_kernel(cmdtp, argv[2]);
    if (result)
        begun = false;
    return result;
}

U_BOOT_CMD(bpi_k3, 10, 0, do_bpi_k3,
           "K3 唯讀狀態、有界載入及原廠一次性交接",
           "state | begin <lcs> | mmc <sd|emmc> <device> | gpt <device> | envsha <device>\n"
           "bpi_k3 load <role> <device> <partition> <address> <bytes> <capacity> <sha256> <path>\n"
           "bpi_k3 hash <role> | env <key> | prefix <sd-device> | import | boot <original|sd-rescue>");
