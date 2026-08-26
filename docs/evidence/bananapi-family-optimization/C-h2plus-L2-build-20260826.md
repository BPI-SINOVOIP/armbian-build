# Banana Pi H2+ 雙板候選映像 L2 建置證據

日期：2026-08-26

## 結論

`bananapim2zero` 與 `bananapip2zero` 已分別由乾淨來源完整建置 Debian Trixie current minimal CLI，並通過 L2 唯讀守門。此結果證明兩張候選映像的來源、壓縮同一性、板級 U-Boot、H2+ DTB、核心功能、儲存匯流排、overlay 與板級工具符合本次受控政策。

此結果不代表實體板已完成 UART、SD 冷啟動、P2 Zero eMMC、Wi-Fi、Bluetooth、Ethernet、HDMI、GPU、VPU、USB、音訊或 40-pin 驗證；取得實機證據前維持 L2。

## 可重現基線

| 項目 | 值 |
| --- | --- |
| 映像來源提交 | `b58df4f8f2eaf3954c52d09c1dd6c6cdd73d1fba` |
| 映像來源樹 | `e9d83cc7b7c81106c080695f3e8b86a574ac0bbd` |
| 驗證器提交 | `b58df4f8f2eaf3954c52d09c1dd6c6cdd73d1fba` |
| 建置與驗證設定 SHA-256 | `5c831c750c088b63e57dfa13a446561a4ec61d15460ef9679da16a3e4511003f` |
| Linux | `6.18.46`，來源提交 `1f99e9ab748fc5c32120de9c4eca31abfe54a4d5` |
| U-Boot | `v2024.01`，來源提交 `866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e` |
| Armbian firmware 套件識別 | `1-SAf50a-B96c8-R448a` |
| 發行版與設定 | `RELEASE=trixie BRANCH=current BUILD_DESKTOP=no BUILD_MINIMAL=yes` |
| 重新建置政策 | `ARTIFACT_IGNORE_CACHE=yes CLEAN_LEVEL=make-kernel,make-uboot` |
| M2 Zero 完整建置時間 | 21 分 56 秒；核心 727 秒 |
| P2 Zero 完整建置時間 | 20 分 01 秒；核心 733 秒 |

建置使用 OverlayFS 隔離快取，既有快取只作唯讀下層。兩張板的 U-Boot、核心與 rootfs 均分別重新產生，不是由同一映像改名或只替換 DTB。

## 映像雜湊

| 板卡 | 產物 | 大小 | SHA-256 |
| --- | --- | ---: | --- |
| `bananapim2zero` | IMG | 1438646272 | `04ece3a6bd2ee6c3b423da2502d965eb3d5a9fbd1135210560e3ac16f5ab2089` |
| `bananapim2zero` | XZ | 344096404 | `34b2d9dde8d455d55838190b9494a809a4a7fba879c693c9b2b3c5b8ebc2bcd4` |
| `bananapip2zero` | IMG | 1438646272 | `6d3a70052909c74a838076675ae310776c94c645926bd14aace9cd5b36026753` |
| `bananapip2zero` | XZ | 343131744 | `696eacc86e14297ce00b0086f9ceda72f9ebca90c49e23290f9f440a9ca69c65` |

兩張 XZ 均通過 `xz -t`，串流解壓 SHA-256 與對應 IMG 完全相同。

## L2 守門範圍

1. 檢查候選矩陣、中繼資料、來源提交、來源樹、建置參數與政策雜湊一致。
2. 以唯讀 loop 與 `mount -o ro,noload` 檢查映像，不執行映像內程式。
3. 比對映像 8 KiB 偏移與各板 U-Boot 套件內 `u-boot-sunxi-with-spl.bin` 的實際位元組。
4. 檢查核心、initrd、`overlay_prefix=sun8i-h3` 與各板 DTB 的 model、compatible 及檔名身分。
5. 檢查兩板 SD 控制器為 4-bit，板載 Wi-Fi SDIO 控制器為 4-bit；另確認 P2 Zero eMMC 控制器為 8-bit。
6. 依板卡檢查顯示引擎、HDMI、OTG、USB host、SDIO、P2 Zero Ethernet 與 eMMC 節點處於啟用狀態。
7. 檢查 H2+/H3 的 I2C、SPI、UART、PWM、USB、音訊與 1-Wire overlay 已封裝。
8. 檢查 Lima GPU、Sun4i DRM、Cedrus VPU、Crypto、Wi-Fi、Bluetooth、MMC、I2C、SPI、PWM 及 USB gadget mass-storage 核心設定。
9. 檢查 `rfkill`、`bluetooth`、`bluez`、`bluez-tools`、`gpiod`、`i2c-tools`、`python3-libgpiod`、`python3-spidev` 與 `v4l-utils` 已安裝。

overlay 存在只證明映像提供可選功能；腳位衝突的 overlay 不應同時啟用，仍須依實際接線選擇。

## L3 實機門檻

- 每個板型至少執行 30 次完整斷電冷啟，保存 UART 全程日誌及失敗率。
- 驗證 SD 與板載 Wi-Fi；P2 Zero 另外驗證 eMMC、Ethernet 及 SD/eMMC 交互啟動順序。
- 驗證 HDMI 多解析度、Lima OpenGL ES、Cedrus V4L2 解碼、音訊輸入輸出與 CPU 使用率。
- 驗證 Bluetooth、USB host、OTG gadget mass-storage、GPIO、I2C、SPI、UART、PWM 與 1-Wire 實體迴路。
- 分別驗證每個欲發布的 overlay；不可由檔案存在推論外接裝置已通過。

## 本機證據位置

```text
output/images/2026.08/bananapi-sunxi-h2plus-trixie-current-cli/
```

此目錄包含 `CANDIDATES.tsv`、`COMPLETION_STATUS.json`、`VERIFICATION.tsv`、`VERIFICATION_STATUS.json`、IMG／XZ／SHA-256、中繼資料與完整建置日誌。大型映像與日誌不加入 Git。
