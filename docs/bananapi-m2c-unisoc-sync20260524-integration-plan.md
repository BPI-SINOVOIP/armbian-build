# Banana Pi BPI-M2C UNISOC Sync-20260524 Integration Plan

Date: 2026-05-24

Branch: `bpi-v26.8.0-trunk`

Armbian workspace:

```text
/media/pi/SMCI/armbian/bpi-v26.2.1
```

UNISOC vendor workspace:

```text
/media/pi/SMCI/bpi/unisoc
```

## Purpose

The BPI-M2C Armbian work must now use the new UNISOC sync tree under
`/media/pi/SMCI/bpi/unisoc/sync-20260524` as the active vendor source of
truth. Older expanded trees remain historical validation references only.

The goal is not to import a full Yocto distribution into Armbian. The goal is
to use the official UNISOC Yocto output as the trusted source for:

- signed boot chain artifacts;
- UNISOC kernel and modules;
- U-Boot/chipram secure boot payloads;
- firmware, modem, NV, and product payloads;
- PAC packaging layout and signing rules.

Armbian provides the userland root filesystem and board release automation.

## Current Source Of Truth

Primary vendor tree:

```text
/media/pi/SMCI/bpi/unisoc/sync-20260524/source_sync_rls_25c
```

Secondary reference tree only:

```text
/media/pi/SMCI/bpi/unisoc/sync-20260524/source_sync_trunk_3_0_dev
```

Historical trees, no longer default inputs:

```text
/media/pi/SMCI/bpi/unisoc/source_rls_25c_w26_05_5
/media/pi/SMCI/bpi/unisoc/source_rls_25c_w26_07_2
/media/pi/SMCI/bpi/unisoc/source_trunk_3_0_dev_w24_05_2_p1_2
```

## Platform Mapping

| Armbian item | Value |
| --- | --- |
| Board id | `bananapim2c` |
| Public board name | `Banana Pi M2C` |
| Armbian family | `unisoc-uis7885-bpi` |
| Vendor Yocto machine | `uis7885-2h10` |
| Vendor distro | `unisoc-wayland` |
| Vendor image | `unisoc-wayland-image` |
| Kernel | UNISOC `linux-unisoc-5.4` |
| U-Boot | UNISOC `u-boot22` |
| Signing profile | `QOGIRN6PRO_UIS7885_2H10_SEC` |
| Default vendor build | `uis7885-2h10+wayland+wayland+sec+uboot22 userdebug` |

The BPI-M2C board identity is handled on the Armbian side. The vendor build
still uses the UNISOC `uis7885-2h10` machine because the current vendor source
does not expose a separate Yocto machine named `bpi-m2c`.

## Official Vendor Reference

Primary documentation is under:

```text
/media/pi/SMCI/bpi/unisoc/doc/08_System_Kernel
```

Use these documents as the build reference:

- `105534__Yocto GLP3.0软件开发指南V1.3.pdf`
- `103154__Yocto GLP Ubuntu开发指南V1.3.pdf`
- `101784__Yocto平台增量编译与bitbake配置介绍V1.1.pdf`

The local extracted notes are maintained in:

```text
/media/pi/SMCI/bpi/unisoc/UNISOC_YOCTO_OFFICIAL_BUILD_NOTES.md
```

## Confirmed Vendor Output

The latest `sync-20260524/source_sync_rls_25c` tree has produced a successful
PAC:

```text
/media/pi/SMCI/bpi/unisoc/sync-20260524/source_sync_rls_25c/out/target/product/uis7885-2h10/cp_sign/QOGIRN6PRO_UIS7885_2H10_SEC/uis7885_2h10+wayland+wayland+sec+uboot22-userdebug-native_QOGIRN6PRO_UIS7885_2H10_SEC.pac
```

Known checksum:

```text
c41d81e355d4ab75d5dea3d82e4bf2fbef408d4f302bf6a150322404b1a23359
```

## Armbian Integration Strategy

The first supported shape is a vendor PAC hybrid:

1. Use the new vendor tree's successful product output as the base.
2. Keep vendor signed boot chain, kernel, DTB/DTBO, modem firmware, NV, and
   PAC signing data unchanged.
3. Replace only the `System` root filesystem payload with an Armbian rootfs.
4. Inject the vendor kernel modules and firmware from the vendor rootfs into
   the Armbian rootfs.
5. Configure Armbian identity as `bananapim2c` and serial console as `ttyS1`
   at `921600`.
6. Repack the product directory with the vendor PAC tooling.

This is intentionally not a normal raw SD/eMMC Armbian image target yet.
Normal Armbian image generation must remain guarded until real hardware proves
that the secure boot and storage path can boot without the vendor PAC wrapper.

## Implementation Tasks

### 1. Record this plan

- Add this document to Git.
- Commit and push it before implementation changes so the intended direction is
  preserved separately from code edits.

### 2. Repoint Armbian BPI-M2C tooling

Update the BPI-M2C helper scripts so their default baseline is the new sync
tree:

```text
sync-20260524-rls-25c
```

The mapping must resolve to:

```text
/media/pi/SMCI/bpi/unisoc/sync-20260524/source_sync_rls_25c
```

The scripts should keep legacy baselines as explicit opt-in choices only.

Affected scripts:

```text
tools/build-bpi-m2c-unisoc-yocto.sh
tools/stage-bpi-m2c-unisoc-release.sh
tools/make-bpi-m2c-unisoc-hybrid-pac.sh
tools/make-bpi-m2c-unisoc-hybrid-matrix.sh
tools/stage-bpi-m2c-unisoc-hybrid-release.sh
```

### 3. Update artifact naming

The latest RLS_25C PAC name includes the `uboot22` product field:

```text
uis7885_2h10+wayland+wayland+sec+uboot22-userdebug-native_QOGIRN6PRO_UIS7885_2H10_SEC.pac
```

The old PAC name without `+uboot22` must not be used for the new sync tree.
Legacy baseline support may keep the old name when explicitly requested.

### 4. Update docs

Update the existing BPI-M2C porting notes so the default path is now
`sync-20260524/source_sync_rls_25c`. Keep the 2026-05-23 multi-baseline
results as historical context.

### 5. Validate script interfaces

Minimum validation after edits:

```bash
bash -n tools/build-bpi-m2c-unisoc-yocto.sh
bash -n tools/stage-bpi-m2c-unisoc-release.sh
bash -n tools/make-bpi-m2c-unisoc-hybrid-pac.sh
bash -n tools/make-bpi-m2c-unisoc-hybrid-matrix.sh
bash -n tools/stage-bpi-m2c-unisoc-hybrid-release.sh
```

Also verify help output for the modified scripts:

```bash
tools/build-bpi-m2c-unisoc-yocto.sh --help
tools/stage-bpi-m2c-unisoc-release.sh --help
tools/make-bpi-m2c-unisoc-hybrid-pac.sh --help
tools/make-bpi-m2c-unisoc-hybrid-matrix.sh --help
```

### 6. Stage latest vendor release artifacts

After scripts are repointed, stage the current latest vendor output into a new
release directory:

```bash
DATE_TAG=20260524-sync20260524 LINK_MODE=hardlink \
  tools/stage-bpi-m2c-unisoc-release.sh
```

Expected staged source:

```text
/media/pi/SMCI/bpi/unisoc/sync-20260524/source_sync_rls_25c/out/target/product/uis7885-2h10
```

### 7. Build an Armbian-rootfs hybrid PAC

Use an existing Armbian arm64 rootfs cache first:

```bash
BASELINE=sync-20260524-rls-25c RELEASE=trixie ROOTFS_FLAVOR=cli \
  tools/make-bpi-m2c-unisoc-hybrid-pac.sh --force
```

If this succeeds, extend to the matrix helper.

### 8. Hardware validation gate

No board status upgrade is allowed until real BPI-M2C hardware proves:

- UART reaches Linux login;
- rootfs mounts read/write;
- eMMC is stable;
- Ethernet DHCP works;
- USB host enumerates storage;
- reboot and poweroff work;
- `dmesg` has no boot-critical storage or firmware errors.

## Deliverables

- Pushed plan document.
- Updated scripts defaulting to `sync-20260524/source_sync_rls_25c`.
- Updated porting notes.
- Staged latest vendor artifacts under `/media/pi/SMCI/bpi/unisoc/release/bpi-m2c`.
- One generated Armbian-rootfs hybrid PAC for BPI-M2C.
- Validation logs and checksums.

## Non-Goals

- Do not commit UNISOC proprietary binaries, PAC files, modem firmware, or NV
  data into the Armbian Git tree.
- Do not remove the `.wip` guard from `bananapim2c`.
- Do not treat old `source_rls_25c_w26_*` trees as current default inputs.
