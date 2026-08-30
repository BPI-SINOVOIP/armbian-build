# Banana Pi M64 current 來源與功能政策

日期：2026-08-26

## 結論

`bananapim64` 具備可建立 current L2 候選的主線 Linux、U-Boot、ATF、Crust 與板級 DTB。本次將五段來源固定到精確提交，保留 A64 共用的 648 MHz DRAM 保守修補，並把候選守門限制在主線 DTB 與核心設定可證明的功能。

## 固定來源

| 元件 | 來源 | 提交 |
| --- | --- | --- |
| Linux 6.18.46 | `https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git` | `1f99e9ab748fc5c32120de9c4eca31abfe54a4d5` |
| U-Boot 2024.01 | `https://github.com/u-boot/u-boot` | `866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e` |
| ARM Trusted Firmware | `https://github.com/ARM-software/arm-trusted-firmware` | `c2a0e7080d64d69940be4ad0ff6578501f3cbf9e` |
| Crust | `https://github.com/crust-firmware/crust` | `ffe9f1ac9c675e6e67db9084bd19fbdeffd8e162` |
| Armbian firmware | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |

ATF 提交對應 `lts-v2.12.9`，Crust 提交對應 `v0.6` 的去參照 tag。候選映像中繼資料與已安裝 U-Boot 套件必須逐欄證明這些來源，不能只記錄 tag 名稱。

## 軟體功能範圍

- 儲存：4-bit SD、4-bit SDIO Wi-Fi、8-bit eMMC、eMMC hardware reset。
- 連線：AP6212 Wi-Fi／Bluetooth、RGMII-ID Gigabit Ethernet。
- USB：MUSB OTG、兩組 EHCI 與兩組 OHCI host；核心包含 USB configfs mass-storage。
- 顯示與媒體：主線 HDMI、HDMI 音訊、類比音訊、Lima、Cedrus 與 Allwinner Crypto Engine。
- 開發介面：GPIO character device、I2C、SPI、UART、1-Wire 與 PPS；候選必須帶入 `i2c1`、`pps-gpio`、`spi-spidev`、`uart2`、`uart3`、`uart4`、`w1-gpio` overlay。
- 電源：AXP803 與 Crust SCP 韌體建置鏈。

Bluetooth 使用 UART1，故不把 `uart1` 列為通用排針 overlay。U-Boot 的 DE2 停用修補只影響 bootloader 階段顯示，Linux HDMI 仍由主線 DRM 驅動；取得實機證據前，不宣稱 U-Boot HDMI 畫面、無線、GPU、VPU、OTG gadget、待機或喚醒已可用。

## L2 守門

1. 由乾淨來源完整重建 U-Boot、ATF、Crust、Linux、firmware、rootfs 與映像。
2. 驗證 U-Boot 套件中繼資料的 U-Boot／ATF／Crust 來源、ref 與 revision。
3. 比對套件 payload 與映像 8 KiB 偏移的實際位元組。
4. 驗證 IMG／XZ 雜湊與串流解壓同一性。
5. 唯讀檢查 DTB 身分、儲存匯流排寬度、節點、屬性、核心設定、無線韌體、overlay 與工具套件。

通過上述項目只可升級為 L2。L3 仍需實體 M64 完成 UART、冷啟動、SD／eMMC、網路、無線、USB、HDMI、音訊、GPU、VPU、排針與電源管理驗證。
