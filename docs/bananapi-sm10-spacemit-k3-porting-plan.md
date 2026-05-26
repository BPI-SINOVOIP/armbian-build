# Banana Pi BPI-SM10 SpacemiT K3 Porting Plan

Date: 2026-05-26

Armbian workspace:

```text
/media/pi/SMCI/armbian/bpi-v26.2.1
```

Vendor BSP workspace:

```text
/media/pi/SMCI/bpi/bpi-sm10
```

## Objective

Bring Banana Pi BPI-SM10, based on the SpacemiT K3 / K3-CoM260 RISC-V
platform, into the local Armbian build tree.

The port must start from a reproducible vendor BSP build. Armbian integration
should only begin after the official BSP can produce a bootable image and the
boot chain, partition layout, kernel, modules, firmware, and flashing method
are understood.

## Source References

Primary product page:

```text
https://docs.banana-pi.org/zh/BPI-SM10/BananaPi_BPI-SM10
```

Primary K3 Buildroot source guide provided for this task:

```text
https://www.spacemit.com/community/document/info?lang=zh&nodepath=software/SDK/buildroot/k3_buildroot/source.md
```

Browser-rendered copy captured on 2026-05-26:

```text
/media/pi/SMCI/bpi/bpi-sm10/notes/spacemit-k3-buildroot-source-20260526.txt
/media/pi/SMCI/bpi/bpi-sm10/notes/spacemit-k3-buildroot-source-20260526.json
/media/pi/SMCI/bpi/bpi-sm10/notes/spacemit-k3-buildroot-source-20260526.png
```

Related K3 hardware and release references:

```text
https://forum.spacemit.com/t/topic/970
https://forum.spacemit.com/t/topic/963
```

Notes:

- The Banana Pi page confirms BPI-SM10 uses the SpacemiT K3 RISC-V AI CPU and
  supports UFS, SD card, and external NVMe storage.
- The SpacemiT K3 ecosystem notes identify K3 Pico-ITX and K3 CoM260 Kit as
  official K3 reference designs.
- The K3 hardware guide documents FDL flashing mode and use of Titan or
  `fastboot`, plus UART debug at `115200`.
- The K3 Buildroot source guide is the required source of truth for exact
  repo/manifest/defconfig/build commands.

## Browser Extraction Requirement

The SpacemiT documentation page is rendered by the website frontend. Plain
`curl` or a static web fetch is not sufficient for reliable development work.
Use a real browser renderer for this page and save the extracted evidence before
writing scripts.

Validated local method:

```text
Google Chrome + Playwright headless renderer
```

The captured page reported:

- title: `源码`
- last update: `2026-05-23 10:34:03`
- extracted body length: `9340` characters
- saved text, structured JSON, and screenshot under
  `/media/pi/SMCI/bpi/bpi-sm10/notes/`

## Confirmed SDK Source Command

The official K3 Buildroot SDK source command is:

```bash
mkdir ~/k3-buildroot-sdk-1.0
cd ~/k3-buildroot-sdk-1.0
repo init -u git@github.com:spacemit-com/manifests.git -b main -m k3-br-v1.0.y.xml
repo sync
repo start k3-br-v1.0.y --all
```

Validation completed on 2026-05-26:

- `https://github.com/spacemit-com/manifests` exists and has branch `main`.
- `k3-br-v1.0.y.xml` exists in the `main` branch.
- The manifest default revision is `k3-br-v1.0.y`.
- The manifest fetch remote is `git@github.com:spacemit-com`, so `repo sync`
  requires GitHub SSH access unless the remotes are intentionally rewritten for
  HTTPS.
- The manifest includes these core projects:
  - `bsp-src/linux-6.18`
  - `bsp-src/opensbi`
  - `bsp-src/uboot-2022.10`
  - `buildroot`
  - `buildroot-ext`
  - `scripts`
- The manifest also includes GPU, VPU, camera, ESOS, MPP, Mesa, Bluetooth, and
  factory-test package sources under `package-src/`.

The same page also documents a Gitee mirror:

```bash
mkdir ~/k3-buildroot-sdk-1.0
cd ~/k3-buildroot-sdk-1.0
repo init -u git@gitee.com:spacemit-buildroot/manifests.git -b main -m k3-br-v1.0.y.xml
repo sync
repo start k3-br-v1.0.y --all
```

For this local project, replace the example home directory with:

```text
/media/pi/SMCI/bpi/bpi-sm10/sdk/k3-buildroot-sdk-1.0
```

Recommended command for this workspace:

```bash
mkdir -p /media/pi/SMCI/bpi/bpi-sm10/sdk/k3-buildroot-sdk-1.0
cd /media/pi/SMCI/bpi/bpi-sm10/sdk/k3-buildroot-sdk-1.0
repo init -u git@github.com:spacemit-com/manifests.git -b main -m k3-br-v1.0.y.xml
repo sync
repo start k3-br-v1.0.y --all
```

The page recommends downloading Buildroot third-party source packages ahead of
time:

```bash
wget -c -r -nv -np -nH -R "index.html*" http://archive.spacemit.com/buildroot/dl/
```

For this workspace, run that under:

```text
/media/pi/SMCI/bpi/bpi-sm10/downloads/buildroot-dl
```

## Confirmed Build Environment

Recommended hardware:

| Item | Requirement |
| --- | --- |
| CPU | 12th Gen Intel Core i5 or better |
| Memory | 16 GB or more |
| Disk | SSD, 256 GB or more |
| OS | Ubuntu 20.04 or newer LTS, or another Docker-capable Linux |

The K3 Buildroot SDK defaults to container builds, so Docker CE is the primary
host dependency. If building directly on the host, Ubuntu 20.04 or newer needs:

```bash
sudo apt-get install git build-essential cpio unzip rsync file bc wget python3 python-is-python3 libncurses5-dev libssl-dev dosfstools mtools u-boot-tools flex bison python3-pip
sudo pip3 install pyyaml
```

The page requires `repo >= 2.41`; the example page output used `repo v2.48`.

## Confirmed Vendor Build Commands

The page recommends `make envconfig` for the first complete build:

```bash
cd /media/pi/SMCI/bpi/bpi-sm10/sdk/k3-buildroot-sdk-1.0
make envconfig
```

The documented `buildroot-ext/configs` choices are:

```text
1. spacemit_k3_ci_defconfig
2. spacemit_k3_defconfig
3. spacemit_k3_plt_defconfig
4. spacemit_k3_rt_defconfig
```

Use choice `2` for standard Buildroot 1.0. Use choice `4` for PREEMPT_RT.

The page also documents the newer solution commands. These are not compatible
with an existing `env.mk`, so remove it before switching command styles:

```bash
cd /media/pi/SMCI/bpi/bpi-sm10/sdk/k3-buildroot-sdk-1.0
rm -f env.mk
make help
make k3-build
```

Useful solution commands:

```text
make k3-config
make k3-menuconfig
make k3-linux-menuconfig
make k3-uboot-menuconfig
make k3-build
make k3-pkg PKG=<package>
make k3-shell
make k3-source
make k3-clean
make k3-cleanbuild
```

The page says the recommended hardware takes about one hour when third-party
Buildroot source packages have already been downloaded.

## Confirmed Vendor Outputs

The page says build products are under:

```text
/output/k3/images/
```

Example successful outputs:

```text
Buildroot-k3-<timestamp>.zip
Buildroot-k3-<timestamp>-sdcard.img
```

Use:

- `Buildroot-k3-xxx.zip` with Titan Flasher, or extract it and flash with
  `fastboot`;
- `Buildroot-k3-xxx-sdcard.img` as an SD-card image, written with `dd` or
  balenaEtcher.

The generated SD-card image includes these partitions/artifacts:

| Partition | Source artifact |
| --- | --- |
| `env` | `env.bin` |
| `bootinfo` | `factory/bootinfo_block.bin` |
| `fsbl` | `factory/FSBL.bin` |
| `esos` | `esos.itb` |
| `opensbi` | `fw_dynamic.itb` |
| `uboot` | `u-boot.itb` |
| `bootfs` | `bootfs.img` |
| `rootfs` | `rootfs.ext4` |

The default firmware login documented by the page is:

```text
username: root
password: bianbu
```

## Confirmed Defconfigs And Component Builds

Configuration save targets:

| Component | Configure command | Save command | Defconfig path |
| --- | --- | --- | --- |
| Buildroot | `make menuconfig` | `make savedefconfig` | `buildroot-ext/configs/spacemit_k3_defconfig` |
| Linux | `make linux-menuconfig` | `make linux-update-defconfig` | `bsp-src/linux-6.18/arch/riscv/configs/k3_bianbu_defconfig` |
| U-Boot | `make uboot-menuconfig` | `make uboot-update-defconfig` | `bsp-src/uboot-2022.10/configs/k3_defconfig` |

After a complete `make envconfig` build, a repeat full build can use:

```bash
make
```

Common package/component commands:

```bash
make <pkg>-dirclean
make <pkg>
make <pkg>-rebuild

make uboot
make uboot-dirclean
make uboot-rebuild

make linux
make linux-dirclean
make linux-rebuild

make opensbi
make opensbi-dirclean
make opensbi-rebuild
```

The page also documents standalone OS vendor builds with the SpacemiT GCC 15
toolchain:

```text
spacemit-toolchain-linux-glibc-x86_64-v1.2.2.tar.xz
```

Standalone OpenSBI:

```bash
cd bsp-src/opensbi
make -j$(nproc) PLATFORM_DEFCONFIG=k3_defconfig PLATFORM=generic
```

Output:

```text
build/platform/generic/firmware/fw_dynamic.itb
```

Standalone U-Boot:

```bash
cd bsp-src/uboot-2022.10
make k3_defconfig
make -j$(nproc)
```

U-Boot build notes:

- `board/spacemit/k3/k3.env` generates `u-boot-env-default.bin`;
- generated boot artifacts include `bootinfo.bin`, `FSBL.bin`, and
  `u-boot.itb`.

Standalone Linux:

```bash
cd bsp-src/linux-6.18
make k3_bianbu_defconfig
LOCALVERSION="" make -j$(nproc)
```

## Current Armbian Baseline

The current Armbian tree already has RISC-V and SpacemiT K1 support:

```text
config/sources/riscv64.conf
config/sources/families/spacemit.conf
config/boards/bananapif3.conf
config/boards/bananapicm6.wip
```

The existing `spacemit` family targets K1 generation boards. It provides a
useful pattern for:

- RISC-V architecture selection;
- OpenSBI plus U-Boot plus Linux boot flow;
- extlinux boot configuration;
- boot firmware installation such as `esos.elf`;
- board-level `.wip` staging.

It must not be reused blindly for K3. K3 is a newer platform and may use
different boot ROM rules, SPL/FSBL artifacts, U-Boot image layout, firmware
names, kernel branches, device trees, and flashing tools.

## Working Rules

1. Keep vendor BSP source, downloaded archives, generated images, proprietary
   firmware, GPU/NPU userspace payloads, and flashing packages under
   `/media/pi/SMCI/bpi/bpi-sm10`, not in Git.
2. Commit only Armbian board definitions, scripts, patches, and documentation.
3. Treat BPI-SM10 as `.wip` until real hardware boots an Armbian image and the
   core hardware test matrix passes.
4. Pin every vendor source revision or manifest used for a successful build.
5. Preserve the original vendor build output before modifying it for Armbian.
6. Do not infer K3 boot offsets from K1. Use the official K3 image layout or
   inspect the generated vendor image.

## Phase 1: Vendor BSP Workspace

Create a controlled layout:

```text
/media/pi/SMCI/bpi/bpi-sm10/
  downloads/
  sdk/
  logs/
  release/
  notes/
  scripts/
```

Planned helper scripts:

```text
/media/pi/SMCI/bpi/bpi-sm10/scripts/fetch-bpi-sm10-spacemit-k3-bsp.sh
/media/pi/SMCI/bpi/bpi-sm10/scripts/build-bpi-sm10-spacemit-k3-bsp.sh
/media/pi/SMCI/bpi/bpi-sm10/scripts/stage-bpi-sm10-spacemit-k3-release.sh
```

The fetch script should:

- install or validate `repo` version requirements;
- record the exact K3 Buildroot source guide URL;
- run the official `repo init`, `repo sync`, and `repo start` commands recorded
  above;
- write `manifest.xml`, repo branch, and project revisions into
  `notes/source-manifest-<date>.txt`;
- avoid modifying the downloaded tree after sync.

The build script should:

- support containerized build if the official K3 guide recommends it;
- also support direct host build if dependencies are satisfied;
- run the official full Buildroot BSP build first with either `make envconfig`
  choice `2` or `make k3-build`;
- capture the full console log under `logs/`;
- record host OS, tool versions, source revisions, build command, and output
  paths;
- fail if the expected bootable image and boot artifacts are missing.

The staging script should:

- copy or hardlink generated images into `release/<date>-vendor-bsp/`;
- include kernel image, DTBs, OpenSBI/FSBL/U-Boot artifacts, bootfs/rootfs
  images, flashing package, and any required firmware;
- generate `SHA256SUMS`;
- generate a concise `manifest.tsv` with artifact name, source path, size,
  checksum, and purpose.

## Phase 2: Vendor BSP Build Validation

The first successful BSP build must answer these questions:

| Item | Required answer |
| --- | --- |
| Exact SDK manifest | repo URL, branch, manifest file, commit IDs |
| Board/device defconfig | SM10, K3-CoM260, K3 Pico-ITX, or reference board name |
| Boot chain | ROM -> FDL/FSBL -> OpenSBI -> U-Boot -> Linux, or actual vendor variant |
| Kernel version | exact version and branch |
| U-Boot version | exact version and branch |
| Device tree | exact Linux DTB and U-Boot DTS used for BPI-SM10 |
| Boot media | UFS, SD, NVMe support and default boot target |
| Flashing method | Titan package, `fastboot`, raw SD image, or another image format |
| Console | UART device and baud rate |
| Firmware | required RCPU/GPU/NPU/VPU firmware names and install locations |

Minimum vendor validation:

```bash
sha256sum -c SHA256SUMS
file release/<date>-vendor-bsp/*
```

Hardware validation after flashing vendor image:

```bash
uname -a
cat /proc/cmdline
cat /proc/device-tree/model || true
lsblk -f
findmnt /
dmesg | grep -Ei 'spacemit|k3|ufs|nvme|mmc|sdhci|eth|riscv|firmware|gpu|npu'
ip link
```

Vendor BSP is considered usable for Armbian work only when:

- the board reaches Linux login;
- rootfs mounts read/write;
- UART logging is stable;
- UFS is stable;
- SD card is detected;
- NVMe is detected if installed;
- Ethernet link works;
- reboot and poweroff do not corrupt storage;
- no required firmware is missing during boot.

## Phase 3: Armbian Integration Strategy

### Initial Strategy: Vendor Boot Chain Plus Armbian Rootfs

The first Armbian artifact should be a hybrid image, not a full native Armbian
boot chain.

Rationale:

- K3 is new and the official BSP should remain the reference for the first
  boot-chain validation.
- GPU/NPU/media userspace may depend on vendor Buildroot packages.
- Vendor image layout and flashing format must be preserved until understood.
- The fastest useful result is proving that Armbian userland can run on the
  vendor kernel and boot chain.

Planned flow:

1. Build and stage the vendor BSP image.
2. Extract the vendor rootfs, kernel modules, firmware, and boot configuration.
3. Build an Armbian riscv64 CLI rootfs from
   `/media/pi/SMCI/armbian/bpi-v26.2.1`.
4. Inject vendor kernel modules and required firmware into the Armbian rootfs.
5. Preserve vendor boot artifacts and replace only the rootfs payload or rootfs
   partition.
6. Regenerate checksums and any required image metadata.
7. Boot on real hardware and validate basic Linux operation.

Expected helper scripts in the Armbian tree:

```text
tools/build-bpi-sm10-spacemit-bsp.sh
tools/stage-bpi-sm10-spacemit-release.sh
tools/make-bpi-sm10-spacemit-hybrid-rootfs.sh
tools/inspect-bpi-sm10-spacemit-image.sh
```

### Native Armbian Strategy

After the hybrid image boots, add a native Armbian board skeleton:

```text
config/boards/bananapism10.wip
```

Preferred family direction:

```text
config/sources/families/spacemit-k3-bpi.conf
```

Use a new family if K3 source layout, boot image generation, or flashing rules
are different from the existing K1 `spacemit` family. Extend the current
`spacemit` family only if the K3 BSP proves that K1 and K3 share the same
OpenSBI/U-Boot artifact names, image offsets, and kernel integration model.

Initial board settings to confirm from the BSP:

```text
BOARD_NAME="BananaPi BPI-SM10"
BOARDFAMILY="spacemit-k3-bpi"
ARCH="riscv64"
BOARD_VENDOR="sinovoip"
KERNEL_TARGET="vendor"
SRC_EXTLINUX="yes" or vendor bootfs layout
BOOT_FDT_FILE="spacemit/<confirmed-sm10-dtb>.dtb"
```

Do not set final `BOOT_FDT_FILE`, `BOOTCONFIG`, boot offsets, or image writer
logic until they are extracted from the successful vendor BSP build.

## Phase 4: Source Extraction For Armbian

From the vendor BSP, identify and pin:

- OpenSBI source path and commit;
- U-Boot source path and commit;
- Linux source path and commit;
- board DTS files in U-Boot and Linux;
- defconfig files;
- firmware overlays and target install paths;
- image packaging scripts;
- flashing tool/package format.

Local mirrors may be created under:

```text
/media/pi/SMCI/bpi/bpi-sm10/mirrors/
```

Armbian source configuration should reference public upstream URLs when
available. If the BSP uses private or credentialed repositories, keep Armbian
scripts capable of consuming the local checked-out source tree instead of
embedding private URLs.

## Phase 5: Test Matrix

First target:

| Item | Value |
| --- | --- |
| Release | `trixie` |
| Flavor | CLI/minimal |
| Boot chain | vendor BSP |
| Kernel | vendor K3 kernel |
| Rootfs | Armbian riscv64 |
| Boot media | vendor default, likely UFS first |

Secondary targets after first boot:

- Debian trixie CLI on UFS;
- Debian trixie CLI rootfs on SD if the vendor boot chain can point root to SD;
- Ubuntu noble or resolute CLI if riscv64 rootfs builds cleanly;
- desktop only after GPU/display userspace dependency mapping is clear.

Hardware checks:

- UART console;
- UFS rootfs;
- SD card detection and mount;
- NVMe detection and mount;
- GbE DHCP;
- USB host;
- Type-C flashing/recovery path;
- reboot/poweroff;
- thermal/fan behavior if exposed;
- GPU/NPU/media only after base Linux is stable.

## Risks And Unknowns

- A public BPI-SM10 BSP repository was not found in the BPI-SINOVOIP GitHub
  search results at planning time.
- The K3 source guide uses GitHub SSH remotes. The build host needs a GitHub SSH
  key with access to `spacemit-com/*`, or the manifest remotes must be rewritten
  to HTTPS if all repositories are public.
- The K3 source guide may require a specific `repo` version.
- K3 may require firmware or binary userspace components that cannot be
  committed to Git.
- K3 may use a Titan/fastboot flashing package rather than an Armbian-style raw
  SD image.
- K3 may not share K1 image offsets or firmware names.
- Desktop/GPU/NPU support may require vendor Bianbu/Buildroot packages before
  it can work on Debian or Ubuntu rootfs.

## Immediate Next Actions

1. Done: opened the official K3 Buildroot source guide in a browser and
   recorded the exact download command, manifest branch, manifest XML,
   supported board defconfig, and build command.
2. Done: implemented local BSP fetch/build/stage scripts under
   `/media/pi/SMCI/bpi/bpi-sm10/scripts`.
3. Done: downloaded the official BSP into
   `/media/pi/SMCI/bpi/bpi-sm10/sdk/k3-buildroot-sdk-1.0`.
4. Done: built the unmodified vendor BSP.
5. Done: staged and checksummed the vendor outputs.
6. Pending: flash and boot the unmodified vendor image on BPI-SM10.
7. Pending: only after vendor boot succeeds, add `bananapism10.wip` and
   hybrid-rootfs tooling to the Armbian tree.

## Execution Record: 2026-05-26 Vendor BSP Build

Official documentation evidence:

- Dynamic SpacemiT guide capture:
  `/media/pi/SMCI/bpi/bpi-sm10/notes/spacemit-k3-buildroot-source-20260526.txt`
- Screenshot:
  `/media/pi/SMCI/bpi/bpi-sm10/notes/spacemit-k3-buildroot-source-20260526.png`
- Page last-updated value recorded from the guide: `2026-05-23 10:34:03`

Vendor SDK workspace:

```text
/media/pi/SMCI/bpi/bpi-sm10/sdk/k3-buildroot-sdk-1.0
```

Source manifest evidence:

```text
/media/pi/SMCI/bpi/bpi-sm10/notes/source-manifest-20260526-091258.xml
/media/pi/SMCI/bpi/bpi-sm10/notes/source-manifest-20260526-091258.tsv
/media/pi/SMCI/bpi/bpi-sm10/notes/source-manifest-20260526-091258.txt
```

Key pinned revisions from `repo manifest -r`:

| Component | Path | Revision |
| --- | --- | --- |
| Buildroot | `buildroot` | `06a303b332a7216c6ca9360dd7c7f52a3fb8b1da` |
| Buildroot external tree | `buildroot-ext` | `67a5f68cf4f3720d9f31fff96860863ef1fe6d51` |
| Linux | `bsp-src/linux-6.18` | `27275ec8240cc49af3a525b8bc325d9b5029fb81` |
| U-Boot | `bsp-src/uboot-2022.10` | `1b10c8119e1a9b5451a4236f6b384f7c91eed1e2` |
| OpenSBI | `bsp-src/opensbi` | `3e2f9efc9660b8d5fcae4e0b6495f306d5c64078` |
| ESOS | `package-src/esos` | `92a8baf250e42853a094a7af6f7ee849adb3de4a` |
| Mesa | `package-src/mesa` | `e0f7500a6571846265f4442befdd4a012c5170af` |
| PowerVR userspace | `package-src/img-gpu-powervr` | `f934f308946f35f8eec25e746c04e4cf91b33853` |
| K3 VPU firmware | `package-src/k3x-vpu-firmware` | `8ece3da96f8cbfbd29c64a0a2366fd27652e9353` |
| SDK scripts | `scripts` | `96418825a37a1cf07d3275c13d9d3329934224f0` |

Builder image:

```text
harbor.spacemit.com/bianbu/k3-bsp-builder@sha256:d192640a2503f4d5ca5eadccca545d4fd53d13a1d1f158990078f33fb076c155
```

Build command:

```text
BATCH_MODE=1 /media/pi/SMCI/bpi/bpi-sm10/scripts/build-bpi-sm10-spacemit-k3-bsp.sh --mode k3-build
```

Build log:

```text
/media/pi/SMCI/bpi/bpi-sm10/logs/bpi-sm10-k3-build-k3-build-20260526-091653.log
```

Result:

- The unmodified vendor Buildroot BSP completed successfully.
- Kernel built from `bsp-src/linux-6.18`; installed module version observed in
  the log: `6.18.3`.
- U-Boot built from `bsp-src/uboot-2022.10` and produced `u-boot.itb`.
- OpenSBI produced `fw_dynamic.itb`.
- The generated SD image contains vendor partitions for `env`, `bootinfo`,
  `fsbl`, `esos`, `opensbi`, `uboot`, `bootfs`, and `rootfs`.

Staged release:

```text
/media/pi/SMCI/bpi/bpi-sm10/release/20260526-k3-buildroot-v1.0-vendor-bsp
```

Primary artifacts:

| Artifact | Size | SHA256 |
| --- | ---: | --- |
| `Buildroot-k3-20260526110427-sdcard.img` | 1891651584 | `ce2c9e82aa46f877f85b2915b3cc7267e59c1fdfd150d17f71c853de4489d1a6` |
| `Buildroot-k3-20260526110427.zip` | 363327116 | `3dc26cf5e992727ea16ad875527f069170adf368311ac389c9854109ff51212c` |
| `FSBL.bin` | 449984 | `d18ceb20ae2433e441e9a5d935b1db34a7d35b5cf074979d8104ffc35c4971f2` |
| `fw_dynamic.itb` | 272223 | `6ba858dcbf79371cdf3cc4770e036ea448e7d81547bf880af5b2903e7296a044` |
| `u-boot.itb` | 2134494 | `1f7752ad032e3b04e30ffce5e9e3a79b427c05efc7fc7ef4130fde23a7990982` |
| `bootfs.img` | 268435456 | `c428a76ec6384046cc4053d413ebf201c0642c4b39e38b4f0c29c9df37a125a9` |

Full artifact manifest and checksum file:

```text
/media/pi/SMCI/bpi/bpi-sm10/release/20260526-k3-buildroot-v1.0-vendor-bsp/manifest.tsv
/media/pi/SMCI/bpi/bpi-sm10/release/20260526-k3-buildroot-v1.0-vendor-bsp/SHA256SUMS
```

Validation:

```text
cd /media/pi/SMCI/bpi/bpi-sm10/release/20260526-k3-buildroot-v1.0-vendor-bsp
sha256sum -c SHA256SUMS
```

All staged artifacts passed checksum verification on 2026-05-26.
