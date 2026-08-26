# Banana Pi M3 A83T current 來源固定政策

日期：2026-08-26

## 結論

`bananapim3` 可在 current 建立受控的 A83T L2 軟體候選。本階段固定 Linux、U-Boot 與 AP6212 韌體來源，驗證 SD、SDIO Wi-Fi、8-bit eMMC、Bluetooth UART、USB OTG／host、GbE、HDMI、HDMI 音訊、IR、AC100、Cedrus 與基本 I/O 軟體能力。

主線 DTB 沒有 GPU 節點，也沒有 A83T 排針 overlay；因此核心雖包含通用 Panfrost 與 SPI 驅動，本候選不宣稱 GPU 硬體加速或完整 40-pin 排針已支援。

## 固定來源

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| Linux 6.18.46 | `https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git` | `1f99e9ab748fc5c32120de9c4eca31abfe54a4d5` |
| U-Boot 2024.01 | `https://github.com/u-boot/u-boot` | `866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e` |
| Armbian firmware | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |

三個提交只在 `bananapim3` 的 `current` hook 生效，不修改 `edge`、`legacy` 或其他 Sunxi 板卡。

## U-Boot 與 DRAM 政策

板卡繼續使用 `u-boot-sunxi/board_bananapim3`，只套用 `Add-MACH_SUN8I_A83T-to-can-calibrate.patch`。該修補讓 A83T 進入 Sunxi MMC 校準路徑。共用 U-Boot 目錄內其餘修補分別針對 H3、H5、H6、H616、A64 或特定板卡，未發現 M3 current 必須合併的修補，因此不刪除板級覆寫，也不盲目套用共用集合。

固定 U-Boot defconfig 使用 480 MHz DRAM。本次不覆寫頻率、時序或 ZQ；480 MHz 只代表可重現的上游設定，不代表任何記憶體批次已完成冷啟動與壓力驗證。

## L2 軟體門檻

- 驗證 IMG／XZ 同一性、第一階段開機區及 U-Boot 套件 payload。
- 驗證 M3 DTB 的 model、compatible、SD／SDIO／eMMC、OTG、GbE、HDMI、IR、RSB、AXP813 與 AC100 節點。
- 驗證 AP6212 Wi-Fi、Bluetooth 韌體與校準資料雜湊。
- 驗證 HDMI 音訊、Cedrus、USB gadget mass-storage、GPIO、I2C、SPI、RTC 與儲存核心設定。
- 驗證無線、Bluetooth、網路、音訊、USB、儲存、GPIO、I2C、SPI 與 V4L2 診斷工具已安裝。

裝置樹節點、驅動、韌體或工具存在只代表軟體封裝完整，不等同硬體功能通過。SATA 供電節點只服務板載 USB-to-SATA 路徑，不得宣稱 A83T 具備原生 AHCI。

## L3 實機門檻

- 保存 UART 完整日誌，執行至少 30 次斷電冷啟與重啟。
- 分別驗證 SD 與 eMMC 開機、安裝、交互啟動及長時間讀寫。
- 驗證 AP6212 Wi-Fi／Bluetooth、GbE、USB host／OTG、HDMI 影像與音訊、IR、AC100 及 USB-to-SATA。
- 以 V4L2 實際解碼工作負載驗證 Cedrus，不以模組存在代替。
- 依實際接腳表逐項驗證 GPIO、I2C、SPI 與 UART；建立 A83T 專用 overlay 前，不發布完整排針支援聲明。
