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
- BPI-M64
- BPI-M7
- BPI-CM4IO
- BPI-F3
- BPI-R2 / R2 Pro / R4
- BPI-R3 / R3 Mini as `.wip`
- BPI-R64 as `.wip`
- BPI-W3 as `.wip`
- Banana Pi Pro
- Lamobo R1

Needs support decision or porting investigation:

| Candidate | BPI source found | Local board config | Proposed path |
| --- | --- | --- | --- |
| BPI-F2S | `BPI-F2S-bsp` | Missing | SP7021 vendor family and BPI xboot/FAT boot layout required |
| BPI-R3 | `BPI-R3-bsp`, `BPI-R3-bsp-5.15`, OpenWrt trees | Added as `.wip` | MT7986 filogic smoke image builds; needs hardware boot validation |
| BPI-R3 Mini | `BPI-R3MINI-OPENWRT-V21.02.3` | Added as `.wip` | MT7986 eMMC smoke image builds; needs hardware boot validation |
| BPI-R64 | `BPI-R64-BSP`, `BPI-R64-bsp-4.19`, `BPI-R64-bsp-5.4` | Added as `.wip` | MT7622 smoke image builds; needs hardware boot validation because legacy BSP boot layout differs |
| BPI-W2 | `BPI-W2-bsp` | Missing | RTD1296 vendor family and BPI boot layout required |
| BPI-W3 | `BPI-W3-BSP` | Added as `.wip` | RK3588 vendor smoke image builds; needs hardware boot validation |
| BPI-M4 plain | `BPI-M4-bsp` | Ambiguous | Compare against existing `M4 Berry` / `M4 Zero` support |
| BPI-R4 Lite / R4 Pro | `BPI-R4Lite-*`, `BPI-R4PRO-*` OpenWrt trees | Missing | Decide whether these are separate release boards |
| BPI-RV2 | `BPI-RV2-SF21H8898-*` | Missing | Architecture/toolchain feasibility check required |

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

## BSP Porting Blockers

BPI-W2 and BPI-F2S were inspected against their official BPI BSP repositories, but no `.wip` board files were added because they would not build through the existing Armbian family paths yet.

BPI-W2 findings:

- Source: `BPI-W2-bsp` at `6e6aefc35`
- BSP uses U-Boot 2015.7 and Linux 4.9.119 for RTD1296.
- Board files exist in the BSP: `rtd-1296-bananapi-w2-2GB.dts`, `rtd129x_bpi_defconfig`, and RTD1296 Banana Pi U-Boot defconfigs.
- Existing Armbian `realtek-rtd1619b` support is for XpressReal T3 and is not reusable as-is.
- Required next work: new `realtek-rtd1296` vendor family, custom source hooks for the BSP monorepo, and BPI `bpi-bootsel` bootloader/image layout support.

BPI-F2S findings:

- Source: `BPI-F2S-bsp` at `3eee97bd8`
- BSP uses U-Boot 2019.4 and Linux 5.4.35 for Sunplus SP7021.
- Board files exist in the BSP: `sp7021-bpi-f2s.dts`, `sp7021_bpi_f2s_defconfig`, and `sp7021_chipC_bpi-f2s_defconfig`.
- This branch has no existing Sunplus/SP7021 family.
- Required next work: new `sunplus-sp7021` vendor family, armhf vendor kernel packaging, xboot/`u-boot.img` packaging, and the old BPI FAT boot layout.
