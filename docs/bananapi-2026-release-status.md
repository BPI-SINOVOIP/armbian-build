# Banana Pi 2026 Release Status

Branch: `bpi-v26.8.0-trunk`

Last updated: 2026-05-22

## Plan Push

The release execution plan was committed and pushed first as requested.

- Commit: `f25c37eaf`
- File: `docs/bananapi-2026-release-plan.md`
- Remote branch: `origin/bpi-v26.8.0-trunk`

## Matrix Snapshot

Generated with:

```bash
./b-bananapi-2026 list
./b-bananapi-2026 dry-run
```

Current selection:

- Board entries: 25, including `lamobo-r1`
- Build jobs selected: 248
- Skipped jobs: 1
- Skip reason: `bananapif3/current bookworm` is skipped because Debian 12 `bookworm` does not support `riscv64` in this tree.
- The release driver intentionally selects `.conf`, `.csc`, and `.eos` boards only. Newly added `.wip` boards are smoke-build candidates until hardware validation promotes them.

Target releases:

- `bookworm`
- `trixie`
- `jammy`
- `noble`
- `resolute`

Target image types:

- Server
- XFCE desktop

## Existing Release Artifact Audit

Audited folder: `output/images/2026.05`

Current result:

- `.img.xz`: 248
- `.img.xz.sha` plus raw `.img.sha`: 250 checksum files
- `.img.txt`: 248
- Total files in release folders: 748

The release folder currently has the expected compressed image count for the 248 selected jobs. `bpi-cm4io` has 12 image payload files because two Ubuntu 26.04 `resolute` raw `.img` files are also retained in addition to the required `.img.xz` files.

Per-folder payload count:

| Folder | Image payloads |
| --- | ---: |
| `bpi-cm4io` | 12 |
| `bpi-f3` | 8 |
| `bpi-m1` | 10 |
| `bpi-m1p` | 10 |
| `bpi-m2` | 10 |
| `bpi-m2b` | 10 |
| `bpi-m2m` | 10 |
| `bpi-m2p` | 10 |
| `bpi-m2pro` | 10 |
| `bpi-m2s` | 10 |
| `bpi-m2u` | 10 |
| `bpi-m2z` | 10 |
| `bpi-m3` | 10 |
| `bpi-m4b` | 10 |
| `bpi-m4z` | 10 |
| `bpi-m5` | 10 |
| `bpi-m5pro` | 10 |
| `bpi-m64` | 10 |
| `bpi-m7` | 10 |
| `bpi-p2z` | 10 |
| `bpi-pro` | 10 |
| `bpi-r1` | 10 |
| `bpi-r2` | 10 |
| `bpi-r2pro` | 10 |
| `bpi-r4` | 10 |

## Upstream Armbian Comparison

Compared local `config/boards` with upstream Armbian `main` board configs.

Local-only Banana Pi board files:

- `bananapim2.csc`
- `bananapim2berry.csc`
- `bananapim2magic.csc`
- `bananapip2zero.csc`

Upstream-only Banana Pi board file:

- `bananapim2.eos`

Important conclusion: `P2 Zero` and `M2 Berry` are already present in this BPI release branch, while upstream Armbian does not currently have those board files in the same form.

## BPI-SINOVOIP GitHub Coverage Check

Checked BPI-SINOVOIP repositories through the GitHub API and compared obvious board BSP/OpenWrt repositories with local Armbian board configs.

Already represented in this branch:

- BPI-M1 / M1+
- BPI-M2 / M2+ / M2 Pro / M2S / M2 Ultra / M2 Zero / M2 Berry / M2 Magic
- BPI-M3
- BPI-M4 Berry / M4 Zero
- BPI-M5 / M5 Pro
- BPI-M6 as `.wip`
- BPI-M64
- BPI-M7
- BPI-CM4IO
- BPI-F3
- BPI-F2S as `.wip`
- BPI-R2 / R2 Pro / R4
- BPI-R3 / R3 Mini as `.wip`
- BPI-R64 as `.wip`
- BPI-R4 Lite / R4 Pro as `.wip`
- BPI-W2 as `.wip`
- BPI-W3 as `.wip`
- BPI-M4 plain as `.wip`
- Banana Pi Pro
- Lamobo R1

Needs support decision or porting investigation:

| Candidate | BPI source found | Local board config | Proposed path |
| --- | --- | --- | --- |
| BPI-F2S | `BPI-F2S-bsp` | Added as `.wip` | SP7021 legacy BSP smoke image builds with FAT BPI boot layout; needs hardware validation |
| BPI-R3 | `BPI-R3-bsp`, `BPI-R3-bsp-5.15`, OpenWrt trees | Added as `.wip` | MT7986 filogic smoke image builds; needs hardware boot validation |
| BPI-R3 Mini | `BPI-R3MINI-OPENWRT-V21.02.3` | Added as `.wip` | MT7986 eMMC smoke image builds; needs hardware boot validation |
| BPI-R64 | `BPI-R64-BSP`, `BPI-R64-bsp-4.19`, `BPI-R64-bsp-5.4` | Added as `.wip` | MT7622 smoke image builds; needs hardware boot validation because legacy BSP boot layout differs |
| BPI-W2 | `BPI-W2-bsp` | Added as `.wip` | RTD1296 legacy BSP smoke image builds with FAT BPI boot layout; needs hardware validation |
| BPI-W3 | `BPI-W3-BSP` | Added as `.wip` | RK3588 vendor smoke image builds; needs hardware boot validation |
| BPI-M4 plain | `BPI-M4-bsp` | Added as `.wip` | RTD1395 legacy BSP smoke image builds with FAT BPI boot layout; needs hardware validation |
| BPI-M6 | older BPI Armbian VS680 support, `pi-linux`, `pi-u-boot` | Added as `.wip` | VS680 legacy BSP smoke image builds with TZK plus U-Boot layout; needs hardware validation and desktop acceleration work |
| BPI-R4 Lite / R4 Pro | `BPI-R4Lite-*`, `BPI-R4PRO-*` OpenWrt trees | Added as `.wip` | Filogic smoke image builds pass; needs hardware boot validation |
| BPI-RV2 | `BPI-RV2-SF21H8898-*` | Missing | New Siflower SF21H8898 RISC-V family required |

## Immediate Execution Result

The current release artifact folder is complete by file count for the planned matrix.

Integrity validation result:

- Log folder: `output/bananapi-2026/integrity-20260522T050208Z-p4`
- `xz -t` checked 248 `.img.xz` files.
- `xz -t` errors: 0
- Path-fixed sha256 validation checked 248 `.img.xz.sha` files.
- sha256 errors: 0

The plain `sha256sum -c` command is not usable directly from the release subfolders because the `.sha` files record `output/images/<filename>` paths while the final release files live in per-board folders under `output/images/2026.05/bpi-*`. The successful validation used the hash from each `.sha` file and verified the image with the same basename in that `.sha` file's directory.

Validation commands used:

```bash
find output/images/2026.05 -maxdepth 2 -type f -name '*.img.xz' -print0 |
  sort -z |
  xargs -0 -n1 -P4 sh -c 'xz -t "$1"' _

find output/images/2026.05 -maxdepth 2 -type f -name '*.img.xz.sha' -print0 |
  sort -z |
  xargs -0 -n1 -P4 sh -c '
    sha="$1"
    dir=$(dirname "$sha")
    read -r expected recorded < "$sha"
    file="$dir/$(basename "$recorded")"
    actual_line=$(sha256sum "$file")
    actual=${actual_line%% *}
    test "$actual" = "$expected"
  ' _
```

Because the existing 2026.05 release set is complete and passes file integrity checks, the next code work is not rebuilding these completed images. The next code work is to investigate and add missing board families in small branches/commits, starting with router/vendor BSP boards because they are the clearest gap versus BPI GitHub.

## BPI-R3 WIP Result

BPI-R3 was added as a `.wip` board after refactoring the shared `filogic` family so board files can select the ATF SoC, boot device, and DRAM flags. The current R3 path uses MT7986 SDMMC boot with `mt7986a_bpir3_sd_defconfig`.

Smoke validation:

- U-Boot/ATF package build passed for `BOARD=bananapir3 BRANCH=current RELEASE=trixie`.
- Trixie server image build passed for `BOARD=bananapir3 BRANCH=current RELEASE=trixie BUILD_DESKTOP=no`.
- Generated image: `output/images/Armbian-unofficial_26.05.0-trunk_Bananapir3_trixie_current_6.12.82.img.xz`
- `xz -t` passed for the generated image.

R3 remains outside the default release matrix until hardware boot validation confirms storage, Ethernet, and reset behavior.

## BPI-R3 Mini WIP Result

BPI-R3 Mini was added as a `.wip` board on the same `filogic` family after extending the family to allow a board-specific FIP filename. The R3 Mini path uses MT7986 eMMC boot and a minimal U-Boot-local DTS because U-Boot v2025.04 does not carry a matching defconfig and the upstream Linux DTS requires newer bindings than this U-Boot tree provides.

Smoke validation:

- U-Boot/ATF package build passed for `BOARD=bananapir3mini BRANCH=current RELEASE=trixie`.
- Trixie server image build passed for `BOARD=bananapir3mini BRANCH=current RELEASE=trixie BUILD_DESKTOP=no`.
- Generated image: `output/images/Armbian-unofficial_26.05.0-trunk_Bananapir3mini_trixie_current_6.12.82.img.xz`
- `xz -t` and sha256 validation passed for the generated image.

R3 Mini remains outside the default release matrix until hardware boot validation confirms eMMC boot, network, and reset behavior.

## BPI-R64 WIP Result

BPI-R64 was added as a `.wip` board using the existing `filogic` family extended earlier for board-specific SoC and FIP selection. The R64 path uses MT7622 SDMMC boot with a new `mt7622_bananapi_bpi-r64-sdmmc_defconfig` and the existing kernel DTB `mediatek/mt7622-bananapi-bpi-r64.dtb`.

Smoke validation:

- U-Boot/ATF package build passed for `BOARD=bananapir64 BRANCH=current RELEASE=trixie`.
- Trixie server image build passed for `BOARD=bananapir64 BRANCH=current RELEASE=trixie BUILD_DESKTOP=no`.
- Generated image: `output/images/Armbian-unofficial_26.05.0-trunk_Bananapir64_trixie_current_6.12.82.img.xz`
- `xz -t` and sha256 validation passed for the generated image.

R64 remains outside the default release matrix until hardware boot validation confirms the modern ATF/FIP layout, SDMMC boot, Ethernet, and reset behavior.

## BPI-W3 WIP Result

BPI-W3 was added as a `.wip` board using the RK3588 vendor path inherited from `armsom-w3.csc`, with a Banana Pi W3 DT wrapper over `rk3588-armsom-w3.dts`.

Smoke validation:

- Trixie server image build passed for `BOARD=bananapiw3 BRANCH=vendor RELEASE=trixie BUILD_DESKTOP=no`.
- Generated image: `output/images/Armbian-unofficial_26.05.0-trunk_Bananapiw3_trixie_vendor_6.1.115.img.xz`
- `xz -t` and sha256 validation passed for the generated image.
- The generated DTB package contains `rockchip/rk3588-bananapi-w3.dtb`.

W3 remains outside the default release matrix until hardware boot validation confirms storage, Ethernet, display, and boot media behavior.

## BPI-M6 WIP Result

BPI-M6 was added as a `.wip` board using the older BPI Armbian VS680 work as the starting point, with current branch metadata and safer package hooks.

Implementation:

- Board file: `config/boards/bananapim6.wip`
- Family: `config/sources/families/vs680.conf`
- Boot script: `config/bootscripts/boot-vs680.cmd`
- Kernel config: `config/kernel/linux-vs680-legacy.config`
- Required boot blob: `packages/blobs/vs680/bpi-m6-tzk-4MB.bin`
- U-Boot source: `https://github.com/BPI-SINOVOIP/pi-u-boot.git`, branch `v2019.10-vs680-hdmi-rx`
- Kernel source: `https://github.com/BPI-SINOVOIP/pi-linux.git`, branch `pi-5.4-vs680-hdmi-rx`

Smoke validation:

- U-Boot package build passed for `BOARD=bananapim6 BRANCH=legacy RELEASE=trixie`.
- Kernel package build passed for `BOARD=bananapim6 BRANCH=legacy RELEASE=trixie KERNEL_CONFIGURE=no`.
- Trixie server image build passed for `BOARD=bananapim6 BRANCH=legacy RELEASE=trixie BUILD_DESKTOP=no`.
- Kernel package metadata records:
  - `Package: linux-image-legacy-vs680`
  - `Source: linux-5.4.195`
  - `Armbian-Kernel-Version: 5.4.195`
- Generated image: `output/images/Armbian-unofficial_26.05.0-trunk_Bananapim6_trixie_legacy_5.4.195.img.xz`
- SHA256: `44814f8c60d59edb1ebffa6772af0e9086ba0f1eb14d0cc08d4fdc2a723d32b4`
- `xz -t` passed for the generated image.
- Offline boot partition inspection confirmed `Image`, `uInitrd`, `boot.scr`, `armbianEnv.txt`, and `dtb/synaptics/vs680-a0-bananapi-m6.dtb`.
- Raw image checks confirmed non-empty TZK data at 512-byte offset and U-Boot data at 2 MiB offset.

Remaining WIP risk:

- PowerVR Rogue workspace support is disabled in the kernel config because the vendor module fails modern Armbian packaging with unresolved trace/PVR symbols. This lets server images build, but desktop GPU acceleration still needs a separate port.
- The large optional VS680 AMP BSP archives from the old BPI branch were not imported. The family hook now skips them cleanly when absent.
- M6 remains outside the default release matrix until real hardware validation confirms UART, SD/eMMC boot, network, audio/video basics, and reset behavior.

## BPI-R4 Lite / R4 Pro WIP Result

BPI-R4 Lite and BPI-R4 Pro were added as separate `.wip` boards on the `filogic` family. They do not reuse `bananapir4.csc`.

R4 Lite implementation:

- Board file: `config/boards/bananapir4lite.wip`
- U-Boot: `mt7987a_bananapi_bpi-r4-lite-sdmmc_defconfig` from a local U-Boot v2025.04 patch.
- Kernel: `frank-w/BPI-Router-Linux` branch `6.17-r4lite`
- DTB package now contains:
  - `mediatek/mt7987a-bananapi-bpi-r4-lite-sd.dtb`
  - `mediatek/mt7987a-bananapi-bpi-r4-lite-emmc.dtb`

R4 Pro implementation:

- Board file: `config/boards/bananapir4pro.wip`
- U-Boot: `mt7988a_bananapi_bpi-r4-pro-8x-sdmmc_defconfig` from a local U-Boot v2025.04 patch.
- Kernel: `frank-w/BPI-Router-Linux` branch `6.19-mtkdts`
- DTB package contains R4 Pro 8X SD/eMMC DTBs and overlays.

Smoke validation:

- U-Boot/ATF package build passed for both boards.
- Trixie server image build passed for both boards:
  - `output/images/Armbian-unofficial_26.05.0-trunk_Bananapir4lite_trixie_current_6.17.0-rc1.img.xz`
  - `output/images/Armbian-unofficial_26.05.0-trunk_Bananapir4pro_trixie_current_6.19.0-rc1.img.xz`
- `xz -t` passed for both generated images.

Both boards remain outside the default release matrix until real hardware validation confirms bootloader layout, SD/eMMC boot, Ethernet, and reset behavior.

## BPI-F2S WIP Result

BPI-F2S was added as a `.wip` board with a new Sunplus SP7021 legacy BSP family. The implementation builds the vendor U-Boot 2019.04 and Linux 5.4.35 trees from `BPI-F2S-bsp`, packages `u-boot.img`, `ISPBOOOT.BIN`, and the eMMC boot0 xboot gzip, and creates a FAT `/boot` partition matching the old BPI layout.

Smoke validation:

- U-Boot package build passed for `BOARD=bananapif2s BRANCH=legacy RELEASE=trixie`.
- Kernel package build passed for `BOARD=bananapif2s BRANCH=legacy RELEASE=trixie KERNEL_CONFIGURE=no`.
- Trixie server image build passed for `BOARD=bananapif2s BRANCH=legacy RELEASE=trixie BUILD_DESKTOP=no`.
- Generated image: `output/images/Armbian-unofficial_26.05.0-trunk_Bananapif2s_trixie_legacy_0.img.xz`
- `xz -t` passed.
- FAT boot partition inspection confirmed:
  - `ISPBOOOT.BIN`
  - `uEnv.txt`
  - `bananapi/bpi-f2s/linux/uImage`
  - `bananapi/bpi-f2s/linux/uInitrd`
  - `bananapi/bpi-f2s/linux/sp7021-bpi-f2s.dtb`
- Image SHA-256: `abaa350847b2a1504376a287a76468cb73c1b3f1cef32c57816e66ec53527059`

F2S remains outside the default release matrix until real hardware validation confirms xboot, U-Boot, SD/eMMC root selection, UART, Ethernet, and reset behavior.

## BSP Porting Blockers And WIP

BPI-W2, BPI-M4 plain, BPI-M6, and BPI-F2S now have legacy BSP `.wip` entries. BPI-RV2 remains blocked because this branch still lacks a matching Siflower/SF21H8898 family.

BPI-W2 WIP:

- Source: `BPI-W2-bsp` at `6e6aefc35`
- BSP uses U-Boot 2015.7 and Linux 4.9.119 for RTD1296.
- Board files exist in the BSP: `rtd-1296-bananapi-w2-2GB.dts`, `rtd129x_bpi_defconfig`, and RTD1296 Banana Pi U-Boot defconfigs.
- Added board file: `config/boards/bananapiw2.wip`
- Added family: `realtek-rtd129x-bpi`
- Vendor U-Boot and kernel builds passed in the BSP tree after host-toolchain patches.
- Armbian U-Boot package smoke build passed for `BOARD=bananapiw2 BRANCH=legacy RELEASE=trixie`.
- Armbian kernel package smoke build passed for `BOARD=bananapiw2 BRANCH=legacy RELEASE=trixie KERNEL_CONFIGURE=no`; the package metadata now records `Source: linux-4.9.119` and `Armbian-Kernel-Version: 4.9.119`.
- Armbian Trixie server smoke image passed with FAT boot layout and xz validation.
- Generated image: `output/images/Armbian-unofficial_26.05.0-trunk_Bananapiw2_trixie_legacy_4.9.119.img.xz`
- SHA256: `7ad63ba2b85b033a332bf3c84eb5f403378f14880bdb95a7191ba0c74a84dd8f`
- Offline FAT boot layout validation confirmed `uEnv.txt`, `bluecore.audio`, `uImage`, `uInitrd`, and `rtd-1296-bananapi-w2-2GB.dtb` under `bananapi/bpi-w2/linux/`.
- Required next work: real W2 boot validation for the old BPI Realtek boot layout.

BPI-F2S WIP:

- Source: `BPI-F2S-bsp` at `3eee97bd8`
- BSP uses U-Boot 2019.4 and Linux 5.4.35 for Sunplus SP7021.
- Board files exist in the BSP: `sp7021-bpi-f2s.dts`, `sp7021_bpi_f2s_defconfig`, and `sp7021_chipC_bpi-f2s_defconfig`.
- Added board file: `config/boards/bananapif2s.wip`
- Added family: `sunplus-sp7021-bpi`
- Vendor U-Boot and kernel builds passed in the BSP tree after the U-Boot dtc host-toolchain patch.
- Armbian U-Boot package smoke build passed for `BOARD=bananapif2s BRANCH=legacy RELEASE=trixie`.
- Armbian kernel package smoke build passed for `BOARD=bananapif2s BRANCH=legacy RELEASE=trixie KERNEL_CONFIGURE=no`.
- Armbian Trixie server smoke image passed with FAT boot layout and xz validation.
- Required next work: real F2S boot validation for xboot, SD/eMMC root selection, UART, Ethernet, and reset behavior.

BPI-M4 plain WIP:

- Source: `BPI-M4-bsp` at `25f5b88e`
- BSP uses Realtek RTD1395 with U-Boot 2015.7 and Linux 4.9.119.
- Board files exist in the BSP: `rtd-1395-bananapi-m4-1GB.dts`, `rtd-1395-bananapi-m4-2GB.dts`, `rtd139x_bpi_defconfig`, and RTD1395 Banana Pi U-Boot defconfigs.
- Existing `bananapim4berry` and `bananapim4zero` are Allwinner H618 boards and do not cover BPI-M4 plain.
- Added board file: `config/boards/bananapim4.wip`
- Added family: `realtek-rtd139x-bpi`
- Vendor U-Boot and kernel builds passed in the BSP tree after host-toolchain patches.
- Armbian U-Boot package smoke build passed for `BOARD=bananapim4 BRANCH=legacy RELEASE=trixie`.
- Armbian kernel package smoke build passed for `BOARD=bananapim4 BRANCH=legacy RELEASE=trixie KERNEL_CONFIGURE=no`; the package metadata now records `Source: linux-4.9.119` and `Armbian-Kernel-Version: 4.9.119`.
- Armbian Trixie server smoke image passed with FAT boot layout and xz validation.
- Generated image: `output/images/Armbian-unofficial_26.05.0-trunk_Bananapim4_trixie_legacy_4.9.119.img.xz`
- SHA256: `6276c598a46e63c2d769511c0538cc06bcd5646dfcacf161d514966d0ed6d25b`
- Offline FAT boot layout validation confirmed `uEnv.txt`, `bluecore.audio`, `uImage`, `uInitrd`, and both RTD1395 1GB/2GB DTBs under `bananapi/bpi-m4/linux/`.
- Required next work: real M4 boot validation for the old BPI Realtek boot layout.

BPI-RV2 findings:

- Source: `BPI-RV2-SF21H8898-OPENWRT-24.10-BSP` at `320b851d`
- BSP is RISC-V `ARCH:=riscv64`, `SUBTARGET:=sf21h8898`, with OpenWrt FIT-image flow.
- Board files exist in the BSP: `sf21h8898-bpi-rv2.dtsi`, `sf21h8898-bpi-rv2-nand.dts`, `sf21h8898-bpi-rv2-nor.dts`, and BPI-RV2 NAND/NOR OpenWrt defconfigs.
- The BSP emits FIT-based `sysupgrade.bin` artifacts using `KERNEL_LOADADDR=0x20000000`, lzma kernel payloads, `fitblk`, and NAND/NOR partition layouts. It does not describe a normal Armbian raw SD/eMMC image path.
- The NAND path stores the root FIT in a UBI volume named `fit`; the NOR path stores the root FIT in the `firmware` partition at offset `0xa0000`.
- This branch has no existing Siflower/SF21H8898 family, kernel support, or U-Boot support.
- Required next work: design a new `siflower-sf21h8898` RISC-V vendor/OpenWrt-derived family, decide whether the first release artifact is a FIT updater or a raw disk image, and locate/port the missing U-Boot/OpenSBI boot path before adding a `.wip` board config.
