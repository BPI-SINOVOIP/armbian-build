# Banana Pi Board Support Priority

Date: 2026-06-01

Branch: `bpi-v26.8.0-trunk`

Scope:

- Official Banana Pi product list was checked from `https://docs.banana-pi.org/en/home`.
- Armbian support state was checked from local `config/boards/*`, `config/sources/families/*`, and the existing Banana Pi release documents in this tree.
- Kernel versions below are the Armbian family `KERNEL_MAJOR_MINOR` values in this branch, not a promise that the image has already booted on real hardware.

## Kernel Version Map

| Armbian family | Branch | Kernel version | Notes |
| --- | --- | --- | --- |
| `sun7i`, `sun6i`, `sun8i` | `legacy` | 6.12 | Allwinner 32-bit via `sunxi_common.inc` |
| `sun7i`, `sun6i`, `sun8i` | `current` | 6.18 | Allwinner 32-bit via `sunxi_common.inc` |
| `sun7i`, `sun6i`, `sun8i` | `edge` | 7.0 | Allwinner 32-bit via `sunxi_common.inc` |
| `sun50iw1`, `sun50iw9-bpi` | `legacy` | 6.12 | Allwinner 64-bit via `sunxi64_common.inc` |
| `sun50iw1`, `sun50iw9-bpi` | `current` | 6.18 | Allwinner 64-bit via `sunxi64_common.inc` |
| `sun50iw1`, `sun50iw9-bpi` | `edge` | 7.0 | Allwinner 64-bit via `sunxi64_common.inc` |
| `meson-g12b`, `meson-sm1` | `current` | 6.18 | Amlogic via `meson64_common.inc` |
| `meson-g12b`, `meson-sm1` | `edge` | 7.0 | Amlogic via `meson64_common.inc` |
| `mt7623` | `current` | 6.6 | BPI-R2 only |
| `rockchip64` | `current` | 6.18 | RK33xx/RK35xx common path |
| `rockchip64` | `edge` | 7.0 | RK33xx/RK35xx common path |
| `rk35xx` | `vendor` | 6.1 | Rockchip vendor kernel |
| `rk35xx` | `edge` | 7.0 | Mainline-style edge path |
| `rockchip-rk3588` | `vendor` | 6.1 | Rockchip vendor kernel |
| `rockchip-rk3588` | `current` | 6.18 | Mainline-style current path |
| `rockchip-rk3588` | `edge` | 7.0 | Mainline-style edge path |
| `filogic` | `current` | 6.12 | Default BPI router path |
| `filogic` | `current` | 6.17 | BPI-R4 Lite override |
| `filogic` | `current` | 6.19 | BPI-R4 Pro override |
| `realtek-rtd129x-bpi` | `legacy` | 4.9 | Vendor BSP path |
| `realtek-rtd139x-bpi` | `legacy` | 4.9 | Vendor BSP path |
| `sunplus-sp7021-bpi` | `legacy` | 5.4 | Vendor BSP path |
| `vs680` | `legacy` | 5.4 | BPI-M6 vendor BSP path |
| `spacemit` | `legacy` | 6.6 | BPI-F3 / BPI-CM6 K1 path |
| `spacemit` | `current` | 6.18 | BPI-F3 K1 path |
| `spacemit` | `edge` | 7.1 | BPI-F3 edge path |
| `spacemit-k3-bpi` | `current` | 6.18 | BPI-SM10 K3 vendor-sync path |
| `renesas-rzv2n-bpi` | `legacy` | 6.1 | BPI-AI2N path |
| `unisoc-uis7885-bpi` | `vendor` | vendor PAC | Normal Armbian raw-image build is intentionally blocked |

## Current Armbian Banana Pi Matrix

| Board | Armbian id | Status | Family | Kernel support in this branch | State |
| --- | --- | --- | --- | --- | --- |
| BPI-M1 | `bananapi` | `conf` | `sun7i` | current 6.18, edge 7.0, legacy 6.12 | normal |
| BPI-M1 Plus | `bananapim1plus` | `csc` | `sun7i` | current 6.18, edge 7.0, legacy 6.12 | community |
| BPI-Pro | `bananapipro` | `csc` | `sun7i` | current 6.18, edge 7.0, legacy 6.12 | community |
| BPI-R1 | `bananapir1` | `eos` | `sun7i` | current 6.18, edge 7.0 | official naming over existing Lamobo R1 path |
| BPI-M2 | `bananapim2` | `csc` | `sun6i` | current 6.18, legacy 6.12 | community |
| BPI-M2 Plus | `bananapim2plus` | `conf` | `sun8i` | current 6.18, edge 7.0, legacy 6.12 | normal |
| BPI-M2 Berry | `bananapim2berry` | `csc` | `sun8i` | current 6.18, edge 7.0, legacy 6.12 | community |
| BPI-M2 Ultra | `bananapim2ultra` | `csc` | `sun8i` | current 6.18, edge 7.0, legacy 6.12 | community |
| BPI-M2 Zero | `bananapim2zero` | `csc` | `sun8i` | current 6.18, edge 7.0, legacy 6.12 | community |
| BPI-P2 Zero | `bananapip2zero` | `csc` | `sun8i` | current 6.18, edge 7.0, legacy 6.12 | community |
| BPI-P2 Pro | `bananapip2pro` | `wip` | `rockchip64` | current 6.18 | initial RK3308 target; needs hardware validation |
| BPI-M2 Magic | `bananapim2magic` | `csc` | `sun8i` | current 6.18, edge 7.0, legacy 6.12 | community |
| BPI-M3 | `bananapim3` | `csc` | `sun8i` | current 6.18, edge 7.0, legacy 6.12 | community |
| BPI-6204 | `bananapi6204` | `wip` | `sun8i` | legacy 6.12 | smoke build exists; needs hardware validation |
| BPI-M64 | `bananapim64` | `csc` | `sun50iw1` | current 6.18, edge 7.0, legacy 6.12 | community |
| BPI-M4 Berry | `bananapim4berry` | `conf` | `sun50iw9-bpi` | current 6.18, edge 7.0 | normal |
| BPI-M4 Zero | `bananapim4zero` | `conf` | `sun50iw9-bpi` | current 6.18, edge 7.0 | normal |
| BPI-CM4 module + CM4IO carrier | `bananapicm4io` | `conf` | `meson-g12b` | current 6.18, edge 7.0 | normal; DTB is carrier-specific |
| BPI-M2S | `bananapim2s` | `conf` | `meson-g12b` | current 6.18, edge 7.0 | normal |
| BPI-M2 Pro | `bananapim2pro` | `conf` | `meson-sm1` | current 6.18, edge 7.0 | normal |
| BPI-M5 | `bananapim5` | `conf` | `meson-sm1` | current 6.18, edge 7.0 | normal |
| BPI-F3 | `bananapif3` | `conf` | `spacemit` | legacy 6.6, current 6.18, edge 7.1 | normal |
| BPI-CM6 | `bananapicm6` | `wip` | `spacemit` | legacy 6.6 / vendor 6.6.36 branch | smoke build exists; needs hardware validation |
| BPI-SM10 | `bananapism10` | `wip` | `spacemit-k3-bpi` | current 6.18 | offline build verified; needs hardware validation |
| BPI-M2C | `bananapim2c` | `wip` | `unisoc-uis7885-bpi` | vendor PAC path | blocked for normal raw image; PAC/hybrid validation track |
| BPI-CM2 | `bananapicm2` | `wip` | `rockchip64` | current 6.18, edge 7.0 | initial R2 Pro-derived target; no dedicated CM2 carrier DTS yet |
| BPI-CM5 Pro | `bananapicm5pro` | `wip` | `rk35xx` | vendor 6.1 | initial ArmSoM CM5 IO-derived target; needs hardware validation |
| BPI-F2S | `bananapif2s` | `wip` | `sunplus-sp7021-bpi` | legacy 5.4 | smoke build exists; needs hardware validation |
| BPI-M4 plain | `bananapim4` | `wip` | `realtek-rtd139x-bpi` | legacy 4.9 | smoke build exists; needs hardware validation |
| BPI-M4 Super | `bananapim4super` | `wip` | `rk35xx` | vendor 6.1 | initial Sige3-derived target; needs hardware validation |
| BPI-W2 | `bananapiw2` | `wip` | `realtek-rtd129x-bpi` | legacy 4.9 | smoke build exists; needs hardware validation |
| BPI-M6 | `bananapim6` | `wip` | `vs680` | legacy 5.4 | smoke build exists; needs hardware validation |
| BPI-R2 | `bananapir2` | `csc` | `mt7623` | current 6.6 | community |
| BPI-R2 Pro | `bananapir2pro` | `csc` | `rockchip64` | current 6.18, edge 7.0 | community |
| BPI-M5 Pro | `bananapim5pro` | `conf` | `rk35xx` | vendor 6.1, edge 7.0 | normal |
| BPI-M7 | `bananapim7` | `conf` | `rockchip-rk3588` | vendor 6.1, current 6.18, edge 7.0 | normal |
| BPI-W3 / BPI-RK3588 LGA core board / BPI-LM7 development kit | `bananapiw3` | `wip` | `rockchip-rk3588` | vendor 6.1 | smoke build exists; needs hardware validation |
| BPI-AIM7 | `bananapiaim7` | `wip` | `rockchip-rk3588` | vendor 6.1 | initial ArmSoM AIM7 IO-derived target; needs hardware validation |
| BPI-M1 Super | `bananapim1super` | `wip` | `rk35xx` | vendor 6.1 | initial ArmSoM Sige1-derived target; needs hardware validation |
| BPI-F2P | `bananapif2p` | `wip` | `sunplus-sp7021-bpi` | legacy 5.4 | initial F2P target from the shared F2S/F2P BSP; needs hardware validation |
| BPI-R3 | `bananapir3` | `wip` | `filogic` | current 6.12 | smoke build exists; needs hardware validation |
| BPI-R3 Mini | `bananapir3mini` | `wip` | `filogic` | current 6.12 | smoke build exists; needs hardware validation |
| BPI-R64 | `bananapir64` | `wip` | `filogic` | current 6.12 | smoke build exists; needs hardware validation |
| BPI-R4 | `bananapir4` | `csc` | `filogic` | current 6.12 | community |
| BPI-R4 Lite | `bananapir4lite` | `wip` | `filogic` | current 6.17 | smoke build exists; needs hardware validation |
| BPI-R4 Pro | `bananapir4pro` | `wip` | `filogic` | current 6.19 | smoke build exists; needs hardware validation |
| BPI-AI2N | `bpi-ai2n` | `conf` | `renesas-rzv2n-bpi` | legacy 6.1 | normal |

## Official Boards Not Yet Represented as Normal Armbian Targets

This list excludes MCU-only accessories and boards already represented above.

| Board | SoC / family | Current local state | Difficulty | Suggested priority |
| --- | --- | --- | --- | --- |
| BPI-R2 Mini | MT7981B | blocked / no local board DTS | Medium/Blocked | P2: official forum says it is based on OpenWrt One hardware, but do not add an Armbian alias until a dedicated DTS/U-Boot board file or verified OpenWrt One compatibility exists |
| OpenWrt One | MT7981B | deferred / kernel DTS exists | Medium/Blocked | P2: local kernel has `mt7981b-openwrt-one.dts`, but the board is an OpenWrt NAND/NOR target; add Armbian only after a NAND/NOR installer or verified bootchain handoff is designed |
| BPI-WiFi6 Router | Triductor TR6560/TR5220 | missing | Hard | P3: OpenWrt/router-specific, no current Armbian family |
| BPI-WiFi6 Mini | Triductor TR6560/TR5220 | missing | Hard | P3: OpenWrt/router-specific, no current Armbian family |
| BPI-R4 Mini | MT7987 | missing | Medium/Hard | P2/P3: maybe reuse R4 Lite family, but board-specific DTS/boot required |
| BPI-RT2 | Realtek RTL8198 | missing | Hard | P3: new Realtek router path, likely OpenWrt-style |
| BPI-WiFi5 Router | Siflower SF19A2890S | missing | Hard/Blocked | P3: OpenWrt web-upgrade flow, not a normal raw-image target |
| BPI-RV2 Gateway | Siflower SF21H8898 | missing | Hard/Blocked | P3: needs new `siflower-sf21h8898` family and image strategy |
| BPI-F2 | Freescale i.MX6 | missing | Hard | P3: old vendor path; no active BPI i.MX6 family in this branch |
| BPI-F4 | Sunplus SP7350 | missing | Hard | P3: new Sunplus family |
| BPI-F5 | Allwinner T527 | missing | Medium/Hard | P3: needs BSP/DTS review; may reuse new sunxi work if SoC support exists |
| BPI-Forge1 | RK3506J | missing | Hard | P3: new Rockchip low-end family work likely required |
| BPI-CanMV-K230D Zero | Canaan K230D | missing | Hard | P3: new RISC-V/AI SoC family |
| BPI-S64 Core | Actions S700 | missing | Hard | P3: old unsupported SoC family |
| BPI-CM5 | Amlogic A311D2 | missing | Medium/Hard | P3: related to Amlogic but needs exact boot FIP/DTS support |
| BPI-Secure-Pi | MegaHunt SP2302 | missing | Hard | P3: new vendor BSP family |
| BPI-SM9 | SOPHGO BM1688 | missing | Hard | P3: new SOPHGO family |
| BPI-AI2H | Renesas RZ/V2H | missing | Medium/Hard | P3: can learn from AI2N but needs V2H BSP/ATF/U-Boot |
| BPI-KVM | RK3568 | missing / application product | Medium | P3: decide whether it needs a separate board image or an app layer over RK3568 |
| BPI-MNF | MT7622E | missing / router product | Medium/Hard | P3: likely related to R64 but modem/router image scope must be defined |
| BPI-OM7 | RK3588 + Orbbec | covered by M7 base | Easy | No separate board unless camera application image is required |
| BPI-6202 | Allwinner A40i-H | missing | Medium | P3: likely close to BPI-6204; needs DTS and storage validation |
| BPI-5202 / 2K3000 / 3A5000 / 3A6000 | Loongson | missing | Hard | P3: new Loongson/LoongArch product line, outside current BPI matrix |

## Recommended Order

1. Promote already integrated WIP boards by hardware validation first. This is the shortest path because most code is already in the tree:
   - BPI-6204
   - BPI-F2S
   - BPI-W2
   - BPI-M4 plain
   - BPI-M6
   - BPI-CM6
   - BPI-W3
   - BPI-R3, R3 Mini, R64, R4 Lite, R4 Pro
   - BPI-SM10
   - BPI-M2C, but only through its PAC/hybrid test path

2. Add low-risk official coverage aliases or board configs:
   - Done: BPI-R1 has official `bananapir1` naming.
   - Done: BPI-CM4 is documented as covered by `bananapicm4io`; add more carrier-specific targets only when a separate DTB exists.
   - Done: BPI-M4 Super has initial `bananapim4super` WIP coverage through the existing RK3568 Sige3-derived boot path.
   - Done: BPI-CM2 has initial `bananapicm2` WIP coverage through the existing BPI-R2 Pro RK3568 boot path.
   - Done: BPI-CM5 Pro has initial `bananapicm5pro` WIP coverage through the existing ArmSoM CM5 IO RK3576 boot path.
   - Done: BPI-RK3588 LGA core board development kit is documented as covered by the existing `bananapiw3` WIP target.
   - Done: BPI-LM7 is documented as the LGA core module used by the existing `bananapiw3` WIP target.
   - Done: BPI-AIM7 has initial `bananapiaim7` WIP coverage through the existing ArmSoM AIM7 IO RK3588 boot path.
   - Done: BPI-P2 Pro has initial `bananapip2pro` WIP coverage using the RK3308 current kernel DTS and a new U-Boot defconfig.
   - Done: BPI-M1 Super has initial `bananapim1super` WIP coverage through the existing ArmSoM Sige1 RK3528 boot path.
   - Done: BPI-F2P has initial `bananapif2p` WIP coverage through the shared BPI-F2S/F2P Sunplus SP7021 BSP.

3. Port boards that reuse existing kernel families and boot logic:
   - RK3568/RK3576/RK3588 group: completed for this P1 pass; remaining Rockchip boards are older P2 items.
   - SP7021 group: completed for the shared F2S/F2P BSP targets.
   - older Rockchip group: completed for this P2 pass.

4. Router boards after the current Filogic WIP boards boot on hardware:
   - BPI-R2 Mini stays blocked until a board-specific DTS/U-Boot target or verified OpenWrt One compatibility exists.
   - OpenWrt One stays deferred until Armbian has a NAND/NOR image or installer path for the OpenWrt One boot layout.
   - BPI-R4 Mini
   - then Siflower/Triductor/Realtek router boards only after deciding whether non-raw OpenWrt-style images belong in this Armbian release.

5. New SoC families last:
   - BPI-F4, BPI-F5, Forge1, CanMV K230D Zero, S64 Core, Secure-Pi, SM9, AI2H, Loongson industrial boards.

When two boards have the same difficulty, use the older public product order first. For the current tree, that means older M/R series boards before newer AI/industrial boards unless we already have local hardware for the newer board.
