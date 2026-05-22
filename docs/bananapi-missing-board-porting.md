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

## Current Blocker

The current Armbian `filogic` family is R4/MT7988-specific:

- `ATF_TARGET_MAP` is hardcoded to `PLAT=mt7988`.
- `uboot_custom_postprocess()` copies `build/mt7988/release/bl2.img`.
- `uboot_custom_postprocess()` uses `build/mt7988/release/bl31.bin`.
- `bananapir4.csc` uses `BOOTCONFIG="mt7988a_bananapi_bpi-r4-sdmmc_defconfig"`.
- Runtime firmware copy logic only installs `packages/blobs/filogic/firmware/mediatek/mt7988/*`.

Because of this, adding `config/boards/bananapir3.wip` alone would select the wrong ATF platform and produce a known-bad bootloader path.

## Proposed R3 Implementation

1. Refactor `config/sources/families/filogic.conf` so board files can set SoC-specific variables:
   - `FILOGIC_SOC=mt7988` or `mt7986`
   - `FILOGIC_BOOT_DEVICE=sdmmc`
   - `FILOGIC_DRAM_FLAGS`
   - `FILOGIC_ATF_BUILD_DIR`
   - `FILOGIC_UBOOT_FIP`
2. Keep R4 behavior byte-for-byte equivalent after the refactor.
3. Add `config/boards/bananapir3.wip` only after the family refactor is verified with an unchanged R4 build.
4. Start R3 with SDMMC only:
   - `BOOTCONFIG="mt7986a_bpir3_sd_defconfig"`
   - likely `BOOT_FDT_FILE="mediatek/mt7986a-bananapi-bpi-r3-sd-nor.dtb"` for a flattened SD/NOR DTB if the kernel tree emits it
   - `HAS_VIDEO_OUTPUT="no"`
5. Build only one smoke image first:

```bash
./compile.sh build BOARD=bananapir3 BRANCH=current RELEASE=trixie BUILD_DESKTOP=no BUILD_MINIMAL=no KERNEL_CONFIGURE=no EXPERT=yes
```

6. Only after the smoke image builds, add it to the 2026 release matrix.

## Next Candidates After R3

| Candidate | Reason | First action |
| --- | --- | --- |
| BPI-R3 Mini | Shares MT7986 family with R3 | Add after R3 family support works |
| BPI-R64 | BPI has multiple BSP branches, local Armbian board missing | Assess MT7622 U-Boot/ATF path |
| BPI-W2 | BPI has `BPI-W2-bsp`, local board missing | Determine if vendor BSP only |
| BPI-W3 | BPI has `BPI-W3-BSP`, local board missing | Determine if vendor BSP only |
| BPI-F2S | BPI has `BPI-F2S-bsp`, local board missing | Identify SoC/kernel/toolchain requirements |
| BPI-R4 Lite / R4 Pro | BPI has OpenWrt trees, local board missing | Decide if release should treat these as separate boards |
