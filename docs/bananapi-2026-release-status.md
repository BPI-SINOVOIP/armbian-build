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
- Banana Pi Pro
- Lamobo R1

Needs support decision or porting investigation:

| Candidate | BPI source found | Local board config | Proposed path |
| --- | --- | --- | --- |
| BPI-F2S | `BPI-F2S-bsp` | Missing | Assess vendor BSP first, then decide `.wip` or legacy-only |
| BPI-R3 | `BPI-R3-bsp`, `BPI-R3-bsp-5.15`, OpenWrt trees | Missing | Prefer mainline/filogic if practical, else BSP import |
| BPI-R3 Mini | `BPI-R3MINI-OPENWRT-V21.02.3` | Missing | OpenWrt/vendor reference first |
| BPI-R64 | `BPI-R64-BSP`, `BPI-R64-bsp-4.19`, `BPI-R64-bsp-5.4` | Missing | Legacy/vendor path likely needed |
| BPI-W2 | `BPI-W2-bsp` | Missing | Vendor BSP only unless mainline is practical |
| BPI-W3 | `BPI-W3-BSP` | Missing | Vendor BSP only unless mainline is practical |
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
