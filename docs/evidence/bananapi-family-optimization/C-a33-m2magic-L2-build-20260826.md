# Banana Pi A33 M2 Magic current 候選映像 L2 建置證據

日期：2026-08-26

## 結論

`bananapim2magic` 已由乾淨來源完整建置 Debian Trixie current minimal CLI，並通過 A33 專用 L2 唯讀守門。此結果證明候選映像的固定來源、IMG／XZ 同一性、板級 U-Boot 載荷、M2 Magic DTB、SD／SDIO／eMMC、AP6212 Wi-Fi／Bluetooth 韌體、USB OTG、音訊、Lima、Cedrus、Crypto 與標準開發工具符合本次受控政策。

此結果不代表實體板已完成 UART、冷啟動、記憶體、儲存、無線、Bluetooth、USB、音訊、GPU、VPU 或排針驗證；取得實機證據前維持 L2。主線 DTB 明確停用顯示引擎與 DSI，板卡也沒有 HDMI，本候選不宣稱顯示輸出。

## 可重現基線

| 項目 | 值 |
| --- | --- |
| 映像來源提交 | `12ab3f0f4d79503e6151315c53d210f0f591621d` |
| 映像來源樹 | `0340bf23fce42962b5f72d04da53c1d5f31f2990` |
| 驗證器提交 | `12ab3f0f4d79503e6151315c53d210f0f591621d` |
| 建置與驗證設定 SHA-256 | `1e3364f72293b84eafd83522ee81b4ab15203c86e3390b6e57cb39785dc44635` |
| Linux | `6.18.46`，來源提交 `1f99e9ab748fc5c32120de9c4eca31abfe54a4d5` |
| U-Boot | `2024.01`，來源提交 `866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e` |
| Armbian firmware | 來源提交 `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| 發行版與設定 | `RELEASE=trixie BRANCH=current BUILD_DESKTOP=no BUILD_MINIMAL=yes` |
| 重新建置政策 | `ARTIFACT_IGNORE_CACHE=yes CLEAN_LEVEL=make-kernel,make-uboot` |
| 完整建置時間 | 22 分 28 秒；Docker 執行 1354 秒 |
| U-Boot 建置時間 | 11 秒 |
| 核心建置時間 | 731 秒 |

建置使用 OverlayFS 隔離快取，既有快取只作唯讀下層。U-Boot、核心、韌體與 rootfs 均依固定來源重新產生，不是由既有映像改名、只換 DTB 或只替換 bootloader。

## 啟動載荷證據

| 載荷 | 位置 | 偏移 | 大小 | SHA-256 |
| --- | --- | ---: | ---: | --- |
| `u-boot-sunxi-with-spl.bin` | 映像 | 8192 | 576884 | `1d5fb270bfcc657e613c409016e26ae2ccc385d11e187e783fc637719b72b7fb` |

載荷證據清單 SHA-256 為 `a1fb59bae5c5f41019210386bcd02a57483e8dd7669beef52d95a907310d60ed`。守門同時比對 U-Boot 套件 payload 與映像 8 KiB 偏移的實際位元組。

## 映像雜湊

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| IMG | 1438646272 | `d7ff458b368797e11e564f10e18f1c4fcd45bacfa3ef7323720819204d19e434` |
| XZ | 343635356 | `4c7799c0448069c4db4c69339ca90651af22d81485e5e1b4f1a3e335c68e9906` |

XZ 已通過完整性檢查，串流解壓 SHA-256 與 IMG 完全相同。

## L2 守門範圍

1. 檢查候選矩陣、中繼資料、來源提交、來源樹、建置參數及驗證設定雜湊一致。
2. 驗證固定 Linux、U-Boot 與 Armbian firmware 提交，以及 U-Boot 套件與映像載荷位元組一致。
3. 以唯讀 loop 與 `mount -o ro,noload` 檢查映像，不執行映像內程式。
4. 檢查核心、initrd、`overlay_prefix=sun8i-a33` 與 `allwinner/sun8i-r16-bananapi-m2m.dtb` 的 model、compatible 及檔名身分。
5. 檢查 SD 與 SDIO 為 4-bit、eMMC 為 8-bit，並檢查 USB OTG／host、Bluetooth UART、音訊、PMIC、GPU、VPU 與 Crypto 節點。
6. 以 `required_present_nodes` 驗證省略 `status` 的 GPU、VPU 與 Crypto 節點確實存在且未停用。
7. 檢查 BCM43430 Wi-Fi 韌體、校準資料與 BCM43430A1 Bluetooth 韌體雜湊。
8. 檢查 Lima、Cedrus、音訊、USB gadget mass-storage、Wi-Fi、Bluetooth、GPIO、I2C 與 MMC 核心設定。
9. 檢查 `rfkill`、`bluetooth`、`bluez`、`bluez-tools`、`iw`、`usbutils`、`alsa-utils`、`gpiod`、`i2c-tools`、`python3-libgpiod`、`python3-spidev` 與 `v4l-utils` 已安裝。

核心設定、模組、DT 節點、韌體或工具存在只證明軟體已封裝，不等同相關硬體路徑可用。上游 U-Boot 的 600 MHz DRAM 與 USB mass-storage 設定也必須經實機確認。

## L3 實機門檻

- 執行至少 30 次完整斷電冷啟，保存 UART 全程日誌及失敗率。
- 分別驗證 SD 與 eMMC 開機、安裝、交互啟動順序及斷電檔案系統一致性。
- 驗證不同記憶體與 eMMC 批次在 600 MHz DRAM 設定下的冷啟動、重啟、長時間壓力及溫度邊界。
- 驗證 Wi-Fi、Bluetooth、USB host、OTG gadget mass-storage、音訊、Lima OpenGL ES 與 Cedrus V4L2 解碼。
- 驗證 GPIO、I2C、UART 與板卡實際引出的介面；目前沒有 A33 overlay，不可由工具存在推論排針已支援。

## 本機證據位置

```text
output/images/2026.08/bananapi-sunxi-a33-m2magic-trixie-current-cli/
```

此目錄包含 `CANDIDATES.tsv`、`COMPLETION_STATUS.json`、`UBOOT_PAYLOAD_EVIDENCE.tsv`、`VERIFICATION.tsv`、`VERIFICATION_STATUS.json`、IMG／XZ／SHA-256、中繼資料與完整建置日誌。大型映像與日誌不加入 Git。
