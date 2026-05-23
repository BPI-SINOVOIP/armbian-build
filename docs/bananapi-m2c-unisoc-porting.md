# Banana Pi BPI-M2C UNISOC Porting Plan

Branch: `bpi-v26.8.0-trunk`

Date: 2026-05-23

## Objective

Bring up the unpublished Banana Pi `bpi-m2c` platform from the local UNISOC vendor materials, refresh the vendor Yocto outputs first, then use those results to decide the correct Armbian integration path.

The first goal is not to pretend this is a normal upstream Armbian board. The current material is a UNISOC secure boot and PAC-image flow, so the work must preserve the vendor boot chain until hardware proves that a more standard Armbian disk image can boot.

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

- `/media/pi/SMCI/bpi/unisoc/source_rls_25c_w26_05_5`
- `/media/pi/SMCI/bpi/unisoc/source_rls_25c_w26_07_2`
- `/media/pi/SMCI/bpi/unisoc/source_trunk_3_0_dev_w24_05_2_p1_2`

Original source archives:

- `/media/pi/SMCI/bpi/unisoc/source_rls_25c_w26_05_5.tar`
- `/media/pi/SMCI/bpi/unisoc/source_rls_25c_w26_07_2.tar`
- `/media/pi/SMCI/bpi/unisoc/source_trunk_3_0_dev_w24_05_2_p1_2.tar`

Build wrappers:

- `/media/pi/SMCI/bpi/unisoc/build_uis7885_05_5_incremental.sh`
- `/media/pi/SMCI/bpi/unisoc/build_uis7885_07_2_incremental.sh`
- `/media/pi/SMCI/bpi/unisoc/build_uis7885_trunk_incremental.sh`

Known existing PAC outputs:

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
- Build argument: `uis7885-2h10+wayland+wayland`
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

Re-run the three available `uis7885-2h10` Yocto baselines:

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

The helper for this flow is:

```bash
tools/make-bpi-m2c-unisoc-hybrid-pac.sh --release trixie --flavor cli
```

This still requires real hardware validation. Passing PAC generation only
proves that the signed package can be assembled.

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
