# Banana Pi M1+ current 候選映像 L2 建置證據

日期：2026-08-26

## 結論

`bananapim1plus` 已由乾淨來源完整建置 Debian Trixie current minimal CLI，並通過 A20 專用 L2 唯讀守門。首輪驗證發現 A20 的 I2S0 與 SPI0 overlay 原始碼及 README 雖存在，但 6.18／7.0 Makefile 均漏列；提交 `1703de3391a349ae902ea410fb9e207bfd22466e` 修正共用封裝清單後，第二輪完整建置確認兩項 overlay 已進入成品。

此結果不代表實體板已完成 UART、SD 開機、DRAM、SATA、網路、Wi-Fi、顯示、音訊、40-pin 或 USB 角色驗證；取得實機證據前維持 L2。

## 可重現基線

| 項目 | 值 |
| --- | --- |
| 映像來源提交 | `1703de3391a349ae902ea410fb9e207bfd22466e` |
| 映像來源樹 | `6350e50656320fa722bbdcdf18c9a47a835251e9` |
| 驗證器提交 | `1703de3391a349ae902ea410fb9e207bfd22466e` |
| 建置與驗證設定 SHA-256 | `900bbef6da8966bc960778fdea068193437f25e77186daf8de66e6d8ffc86e75` |
| Linux | `6.18.46`，來源提交 `1f99e9ab748fc5c32120de9c4eca31abfe54a4d5` |
| U-Boot | `2024.01`，來源提交 `866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e` |
| 發行版與設定 | `RELEASE=trixie BRANCH=current BUILD_DESKTOP=no BUILD_MINIMAL=yes` |
| 重新建置政策 | `ARTIFACT_IGNORE_CACHE=yes CLEAN_LEVEL=make-kernel,make-uboot` |
| 完整建置時間 | 21 分 30 秒；Docker 執行 1298 秒 |
| 核心建置時間 | 792 秒 |

建置使用 OverlayFS 隔離快取，既有快取只作唯讀下層。U-Boot、核心與 rootfs 均重新產生，不是由既有映像改名、只換 DTB 或只替換 bootloader。

## 啟動載荷證據

| 載荷 | 位置 | 偏移 | 大小 | SHA-256 |
| --- | --- | ---: | ---: | --- |
| `u-boot-sunxi-with-spl.bin` | 映像 | 8192 | 570660 | `93e7b9d341eff98297b3bad5039dc358261cec146b3f142f375be7fdd8fd3aa5` |

載荷證據清單 SHA-256 為 `42d2f5b4e6d088e6ffc3bffd5b9275f325e9a06f7ae31fd71821d6a18c1c48c5`。守門同時比對 U-Boot 套件 MD5、套件 payload 與映像 8 KiB 偏移的實際位元組。

## 映像雜湊

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| IMG | 1430257664 | `579b68e4bbe93be5b4f0d3e7321200433cc6adb4304bf590a5a3d08313b81265` |
| XZ | 341380776 | `74a54d1698c17c08c3f20d4ac99bc8223351175acf2a2afc6417297620da1d7e` |

XZ 已通過完整性檢查，串流解壓 SHA-256 與 IMG 完全相同。

## L2 守門範圍

1. 檢查候選矩陣、中繼資料、來源提交、來源樹、建置參數及驗證設定雜湊一致。
2. 驗證固定 Linux 與 U-Boot 提交，以及 `u-boot-sunxi-with-spl.bin` 的套件與映像位元組一致。
3. 以唯讀 loop 與 `mount -o ro,noload` 檢查映像，不執行映像內程式。
4. 檢查核心、initrd、`armbianEnv.txt` 與 `allwinner/sun7i-a20-bananapi-m1-plus.dtb` 的身分。
5. 檢查 SD、SDIO Wi-Fi、SATA、Gigabit Ethernet、HDMI、音訊、IR、USB OTG、USB host、UART 與相關 PHY 節點。
6. 檢查 Lima、Cedrus、A20 crypto、CAN、PWM、SPI、I2C、音訊與 USB gadget mass-storage 核心設定。
7. 檢查 `can`、`i2c2`、`i2c3`、`i2s0`、`pwm`、`spdif-out`、`spi0`、`spi-add-cs1`、`spi-spidev`、`uart2`、`uart3`、`uart7` 共 12 個 overlay 已封裝。
8. 檢查 BCM43362 韌體與 NVRAM 雜湊，以及 `rfkill`、`iw`、`ethtool`、`usbutils`、`alsa-utils`、`gpiod`、`i2c-tools`、`python3-libgpiod`、`python3-spidev`、`v4l-utils` 已安裝。

核心設定、模組、DT 節點、韌體或工具存在只證明軟體已封裝，不等同相關硬體路徑可用。Bluetooth 沒有足夠的 DT 與接線證據，本候選不宣稱 Bluetooth 支援。

## L3 實機門檻

- 執行至少 30 次完整斷電冷啟，保存 UART 全程日誌及失敗率。
- 驗證不同記憶體與 SD 批次在 432 MHz 上游 DRAM 設定下的冷啟動及長時間壓力。
- 驗證 SD 開機、SATA 持續讀寫、Gigabit Ethernet、Wi-Fi 與斷線重連。
- 驗證 HDMI、類比音訊、I2S、SPDIF、IR、USB host 與 USB gadget 的實際資料路徑。
- 驗證 GPIO、I2C、SPI、UART、PWM、CAN 與欲發布介面的實體迴路、腳位衝突及電壓域。

## 本機證據位置

```text
output/images/2026.08/bananapi-sunxi-a20-m1plus-trixie-current-cli/
```

此目錄包含 `CANDIDATES.tsv`、`COMPLETION_STATUS.json`、`UBOOT_PAYLOAD_EVIDENCE.tsv`、`VERIFICATION.tsv`、`VERIFICATION_STATUS.json`、IMG／XZ／SHA-256、中繼資料與完整建置日誌。大型映像與日誌不加入 Git。
