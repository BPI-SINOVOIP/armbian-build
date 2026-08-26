# Banana Pi P2 Pro 候選映像 L2 建置證據

日期：2026-08-26

## 結論

`bananapip2pro` 已由乾淨來源完整建置 Debian Trixie current minimal CLI，並通過 Rockchip 專用 L2 唯讀守門。此結果證明候選映像的來源、壓縮同一性、板級 U-Boot、固定 RKBin 啟動依賴、RK3308 DTB、核心功能、儲存介面、overlay 與板級工具符合本次受控政策。

此結果不代表實體板已完成 UART、SD 冷啟動、eMMC、SDIO Wi-Fi、Bluetooth、Ethernet、USB、音訊或 40-pin 驗證；取得實機證據前維持 L2。

## 可重現基線

| 項目 | 值 |
| --- | --- |
| 映像來源提交 | `e646f754214ef23df6e4e9cab729de265b75d587` |
| 映像來源樹 | `bffd7041599ed70df24b7fb8ccd2554e156060cc` |
| 驗證器提交 | `e646f754214ef23df6e4e9cab729de265b75d587` |
| 建置與驗證設定 SHA-256 | `f5da3bb8c1ed6059336ee3da7e31ea4d92e78faebbf6e02e907440c3cd042c4c` |
| Linux | `6.18.46`，來源提交 `1f99e9ab748fc5c32120de9c4eca31abfe54a4d5` |
| U-Boot | `v2025.04`，來源提交 `34820924edbc4ec7803eb89d9852f4b870fa760a` |
| RKBin | 固定提交 `46c4793ea2dcea7c8331fce9f07b5c80561a0395` |
| Armbian firmware 套件識別 | `1-SAf50a-B96c8-R448a` |
| 發行版與設定 | `RELEASE=trixie BRANCH=current BUILD_DESKTOP=no BUILD_MINIMAL=yes` |
| 重新建置政策 | `ARTIFACT_IGNORE_CACHE=yes CLEAN_LEVEL=make-kernel,make-uboot` |
| 完整建置時間 | 29 分 58 秒 |
| U-Boot 建置時間 | 12 秒 |
| 核心建置時間 | 1171 秒 |

建置使用 OverlayFS 隔離快取，既有快取只作唯讀下層。U-Boot、核心與 rootfs 均重新產生，不是由既有映像改名或只替換 DTB。

## RKBin 啟動依賴

| 檔案 | SHA-256 | 用途 |
| --- | --- | --- |
| `rk33/rk3308_ddr_589MHz_uart2_m1_v1.30.bin` | `6a7e4b63bed0c131a760b4e63ad0e8ecc44f9a6315d0b761ff611af45b061250` | binman 實際使用的 DDR TPL |
| `rk33/rk3308_bl31_v2.26.elf` | `ae2241f1387f03abc4d7ec6423af126e56029e73183dfd984e5d5ce55d9950f7` | binman 實際使用的 BL31 |
| `rk33/rk3308_miniloader_sd_nand_v1.13.bin` | `ceaa5d81a652cd71e93ae3e74371744129ed4ed41fab365151b4884317456603` | 固定追蹤的救援／替代啟動依賴，本映像的 binman 流程未宣稱使用 |

三個檔案均由固定 RKBin 提交重新取證；建置日誌明確記錄 `ROCKCHIP_TPL` 與 `BL31` 的實際輸入路徑。

## 映像雜湊

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| IMG | 1803550720 | `03d84bc8d3b7f8e680b560dadf2c862a014432c0befd79ba54a93fe5e9030ebf` |
| XZ | 376069748 | `d697662f3e556b9f854e9ed76464cc69c029ee3619b765cb821ba10bd5513f30` |

XZ 已通過 `xz -t`，串流解壓 SHA-256 與 IMG 完全相同。

## L2 守門範圍

1. 檢查候選矩陣、中繼資料、來源提交、來源樹、建置參數及驗證設定雜湊一致。
2. 驗證固定 RKBin 提交、三個啟動依賴雜湊與建置後證據清單一致。
3. 以唯讀 loop 與 `mount -o ro,noload` 檢查映像，不執行映像內程式。
4. 比對映像 32768 bytes 偏移與 U-Boot 套件內 `u-boot-rockchip.bin` 的實際位元組。
5. 檢查核心、initrd、`overlay_prefix=rk3308` 與 `rk3308-bpi-p2-pro.dtb` 的 model、compatible 及檔名身分。
6. 檢查 SD 控制器為 4-bit、eMMC 控制器為 8-bit、SDIO 控制器為 4-bit。
7. 檢查 Ethernet、三個 MMC、UART 與三個 USB 控制器節點處於啟用狀態。
8. 檢查 OTG host 與三個 UART overlay 已封裝於 `rockchip/overlay`。
9. 檢查 Rockchip MMC、Ethernet、I2C、SPI、PWM、Crypto、USB host、DWC2、USB gadget mass-storage、Wi-Fi 與 Bluetooth 核心設定。
10. 檢查 `rfkill`、`bluetooth`、`bluez`、`bluez-tools`、`gpiod`、`i2c-tools`、`python3-libgpiod`、`python3-spidev` 與 `v4l-utils` 已安裝。

overlay 存在只證明映像提供可選功能；腳位衝突的 overlay 不應同時啟用，仍須依實際接線選擇。

## L3 實機門檻

- 執行至少 30 次完整斷電冷啟，保存 UART 全程日誌及失敗率。
- 分別驗證 SD 與 eMMC 開機、安裝及交互啟動順序。
- 驗證 Ethernet、SDIO Wi-Fi、Bluetooth 與斷線重連。
- 驗證 USB host、OTG gadget mass-storage、GPIO、I2C、SPI、UART 與 PWM 實體迴路。
- 驗證音訊輸入輸出、長時間負載、溫度、重啟與斷電檔案系統一致性。
- 分別驗證每個欲發布的 overlay；不可由檔案存在推論外接裝置已通過。

## 本機證據位置

```text
output/images/2026.08/bananapi-rockchip-rk3308-trixie-current-cli/
```

此目錄包含 `CANDIDATES.tsv`、`COMPLETION_STATUS.json`、`RKBIN_EVIDENCE.tsv`、`RKBIN_STATUS.json`、`VERIFICATION.tsv`、`VERIFICATION_STATUS.json`、IMG／XZ／SHA-256、中繼資料與完整建置日誌。大型映像與日誌不加入 Git。
