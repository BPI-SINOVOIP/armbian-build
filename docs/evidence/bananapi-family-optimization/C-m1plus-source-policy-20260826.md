# Banana Pi M1+ current 來源固定政策

日期：2026-08-26

## 結論

`bananapim1plus` 適合沿用 A20 current 軟體候選流程，但必須使用獨立驗證設定，避免修改既有 Banana Pi 與 Banana Pro 的 L2 契約。M1+ 增加 AP6210 SDIO Wi-Fi、第二組 MMC、較完整 USB 與排針介面，因此不能只把既有 A20 映像改名。

## 固定來源

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| Linux 6.18.46 | `https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git` | `1f99e9ab748fc5c32120de9c4eca31abfe54a4d5` |
| U-Boot 2024.01 | `https://github.com/u-boot/u-boot` | `866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e` |

兩個提交只在 M1+ 的 `current` hook 生效，不改動 `edge`、`legacy` 或其他 A20 板卡。

## DRAM 政策

M1+ 上游 `bananapi_m1_plus_defconfig` 與既有 U-Boot 套件使用 432 MHz。Banana Pi 與 Banana Pro 的 384 MHz 保守值是另外兩張板的板級覆寫，沒有 M1+ 冷啟動證據前不得直接套用。本次保留 M1+ 上游 432 MHz，避免在來源固定、Wi-Fi 與介面驗證之外再加入未證實的 DRAM 變因。

432 MHz 只代表建置設定，不代表任何記憶體批次已通過冷啟動或壓力測試。

## Wi-Fi 韌體政策

M1+ DTB 將 `/soc/mmc@1c12000` 設為 4-bit、`non-removable`、`wakeup-source`，子節點使用 `brcm,bcm4329-fmac`。映像固定下列實際存在的通用 BCM43362 韌體：

| 映像路徑 | SHA-256 |
| --- | --- |
| `/lib/firmware/brcm/brcmfmac43362-sdio.bin` | `5783fd90528cc7ae421b6a6056b1572a3840eac4559b26d299d1acae17523e42` |
| `/lib/firmware/brcm/brcmfmac43362-sdio.txt` | `353bc911682d404f2912fc0d3efd3fad6da643c56edda23464e4567c87918b46` |

目前韌體套件沒有 `sinovoip,bpi-m1-plus` 板級 NVRAM 檔，核心將退回通用檔案。DT 靜態資料也不足以證明 Bluetooth UART 已接通，因此本次不封裝 Bluetooth 使用者空間套件，也不宣稱 Bluetooth 支援。

## 軟體候選範圍

- 驗證 SD 4-bit 與 SDIO Wi-Fi 4-bit 設定。
- 驗證 SATA、Gigabit Ethernet、HDMI、類比音訊、IR、I2C、UART、USB OTG 與 USB host 節點。
- 驗證 Lima、Cedrus、A20 crypto、CAN、PWM、SPI、I2C、音訊與 USB gadget mass-storage 核心設定。
- 驗證 `can`、I2C、I2S、PWM、SPDIF、SPI 與 UART 的 12 個 overlay 已封裝。
- 封裝 GPIO、I2C、SPI、視訊、USB、音訊、Wi-Fi 與網路診斷工具。

上述項目只建立 L2 軟體證據。SD 開機、SATA、網路、Wi-Fi、顯示、音訊、排針、USB 角色與記憶體穩定性仍須實體板測試。
