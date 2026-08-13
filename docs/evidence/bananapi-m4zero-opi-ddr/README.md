# BPI-M4 Zero DDR 移植證據索引

本目錄記錄可追溯來源與證據邊界。公開來源用永久網址；本機未提交的原始
證據以絕對路徑與後續產生的雜湊清單追蹤。

## 1. Git 基線

| 項目 | 值 |
| --- | --- |
| Armbian 倉庫 | `git@github.com:BPI-SINOVOIP/armbian-build.git` |
| 基線提交 | `052955507` |
| 工作分支 | `bpi-m4zero-opi-ddr-port-20260813` |
| U-Boot 版本 | `v2026.01` |
| TF-A 版本 | `lts-v2.12.9` |
| TF-A 平台 | `sun50i_h616` |
| U-Boot upstream 提交 | `127a42c7257a6ffbbd1575ed1cbaa8f5408a44b3` |

## 2. 公開主要來源

| 證據 | 網址 | 支持的事實 |
| --- | --- | --- |
| U-Boot H616 DDR 原始碼 | `https://github.com/u-boot/u-boot/blob/master/arch/arm/mach-sunxi/dram_sun50i_h616.c` | PHY 尚有未完全理解的暫定值；主線訓練流程 |
| Orange Pi Zero 3 defconfig | `https://github.com/u-boot/u-boot/blob/master/configs/orangepi_zero3_defconfig` | 792 MHz 與板級 DDR profile |
| H616 LPDDR4 支援討論 | `https://lists.u-boot-project.org/pipermail/u-boot/2023-October/534453.html` | 支援依 `RS1G32LO4D2BDS-53BT` 與 boot0 timing 開發 |
| Orange Pi Zero 3 支援討論 | `https://lists.u-boot-project.org/pipermail/u-boot/2023-November/538764.html` | 原廠 SDK 792 MHz 與完整 DRAM 參數 |
| Orange Pi 記憶體測試 | `https://lists.u-boot-project.org/pipermail/u-boot/2023-November/538916.html` | 初期容量誤判、792 MHz 與 memtester 結果 |
| Allwinner U-Boot 文件 | `https://docs.u-boot.org/en/latest/board/allwinner/sunxi.html` | SPL、TF-A、U-Boot 的主線啟動方式 |
| Allwinner TF-A 文件 | `https://trustedfirmware-a.readthedocs.io/en/latest/plat/allwinner.html` | BL31 與 PSCI 平台責任 |
| sunxi 韌體分析工具 | `https://github.com/apritzel/sunxi-fw` | boot0／TOC 與 DDR 參數提取 |

## 3. 本機主要證據

### 原始硬體日誌與表格

```text
/media/pi/SMCI/bpi/m4z/BPI Armbian镜像测试记录
/media/pi/SMCI/bpi/m4z/频率672镜像测试
/media/pi/SMCI/bpi/m4z/u0-universal-rank-480mhz和v0-vendor-eye-792mhz测试log
/media/pi/SMCI/bpi/m4z/Armbian-unofficial_26.05.0-trunk_Bananapim4zero_noble_current_6.18.32_u0-safe-480mhz_xfce_desktop.img测试情况
/media/pi/SMCI/bpi/m4z/Armbian-unofficial_26.05.0-trunk_Bananapim4zero_noble_current_6.18.32_v2-static-rayson-792mhz_xfce_desktop.img测试情况
```

### 原廠 Android 與 boot0 分析

```text
/media/pi/SMCI/bpi/m4z/2026-04-08-bananapi-m4zero-android12/2026-04-08-bananapi-m4zero-android12.img
/media/pi/SMCI/bpi/m4z/2026-04-08-bananapi-m4zero-android12/analysis-20260730
/media/pi/SMCI/bpi/m4z/m4z_android-2g-boot.log
```

### 既有分析文件

```text
/media/pi/SMCI/armbian/bpi-v26.2.1/docs/bananapi-m4zero-ddr-stability-plan-20260724.md
/media/pi/SMCI/armbian/bpi-v26.2.1/docs/bananapi-m4zero-u0-v2-noble-xfce-comparison-20260801.md
/media/pi/SMCI/armbian/bpi-v26.2.1/docs/bananapi-m4zero-v2-static-rayson-792-hardware-results-20260731.md
/media/pi/SMCI/armbian/bpi-v26.2.1/docs/bananapi-m4zero-h618-boot0-reverse-engineering-20260729.md
```

這些文件在原工作樹可能尚未提交，只能視為本機證據。新分支的結論必須在
工作日誌重新摘要，不能假設遠端使用者可直接取得原檔。

## 4. 顆粒與 geometry

| 料號 | 已知用途 | 邏輯 geometry | 證據狀態 |
| --- | --- | --- | --- |
| `RS512M32LO4D1BDS-53BT` | M4 Zero 2 GiB | x32、1 Rank、16 Rows、10 Columns | BOM 與實機日誌 |
| `RS1G32LO4D2BDS-53BT` | M4 Zero／Orange Pi Zero 3 4 GiB | x32、2 Ranks、16 Rows、10 Columns | BOM、upstream 郵件與實機日誌 |
| `RS1G32LX4D4BNR-53BT` | M4 Zero 4 GiB | x32、2 Ranks、16 Rows、10 Columns | 實機照片與 Android 日誌 |

邏輯 geometry 相同不代表 die 組織、PCB margin 或最佳 PHY profile 相同。

## 5. 必須保留的反證

1. Orange Pi profile 在 M4 Zero 792 MHz 只取得 V2 `5/8`，不是完整通過。
2. U0 480 MHz 同 payload `8/8` 通過，支持頻率／margin 差異。
3. 原廠 boot0 在 `450600826` 曾有 DST `8/10`，不能宣稱原廠 792 MHz
   對所有弱板絕對穩定。
4. V13/V14 的混合鏈問題發生在 DDR 初始化之後，不能用來否定或證明 O0
   的主線 SPL DDR 穩定性。
5. 任何新結果若沒有 UART build ID、完整冷開機邊界與映像回讀雜湊，不能
   納入正式統計。

## 6. 後續產物索引

每個 O0 至 O5 產物完成後，在本目錄新增一份繁體中文清單，至少包含：

- 實驗代號與唯一變因。
- Git 提交與 patch SHA-256。
- 完整建置命令。
- SPL、U-Boot、TF-A、映像 SHA-256。
- UART 日誌 SHA-256。
- 板號、DDR 料號、冷／暖啟動次數與結果。
- 可外推與不可外推的範圍。

## 7. O0 原始碼入口

```text
patch/u-boot/v2026.01/board_bananapim4zero/013-bananapi-m4zero-use-orangepi-zero3-ddr-baseline.patch
tools/build-bpi-m4zero-opi-ddr-o0.sh
```

O0 明確移除原有 150 us 容量探測延遲。若硬體結果需要該延遲，必須建立
O0b，不得直接改寫 O0 的證據。

## 8. O0 建置證據

```text
docs/evidence/bananapi-m4zero-opi-ddr/O0-build-20260813.md
```

O0 U-Boot 已完成建置與離線一致性驗證。實機驗證仍未執行，因此不得把
建置通過記為 DDR 穩定性通過。

## 9. O1 診斷設計證據

```text
docs/evidence/bananapi-m4zero-opi-ddr/O1-diagnostics-design-20260813.md
```

此文件包含 `M4ZDDR1` 格式、暫存器白名單、解析方法、原廠 boot0 的
30 次結果重算、動態選點證據與 RTC 欄位映射。O1 尚未實機驗證。
