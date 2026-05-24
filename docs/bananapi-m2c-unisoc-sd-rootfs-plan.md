# Banana Pi BPI-M2C UNISOC SD Boot and SD Rootfs Plan

Date: 2026-05-24

Active vendor tree:

- `/media/pi/SMCI/bpi/unisoc/sync-20260524/source_sync_rls_25c`

Armbian integration tree:

- `/media/pi/SMCI/armbian/bpi-v26.2.1`

## Current Conclusion

The `sync-20260524` code and documents do not show a complete, ready-to-use
BPI-M2C SD-card first-stage boot flow.

The current `uis7885-2h10` product is configured as an eMMC/UFS UNISOC PAC
target. The practical first storage experiment should therefore be:

1. boot SPL, U-Boot, kernel, DTB, DTBO, Trusty, and modem firmware from the
   existing vendor eMMC/UFS PAC flow;
2. place the Armbian root filesystem on SD;
3. change only the kernel root argument to mount the SD filesystem.

This is not full SD boot. It is eMMC/UFS boot plus SD rootfs.

## Evidence

Machine configuration:

- `layers/meta-unisoc/conf/machine/uis7885-2h10.conf`
- `KERNEL_BOARD = "uis7885-2h10"`
- `UBOOT_BOARD = "uis7885_2h10"`
- `CHIPRAM_SUPPORT_BOTH_EMMC_UFS = "yes"`

The machine file enables eMMC/UFS dual SPL support. It does not enable an SD
SPL build for this board.

PAC configuration:

- `prebuilts/pac_config/uis7885-2h10-uboot22.ini`
- `SPLLoaderEMMC=.../u-boot-spl-16k-emmc-sign.bin`
- `SPLLoaderUFS=.../u-boot-spl-16k-ufs-sign.bin`
- no `SPLLoaderSD` entry

The PAC layout packages signed eMMC and UFS SPL loaders only.

chipram recipe:

- `layers/meta-unisoc/recipes-bsp/chipram/chipram.bb`

For `CHIPRAM_SUPPORT_BOTH_EMMC_UFS = "yes"`, the recipe builds/deploys:

- `u-boot-spl-16k-emmc.bin`
- `u-boot-spl-16k-ufs.bin`

It does not deploy a UIS7885 SD SPL artifact in the active machine path.

chipram source:

- `source/bsp/chipram/include/configs/uis7885_2h10.h`

The header has a conditional `CONFIG_SD_BOOT` section and defines
`SDCARD_BOOT_SECTOR` only when that option is enabled. This proves that the
chipram source has a generic SD hook, but the active BPI-M2C/UIS7885 build
does not currently enable, build, sign, or package an SD SPL.

U-Boot configs:

- `source/bsp/u-boot22/configs/uis7885_2h10_defconfig`

There is no matching `uis7885_2h10_sd_defconfig` in the current U-Boot config
directory.

Secure boot configuration:

- `layers/meta-unisoc/conf/machine/include/uis7885/trusty.inc`

The code supports `SECBOOT_ENABLE=sec` and `SECBOOT_ENABLE=nosec` conditionals,
but `UNISOC_SIGN_ENABLE = "yes"` remains part of the flow. The active
`uis7885-2h10-uboot22.ini` Wayland project entries are secure-boot entries:

- `uis7885_2h10+wayland+wayland+sec+uboot22-user-native`
- `uis7885_2h10+wayland+wayland+sec+uboot22-userdebug-native`

Therefore the currently validated PAC path is secure boot. A nosec experiment
needs separate PAC project/layout work and hardware confirmation that the
target device permits non-secure first-stage images.

## Recommended Next Step

Build and flash one SD-rootfs test package.

The package contains only an Armbian ext4 rootfs image prepared for BPI-M2C,
with vendor kernel modules and firmware injected from the known-good vendor
rootfs. The existing vendor PAC remains responsible for the signed boot chain.

The boot test requires changing the kernel root argument to:

```text
root=UUID=<sd-rootfs-uuid> rootfstype=ext4 rootwait rw
```

Use UUID instead of `/dev/mmcblkXpY` because eMMC, UFS, and removable SD device
numbering can change between U-Boot and Linux.

## Test Flow

1. Generate the SD rootfs image:

   ```bash
   cd /media/pi/SMCI/armbian/bpi-v26.2.1
   tools/make-bpi-m2c-unisoc-sd-rootfs.sh --release trixie --flavor cli --force
   ```

2. Partition the SD card with one Linux ext4 partition.

3. Write the generated `rootfs.ext4` to the SD rootfs partition, not blindly to
   a whole disk unless the card was intentionally prepared that way:

   ```bash
   sudo dd if=/path/to/rootfs.ext4 of=/dev/sdX1 bs=16M conv=fsync status=progress
   ```

4. Flash the current secure vendor or hybrid PAC to eMMC/UFS.

5. Change the boot args to use the SD filesystem UUID recorded in
   `build-info.txt`.

6. Boot with UART attached and collect:

   ```bash
   cat /proc/cmdline
   findmnt /
   lsblk -f
   dmesg | grep -Ei 'mmc|sdhci|root|ext4|ufs|emmc'
   ```

## Success Criteria

- Kernel reaches userspace login.
- `/` is mounted from the SD rootfs UUID.
- Vendor modules load from `/lib/modules/5.4.180`.
- SD card appears consistently in `lsblk -f`.
- `dmesg` has no rootfs, mmc, or ext4 boot-critical errors.

## True SD Boot Investigation

Full SD first-stage boot remains a separate investigation. It should start only
after the SD rootfs test passes or after hardware boot-mode evidence is
available.

Required confirmations:

- BPI-M2C boot strap or boot-mode documentation says ROM can boot SPL from SD.
- UART ROM/SPL logs show an SD boot attempt.
- The board routes the SD slot to a ROM-supported boot controller.
- A UIS7885 SD SPL can be built and signed.
- The PAC or raw SD image layout can place SPL/U-Boot at the required offsets.

If ROM boot from SD is not supported on this board, full SD boot is not
software-fixable. The supported design would remain eMMC/UFS boot plus SD
rootfs.

## Execution Result: 2026-05-24

Generated a first trixie CLI SD-rootfs test image:

```text
/media/pi/SMCI/bpi/unisoc/sdrootfs/bpi-m2c/20260524/sync-20260524-rls-25c-armbian-trixie-cli-sdroot/rootfs.ext4
```

Build metadata:

| Item | Value |
| --- | --- |
| Baseline | `sync-20260524-rls-25c` |
| Source tree | `/media/pi/SMCI/bpi/unisoc/sync-20260524/source_sync_rls_25c` |
| Rootfs source | `/media/pi/SMCI/armbian/bpi-v26.2.1/cache/rootfs/rootfs-arm64-trixie-cli_202605-b2a670e06557-H6eccde-B8e8d00.tar.zst` |
| Rootfs size | `2048 MB` |
| Rootfs UUID | `c43f0ac5-b23c-4797-a0d4-945de5474b37` |
| Rootfs SHA256 | `eed31fc3a2a31b58b8797a41465b51e88b1c6178e2b9cf9b2139157fe363c11e` |
| Suggested kernel root | `root=UUID=c43f0ac5-b23c-4797-a0d4-945de5474b37 rootfstype=ext4 rootwait rw` |

Verification completed:

- `sha256sum -c SHA256SUMS` passed.
- Read-only loop mount succeeded.
- `/etc/armbian-release` contains `BOARD=bananapim2c`,
  `BOARDFAMILY=unisoc-uis7885-bpi`, `BRANCH=vendor`, and
  `IMAGE_TYPE=sd-rootfs-test`.
- Vendor kernel modules include `/lib/modules/5.4.180`.
- Vendor firmware was injected into `/lib/firmware`.

This artifact still needs hardware validation with the current secure vendor or
hybrid PAC boot chain.
