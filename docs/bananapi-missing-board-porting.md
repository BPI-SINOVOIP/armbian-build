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

## Next Candidates After R3

| Candidate | Reason | First action |
| --- | --- | --- |
| BPI-R64 | BPI has multiple BSP branches; mainline kernel DT exists locally | Create or import MT7622 family/U-Boot path before board file |
| BPI-W2 | BPI has `BPI-W2-bsp`; RTD1296 sources found | Vendor BSP path; existing `realtek-rtd1619b` family is not directly reusable |
| BPI-F2S | BPI has `BPI-F2S-bsp`; SP7021 sources found | New Sunplus SP7021 legacy/vendor family required |
| BPI-R4 Lite / R4 Pro | BPI has OpenWrt trees, local board missing | Decide if release should treat these as separate boards |

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
  - Local U-Boot only has generic `mt7622_rfb_defconfig`; this branch has no dedicated MT7622 Armbian family.
  - First real work is an MT7622 bootloader/family assessment, not a board file.
- BPI-W2:
  - BPI source: `BPI-W2-bsp`
  - Vendor tree contains `rtd-1296-bananapi-w2-2GB.dts` and `rtd129x_bpi_defconfig`.
  - Existing local Realtek support is `realtek-rtd1619b`, so W2 needs a separate RTD1296 vendor path.
- BPI-W3:
  - BPI source: `BPI-W3-BSP`
  - Vendor tree contains `rk3588-bpi-w3.dts`, `BoardConfig-rk3588-bpi-w3.mk`, and `bananapi_w3_defconfig`.
  - Because this branch already supports RK3588 vendor boards, W3 now has a WIP board using the ArmSoM W3 base DTS and RK3588 vendor build path.
  - Trixie server smoke image now builds and passes `xz -t` and sha256 validation; hardware validation is still required.
- BPI-F2S:
  - BPI source: `BPI-F2S-bsp`
  - Vendor tree contains `sp7021-bpi-f2s.dts` and `sp7021_chipC_bpi-f2s_defconfig`.
  - No matching local Armbian family exists yet; this should be treated as a new legacy/vendor family.
