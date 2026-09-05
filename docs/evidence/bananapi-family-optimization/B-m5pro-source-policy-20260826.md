# Banana Pi M5 Pro edge 來源固定政策

日期：2026-08-26

## 結論

`bananapim5pro` 目前正式宣告支援 `edge` 與 `vendor`，不宣告 `current`。本次先建立 `edge` 軟體候選，因為它使用上游穩定核心並能與現有 Rockchip L2 唯讀守門整合；`vendor` 的 GPU、VPU、RGA、編碼器與 NPU 能力較完整，但必須獨立驗證，不以 vendor 結果推論 edge 已支援。

## 固定來源

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| Linux 7.0.14 | `https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git` | `458c6079fc1d41d564c37679c8ace02cd83ee817` |
| Linux 修補集 | `patch/kernel/archive/rockchip64-7.0` | 復原先前通過完整建置的固定內容 |
| U-Boot | `https://github.com/radxa/u-boot.git` | `39cd993e5d6296635438e84f4576b3a9bf76f86e` |
| RKBin | `https://github.com/armbian/rkbin` | `1d3c61008fa823936ae7a59615393f8294b64456` |

三個提交皆可由宣告遠端取得。固定只在 M5 Pro 的 `edge` hook 生效，不改動其他 RK3576 板卡或 `vendor` 分支。

Linux 7.0.14 已包含 `serial: 8250_dw: dispatch SysRq character in dw8250_handle_irq()` 的穩定分支修正。舊的 Armbian 7.0 臨時修補會重複修改相同鎖定路徑，因此本次移除該過時修補，避免對已修正的核心重複套用；此變更只影響 `rockchip64-7.0` 修補目錄。

2026-09-06 整併上游後，通用 edge 修補目錄已移至 Linux 7.1，但 M5 Pro 仍固定 Linux 7.0.14，造成三個 7.1 修補無法套用。本板現由板級 hook 明確固定 `archive/rockchip64-7.0`，並復原先前建置通過的完整修補集；其他 Rockchip edge 板仍跟隨上游新版，不受此相容修正影響。

## RK3576 啟動輸入

本次固定 DDR v1.08、BL31 v1.20、FlashBoost v1.02、USB plug v1.03、`RK3576MINIALL.ini` 與 `boot_merger` 的 SHA-256。DDR v1.09 曾在部分 RK3576 板卡出現啟動失敗，因此軟體候選維持 v1.08，後續只有在多板冷啟動證據足夠時才評估更新。

INI 內的原廠 SPL 路徑會在建置時改成固定 U-Boot 提交所產生的 `spl/u-boot-spl.bin`。主映像實際寫入 `idbloader.img` 於 32768 bytes，以及 `u-boot.itb` 於 8388608 bytes；六項 RKBin 輸入與兩個輸出載荷都必須進入 L2 證據。

## 軟體候選範圍

- 驗證 SD 4-bit、SDIO 4-bit 與 eMMC 8-bit 的 DT 設定。
- 驗證雙 Ethernet、PCIe／NVMe、HDMI、GPU、VOP、USB host／OTG、UART、ADC 及基本音訊設定。
- 驗證 edge 核心具有 Panthor／Panfrost、DRM、Hantro、RGA、VDEC、Crypto、RTL8852BS、Bluetooth 與 USB gadget mass-storage 設定。
- 封裝標準 GPIO、I2C、SPI、視訊、PCIe、NVMe、USB、無線與網路診斷工具。

上述項目只建立 L2 軟體證據。Wi-Fi／Bluetooth 天線與韌體、雙網路埠時序、SD／eMMC／NVMe 啟動、顯示、音訊、40-pin、GPU／VPU 及 USB 角色仍須實體板測試；目前板級 DTS 主要沿用 ArmSoM Sige5，不能由靜態相容字串推論所有載板差異已驗收。
