# Banana Pi M2 Berry current 候選映像 L2 建置證據

日期：2026-08-26

## 結論

`bananapim2berry` 已由乾淨來源完整建置 Debian Trixie current minimal CLI，並通過 R40 專用 L2 唯讀守門。此結果證明候選映像的固定來源、IMG／XZ 同一性、板級 U-Boot 載荷、M2 Berry DTB、核心功能、AP6212 韌體、八項排針 overlay 與標準開發工具符合本次受控政策。

此結果不代表實體板已完成 UART、SD 開機、DRAM、SATA、網路、無線、顯示、音訊、USB 或排針驗證；取得實機證據前維持 L2。主線 DTB 沒有啟用 R40 MUSB OTG 控制器，本候選不宣稱 USB gadget 或 OTG。

## 可重現基線

| 項目 | 值 |
| --- | --- |
| 映像來源提交 | `bf900397d418fbf913a014d3febef7f3ed7fa357` |
| 映像來源樹 | `fdb815136c8d05499d34d5acd241c7d0fdba9604` |
| 驗證器提交 | `bf900397d418fbf913a014d3febef7f3ed7fa357` |
| 建置與驗證設定 SHA-256 | `3886dfd6e76c79dfdd0a4cb90c02f29577dcb41196bf060d06fdbdc8e63c04d5` |
| Linux | `6.18.46`，來源提交 `1f99e9ab748fc5c32120de9c4eca31abfe54a4d5` |
| U-Boot | `2024.01`，來源提交 `866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e` |
| Armbian firmware | 來源提交 `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| 發行版與設定 | `RELEASE=trixie BRANCH=current BUILD_DESKTOP=no BUILD_MINIMAL=yes` |
| 重新建置政策 | `ARTIFACT_IGNORE_CACHE=yes CLEAN_LEVEL=make-kernel,make-uboot` |
| 完整建置時間 | 22 分 17 秒；Docker 執行 1343 秒 |
| 核心建置時間 | 732 秒 |

建置使用 OverlayFS 隔離快取，既有快取只作唯讀下層。U-Boot、核心、韌體與 rootfs 均依固定來源重新產生，不是由既有映像改名、只換 DTB 或只替換 bootloader。

## 啟動載荷證據

| 載荷 | 位置 | 偏移 | 大小 | SHA-256 |
| --- | --- | ---: | ---: | --- |
| `u-boot-sunxi-with-spl.bin` | 映像 | 8192 | 546808 | `32ec8b9cba09f52cefe8d6a5dc9d6c6b8f2fededca49f3978d5b87528578175c` |

載荷證據清單 SHA-256 為 `5e4bf0f21bf342e4464034f61be573de6ce820bf00592f24553cf681d84f7376`。守門同時比對 U-Boot 套件 payload 與映像 8 KiB 偏移的實際位元組。

## 映像雜湊

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| IMG | 1442840576 | `da37b5382102a8bff53a544c27b0462d2db2c18f34ec5587e51da87d7751dc69` |
| XZ | 347054752 | `2c2e7206d00e873a1f0e7be0bf3589d0e144bd820b28d5996fed7dd8448f59fb` |

XZ 已通過完整性檢查，串流解壓 SHA-256 與 IMG 完全相同。

## L2 守門範圍

1. 檢查候選矩陣、中繼資料、來源提交、來源樹、建置參數及驗證設定雜湊一致。
2. 驗證固定 Linux、U-Boot 與 Armbian firmware 提交，以及 U-Boot 套件與映像載荷位元組一致。
3. 以唯讀 loop 與 `mount -o ro,noload` 檢查映像，不執行映像內程式。
4. 檢查核心、initrd、`overlay_prefix=sun8i-r40` 與 `allwinner/sun8i-v40-bananapi-m2-berry.dtb` 的 model、compatible 及檔名身分。
5. 檢查 SD、SDIO Wi-Fi、SATA、Gigabit Ethernet、顯示、音訊、GPU、VPU、Crypto、USB host、UART 與相關 PHY 設定。
6. 檢查 AP6212 的 Wi-Fi、Bluetooth 裝置樹設定，以及 BCM43430 韌體、校準資料與 BCM43430A1 Bluetooth 韌體雜湊。
7. 檢查 `i2c2`、`i2c3`、`spi-spidev0`、`spi-spidev1`、`uart2`、`uart4`、`uart5`、`uart7` 共八項 overlay 已封裝。
8. 檢查 `rfkill`、`bluetooth`、`bluez`、`bluez-tools`、`iw`、`ethtool`、`usbutils`、`alsa-utils`、`smartmontools`、`gpiod`、`i2c-tools`、`python3-libgpiod`、`python3-spidev` 與 `v4l-utils` 已安裝。

核心設定、模組、DT 節點、韌體或工具存在只證明軟體已封裝，不等同相關硬體路徑可用。上游 U-Boot 的 576 MHz DRAM 設定只代表可重現基線，不代表任一記憶體批次已通過壓力測試。

## L3 實機門檻

- 執行至少 30 次完整斷電冷啟，保存 UART 全程日誌及失敗率。
- 驗證不同記憶體與 SD 批次在 576 MHz DRAM 設定下的冷啟動、重啟、長時間壓力及溫度邊界。
- 驗證 SD 開機、SATA 持續讀寫、Gigabit Ethernet、Wi-Fi、Bluetooth 與斷線重連。
- 驗證顯示、GPU、VPU、音訊、USB host 與硬體加速的實際資料路徑。
- 驗證 GPIO、I2C、SPI、UART 與欲發布 overlay 的實體迴路、腳位衝突及電壓域。

## 本機證據位置

```text
output/images/2026.08/bananapi-sunxi-r40-m2berry-trixie-current-cli/
```

此目錄包含 `CANDIDATES.tsv`、`COMPLETION_STATUS.json`、`UBOOT_PAYLOAD_EVIDENCE.tsv`、`VERIFICATION.tsv`、`VERIFICATION_STATUS.json`、IMG／XZ／SHA-256、中繼資料與完整建置日誌。大型映像與日誌不加入 Git。
