# Banana Pi M2 Ultra current 候選映像 L2 建置證據

日期：2026-08-26

## 結論

`bananapim2ultra` 已由乾淨來源完整建置 Debian Trixie current minimal CLI，並通過 R40 專用 L2 唯讀守門。此結果證明候選映像的固定來源、IMG／XZ 同一性、板級 U-Boot 載荷、M2 Ultra DTB、8-bit eMMC 設定、核心功能、AP6212 韌體、八項排針 overlay 與標準開發工具符合本次受控政策。

此結果不代表實體板已完成 UART、SD／eMMC 開機、DRAM、SATA、網路、無線、顯示、音訊、IR、USB 或排針驗證；取得實機證據前維持 L2。主線 DTB 沒有啟用 R40 MUSB OTG 控制器，本候選不宣稱 USB gadget 或 OTG。

## 可重現基線

| 項目 | 值 |
| --- | --- |
| 映像來源提交 | `4a5aa8e2d5cca23c7fb3be7546cf444c9f0ef8a8` |
| 映像來源樹 | `f2bcfaaa4045072f338d74f36d5635effbea5c3a` |
| 驗證器提交 | `4a5aa8e2d5cca23c7fb3be7546cf444c9f0ef8a8` |
| 建置與驗證設定 SHA-256 | `3886dfd6e76c79dfdd0a4cb90c02f29577dcb41196bf060d06fdbdc8e63c04d5` |
| Linux | `6.18.46`，來源提交 `1f99e9ab748fc5c32120de9c4eca31abfe54a4d5` |
| U-Boot | `2024.01`，來源提交 `866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e` |
| Armbian firmware | 來源提交 `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| 發行版與設定 | `RELEASE=trixie BRANCH=current BUILD_DESKTOP=no BUILD_MINIMAL=yes` |
| 重新建置政策 | `ARTIFACT_IGNORE_CACHE=yes CLEAN_LEVEL=make-kernel,make-uboot` |
| 完整建置時間 | 22 分 15 秒；Docker 執行 1342 秒 |
| U-Boot 建置時間 | 10 秒 |
| 核心建置時間 | 733 秒 |

建置使用 OverlayFS 隔離快取，既有快取只作唯讀下層。U-Boot、核心、韌體與 rootfs 均依固定來源重新產生，不是由 M2 Berry 映像改名、只換 DTB 或只替換 bootloader。

## 啟動載荷證據

| 載荷 | 位置 | 偏移 | 大小 | SHA-256 |
| --- | --- | ---: | ---: | --- |
| `u-boot-sunxi-with-spl.bin` | 映像 | 8192 | 547208 | `d629f146d8d5e1caf0ef663e6c3130d8cfb303f44154baafd937ff32d8eba052` |

載荷證據清單 SHA-256 為 `dac00ca7fae7ec66fa82988b3c360bd153a531b92571a781fe09740aaf3da62f`。守門同時比對 U-Boot 套件 payload 與映像 8 KiB 偏移的實際位元組。

## 映像雜湊

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| IMG | 1442840576 | `21cff520cb8f6e1d946ab677a1a0d2e780caedac75f80ff98b89b85f909f3961` |
| XZ | 345010856 | `04434704f9221c95a10f0ddba4492be6599419e727e948b24b48c3c71b6aae80` |

XZ 已通過完整性檢查，串流解壓 SHA-256 與 IMG 完全相同。

## L2 守門範圍

1. 檢查候選矩陣、中繼資料、來源提交、來源樹、建置參數及驗證設定雜湊一致。
2. 驗證固定 Linux、U-Boot 與 Armbian firmware 提交，以及 U-Boot 套件與映像載荷位元組一致。
3. 以唯讀 loop 與 `mount -o ro,noload` 檢查映像，不執行映像內程式。
4. 檢查核心、initrd、`overlay_prefix=sun8i-r40` 與 `allwinner/sun8i-r40-bananapi-m2-ultra.dtb` 的 model、compatible 及檔名身分。
5. 檢查 SD 與 SDIO 為 4-bit、eMMC 為 8-bit 且不可移除，並檢查 SATA、Gigabit Ethernet、顯示、音訊、IR、GPU、VPU、Crypto、USB host、UART 與相關 PHY 設定。
6. 檢查 AP6212 的 Wi-Fi、Bluetooth 裝置樹設定，以及 BCM43430 韌體、校準資料與 BCM43430A1 Bluetooth 韌體雜湊。
7. 檢查 `i2c2`、`i2c3`、`spi-spidev0`、`spi-spidev1`、`uart2`、`uart4`、`uart5`、`uart7` 共八項 overlay 已封裝。
8. 檢查 `rfkill`、`bluetooth`、`bluez`、`bluez-tools`、`iw`、`ethtool`、`usbutils`、`alsa-utils`、`smartmontools`、`gpiod`、`i2c-tools`、`python3-libgpiod`、`python3-spidev` 與 `v4l-utils` 已安裝。

核心設定、模組、DT 節點、韌體或工具存在只證明軟體已封裝，不等同相關硬體路徑可用。上游 U-Boot 的 576 MHz DRAM 設定只代表可重現基線，不代表任一記憶體或 eMMC 批次已通過壓力測試。

## L3 實機門檻

- 執行至少 30 次完整斷電冷啟，保存 UART 全程日誌及失敗率。
- 分別驗證 SD 與 eMMC 開機、安裝、交互啟動順序及斷電檔案系統一致性。
- 驗證不同記憶體與 eMMC 批次在 576 MHz DRAM 設定下的冷啟動、重啟、長時間壓力及溫度邊界。
- 驗證 SATA 持續讀寫、Gigabit Ethernet、Wi-Fi、Bluetooth 與斷線重連。
- 驗證顯示、GPU、VPU、音訊、IR、USB host 與硬體加速的實際資料路徑。
- 驗證 GPIO、I2C、SPI、UART 與欲發布 overlay 的實體迴路、腳位衝突及電壓域。

## 本機證據位置

```text
output/images/2026.08/bananapi-sunxi-r40-m2ultra-trixie-current-cli/
```

此目錄包含 `CANDIDATES.tsv`、`COMPLETION_STATUS.json`、`UBOOT_PAYLOAD_EVIDENCE.tsv`、`VERIFICATION.tsv`、`VERIFICATION_STATUS.json`、IMG／XZ／SHA-256、中繼資料與完整建置日誌。大型映像與日誌不加入 Git。
