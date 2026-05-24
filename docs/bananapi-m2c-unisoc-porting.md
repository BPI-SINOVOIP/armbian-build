# Banana Pi BPI-M2C UNISOC Porting Plan

Branch: `bpi-v26.8.0-trunk`

Date: 2026-05-23, updated 2026-05-24

## Objective

Bring up the unpublished Banana Pi `bpi-m2c` platform from the local UNISOC vendor materials, refresh the vendor Yocto outputs first, then use those results to decide the correct Armbian integration path.

The first goal is not to pretend this is a normal upstream Armbian board. The current material is a UNISOC secure boot and PAC-image flow, so the work must preserve the vendor boot chain until hardware proves that a more standard Armbian disk image can boot.

As of 2026-05-24, the active source of truth is the new sync tree:

- `/media/pi/SMCI/bpi/unisoc/sync-20260524/source_sync_rls_25c`

The older expanded trees remain historical validation references only.

## Working Rules

1. Use `/media/pi/SMCI/armbian/bpi-v26.2.1` and branch `bpi-v26.8.0-trunk` for tracked Armbian planning and integration work.
2. Keep proprietary UNISOC archives, generated PAC files, signing payloads, NV/RF data, and vendor binaries outside Git.
3. Keep the local vendor source and staging area under `/media/pi/SMCI/bpi/unisoc`.
4. Rebuild and validate vendor Yocto baselines before adding Armbian board code, because the bootloader, kernel, signing, and PAC layout are tightly coupled.
5. Add Armbian support initially as `.wip` only. Do not include `bpi-m2c` in the 2026 Banana Pi release matrix until a server image or vendor-rootfs hybrid boots on real hardware.

## Local Source Inventory

Local UNISOC root:

- `/media/pi/SMCI/bpi/unisoc`

Expanded Yocto baselines:

- `/media/pi/SMCI/bpi/unisoc/sync-20260524/source_sync_rls_25c` current default
- `/media/pi/SMCI/bpi/unisoc/sync-20260524/source_sync_trunk_3_0_dev` reference only
- `/media/pi/SMCI/bpi/unisoc/source_rls_25c_w26_05_5`
- `/media/pi/SMCI/bpi/unisoc/source_rls_25c_w26_07_2`
- `/media/pi/SMCI/bpi/unisoc/source_trunk_3_0_dev_w24_05_2_p1_2`

Original source archives:

- `/media/pi/SMCI/bpi/unisoc/source_rls_25c_w26_05_5.tar`
- `/media/pi/SMCI/bpi/unisoc/source_rls_25c_w26_07_2.tar`
- `/media/pi/SMCI/bpi/unisoc/source_trunk_3_0_dev_w24_05_2_p1_2.tar`

Build wrappers:

- `/media/pi/SMCI/bpi/unisoc/build_uis7885_latest_official.sh`
- `/media/pi/SMCI/bpi/unisoc/build_uis7885_05_5_incremental.sh`
- `/media/pi/SMCI/bpi/unisoc/build_uis7885_07_2_incremental.sh`
- `/media/pi/SMCI/bpi/unisoc/build_uis7885_trunk_incremental.sh`

Known existing PAC outputs:

- `sync-20260524/source_sync_rls_25c/out/target/product/uis7885-2h10/cp_sign/QOGIRN6PRO_UIS7885_2H10_SEC/uis7885_2h10+wayland+wayland+sec+uboot22-userdebug-native_QOGIRN6PRO_UIS7885_2H10_SEC.pac`
- `source_rls_25c_w26_05_5/out/target/product/uis7885-2h10/cp_sign/QOGIRN6PRO_UIS7885_2H10_SEC/uis7885_2h10+wayland+wayland+sec-userdebug-native_QOGIRN6PRO_UIS7885_2H10_SEC.pac`
- `source_rls_25c_w26_07_2/out/target/product/uis7885-2h10/cp_sign/QOGIRN6PRO_UIS7885_2H10_SEC/uis7885_2h10+wayland+wayland+sec-userdebug-native_QOGIRN6PRO_UIS7885_2H10_SEC.pac`
- `source_trunk_3_0_dev_w24_05_2_p1_2/out/target/product/uis7885-2h10/cp_sign/QOGIRN6PRO_UIS7885_2H10_SEC/uis7885_2h10+wayland+wayland+sec-userdebug-native_QOGIRN6PRO_UIS7885_2H10_SEC.pac`

## 103764 Archive Classification

Archive:

- `/media/pi/SMCI/bpi/unisoc/103764__URDT_R5.26.0511.7z`

Extraction staging path:

- `/media/pi/SMCI/bpi/unisoc/staging/103764__URDT_R5.26.0511`

Extracted top-level payload:

- `9632_R5.R5.26.0304`

Important paths:

- `9632_R5.R5.26.0304/Bin/Product/BPI`
- `9632_R5.R5.26.0304/Bin/Config/N5`
- `9632_R5.R5.26.0304/Bin/Config/N6_9620_9621`
- `9632_R5.R5.26.0304/Bin/Project/NVProject_*`
- `9632_R5.R5.26.0304/Doc_Cus/9620`

Current interpretation:

- This archive is a URDT/NV/RF/product configuration package for UNISOC 9620/9621-class modem configuration.
- The key Banana Pi product configuration area is `Bin/Product/BPI`.
- This archive is not a direct Linux kernel or U-Boot source tree.
- It may be needed to reproduce product NV/RF configuration or final vendor image customization, but it should not be committed to the Armbian tree.

## Current Platform Mapping

The local build wrappers and machine files currently target:

- Yocto machine: `uis7885-2h10`
- Distro: `unisoc-wayland`
- Image: `unisoc-wayland-image`
- Product/signing profile: `QOGIRN6PRO_UIS7885_2H10_SEC`
- Current build argument: `uis7885-2h10+wayland+wayland+sec+uboot22 userdebug`
- Historical build argument: `uis7885-2h10+wayland+wayland+sec userdebug`
- Variant: `userdebug`
- Secure boot setting: `sec`

The local vendor trees do not currently expose a clearly named `bpi-m2c` machine. Until board schematics, boot logs, or vendor confirmation prove otherwise, `bpi-m2c` is treated as a Banana Pi product built from the `uis7885-2h10` machine baseline plus BPI-specific URDT/NV/product configuration.

## Boot Chain Constraints

Known vendor baseline:

- Kernel recipe: `linux-unisoc-5.4`
- Kernel source version: `5.4.180`
- U-Boot recipe: `u-boot22`
- U-Boot source version: `2023.01`

Secure boot artifacts include:

- `chipram`
- `u-boot-spl-16k-emmc-sign.bin`
- `u-boot-spl-16k-ufs-sign.bin`
- `u-boot-sign.bin`
- `sml-sign.bin`
- `tos-sign.bin`
- `teecfg-sign.bin`
- `boot-sign.img`
- `dtbo-sign.img`
- `Image-dtb-sign.dtb`

The practical implication is that the first Armbian port should either:

- reuse the vendor signed boot chain and replace only the root filesystem after proving partition compatibility, or
- generate a PAC-style image from Armbian rootfs content while keeping vendor bootloader, kernel, DTB, and signing flow intact.

## Execution Plan

### Phase 1: Vendor Baseline Revalidation

Current default action:

1. Use `sync-20260524/source_sync_rls_25c`.
2. Use the official UNISOC wrapper:

   ```bash
   cd /media/pi/SMCI/bpi/unisoc
   ./build_uis7885_latest_official.sh check
   ./build_uis7885_latest_official.sh wayland
   ```

3. Stage the resulting PAC and signed artifacts through the Armbian helper.

Historical action, explicit opt-in only:

1. `source_rls_25c_w26_07_2`
2. `source_rls_25c_w26_05_5`
3. `source_trunk_3_0_dev_w24_05_2_p1_2`

For each baseline:

- validate wrapper syntax;
- rebuild the image through the incremental wrapper;
- repack the PAC;
- collect the build log;
- copy the final PAC and core signed boot artifacts into a date-stamped release staging folder;
- write checksums and a manifest.

### Phase 2: BPI Product Configuration Review

Compare the extracted `Bin/Product/BPI` URDT package against the Yocto product config inputs.

Output of this phase:

- exact files that affect final product/NV configuration;
- whether the current Yocto build already includes the BPI configuration;
- whether a separate packaging step is required for `bpi-m2c`.

### Phase 3: Armbian WIP Skeleton

Add a tracked Armbian `.wip` skeleton only after the vendor build path is reproducible.

Expected first board identity:

- board id: `bananapim2c`
- public name: `Banana Pi M2C`
- status: `.wip`
- initial integration mode: vendor secure boot/PAC hybrid

Do not add this board to `b-bananapi-2026` default release selection until hardware boot validation passes.

## Execution Result: 2026-05-23

Vendor Yocto rebuild completed for all three known `uis7885-2h10` baselines:

| Baseline | Log | PAC mtime | PAC bytes | Status |
| --- | --- | --- | --- | --- |
| `rls-25c-w26-07-2` | `/media/pi/SMCI/bpi/unisoc/logs/m2c_07_2_full_2026-05-23_171219.log` | `2026-05-23 17:31:41` | `1354412924` | `mkpac [PASS]` |
| `rls-25c-w26-05-5` | `/media/pi/SMCI/bpi/unisoc/logs/m2c_05_5_full_2026-05-23_182046.log` | `2026-05-23 18:38:17` | `1354411095` | `mkpac [PASS]` |
| `trunk-3-0-dev-w24-05-2-p1-2` | `/media/pi/SMCI/bpi/unisoc/logs/m2c_trunk_full_2026-05-23_185044.log` | `2026-05-23 19:14:11` | `1244445332` | `mkpac [PASS]` |

Final staged release:

- `/media/pi/SMCI/bpi/unisoc/release/bpi-m2c/20260523-final`
- `summary.tsv` reports 15 staged artifacts and 0 missing artifacts per baseline.
- `missing.tsv` contains only the header row.
- `sha256sum -c SHA256SUMS` passed for every staged file in every baseline.

Tracked Armbian side changes:

- `config/boards/bananapim2c.wip` records the public board identity.
- `config/sources/families/unisoc-uis7885-bpi.conf` deliberately guards normal Armbian raw-image builds because BPI-M2C is currently a vendor secure-boot/PAC target.
- `tools/build-bpi-m2c-unisoc-yocto.sh` provides the repeatable local rebuild wrapper for the three vendor baselines.
- `tools/stage-bpi-m2c-unisoc-release.sh` stages the final PAC and core signed artifacts without committing proprietary binaries.

## Hybrid PAC Strategy

The BPI-M2C boot path is not a normal raw SD/eMMC Armbian image flow. The
UNISOC PAC manifest maps the root filesystem through the `System` entry:

```ini
System=1@./out/target/product/uis7885-2h10/rootfs.ext4
```

The first Armbian integration step is therefore a vendor-PAC hybrid:

1. Copy a known-good vendor `out/target/product/uis7885-2h10` tree into a
   scratch directory.
2. Replace only `rootfs.ext4`.
3. Keep the signed boot chain, kernel, DTB, DTBO, modem firmware, and NV
   artifacts from the vendor build.
4. Inject the vendor `5.4.180` modules and `/lib/firmware` into the Armbian
   rootfs.
5. Enable `ttyS1` serial getty at `921600`.
6. Repack PAC with the same vendor `makepac.py` and `mkpac.pl`.

The helper for this flow is now defaulted to `sync-20260524-rls-25c`:

```bash
tools/make-bpi-m2c-unisoc-hybrid-pac.sh --release trixie --flavor cli
```

Equivalent explicit form:

```bash
BASELINE=sync-20260524-rls-25c \
  tools/make-bpi-m2c-unisoc-hybrid-pac.sh --release trixie --flavor cli
```

To generate a server/desktop matrix from the current Armbian arm64 rootfs
cache:

```bash
tools/make-bpi-m2c-unisoc-hybrid-matrix.sh --date-tag 20260523-matrix --force
```

The default matrix covers Debian bookworm/trixie and Ubuntu jammy/noble/resolute
with `cli` and `xfce-desktop-mid` rootfs flavors. Missing rootfs cache entries
are recorded in `matrix-summary.tsv` instead of being silently skipped.

To collect a completed matrix into a flat release staging directory:

```bash
MATRIX_TAG=20260523-matrix DATE_TAG=20260523-hybrid-armbian \
  tools/stage-bpi-m2c-unisoc-hybrid-release.sh
```

The staged release keeps the generated PAC files under `pac/`, per-image
metadata under `metadata/`, and top-level `manifest.tsv`/`SHA256SUMS` files.

This still requires real hardware validation. Passing PAC generation only
proves that the signed package can be assembled.

## Execution Result: 2026-05-24 Sync Tree

The BPI-M2C Armbian helpers were repointed to the current vendor sync tree:

- baseline id: `sync-20260524-rls-25c`
- source tree: `/media/pi/SMCI/bpi/unisoc/sync-20260524/source_sync_rls_25c`
- vendor PAC:
  `/media/pi/SMCI/bpi/unisoc/sync-20260524/source_sync_rls_25c/out/target/product/uis7885-2h10/cp_sign/QOGIRN6PRO_UIS7885_2H10_SEC/uis7885_2h10+wayland+wayland+sec+uboot22-userdebug-native_QOGIRN6PRO_UIS7885_2H10_SEC.pac`

Latest vendor artifacts were staged here:

```text
/media/pi/SMCI/bpi/unisoc/release/bpi-m2c/20260524-sync20260524
```

Staging result:

| Baseline | Staged | Missing | Checksum |
| --- | ---: | ---: | --- |
| `sync-20260524-rls-25c` | 15 | 0 | `sha256sum -c SHA256SUMS` passed |

A first Armbian-rootfs hybrid PAC was generated from the current trixie CLI
rootfs cache:

```text
/media/pi/SMCI/bpi/unisoc/hybrid/bpi-m2c/20260524-sync20260524/sync-20260524-rls-25c-armbian-trixie-cli/product/cp_sign/QOGIRN6PRO_UIS7885_2H10_SEC/bpi-m2c_sync-20260524-rls-25c_armbian-trixie-cli_QOGIRN6PRO_UIS7885_2H10_SEC.pac
```

Hybrid PAC result:

| Item | Value |
| --- | --- |
| PAC bytes | `2409116954` |
| PAC SHA256 | `91b9ed6eed98ddf376a32b70dedcc28955d0e10d7eb97864991bc8ebfcb6625f` |
| Rootfs size | `2048 MB` |
| Rootfs SHA256 | `5db8231764fa00e564c1fb34a2fb6382b607d7fbfb1670a120d944a401e25344` |
| Checksum status | `sha256sum -c SHA256SUMS` passed |

This is still a packaging validation result only. It must be flashed and booted
on real BPI-M2C hardware before the board can move beyond `.wip`.

## SD Rootfs Test Plan

The `sync-20260524` code and documents do not show a complete BPI-M2C SD-card
first-stage boot path. The current `uis7885-2h10` machine and PAC layout build
signed eMMC/UFS loaders, not an SD SPL. The next storage experiment is therefore
eMMC/UFS boot plus SD-mounted Armbian rootfs.

Detailed analysis and the test procedure are tracked in:

- `docs/bananapi-m2c-unisoc-sd-rootfs-plan.md`

The helper for generating the SD rootfs image is:

```bash
tools/make-bpi-m2c-unisoc-sd-rootfs.sh --release trixie --flavor cli --force
```

First generated SD-rootfs test image:

```text
/media/pi/SMCI/bpi/unisoc/sdrootfs/bpi-m2c/20260524/sync-20260524-rls-25c-armbian-trixie-cli-sdroot/rootfs.ext4
```

Use this kernel root argument for the first hardware test:

```text
root=UUID=c43f0ac5-b23c-4797-a0d4-945de5474b37 rootfstype=ext4 rootwait rw
```

### Phase 4: Hardware Validation

Minimum boot validation:

- UART boot log reaches Linux login.
- Root filesystem mounts read/write.
- eMMC appears consistently.
- Ethernet reaches DHCP.
- USB host enumerates a storage device.
- Reboot and poweroff behave predictably.
- `dmesg` has no storage or boot-critical errors.

Minimum storage validation:

```bash
dd if=/dev/mmcblk1 of=/dev/null bs=1M count=1000 status=progress
```

Only after this passes should we expand toward Debian/Ubuntu Armbian-style releases.
