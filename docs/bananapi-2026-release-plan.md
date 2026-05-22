# Banana Pi 2026 Release Plan

Branch: `bpi-v26.8.0-trunk`

Repository: `git@github.com:BPI-SINOVOIP/armbian-build.git`

Date: 2026-05-22

## Objective

Build a complete 2026 Banana Pi image release from this branch, with a consistent and auditable process instead of per-board trial and error.

Target operating systems:

- Debian 12 `bookworm`
- Debian 13 `trixie`
- Ubuntu 22.04 `jammy`
- Ubuntu 24.04 `noble`
- Ubuntu 26.04 `resolute`

Target image types:

- Server image
- Desktop image, defaulting to XFCE/mid profile unless a board requires a different desktop policy

## Working Rules

1. Use `/media/pi/SMCI/armbian/bpi-v26.2.1` and branch `bpi-v26.8.0-trunk` as the development and release branch.
2. Commit and push planning or code changes before long unattended execution so the work is visible remotely.
3. Preserve validated CM4/CM4IO eMMC behavior. Do not overwrite the known-good conservative CM4 eMMC changes while adding new board support.
4. Use mainline/current or edge kernels when they are stable enough. Use legacy/vendor BSP only when it is the practical path for an old or missing Banana Pi board, and label those images clearly.
5. Build one representative server image first when a board or family has new code, then expand to the full OS and desktop matrix after that board family is proven.
6. Every release artifact must have compressed `.img.xz`, checksum, text metadata, and build log or manifest trail.
7. `.csc`, `.wip`, and legacy boards are allowed in the release set only when they are clearly marked in the matrix and validated at the level available for that hardware.

## Current Board Inventory

These board configs are present in this branch now:

| Board id | Status | Family | Branches | Board name |
| --- | --- | --- | --- | --- |
| `bananapi` | `conf` | `sun7i` | `current,edge,legacy` | Banana Pi |
| `bananapicm4io` | `conf` | `meson-g12b` | `current,edge` | Banana Pi CM4IO |
| `bananapif3` | `conf` | `spacemit` | `legacy,current,edge` | BananaPi BPI-F3 |
| `bananapim1plus` | `csc` | `sun7i` | `current,edge,legacy` | Banana Pi M1+ |
| `bananapim2` | `csc` | `sun6i` | `current,legacy` | Banana Pi M2 |
| `bananapim2berry` | `csc` | `sun8i` | `current,edge,legacy` | Banana Pi M2 Berry |
| `bananapim2magic` | `csc` | `sun8i` | `current,edge,legacy` | Banana Pi M2 Magic |
| `bananapim2plus` | `conf` | `sun8i` | `current,edge,legacy` | Banana Pi M2+ |
| `bananapim2pro` | `conf` | `meson-sm1` | `current,edge` | Banana Pi M2Pro |
| `bananapim2s` | `conf` | `meson-g12b` | `current,edge` | Banana Pi M2S |
| `bananapim2ultra` | `csc` | `sun8i` | `current,edge,legacy` | Banana Pi M2 Ultra |
| `bananapim2zero` | `csc` | `sun8i` | `current,edge,legacy` | Banana Pi M2 Zero |
| `bananapim3` | `csc` | `sun8i` | `current,edge,legacy` | Banana Pi M3 |
| `bananapim4berry` | `conf` | `sun50iw9-bpi` | `current,edge` | BananaPi M4 Berry |
| `bananapim4zero` | `conf` | `sun50iw9-bpi` | `current,edge` | BananaPi BPI-M4-Zero |
| `bananapim5` | `conf` | `meson-sm1` | `current,edge` | Banana Pi M5 |
| `bananapim5pro` | `conf` | `rk35xx` | `edge,vendor` | Banana Pi M5 Pro |
| `bananapim64` | `csc` | `sun50iw1` | `current,edge,legacy` | Banana Pi M64 |
| `bananapim7` | `conf` | `rockchip-rk3588` | `current,edge,vendor` | Banana Pi M7 |
| `bananapip2zero` | `csc` | `sun8i` | `current,edge,legacy` | Banana Pi P2 Zero |
| `bananapipro` | `csc` | `sun7i` | `current,edge,legacy` | Banana Pi Pro |
| `bananapir2` | `csc` | `mt7623` | `current` | Banana Pi R2 |
| `bananapir2pro` | `csc` | `rockchip64` | `current,edge` | Banana Pi R2 Pro |
| `bananapir3` | `wip` | `filogic` | `current` | Banana Pi R3 |
| `bananapir3mini` | `wip` | `filogic` | `current` | Banana Pi R3 Mini |
| `bananapir64` | `wip` | `filogic` | `current` | Banana Pi R64 |
| `bananapir4` | `csc` | `filogic` | `current` | Banana Pi R4 |
| `bananapiw3` | `wip` | `rockchip-rk3588` | `vendor` | Banana Pi W3 |
| `lamobo-r1` | `eos` | `sun7i` | `current,edge` | Lamobo R1 |

Important current findings:

- `bananapip2zero` and `bananapim2berry` are already present in this branch. They are not missing board files, but they still need release build and boot validation.
- `bananapir3` was added as `.wip` after a successful Trixie server smoke build. It remains outside the default release matrix until hardware boot validation passes.
- `bananapir3mini` was added as `.wip` after a successful Trixie server smoke build using an MT7986 eMMC U-Boot path. It remains outside the default release matrix until hardware boot validation passes.
- `bananapir64` was added as `.wip` after a successful Trixie server smoke build using an MT7622 SDMMC U-Boot/ATF path. It remains outside the default release matrix until hardware boot validation confirms the modern ATF/FIP layout on real hardware.
- `bananapiw3` was added as `.wip` after a successful Trixie server smoke build using the RK3588 vendor path derived from ArmSoM W3. It remains outside the default release matrix until hardware boot validation passes.
- `bananapiw2` and `bananapif2s` were not added yet because the official BPI BSPs require new vendor families and old BPI boot layouts before Armbian can produce meaningful images for them.

## Missing-Board Investigation Plan

The missing-board work is a separate track from building existing configs.

Sources to compare:

1. Local board configs in `config/boards`.
2. BPI-SINOVOIP GitHub repositories, including Armbian/BSP trees and board-specific BSP repositories.
3. Upstream Armbian `build` branches and board configs.
4. Existing local BPI BSP trees under `/media/pi/SMCI/bpi`.

Initial candidate families to check for missing or incomplete Armbian support:

- MediaTek router boards not listed locally or not yet buildable. BPI-R64 now has a `.wip` smoke-build path; R4 Lite and R4 Pro still need a support decision.
- Realtek or vendor BSP boards such as BPI-W2 if they cannot be supported by mainline.
- Rockchip vendor BSP boards such as BPI-W3 where local Armbian support may be reusable from existing RK3588 board families.
- Older Allwinner variants whose local config exists only as `.csc` and may need legacy kernel/bootloader fallback.
- Local hardware-specific additions such as BPI-6204 if they are intended for the public Banana Pi release set.

Output of this step:

- A support matrix documenting `mainline`, `legacy`, `vendor`, `blocked`, or `needs hardware` for each board.
- One board-config patch per missing board family.
- No full release build for a newly added board until one server image boots far enough to verify storage, network, and serial console.

## Execution Phases

### Phase 0: Push This Plan

Commit and push this document first:

```bash
git status --short --branch
git add docs/bananapi-2026-release-plan.md
git commit -m "docs: add Banana Pi 2026 release execution plan"
git push origin bpi-v26.8.0-trunk
```

### Phase 1: Freeze the Starting Point

Record the exact source and current board selection:

```bash
git status --short --branch
git rev-parse HEAD
./b-bananapi-2026 list | tee output/bananapi-2026/board-matrix.tsv
./b-bananapi-2026 dry-run | tee output/bananapi-2026/full-matrix-dry-run.txt
```

Also audit existing archived images:

```bash
find output/images -maxdepth 2 -type f -name 'Armbian-*Banana*' -o -name 'Armbian-*Bananapi*'
```

The audit must classify each board/release/type as:

- Present and compressed
- Present but missing `.xz` or checksum
- Built but old kernel or old bootloader
- Missing image
- Failed build log exists

### Phase 2: Validate Existing Board Families

Use the existing `b-bananapi-2026` script as the release driver.

First pass uses server images only:

```bash
STOP_ON_FAIL=no SKIP_EXISTING=yes ./b-bananapi-2026 build --type server
```

Then desktop images after server build failures are understood:

```bash
STOP_ON_FAIL=no SKIP_EXISTING=yes ./b-bananapi-2026 build --type desktop
```

Build order by risk:

1. Known-good CM4/CM4IO, Meson SM1/G12B boards, and already validated 2026 changes.
2. Allwinner old boards currently marked `.csc`, including P2 Zero and M2 Berry.
3. Rockchip and RK3588 boards.
4. Spacemit F3 and other newer or less deterministic families.
5. Legacy/vendor-only boards.

### Phase 3: Fix Failures by Family

Fix build failures in small logical commits:

- Rootfs/release failures: fix once in common build logic.
- Kernel patch failures: fix in the affected family archive only.
- U-Boot failures: fix board defconfig, boot script, or packaging only for that board/family.
- Missing board files: add as `.wip` or `.csc` first, then promote only after hardware validation.

Each fix must be verified by at least one server image before expanding to the full OS/desktop matrix.

### Phase 4: CM4/CM4IO Release Standard

CM4/CM4IO remains the reference hardware-sensitive board.

Expected release behavior:

- U-Boot uses the validated stable eMMC timing profile, not the failed 50 MHz experiment.
- Kernel uses the validated CM4 eMMC stability profile from the current branch.
- eMMC boot can load `uInitrd`, `Image`, and DTB without CRC/read errors.
- Kernel sees `/dev/mmcblk1` and rootfs mounts from eMMC.
- A 1 GiB read test completes without I/O errors.

Minimum validation commands on hardware:

```bash
dmesg | grep -Ei 'mmc|emmc|error|fail|crc'
lsblk
dd if=/dev/mmcblk1 of=/dev/null bs=1M count=1000 status=progress
```

### Phase 5: Package Release Artifacts

For each completed image:

- Keep `.img.xz`.
- Generate `.img.xz.sha`.
- Keep `.txt` metadata.
- Keep build logs under `output/bananapi-2026/<run-id>/`.
- Archive into board folders, for example `output/images/bpi-<board>/`.
- Use filenames with the current release date and exact kernel branch/version.

### Phase 6: Publish and Report

After each clean batch:

```bash
git status --short --branch
git add <changed files>
git commit -m "<specific batch message>"
git push origin bpi-v26.8.0-trunk
```

The report must include:

- Board list built.
- OS list built.
- Server/desktop status.
- Kernel and U-Boot versions.
- Failed boards with first error and next action.
- Hardware validation status where available.

## Acceptance Criteria

The release is considered complete when:

1. Every existing Banana Pi board config has either server and desktop images for the supported OS list, or a documented blocker.
2. Debian 12, Debian 13, Ubuntu 22.04, Ubuntu 24.04, and Ubuntu 26.04 support is attempted and logged for every eligible architecture.
3. CM4/CM4IO eMMC images boot from eMMC with the stable profile.
4. Missing Banana Pi boards have been compared against BPI GitHub and upstream Armbian, with each board classified.
5. All generated release images are compressed as `.xz` with checksum and metadata.
6. The branch is pushed to `origin/bpi-v26.8.0-trunk`.

## Immediate Next Actions After This Push

1. Generate the board/release dry-run matrix.
2. Audit `output/images` for existing complete and incomplete images.
3. Compare Banana Pi board coverage against BPI-SINOVOIP GitHub and upstream Armbian.
4. Start server-only builds with `SKIP_EXISTING=yes`.
5. Fix first build failures by family, commit, push, and continue the matrix.
