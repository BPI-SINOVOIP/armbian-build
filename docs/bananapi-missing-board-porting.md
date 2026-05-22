# Banana Pi Missing Board Porting Notes

Branch: `bpi-v26.8.0-trunk`

Date: 2026-05-22

## Rule

Do not add a missing Banana Pi board file to the default release matrix until one server image can be built far enough to validate bootloader packaging and kernel DTB selection. A `.wip` board that is known to fail would make the 2026 matrix noisier without helping the release.

## BPI-R3 First Assessment

BPI-R3 is the first missing-board candidate because BPI-SINOVOIP has R3 BSP/OpenWrt sources, while this branch does not have `config/boards/bananapir3.*`.

Relevant sources checked:

- `https://github.com/BPI-SINOVOIP/BPI-R3-bsp`
- `https://github.com/BPI-SINOVOIP/BPI-R3-bsp-5.15`
- Local `config/sources/families/filogic.conf`
- Local `patch/kernel/archive/filogic-6.12`
- Local `patch/kernel/archive/filogic-6.16`
- Local U-Boot worktrees under `cache/sources/u-boot-worktree/u-boot`

## What Already Exists

Kernel side:

- BPI-R3 DT sources are present in local kernel worktrees:
  - `mt7986a-bananapi-bpi-r3.dts`
  - `mt7986a-bananapi-bpi-r3-mini.dts`
  - `mt7986a-bananapi-bpi-r3-sd.dtso`
  - `mt7986a-bananapi-bpi-r3-emmc.dtso`
  - `mt7986a-bananapi-bpi-r3-nand.dtso`
  - `mt7986a-bananapi-bpi-r3-nor.dtso`
  - `mt7986a-bananapi-bpi-r3-sata.dtso`
- `patch/kernel/archive/filogic-6.12/patches.armbian/mt7988a-bananapi-bpi-r4-sd.patch` already references R3 composite DTB targets.
- `patch/kernel/archive/filogic-6.16/patches.armbian/0039-enable-bpi-r3-DTBs-for-testing.patch` enables R3/R3 Mini DTBs for the 6.16 filogic patchset.

U-Boot side:

- Local U-Boot worktrees contain modern R3 defconfigs:
  - `mt7986a_bpir3_sd_defconfig`
  - `mt7986a_bpir3_emmc_defconfig`
- The BPI R3 BSP 5.15 tree uses older U-Boot config names:
  - `mt7986a_bpi-r3-sd_config`
  - `mt7986a_bpi-r3-emmc_config`

BPI BSP ATF/U-Boot build details:

- SDMMC ATF target:
  - `PLAT=mt7986`
  - `BOOT_DEVICE=sdmmc`
  - `DRAM_USE_DDR4=1`
  - `HAVE_DRAM_OBJ_FILE=yes`
- EMMC ATF target:
  - `PLAT=mt7986`
  - `BOOT_DEVICE=emmc`
  - `DRAM_USE_DDR4=1`
  - `HAVE_DRAM_OBJ_FILE=yes`
- BPI BSP FIP creation:
  - `fiptool create --soc-fw atf-mt/build_sdmmc/mt7986/release/bl31.bin --nt-fw u-boot-mt/build_sdmmc/u-boot.bin u-boot-mt/build_sdmmc/u-boot_sdmmc.fip`
  - `fiptool create --soc-fw atf-mt/build_emmc/mt7986/release/bl31.bin --nt-fw u-boot-mt/build_emmc/u-boot.bin u-boot-mt/build_emmc/u-boot_emmc.fip`

## Initial Blocker

The original Armbian `filogic` family was R4/MT7988-specific:

- `ATF_TARGET_MAP` is hardcoded to `PLAT=mt7988`.
- `uboot_custom_postprocess()` copies `build/mt7988/release/bl2.img`.
- `uboot_custom_postprocess()` uses `build/mt7988/release/bl31.bin`.
- `bananapir4.csc` uses `BOOTCONFIG="mt7988a_bananapi_bpi-r4-sdmmc_defconfig"`.
- Runtime firmware copy logic only installs `packages/blobs/filogic/firmware/mediatek/mt7988/*`.

Because of this, adding `config/boards/bananapir3.wip` alone would select the wrong ATF platform and produce a known-bad bootloader path.

## R3 WIP Implementation

Implemented in this branch:

- `config/sources/families/filogic.conf` now allows board files to override:
  - `FILOGIC_SOC`
  - `FILOGIC_BOOT_DEVICE`
  - `FILOGIC_ATF_FLAGS`
- Existing R4 defaults remain `mt7988`, `sdmmc`, and the previous R4 ATF flags.
- `config/boards/bananapir3.wip` starts R3 with SDMMC only:
  - `BOOTCONFIG="mt7986a_bpir3_sd_defconfig"`
  - `BOOT_FDT_FILE="mediatek/mt7986a-bananapi-bpi-r3-sd-nor.dtb"`
  - `FILOGIC_SOC="mt7986"`
  - `FILOGIC_ATF_FLAGS="DRAM_USE_DDR4=1 HAVE_DRAM_OBJ_FILE=yes"`
  - `HAS_VIDEO_OUTPUT="no"`

Validation completed:

- U-Boot/ATF package smoke build passed:

```bash
./compile.sh uboot BOARD=bananapir3 BRANCH=current RELEASE=trixie EXPERT=yes
```

- Trixie server smoke image build passed:

```bash
./compile.sh build BOARD=bananapir3 BRANCH=current RELEASE=trixie BUILD_DESKTOP=no BUILD_MINIMAL=no KERNEL_CONFIGURE=no EXPERT=yes COMPRESS_OUTPUTIMAGE=xz
```

- Output image:
  - `output/images/Armbian-unofficial_26.05.0-trunk_Bananapir3_trixie_current_6.12.82.img.xz`
- `xz -t` passed for the generated image.

Keep R3 as `.wip` until a real board boot test confirms UART, storage, Ethernet, and reset behavior. Do not add R3 to the default 2026 release matrix before that hardware validation.

## R3 Mini WIP Implementation

Implemented in this branch:

- `config/boards/bananapir3mini.wip` starts R3 Mini with MT7986 eMMC boot:
  - `BOOTCONFIG="mt7986a_bpir3mini_emmc_defconfig"`
  - `BOOT_FDT_FILE="mediatek/mt7986a-bananapi-bpi-r3-mini.dtb"`
  - `FILOGIC_SOC="mt7986"`
  - `FILOGIC_BOOT_DEVICE="emmc"`
  - `FILOGIC_ATF_FLAGS="DRAM_USE_DDR4=1 HAVE_DRAM_OBJ_FILE=yes"`
- `config/sources/families/filogic.conf` now allows a board-specific `FILOGIC_FIP_NAME`, so R3/R4 can keep `u-boot_sdmmc.fip` while R3 Mini packages and writes `u-boot_emmc.fip`.
- `patch/u-boot/u-boot-filogic/452-add-bpi-r3-mini-defconfig.patch` adds the eMMC U-Boot defconfig.
- `patch/u-boot/u-boot-filogic/453-add-bpi-r3-mini-u-boot-dts.patch` adds a small U-Boot-local DTS for UART and eMMC early boot. This is needed because the upstream Linux R3 Mini DTS in U-Boot v2025.04 depends on newer clock bindings than that U-Boot tree carries.

Validation completed:

- U-Boot/ATF package smoke build passed:

```bash
./compile.sh uboot BOARD=bananapir3mini BRANCH=current RELEASE=trixie EXPERT=yes
```

- Trixie server smoke image build passed:

```bash
./compile.sh build BOARD=bananapir3mini BRANCH=current RELEASE=trixie BUILD_DESKTOP=no BUILD_MINIMAL=no KERNEL_CONFIGURE=no EXPERT=yes COMPRESS_OUTPUTIMAGE=xz
```

- Output image:
  - `output/images/Armbian-unofficial_26.05.0-trunk_Bananapir3mini_trixie_current_6.12.82.img.xz`
- `xz -t` and sha256 validation passed for the generated image.

Keep R3 Mini as `.wip` until a real board boot test confirms eMMC boot, Ethernet, reset, and UART behavior.

## W3 WIP Implementation

Implemented in this branch:

- `config/boards/bananapiw3.wip` sources `armsom-w3.csc`, changes the visible board identity to Banana Pi W3, and selects `rockchip/rk3588-bananapi-w3.dtb`.
- `patch/kernel/rk35xx-vendor-6.1/dt/rk3588-bananapi-w3.dts` wraps `rk3588-armsom-w3.dts` with Banana Pi W3 model and compatible strings.
- The board is intentionally limited to the RK3588 vendor branch until hardware validation proves boot media, storage, network, and display behavior.

Validation completed:

- Trixie server smoke image build passed:

```bash
./compile.sh build BOARD=bananapiw3 BRANCH=vendor RELEASE=trixie BUILD_DESKTOP=no BUILD_MINIMAL=no KERNEL_CONFIGURE=no EXPERT=yes COMPRESS_OUTPUTIMAGE=xz
```

- Output image:
  - `output/images/Armbian-unofficial_26.05.0-trunk_Bananapiw3_trixie_vendor_6.1.115.img.xz`
- `xz -t` and sha256 validation passed for the generated image.
- The generated DTB package contains:
  - `rockchip/rk3588-bananapi-w3.dtb`

Keep W3 as `.wip` until a real board boot test confirms storage, Ethernet, display, and boot media behavior.

## R64 WIP Implementation

Implemented in this branch:

- `config/boards/bananapir64.wip` adds Banana Pi R64 as an MT7622 board on the existing `filogic` family.
- `patch/u-boot/u-boot-filogic/454-add-bpi-r64-defconfig.patch` adds `mt7622_bananapi_bpi-r64-sdmmc_defconfig`, derived from the generic MT7622 RFB config but using the Banana Pi R64 device tree and prompt.
- The kernel DTB already exists in the current filogic DTB package:
  - `mediatek/mt7622-bananapi-bpi-r64.dtb`
- The board sets:
  - `BOOTCONFIG="mt7622_bananapi_bpi-r64-sdmmc_defconfig"`
  - `BOOT_FDT_FILE="mediatek/mt7622-bananapi-bpi-r64.dtb"`
  - `FILOGIC_SOC="mt7622"`
  - `FILOGIC_BOOT_DEVICE="sdmmc"`
  - `FILOGIC_FIP_NAME="u-boot_sdmmc.fip"`

Validation completed:

- U-Boot/ATF package smoke build passed:

```bash
./compile.sh uboot BOARD=bananapir64 BRANCH=current RELEASE=trixie EXPERT=yes
```

- Trixie server smoke image build passed:

```bash
./compile.sh build BOARD=bananapir64 BRANCH=current RELEASE=trixie BUILD_DESKTOP=no BUILD_MINIMAL=no KERNEL_CONFIGURE=no EXPERT=yes COMPRESS_OUTPUTIMAGE=xz
```

- Output image:
  - `output/images/Armbian-unofficial_26.05.0-trunk_Bananapir64_trixie_current_6.12.82.img.xz`
- `xz -t` and sha256 validation passed for the generated image.

Important hardware note:

- The legacy BPI-R64 BSP writes `preloader_bpi-r64_forsdcard-2k.img` at 2 KiB, `BPI-R64-atf.img` at 512 KiB, and `u-boot-mtk.bin` at 768 KiB.
- This WIP Armbian path uses the current mtk-openwrt ATF/FIP layout (`bl2.img` plus `u-boot_sdmmc.fip`) from the shared `filogic` family.
- Keep R64 as `.wip` until a real board boot test confirms the modern ATF/FIP layout, SDMMC boot, Ethernet, and reset behavior.

## W2 BSP Porting Assessment

Checked BPI source:

- Repository: `https://github.com/BPI-SINOVOIP/BPI-W2-bsp`
- Checked commit: `6e6aefc35`
- Branches found: `master`, `rtk1296_gl`, `rtk1296_ow`

What the BSP provides:

- Realtek RTD1296 board DTS:
  - `linux-rtk/arch/arm64/boot/dts/realtek/rtd129x/rtd-1296-bananapi-w2-2GB.dts`
- Kernel defconfig:
  - `linux-rtk/arch/arm64/configs/rtd129x_bpi_defconfig`
- U-Boot defconfigs:
  - `u-boot-rtk/configs/rtd1296_sd_bananapi_defconfig`
  - `u-boot-rtk/configs/rtd1296_emmc_bananapi_defconfig`
  - `u-boot-rtk/configs/rtd1296_spi_bananapi_defconfig`
- BSP generation uses U-Boot 2015.7 and Linux 4.9.119.
- The BSP expects the old BPI boot layout:
  - boot files under `bananapi/bpi-w2/linux/`
  - `uImage`, `uInitrd`, `rtd-1296-bananapi-w2-2GB.dtb`, and `bluecore.audio`
  - root defaults to `/dev/mmcblk0p2`
  - bootloader package `100MB/BPI-W2-720P-2k.img.gz`
- `scripts/bootloader.sh` creates a 1 MiB temporary image, writes `rtk-pack/rtk/bpi-w2/bin/u-boot.bin` at `bs=1k seek=40`, then exports the 2 KiB-offset image used by `bpi-bootsel`.

## W2 WIP Implementation

Implemented in this branch:

- `config/boards/bananapiw2.wip` adds Banana Pi W2 as a Realtek RTD1296 legacy BSP board.
- `config/sources/families/realtek-rtd129x-bpi.conf` and the shared `realtek_bpi_legacy_common.inc` add custom Realtek BSP build hooks.
- The U-Boot path builds the vendor monorepo through `./configure BPI-W2-720P && make u-boot`, then packages `u-boot-rtk/u-boot.bin`, vendor `uEnv.txt`, and `bluecore.audio`.
- The kernel path uses the vendor Linux 4.9.119 tree under `linux-rtk`, with the vendor `rtd129x_bpi_defconfig`. The shared Realtek hook flattens `linux-rtk` before packaging and forces Armbian metadata to use kernel version `4.9.119` instead of the BSP monorepo top-level fallback `0`.
- The image path uses a FAT `/boot` partition and installs the old BPI Realtek boot layout under `bananapi/bpi-w2/linux/`.
- Host build fixes are carried as Armbian patches:
  - U-Boot host tools use the BSP-local libfdt headers instead of the system libfdt headers.
  - Linux dtc removes the duplicate `YYLTYPE yylloc` definition that fails with modern host toolchains.

Validation completed outside Armbian before integration:

- Vendor W2 U-Boot build passed and produced `u-boot-rtk/u-boot.bin`.
- Vendor W2 kernel build passed and produced `Image` plus `rtd-1296-bananapi-w2-2GB.dtb`.

Armbian smoke validation:

- U-Boot package build passed:

```bash
./compile.sh uboot BOARD=bananapiw2 BRANCH=legacy RELEASE=trixie EXPERT=yes
```

- Kernel package build passed:

```bash
./compile.sh kernel BOARD=bananapiw2 BRANCH=legacy RELEASE=trixie EXPERT=yes KERNEL_CONFIGURE=no
```

- Kernel packaging produced `linux-image`, `linux-dtb`, and `linux-libc-dev` packages for `realtek-rtd129x-bpi`.
- Linux headers are intentionally skipped for this 4.9 legacy BSP path.
- Trixie server image build passed:

```bash
./compile.sh build BOARD=bananapiw2 BRANCH=legacy RELEASE=trixie BUILD_DESKTOP=no BUILD_MINIMAL=no KERNEL_CONFIGURE=no EXPERT=yes COMPRESS_OUTPUTIMAGE=xz
```

- Output image:
  - `output/images/Armbian-unofficial_26.05.0-trunk_Bananapiw2_trixie_legacy_4.9.119.img.xz`
- `xz -t` passed.
- SHA256:
  - `7ad63ba2b85b033a332bf3c84eb5f403378f14880bdb95a7191ba0c74a84dd8f`
- Offline FAT boot layout check passed for:
  - `uEnv.txt`
  - `bananapi/bpi-w2/linux/uEnv.txt`
  - `bananapi/bpi-w2/linux/bluecore.audio`
  - `bananapi/bpi-w2/linux/uImage`
  - `bananapi/bpi-w2/linux/uInitrd`
  - `bananapi/bpi-w2/linux/rtd-1296-bananapi-w2-2GB.dtb`

Remaining WIP risk:

- The current Armbian board entry proves the BSP source, U-Boot package, kernel package, initramfs, and offline FAT boot layout paths, but still needs a boot test on real W2 hardware before it can enter the release matrix.

## F2S BSP Porting Assessment

Checked BPI source:

- Repository: `https://github.com/BPI-SINOVOIP/BPI-F2S-bsp`
- Checked commit: `3eee97bd8`
- Branches found: `master`, `sp-kernel-4.19.37`

What the BSP provides:

- Sunplus SP7021 board DTS:
  - `linux-sp/arch/arm/boot/dts/sp7021-bpi-f2s.dts`
  - `u-boot-sp/arch/arm/dts/sp7021-bpi-f2s.dts`
- Kernel defconfig:
  - `linux-sp/arch/arm/configs/sp7021_chipC_bpi-f2s_defconfig`
- U-Boot defconfig:
  - `u-boot-sp/configs/sp7021_bpi_f2s_defconfig`
- BSP generation uses U-Boot 2019.4 and Linux 5.4.35.
- `configure bpi-f2s` selects:
  - `ARCH=arm`
  - `UBOOT_CONFIG=sp7021_bpi_f2s_defconfig`
  - `KERNEL_CONFIG=sp7021_chipC_bpi-f2s_defconfig`
  - `KERNEL_DTB=sp7021-bpi-f2s.dtb`
- The BSP expects the old BPI boot layout:
  - FAT boot file `/ISPBOOOT.BIN` for xboot
  - `u-boot.img`
  - boot files under `bananapi/bpi-f2s/linux/`
  - `uImage`, `uInitrd`, and `sp7021-bpi-f2s.dtb`
- `scripts/bootloader.sh` writes `u-boot.img` at `bs=512 seek=34` and exports a 2 KiB-offset image.
- The BSP also provides prebuilt common boot assets:
  - `sp-pack/sp7021/common/bin/BPI-F2S-xboot-emmc-boot0-0k.img.gz`
  - `sp-pack/sp7021/common/bin/ISPBOOOT.BIN`

## F2S WIP Implementation

Implemented in this branch:

- `config/boards/bananapif2s.wip` adds Banana Pi F2S as a Sunplus SP7021 legacy BSP board.
- `config/sources/families/sunplus-sp7021-bpi.conf` and `sunplus_sp7021_bpi_legacy_common.inc` add custom BSP build hooks for the vendor monorepo.
- The U-Boot path builds through `./configure bpi-f2s`, `u-boot-sp`, and packages `u-boot.img`, `ISPBOOOT.BIN`, and `BPI-F2S-xboot-emmc-boot0-0k.img.gz`.
- The kernel path uses the vendor Linux 5.4.35 tree under `linux-sp`, with `sp7021_chipC_bpi-f2s_defconfig` imported as the Armbian legacy kernel config.
- `CONFIG_RD_GZIP=y` is enabled locally so Armbian's generated gzip initramfs can boot.
- The image path uses a FAT `/boot` partition and installs the BPI boot layout:
  - `/ISPBOOOT.BIN`
  - `/uEnv.txt`
  - `/bananapi/bpi-f2s/linux/uImage`
  - `/bananapi/bpi-f2s/linux/uInitrd`
  - `/bananapi/bpi-f2s/linux/sp7021-bpi-f2s.dtb`
- U-Boot is written to the raw image at `bs=512 seek=34`, matching the BSP `scripts/bootloader.sh` layout.

Validation completed outside Armbian before integration:

- Vendor F2S U-Boot build passed and produced `u-boot-sp/u-boot.img`.
- Vendor F2S kernel build passed and produced `uImage`, `zImage`, `sp7021-bpi-f2s.dtb`, and `sp7021-bpi-f2p.dtb`.

Armbian smoke validation:

- U-Boot package build passed:

```bash
./compile.sh uboot BOARD=bananapif2s BRANCH=legacy RELEASE=trixie EXPERT=yes
```

- Kernel package build passed:

```bash
./compile.sh kernel BOARD=bananapif2s BRANCH=legacy RELEASE=trixie EXPERT=yes KERNEL_CONFIGURE=no
```

- Trixie server smoke image build passed:

```bash
./compile.sh build BOARD=bananapif2s BRANCH=legacy RELEASE=trixie BUILD_DESKTOP=no BUILD_MINIMAL=no KERNEL_CONFIGURE=no EXPERT=yes COMPRESS_OUTPUTIMAGE=xz
```

- Output image:
  - `output/images/Armbian-unofficial_26.05.0-trunk_Bananapif2s_trixie_legacy_0.img.xz`
- `xz -t` passed.
- FAT boot partition inspection confirmed `ISPBOOOT.BIN`, `uEnv.txt`, and the BPI `bananapi/bpi-f2s/linux/` boot files are present.
- Image SHA-256:
  - `abaa350847b2a1504376a287a76468cb73c1b3f1cef32c57816e66ec53527059`

Keep F2S as `.wip` until a real board boot test confirms xboot, U-Boot, SD/eMMC root selection, Ethernet, UART, and reset behavior.

## R4 Lite / R4 Pro OpenWrt Porting Assessment

Checked BPI sources:

- R4 Lite repository: `https://github.com/BPI-SINOVOIP/BPI-R4Lite-OPENWRT-V24.10.0-Master-Devel`
- R4 Lite checked commit: `42f4c647`
- R4 Pro repository: `https://github.com/BPI-SINOVOIP/BPI-R4PRO-8X-OPENWRT-V24.10.0-Master-Devel`
- R4 Pro checked commit: `56e0e77a`

What the BPI OpenWrt trees provide:

- R4 Lite kernel DTS and overlays:
  - `target/linux/mediatek/files-6.6/arch/arm64/boot/dts/mediatek/mt7987a-bananapi-bpi-r4-lite.dts`
  - `mt7987a-bananapi-bpi-r4-lite-sd.dtso`
  - `mt7987a-bananapi-bpi-r4-lite-emmc.dtso`
  - `mt7987a-bananapi-bpi-r4-lite-spim-nand.dtso`
  - `mt7987a-bananapi-bpi-r4-lite-spim-nor.dtso`
- R4 Lite U-Boot and ATF patches:
  - `package/boot/uboot-mediatek/patches/999-add-bananapi-bpi-r4-lite.patch`
  - `package/boot/arm-trusted-firmware-mediatek/patches/9999-mediatek-bananapi-bpi-r4-ite-atf-fixed.patch`
- R4 Pro 8X kernel DTS and overlays:
  - `target/linux/mediatek/files-6.6/arch/arm64/boot/dts/mediatek/mt7988a-bananapi-bpi-r4-pro-8x.dts`
  - `mt7988a-bananapi-bpi-r4-pro-8x-sd.dtso`
  - `mt7988a-bananapi-bpi-r4-pro-8x-emmc.dtso`
  - `mt7988a-bananapi-bpi-r4-pro-8x-rtc.dtso`
  - `mt7988a-bananapi-bpi-r4-pro-8x-wifi-mt7996a.dtso`
- R4 Pro U-Boot and kernel patches:
  - `package/boot/uboot-mediatek/patches/999-add-bananapi_bpi-r4-pro-8x.patch`
  - `target/linux/mediatek/patches-6.6/999-9800-mt7988a-bananapi-bpi-r4pro-support-multiple-dsa-switch-fixed.patch`

Local Armbian status:

- Existing `bananapir4.csc` is MT7988A BPI-R4 only.
- The current filogic DTB package contains BPI-R4 DTBs, but not R4 Lite or R4 Pro DTBs:
  - `mediatek/mt7988a-bananapi-bpi-r4.dtb`
  - `mediatek/mt7988a-bananapi-bpi-r4-sd.dtb`
  - `mediatek/mt7988a-bananapi-bpi-r4-emmc.dtb`
- Local U-Boot v2025.04 has generic MT7987 RFB defconfigs and BPI-R4 defconfigs, but no BPI-R4 Lite or BPI-R4 Pro defconfigs.
- A newer cached Linux tree already has upstream-style R4 Pro DT sources under `arch/arm64/boot/dts/mediatek`, but the active `filogic/current` build uses the 6.12 DTB package where those files are not present.

## R4 Lite / R4 Pro WIP Implementation

Implemented in this branch:

- `config/sources/families/filogic.conf` now lets a board override kernel source branch, kernel major/minor, patch directory, and kernel config file while keeping the existing R4 defaults unchanged.
- `config/boards/bananapir4lite.wip` adds BPI-R4 Lite as an MT7987 SDMMC board:
  - `BOOTCONFIG="mt7987a_bananapi_bpi-r4-lite-sdmmc_defconfig"`
  - `BOOT_FDT_FILE="mediatek/mt7987a-bananapi-bpi-r4-lite-sd.dtb"`
  - `FILOGIC_SOC="mt7987"`
  - `FILOGIC_BOOT_DEVICE="sdmmc"`
  - `FILOGIC_KERNELBRANCH="branch:6.17-r4lite"`
  - `FILOGIC_KERNEL_MAJOR_MINOR="6.17"`
- `config/boards/bananapir4pro.wip` adds BPI-R4 Pro 8X as an MT7988 SDMMC board:
  - `BOOTCONFIG="mt7988a_bananapi_bpi-r4-pro-8x-sdmmc_defconfig"`
  - `BOOT_FDT_FILE="mediatek/mt7988a-bananapi-bpi-r4-pro-8x-sd.dtb"`
  - `FILOGIC_KERNELBRANCH="branch:6.19-mtkdts"`
  - `FILOGIC_KERNEL_MAJOR_MINOR="6.19"`
- `patch/u-boot/u-boot-filogic/455-add-bpi-r4-pro-8x.patch` imports the R4 Pro U-Boot support.
- `patch/u-boot/u-boot-filogic/456-add-bpi-r4-lite-sd-emmc.patch` imports the R4 Lite SD/eMMC U-Boot support.
- `patch/kernel/archive/filogic-6.17/patches.armbian/mt7987a-bananapi-bpi-r4-lite-sd.patch` adds SD/eMMC composite DTB build targets for Armbian's single-DTB boot flow.

Validation completed:

- R4 Pro U-Boot/ATF package build passed:

```bash
./compile.sh uboot BOARD=bananapir4pro BRANCH=current RELEASE=trixie EXPERT=yes
```

- R4 Pro Trixie server smoke image build passed:

```bash
./compile.sh build BOARD=bananapir4pro BRANCH=current RELEASE=trixie BUILD_DESKTOP=no BUILD_MINIMAL=no KERNEL_CONFIGURE=no EXPERT=yes COMPRESS_OUTPUTIMAGE=xz
```

- R4 Pro output image:
  - `output/images/Armbian-unofficial_26.05.0-trunk_Bananapir4pro_trixie_current_6.19.0-rc1.img.xz`
- R4 Pro `xz -t` passed, and the DTB package contains R4 Pro 8X SD/eMMC DTBs.

- R4 Lite U-Boot/ATF package build passed:

```bash
./compile.sh uboot BOARD=bananapir4lite BRANCH=current RELEASE=trixie EXPERT=yes
```

- R4 Lite Trixie server smoke image build passed:

```bash
./compile.sh build BOARD=bananapir4lite BRANCH=current RELEASE=trixie BUILD_DESKTOP=no BUILD_MINIMAL=no KERNEL_CONFIGURE=no EXPERT=yes COMPRESS_OUTPUTIMAGE=xz
```

- R4 Lite output image:
  - `output/images/Armbian-unofficial_26.05.0-trunk_Bananapir4lite_trixie_current_6.17.0-rc1.img.xz`
- R4 Lite `xz -t` passed, and the DTB package contains:
  - `mediatek/mt7987a-bananapi-bpi-r4-lite-sd.dtb`
  - `mediatek/mt7987a-bananapi-bpi-r4-lite-emmc.dtb`

Keep R4 Lite and R4 Pro as `.wip` until real board boot tests confirm bootloader layout, SD/eMMC boot, Ethernet, reset, and UART behavior.

## BPI-M4 Plain RTD1395 Porting Assessment

Checked BPI source:

- Repository: `https://github.com/BPI-SINOVOIP/BPI-M4-bsp`
- Checked commit: `25f5b88e`

What the BSP provides:

- Realtek RTD1395 board DTS files:
  - `linux-rtk/arch/arm64/boot/dts/realtek/rtd139x/rtd-1395-bananapi-m4-1GB.dts`
  - `linux-rtk/arch/arm64/boot/dts/realtek/rtd139x/rtd-1395-bananapi-m4-2GB.dts`
- Kernel defconfig:
  - `linux-rtk/arch/arm64/configs/rtd139x_bpi_defconfig`
- U-Boot defconfigs:
  - `u-boot-rtk/configs/rtd1395_bananapi_defconfig`
  - `u-boot-rtk/configs/rtd1395_sd_bananapi_defconfig`
  - `u-boot-rtk/configs/rtd1395_emmc_bananapi_defconfig`
  - `u-boot-rtk/configs/rtd1395_spi_bananapi_defconfig`
- BSP generation uses U-Boot 2015.7 and Linux 4.9.119.
- The BSP boot layout matches the old Realtek BPI style:
  - boot files under `bananapi/bpi-m4/linux/`
  - `uImage`, `uInitrd`, `rtd-1395-bananapi-m4-1GB.dtb` or `rtd-1395-bananapi-m4-2GB.dtb`, and `bluecore.audio`
  - `scripts/bootloader.sh` writes `rtk-pack/rtk/bpi-m4/bin/u-boot.bin` at `bs=1k seek=40` and exports a 2 KiB-offset image.

Local Armbian status:

- Existing `bananapim4berry.conf` and `bananapim4zero.conf` are Allwinner H618 boards on `sun50iw9-bpi`.
- BPI-M4 plain is Realtek RTD1395, so it is not covered by M4 Berry or M4 Zero.
- Existing local Realtek support is `realtek-rtd1619b` for XpressReal T3, not RTD1395.

## BPI-M4 Plain RTD1395 WIP Implementation

Implemented in this branch:

- `config/boards/bananapim4.wip` adds the plain Banana Pi M4 as a Realtek RTD1395 legacy BSP board.
- `config/sources/families/realtek-rtd139x-bpi.conf` reuses the shared Realtek BSP hooks added for W2.
- The U-Boot path builds the vendor monorepo through `./configure BPI-M4-720P && make u-boot`, then packages `u-boot-rtk/u-boot.bin`, vendor `uEnv.txt`, and `bluecore.audio`.
- The kernel path uses the vendor Linux 4.9.119 tree under `linux-rtk`, with the vendor `rtd139x_bpi_defconfig`. The shared Realtek hook flattens `linux-rtk` before packaging and forces Armbian metadata to use kernel version `4.9.119` instead of the BSP monorepo top-level fallback `0`.
- The image path uses a FAT `/boot` partition and installs the old BPI Realtek boot layout under `bananapi/bpi-m4/linux/`.
- Host build fixes are carried as Armbian patches for the M4 U-Boot/libfdt and Linux dtc issues.

Validation completed outside Armbian before integration:

- Vendor M4 U-Boot build passed and produced `u-boot-rtk/u-boot.bin`.
- Vendor M4 kernel build passed and produced `Image` plus both `rtd-1395-bananapi-m4-1GB.dtb` and `rtd-1395-bananapi-m4-2GB.dtb`.

Armbian smoke validation:

- U-Boot package build passed:

```bash
./compile.sh uboot BOARD=bananapim4 BRANCH=legacy RELEASE=trixie EXPERT=yes
```

- Kernel package build passed:

```bash
./compile.sh kernel BOARD=bananapim4 BRANCH=legacy RELEASE=trixie EXPERT=yes KERNEL_CONFIGURE=no
```

- Kernel packaging produced `linux-image`, `linux-dtb`, and `linux-libc-dev` packages for `realtek-rtd139x-bpi`.
- Linux headers are intentionally skipped for this 4.9 legacy BSP path.
- Trixie server image build passed:

```bash
./compile.sh build BOARD=bananapim4 BRANCH=legacy RELEASE=trixie BUILD_DESKTOP=no BUILD_MINIMAL=no KERNEL_CONFIGURE=no EXPERT=yes COMPRESS_OUTPUTIMAGE=xz
```

- Output image:
  - `output/images/Armbian-unofficial_26.05.0-trunk_Bananapim4_trixie_legacy_4.9.119.img.xz`
- `xz -t` passed.
- SHA256:
  - `6276c598a46e63c2d769511c0538cc06bcd5646dfcacf161d514966d0ed6d25b`
- Offline FAT boot layout check passed for:
  - `uEnv.txt`
  - `bananapi/bpi-m4/linux/uEnv.txt`
  - `bananapi/bpi-m4/linux/bluecore.audio`
  - `bananapi/bpi-m4/linux/uImage`
  - `bananapi/bpi-m4/linux/uInitrd`
  - `bananapi/bpi-m4/linux/rtd-1395-bananapi-m4-1GB.dtb`
  - `bananapi/bpi-m4/linux/rtd-1395-bananapi-m4-2GB.dtb`

Remaining WIP risk:

- The current Armbian board entry proves the BSP source, U-Boot package, kernel package, initramfs, and offline FAT boot layout paths, but still needs a boot test on real M4 hardware before it can enter the release matrix.

## BPI-M6 VS680 WIP Implementation

Implemented in this branch:

- `config/boards/bananapim6.wip` adds Banana Pi M6 as a Synaptics VS680 legacy BSP board.
- `config/sources/families/vs680.conf` restores the VS680 family path from older BPI Armbian work and adds safer packaging checks.
- `config/bootscripts/boot-vs680.cmd` loads `dtb/synaptics/vs680-a0-bananapi-m6.dtb`, `uInitrd`, and `Image`.
- `packages/blobs/vs680/bpi-m6-tzk-4MB.bin` is required for the VS680 boot image.
- The U-Boot path uses `BPI-SINOVOIP/pi-u-boot`, branch `v2019.10-vs680-hdmi-rx`, with `vs680_oemboot_c05_defconfig`.
- The kernel path uses `BPI-SINOVOIP/pi-linux`, branch `pi-5.4-vs680-hdmi-rx`, with Linux `5.4.195`.
- PowerVR Rogue workspace support is disabled for now because the vendor module fails Armbian kernel packaging with unresolved trace/PVR symbols. This is acceptable for server smoke images, but not enough for a final desktop image.
- The old optional VS680 AMP BSP archives were not imported; the family hook skips them cleanly when absent.

Armbian smoke validation:

- U-Boot package build passed:

```bash
./compile.sh uboot BOARD=bananapim6 BRANCH=legacy RELEASE=trixie EXPERT=yes
```

- Kernel package build passed:

```bash
./compile.sh kernel BOARD=bananapim6 BRANCH=legacy RELEASE=trixie EXPERT=yes KERNEL_CONFIGURE=no
```

- Kernel packaging produced `linux-image-legacy-vs680` with:
  - `Source: linux-5.4.195`
  - `Armbian-Kernel-Version: 5.4.195`
- Trixie server image build passed:

```bash
./compile.sh build BOARD=bananapim6 BRANCH=legacy RELEASE=trixie BUILD_DESKTOP=no BUILD_MINIMAL=no KERNEL_CONFIGURE=no EXPERT=yes COMPRESS_OUTPUTIMAGE=xz
```

- Output image:
  - `output/images/Armbian-unofficial_26.05.0-trunk_Bananapim6_trixie_legacy_5.4.195.img.xz`
- SHA256:
  - `44814f8c60d59edb1ebffa6772af0e9086ba0f1eb14d0cc08d4fdc2a723d32b4`
- `xz -t` passed.
- Offline boot layout check confirmed:
  - `Image`
  - `uInitrd`
  - `boot.scr`
  - `armbianEnv.txt`
  - `dtb/synaptics/vs680-a0-bananapi-m6.dtb`
- Raw image checks confirmed the VS680 TZK blob area at 512-byte offset and U-Boot area at 2 MiB offset are non-empty.

Remaining WIP risk:

- The current Armbian board entry proves the source selection, U-Boot package, kernel package, initramfs, boot script, and offline bootloader layout paths, but still needs a boot test on real M6 hardware before it can enter the release matrix.
- Desktop images need separate PowerVR/AMP multimedia work after the server boot path is validated.

## BPI-CM6 SpacemiT K1 WIP Implementation

Source audit:

- Official BPI CM6 images inspected locally under `/media/pi/SMCI/bpi/bpi-cm6`.
- U-Boot source: `https://github.com/BPI-SINOVOIP/pi-u-boot.git`, branch `v2022.10-k1-v2.1`, checked commit `066cccd77f35e57d13363fea524a439759196dca`.
- Kernel source: `https://github.com/BPI-SINOVOIP/pi-linux.git`, branch `linux-6.6.36-k1-cm6`, checked commit `0d0af0d895251383baee939d44e523699e31889f`.
- The BPI kernel branch builds the CM6 DTB as `arch/riscv/boot/dts/spacemit/k1-x_deb1.dtb`.
- The BPI U-Boot branch carries `configs/k1_defconfig`, `include/configs/k1-x.h`, and `board/spacemit/k1-x/k1-x.env` with the CM6 product/DTB selection.

Official image layout observed:

- GPT image with small bootloader partitions, a dedicated ext4 `/boot`, and rootfs.
- U-Boot environment includes `product_name=k1-x_deb1`.
- `/boot/env_k1-x.txt` selects Linux `vmlinuz-6.6.63`, `initrd.img-6.6.63`, and `spacemit/6.6.63/k1-x_deb1.dtb`.

Implemented in this branch:

- `config/boards/bananapicm6.wip` adds Banana Pi BPI-CM6 as a SpacemiT K1 legacy BSP board.
- The board uses the existing `spacemit` family but overrides sources to the BPI CM6 U-Boot and kernel branches.
- `patch/u-boot/legacy/u-boot-spacemit-k1-cm6/001-add-extlinux-boot.patch` adds an Armbian extlinux/boot script/EFI fallback path ahead of the vendor autoboot fallback.
- `patch/u-boot/legacy/u-boot-spacemit-k1-cm6/002-fixup-circular-deps.patch` keeps the U-Boot tools build compatible with the current host toolchain.
- The board sets `SRC_EXTLINUX=yes`, `BOOT_FDT_FILE=spacemit/k1-x_deb1.dtb`, and `SRC_CMDLINE` for `ttyS0`.

Armbian smoke validation:

- U-Boot package build passed:

```bash
./compile.sh uboot BOARD=bananapicm6 BRANCH=legacy RELEASE=trixie EXPERT=yes
```

- Kernel package build passed:

```bash
./compile.sh kernel BOARD=bananapicm6 BRANCH=legacy RELEASE=trixie EXPERT=yes KERNEL_CONFIGURE=no
```

- Kernel packaging produced `linux-image-legacy-spacemit` with:
  - `Armbian-Kernel-Version: 6.6.36`
  - `Architecture: riscv64`
- Trixie server image build passed:

```bash
./compile.sh build BOARD=bananapicm6 BRANCH=legacy RELEASE=trixie BUILD_DESKTOP=no BUILD_MINIMAL=no KERNEL_CONFIGURE=no EXPERT=yes COMPRESS_OUTPUTIMAGE=xz
```

- Output image:
  - `output/images/Armbian-unofficial_26.05.0-trunk_Bananapicm6_trixie_legacy_6.6.36.img.xz`
- SHA256:
  - `312742f70baf8f496ac3acb67b86d21fb395652eeb080aa3a65a1908c5cee7b6`
- `xz -t` passed.
- Offline boot layout check confirmed:
  - `Image -> vmlinuz-6.6.36-legacy-spacemit`
  - `uInitrd -> uInitrd-6.6.36-legacy-spacemit`
  - `dtb -> dtb-6.6.36-legacy-spacemit`
  - `extlinux/extlinux.conf`
  - `dtb/spacemit/k1-x_deb1.dtb`
- Raw image checks confirmed non-empty bootloader payloads at:
  - block `0`: `bootinfo_emmc.bin`
  - block `1`: `FSBL.bin`
  - block `1280`: `fw_dynamic.itb`
  - block `2048`: `u-boot.itb`

Remaining WIP risk:

- The current image proves package creation, extlinux content, DTB selection, and raw bootloader placement, but it still needs real CM6 boot testing.
- The BPI reference image uses a different GPT bootloader partition layout. Hardware validation must confirm that the Armbian raw-offset layout works on CM6 eMMC/SD before this board can enter the release matrix.
- Desktop images should wait until server boot, network, USB, storage, and reboot/shutdown behavior are confirmed on hardware.

## BPI-6204 Allwinner R40 WIP Implementation

Source audit:

- Local BSP source: `/media/pi/SMCI/bpi/bpi-6204/bpi-cs6204-linux-6.12`
- Vendor board DTS: `linux-6.12/arch/arm/boot/dts/allwinner/sun8i-r40-bpi-6204.dts`
- Vendor notes describe BPI-6204 as an Allwinner R40 industrial board derived from the Banana Pi M2 Ultra design.
- Vendor eMMC stability note: the R40 eMMC path is unstable when DDR52 is enabled on this hardware, so the stable path disables DDR52 and keeps eMMC in conservative legacy timing.

Implemented in this branch:

- `config/boards/bananapi6204.wip` adds Banana Pi BPI-6204 as a `sun8i` legacy board.
- U-Boot reuses the existing mainline `Bananapi_M2_Ultra_defconfig`, which matches the R40 base boot path.
- `patch/kernel/archive/sunxi-6.12/patches.armbian/arm-dts-sun8i-r40-add-bpi-6204.patch` adds `sun8i-r40-bpi-6204.dts` and the DTB target.
- `patch/kernel/archive/sunxi-6.12/patches.armbian/drv-mmc-host-sunxi-mmc-disable-ddr52-bpi-6204.patch` disables DDR52 only when the machine compatible is `sinovoip,bpi-6204`.
- The BPI-6204 patches are registered in both `series.armbian` and the active `series.conf` used by this kernel archive.

Armbian smoke validation:

- U-Boot package build passed:

```bash
./compile.sh uboot BOARD=bananapi6204 BRANCH=legacy RELEASE=trixie EXPERT=yes
```

- Kernel package build passed after forcing a clean kernel rebuild:

```bash
./compile.sh kernel BOARD=bananapi6204 BRANCH=legacy RELEASE=trixie EXPERT=yes KERNEL_CONFIGURE=no ARTIFACT_IGNORE_CACHE=yes CLEAN_LEVEL=make-kernel
```

- Kernel package version:
  - `6.12.90-S2538-D9898-P5823-Cd5a5-H23bf-HK01ba-V014b-B8c04-R448a`
- The generated DTB package contains:
  - `boot/dtb-6.12.90-legacy-sunxi/sun8i-r40-bpi-6204.dtb`
- Trixie server image build passed:

```bash
./compile.sh build BOARD=bananapi6204 BRANCH=legacy RELEASE=trixie BUILD_DESKTOP=no BUILD_MINIMAL=no KERNEL_CONFIGURE=no EXPERT=yes COMPRESS_OUTPUTIMAGE=xz
```

- Output image:
  - `output/images/Armbian-unofficial_26.05.0-trunk_Bananapi6204_trixie_legacy_6.12.90.img.xz`
- SHA256:
  - `7afcde4755a8de4d2bef4723e30b8ca51f8dba0c216d3bd96552349ec6d14306`
- `xz -t` passed.
- Offline boot layout check confirmed:
  - raw U-Boot SPL header at 8 KiB (`eGON.BT0`)
  - `fdtfile=allwinner/sun8i-r40-bpi-6204.dtb`
  - `boot.scr`
  - `zImage`
  - `uInitrd`
  - `dtb-6.12.90-legacy-sunxi/sun8i-r40-bpi-6204.dtb`
  - installed kernel package hash `P5823`

Important fix from validation:

- The first generated image had `armbianEnv.txt` pointing at the BPI-6204 DTB but no BPI-6204 DTB installed, because the new patches were initially added only to `series.armbian`.
- `sunxi-6.12` uses the manually maintained `series.conf` for the active patch set, so the final implementation registers the patches there as well and rebuilds the kernel/image from a clean kernel tree.

Remaining WIP risk:

- The image proves U-Boot packaging, kernel DTB packaging, boot script selection, and conservative eMMC timing in the built artifacts.
- Hardware validation is still required for SD boot, eMMC stability, Ethernet, UART, CAN, LEDs, and reboot/shutdown behavior before BPI-6204 can enter the public release matrix.

## BPI-RV2 Siflower Porting Assessment

Checked BPI source:

- Repository: `https://github.com/BPI-SINOVOIP/BPI-RV2-SF21H8898-OPENWRT-24.10-BSP`
- Checked commit: `320b851d`

What the BPI OpenWrt tree provides:

- RISC-V target description:
  - `target/linux/siflower/sf21h8898/target.mk`
  - `ARCH:=riscv64`
  - `SUBTARGET:=sf21h8898`
  - `KERNELNAME:=Image`
- OpenWrt image definitions:
  - `target/linux/siflower/image/sf21h8898.mk`
  - `Device/bpi-rv2-nand`
  - `Device/bpi-rv2-nor`
  - FIT image flow with lzma kernel and external static rootfs
  - `KERNEL_LOADADDR := 0x20000000`
  - `FILESYSTEMS := squashfs`
  - generated payload is `sysupgrade.bin`, not a raw SD/eMMC disk image
- Kernel DTS sources:
  - `sf_kernel/linux-6.6/arch/riscv/boot/dts/siflower/sf21h8898-bpi-rv2.dtsi`
  - `sf_kernel/linux-6.6/arch/riscv/boot/dts/siflower/sf21h8898-bpi-rv2-nand.dts`
  - `sf_kernel/linux-6.6/arch/riscv/boot/dts/siflower/sf21h8898-bpi-rv2-nor.dts`
- Board defconfigs:
  - `target/linux/siflower/sf21h8898_bpi-rv2-nand_def.config`
  - `target/linux/siflower/sf21h8898_bpi-rv2-nor_def.config`
- Runtime/upgrade helpers:
  - `package/utils/fitblk`
  - `sf_kernel/linux-6.6/drivers/block/fitblk.c`
  - `target/linux/siflower/sf21h8898/base-files/lib/upgrade/platform.sh`

Boot layout details from the BSP:

- NAND DTS compatible is `bananapi,bpi-rv2-nand`, with SPI NAND `fbl` at `0x0..0x20000` and a UBI partition starting at `0x20000`. The OpenWrt root disk points at the UBI volume named `fit`.
- NOR DTS compatible is `bananapi,bpi-rv2-nor`, with SPI NOR partitions `bootloader` at `0x0..0x90000`, `factory` at `0x90000..0xa0000`, and `firmware` at `0xa0000..0x1000000`; the `firmware` partition is marked `denx,fit`.
- The BSP network script maps NAND as LAN `eth0 eth1 eth2 eth3 eth5` and WAN `eth4`; the default/NOR path maps LAN `eth0 eth1 eth2 eth3 eth4` and WAN `eth5`.
- The upgrade script requires OpenWrt metadata, copies `fitblk`, and writes to `PART_NAME=firmware`. It currently matches `bananapi,bpi-rv2`, while the DTS files expose `bananapi,bpi-rv2-nand` and `bananapi,bpi-rv2-nor`; this needs verification during porting.

Local Armbian status:

- This branch has no Siflower or SF21H8898 Armbian family.
- Local cached kernels only contain the Siflower vendor prefix binding, not SF21H8898 SoC support or BPI-RV2 DTBs.
- Local U-Boot trees do not contain BPI-RV2 or SF21H8898 board support.
- The BPI BSP does not provide an obvious U-Boot/OpenSBI package path for Armbian-style raw SD/eMMC images. It appears to assume the vendor first-stage/bootloader is already present in NOR/NAND and then upgrades a FIT payload.

Practical porting direction:

1. First create a `siflower-sf21h8898` vendor family only after deciding whether the release target is OpenWrt-style NOR/NAND FIT update images or Armbian raw disk images.
2. For an OpenWrt-style first milestone, import the vendor Linux 6.6 tree, BPI-RV2 NAND/NOR DTS files, `fitblk`, and a FIT payload writer. This would produce board-specific recovery/update artifacts, not the same format as the normal Armbian SD images.
3. For a normal Armbian disk-image milestone, first locate or port SF21H8898 U-Boot/OpenSBI support and define a boot-media writer. Without this, an Armbian `.wip` board file would build nothing useful.
4. Validate serial console, switch/port mapping, FIT root device handling, and NAND/NOR flashing before adding RV2 to any release matrix.

Status: blocked for Armbian image build until a new `siflower-sf21h8898` RISC-V vendor/OpenWrt-derived family is designed. This should be treated as a separate architecture family, not a small board-file addition.

## BPI GitHub Coverage Refresh

Checked on 2026-05-22 after fetching upstream Armbian `main` at `869f0df25`
and the public `BPI-SINOVOIP` GitHub repository list.

Additional repositories that look like missing boards at first glance:

- `bpi-cs6202`
  - Checked commit: `913b4732b`.
  - README title is `bpi-cs6204`, and the scripts build the same Allwinner R40/M2 Ultra U-Boot and Linux 5.4 family used by the BPI-6204 BSP.
  - The DTS comments explicitly mention CS6204 reusing the CS6202 SD-card detect GPIO for CAN interrupt and keeping `broken-cd` for CS6202 compatibility.
  - Current Armbian action: treat this as covered by `bananapi6204.wip` until BPI provides a separate CS6202 schematic/DT requirement. Do not add a duplicate board id that points at the BPI-6204 DTB.
- `BPI-WiFi5-Siflower`
  - Checked repository default branch `main` plus public README.
  - Provides U-Boot, Linux 4.14, and OpenWrt 18.06 build commands for `sfa28_ac28` / `a28_bpi`.
  - Flashing is documented as a web-interface firmware upgrade, not an SD/eMMC Debian/Ubuntu image.
  - Current Armbian action: group with the Siflower/OpenWrt-style blocked work. It needs a Siflower family and an explicit router firmware artifact policy before it can enter this release.
- `BPI-EAI80-bsp`
  - Checked repository default branch `master`.
  - Provides an Edgeless EAI80 AIoT/MCU SDK with `KelisSDK/ugelis` board files, not a Linux SBC boot stack.
  - Current Armbian action: not applicable for Debian/Ubuntu image release.
- `BPI-OM7-orbbec_reconstruction`
  - Checked repository default branch `main`.
  - README says it is tested on BPI-OM7, an integrated platform consisting of BPI-M7 plus an ORBBEC Gemini 2 camera, running Ubuntu 24.04.
  - Current Armbian action: base OS remains `bananapim7`; this repository is an application stack, not a separate image target.

Result: after the current WIP additions, the only clear Linux-system-capable
BPI GitHub board without a local Armbian board entry is still BPI-RV2, and it
is blocked by the Siflower/OpenWrt FIT-image boot model. BPI-WiFi5 is related
but uses an older Siflower OpenWrt router flow rather than the requested
Debian/Ubuntu raw image release model.

## Next Candidates After Current WIP Batch

| Candidate | Reason | First action |
| --- | --- | --- |
| BPI-W2 | WIP Realtek RTD1296 BSP family and FAT boot layout added | Validate generated legacy image on real W2 hardware |
| BPI-F2S | WIP Sunplus SP7021 BSP family and FAT boot layout added | Validate generated legacy image on real F2S hardware |
| BPI-M4 plain | WIP Realtek RTD1395 BSP family and FAT boot layout added | Validate generated legacy image on real M4 hardware |
| BPI-M6 | WIP Synaptics VS680 BSP family and TZK/U-Boot layout added | Validate generated legacy image on real M6 hardware |
| BPI-CM6 | WIP SpacemiT K1 BSP path and extlinux/raw bootloader layout added | Validate generated legacy image on real CM6 hardware |
| BPI-6204 | WIP Allwinner R40 path and conservative eMMC timing added | Validate generated legacy image on real BPI-6204 hardware |
| BPI-RV2 | BPI has SF21H8898 OpenWrt BSP | Design new `siflower-sf21h8898` RISC-V family |
| BPI-WiFi5 Router | BPI has older Siflower OpenWrt BSP | Decide whether router web-upgrade firmware belongs in the Armbian release scope |
| BPI-R3/R3 Mini/R64/R4 Lite/R4 Pro/W3 | WIP image builds now pass | Hardware boot validation before promotion |

## Second-Pass Candidate Findings

Checked on 2026-05-22 after the R3 WIP build:

- BPI-R3 Mini:
  - BPI source: `BPI-R3MINI-OPENWRT-V21.02.3`
  - Vendor image files reference `mt7986a-bananapi-bpi-r3mini-emmc.dts`, `mt7986a-bananapi-bpi-r3mini-nand.dts`, `bl2_emmc.img`, and `fip_emmc.bin`.
  - Local kernel trees already build `mediatek/mt7986a-bananapi-bpi-r3-mini.dtb`.
  - Local U-Boot v2025.04 has no R3 Mini defconfig, so this branch adds a WIP eMMC defconfig and a minimal U-Boot-local DTS instead of blindly reusing full-size R3 eMMC U-Boot.
  - Trixie server smoke image now builds and passes `xz -t` and sha256 validation; hardware validation is still required.
- BPI-R64:
  - BPI source: `BPI-R64-BSP`, `BPI-R64-bsp-4.19`, `BPI-R64-bsp-5.4`
  - Local kernel trees contain `mediatek/mt7622-bananapi-bpi-r64.dtb`.
  - This branch now adds `bananapir64.wip` and a board-specific MT7622 U-Boot defconfig.
  - Trixie server smoke image now builds and passes `xz -t` and sha256 validation; hardware validation is still required because the legacy BPI bootloader layout differs from the modern ATF/FIP layout used by this Armbian family.
- BPI-W2:
  - BPI source: `BPI-W2-bsp`
  - Vendor tree contains `rtd-1296-bananapi-w2-2GB.dts` and `rtd129x_bpi_defconfig`.
  - Existing local Realtek support is `realtek-rtd1619b`, so W2 needs a separate RTD1296 vendor path with custom BSP source and bootloader packaging hooks.
- BPI-W3:
  - BPI source: `BPI-W3-BSP`
  - Vendor tree contains `rk3588-bpi-w3.dts`, `BoardConfig-rk3588-bpi-w3.mk`, and `bananapi_w3_defconfig`.
  - Because this branch already supports RK3588 vendor boards, W3 now has a WIP board using the ArmSoM W3 base DTS and RK3588 vendor build path.
  - Trixie server smoke image now builds and passes `xz -t` and sha256 validation; hardware validation is still required.
- BPI-F2S:
  - BPI source: `BPI-F2S-bsp`
  - Vendor tree contains `sp7021-bpi-f2s.dts` and `sp7021_chipC_bpi-f2s_defconfig`.
  - This branch now adds `bananapif2s.wip`, a `sunplus-sp7021-bpi` legacy family, xboot/`u-boot.img` packaging, FAT `/boot`, and the old BPI `bananapi/bpi-f2s/linux/` boot layout.
  - Trixie server smoke image now builds, passes `xz -t`, and contains the expected FAT boot files; hardware validation is still required.
- BPI-R4 Lite:
  - BPI source: `BPI-R4Lite-OPENWRT-V24.10.0-Master-Devel`
  - Vendor tree contains MT7987 kernel DTS/overlay files plus U-Boot and ATF patches.
  - This branch now adds `bananapir4lite.wip`, local U-Boot support, and a 6.17-r4lite kernel patch for SD/eMMC composite DTBs.
  - Trixie server smoke image now builds and passes `xz -t`; hardware validation is still required.
- BPI-R4 Pro:
  - BPI source: `BPI-R4PRO-8X-OPENWRT-V24.10.0-Master-Devel`
  - Vendor tree contains MT7988A R4 Pro 8X DTS/overlay files plus a BPI-R4-Pro-specific U-Boot patch.
  - This branch now adds `bananapir4pro.wip`, local U-Boot support, and uses the 6.19-mtkdts kernel branch with R4 Pro 8X SD/eMMC DTBs.
  - Trixie server smoke image now builds and passes `xz -t`; hardware validation is still required.
- BPI-M4 plain:
  - BPI source: `BPI-M4-bsp`
  - Vendor tree is Realtek RTD1395 with U-Boot 2015.7 and Linux 4.9.119.
  - It is not covered by existing `bananapim4berry` or `bananapim4zero`, which are Allwinner H618 boards.
  - It should share future Realtek vendor-family work with W2 where practical.
- BPI-M6:
  - BPI source: older BPI Armbian branch `v24.03.20`, `pi-linux`, and `pi-u-boot`.
  - Vendor tree is Synaptics VS680 with U-Boot 2019.10 and Linux 5.4.195.
  - This branch now adds `bananapim6.wip`, the VS680 family, the BPI boot script, and the required `bpi-m6-tzk-4MB.bin` boot blob.
  - Trixie server smoke image now builds, passes `xz -t`, and contains the expected `/boot` files; hardware validation is still required.
  - PowerVR/AMP desktop acceleration is not yet release-ready and should be handled after server boot is proven on hardware.
- BPI-CM6:
  - BPI source: official CM6 images plus `pi-u-boot` branch `v2022.10-k1-v2.1` and `pi-linux` branch `linux-6.6.36-k1-cm6`.
  - Vendor tree is SpacemiT K1 RISC-V with U-Boot 2022.10 and Linux 6.6.36.
  - This branch now adds `bananapicm6.wip` and a CM6-specific U-Boot patch set for extlinux fallback.
  - Trixie server smoke image now builds, passes `xz -t`, contains the expected `/boot` files, and has non-empty raw bootloader offsets; hardware validation is still required.
- BPI-6204:
  - BPI source: local `/media/pi/SMCI/bpi/bpi-6204/bpi-cs6204-linux-6.12`.
  - Vendor tree is Allwinner R40 and close enough to the M2 Ultra boot path to reuse mainline `Bananapi_M2_Ultra_defconfig`.
  - This branch now adds `bananapi6204.wip`, a BPI-6204 6.12 DTB patch, and an eMMC DDR52 disable patch keyed to `sinovoip,bpi-6204`.
  - Trixie server smoke image now builds, passes `xz -t`, contains the expected `zImage`, `uInitrd`, `boot.scr`, and BPI-6204 DTB, and has a valid raw U-Boot SPL header; hardware validation is still required.
- BPI-RV2:
  - BPI source: `BPI-RV2-SF21H8898-OPENWRT-24.10-BSP`
  - Vendor tree is RISC-V SF21H8898 with OpenWrt 6.6 DTS and FIT-image flow.
  - No local Siflower family, kernel support, or U-Boot support exists yet; this is a new family port.
