# Banana Pi M5 Pro edge 候選映像 L2 建置證據

日期：2026-08-26

## 結論

`bananapim5pro` 已由乾淨來源完整建置 Debian Trixie edge minimal CLI，並通過 Rockchip RK3576 專用 L2 唯讀守門。此結果證明候選映像的來源、IMG／XZ 同一性、兩項 U-Boot 載荷、六項 RKBin 輸入、M5 Pro DTB、核心功能與標準開發工具符合本次受控政策。

此結果不代表實體板已完成 UART、SD／eMMC 開機、DDR、網路、無線、顯示、PCIe、40-pin 或硬體加速驗證；取得實機證據前維持 L2。

## 可重現基線

| 項目 | 值 |
| --- | --- |
| 映像來源提交 | `41b9685feae5153511a7de7a0e1ff0639f0a8100` |
| 映像來源樹 | `fbe834c5cf78659a8d22735c2aca7da4ff82e4a3` |
| 驗證器提交 | `41b9685feae5153511a7de7a0e1ff0639f0a8100` |
| 建置與驗證設定 SHA-256 | `52afbee66c79ff89235c572b83bfc7d739d6234a13ce73ed77347661788a3cdf` |
| Linux | `7.0.14`，來源提交 `458c6079fc1d41d564c37679c8ace02cd83ee817` |
| U-Boot | `2017.09`，Radxa 來源提交 `39cd993e5d6296635438e84f4576b3a9bf76f86e` |
| RKBin | 來源提交 `1d3c61008fa823936ae7a59615393f8294b64456` |
| 發行版與設定 | `RELEASE=trixie BRANCH=edge BUILD_DESKTOP=no BUILD_MINIMAL=yes` |
| 重新建置政策 | `ARTIFACT_IGNORE_CACHE=yes CLEAN_LEVEL=make-kernel,make-uboot` |
| 完整建置時間 | 28 分 48 秒；Docker 執行 1734 秒 |
| 核心建置時間 | 1239 秒 |

建置使用 OverlayFS 隔離快取，既有快取只作唯讀下層。U-Boot、核心與 rootfs 均重新產生，不是由既有映像改名、只換 DTB 或只替換 bootloader。

## RKBin 輸入證據

| 輸入 | SHA-256 |
| --- | --- |
| `rk35/RK3576MINIALL.ini` | `5f33eb26bfcb098096d0fea5261b56fe523f3666354b4f4c8c29134f9caf89d0` |
| `rk35/rk3576_bl31_v1.20.elf` | `34f875f6cc4fd2a633e08dcbe9f4f031ddf95a1bf6d5eaeb7621ac750d997cac` |
| `rk35/rk3576_boost_v1.02.bin` | `c07aa38c1b816d53e1c2635577356a75627f29667dd9e1133d44dd4b00fd17f6` |
| `rk35/rk3576_ddr_lp4_2112MHz_lp5_2736MHz_v1.08.bin` | `7403cf1663d4b0c9d7c692014e0852e60f0d3977209ec75fa755c293f54e2d50` |
| `rk35/rk3576_usbplug_v1.03.bin` | `1ccac7349056bd68f72d93b2ad717e82ce1ec67b3f8c69fd3f10e60be58c7be2` |
| `tools/boot_merger` | `30c5ae87038b77117fa27fb4c39d697e835492386f96f9553f57f343cee9f4dc` |

RKBin 證據清單 SHA-256 為 `4168db8d0441fa7264c6190ebf1c91fa3f1a8dcd2cfc52a9d1dc3f90954210dc`。這些封閉二進位檔的存在與雜湊可證明建置輸入一致，但不等同其原始碼、授權或實機行為已通過審核。

## 啟動載荷證據

| 載荷 | 位置 | 偏移 | 大小 | SHA-256 |
| --- | --- | ---: | ---: | --- |
| `idbloader.img` | 映像 | 32768 | 319488 | `85033bbc1e6b23f3f8cca1ce443fe9c4feab3e4c5eaba30686ccdc26e1930118` |
| `u-boot.itb` | 映像 | 8388608 | 1452544 | `6d72a19ab12e9c2bc13c4234fea1fd0bc9965240690039abf299d4c861b61618` |

兩項載荷證據清單 SHA-256 為 `a7752651d3501d82947beb323c8c715fa567067bb9f7804bc0ed1fc413225c8c`。

## 映像雜湊

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| IMG | 1803550720 | `66d61bf5bd7d470edcff76d0e1c1285ecf1cd0f584670d9c13ccd662c5cab949` |
| XZ | 381613256 | `f8ba9784db7adfbf593278ab45afaace066c82ea2eb0078e5d8fcf976885d4d5` |

XZ 已通過完整性檢查，串流解壓 SHA-256 與 IMG 完全相同。

## L2 守門範圍

1. 檢查候選矩陣、中繼資料、來源提交、來源樹、建置參數及驗證設定雜湊一致。
2. 驗證固定 Linux、U-Boot 與 RKBin 提交，並逐項比對六項 RKBin 輸入雜湊。
3. 以唯讀 loop 與 `mount -o ro,noload` 檢查映像，不執行映像內程式。
4. 比對 `idbloader.img` 與 `u-boot.itb` 的映像偏移、長度、雜湊及 U-Boot 套件實際位元組。
5. 檢查核心、initrd、開機設定及 `rockchip/rk3576-bananapi-m5-pro.dtb` 的 model、compatible 及檔名身分。
6. 檢查 ADC、雙 Ethernet、GPU、HDMI、SD、SDIO、eMMC、PCIe、UART、USB OTG、USB host 與 VOP 節點為 `okay`，並檢查 MMC 寬度及 USB 模式。
7. 檢查 NVMe、PCIe、Panfrost／Panthor、RGA、Hantro、Rockchip VDEC、HDMI、音訊、Wi-Fi、Bluetooth 與 USB gadget mass-storage 核心能力。
8. 檢查 `gpiod`、`i2c-tools`、`python3-libgpiod`、`python3-spidev`、`v4l-utils`、`pciutils`、`nvme-cli`、`usbutils`、`alsa-utils`、`iw` 與 `ethtool` 已安裝。

核心設定、模組、DT 節點或工具存在只證明軟體已封裝，不等同相關硬體路徑可用。

## L3 實機門檻

- 執行至少 30 次完整斷電冷啟，保存 UART 全程日誌及失敗率。
- 分別驗證 SD 與 eMMC 開機、兩者同時存在時的選擇順序及救援燒錄路徑。
- 驗證不同 LPDDR4／LPDDR5 與 eMMC 配置的冷啟動、重啟、長時間壓力及溫度邊界。
- 驗證雙 Ethernet、Wi-Fi、Bluetooth、USB host、USB gadget 與斷線重連。
- 驗證 HDMI、GPU、視訊編解碼、音訊、PCIe 與 NVMe 的實際資料路徑。
- 驗證 GPIO、I2C、SPI、UART、PWM 與欲發布介面的實體迴路、腳位衝突及電壓域。

## 本機證據位置

```text
output/images/2026.08/bananapi-rockchip-rk3576-m5pro-trixie-edge-cli/
```

此目錄包含 `CANDIDATES.tsv`、`COMPLETION_STATUS.json`、`RKBIN_EVIDENCE.tsv`、`RKBIN_STATUS.json`、`UBOOT_PAYLOAD_EVIDENCE.tsv`、`VERIFICATION.tsv`、`VERIFICATION_STATUS.json`、IMG／XZ／SHA-256、中繼資料與完整建置日誌。大型映像與日誌不加入 Git。
