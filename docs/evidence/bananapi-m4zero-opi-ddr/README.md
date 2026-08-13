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
| `K4F6E3S4HM-MGCJ` | M4 Zero 三星樣本，預期 2 GiB | x32、1 Rank；Rows／Columns 待實機確認 | 兩片實機照片與 Renesas 相容清單 |

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

## 10. O1 正式建置證據

```text
docs/evidence/bananapi-m4zero-opi-ddr/O1-build-20260813.md
```

正式通過產物由提交 `238e3e244` 建置，包含 hashed DEB、未封裝 SPL、
固定 40 KiB SPL、完整 U-Boot、TF-A、設定、日誌及雜湊。實機仍未驗證。

## 11. O1 可燒錄映像證據

```text
docs/evidence/bananapi-m4zero-opi-ddr/O1-test-image-20260813.md
```

此映像以已知可用的 U0 Jammy payload 為底，只在 8 KiB 偏移替換正式 O1
bootloader。封裝與獨立複驗皆通過，但實機仍未驗證。

## 12. O1 實機測試入口

```text
docs/bananapi-m4zero-o1-hardware-test-guide-20260813.md
```

手冊固定映像雜湊、燒錄回讀、UART 邊界、板號欄位與弱板驗收矩陣，避免
不同操作者使用不同映像或把單次開機誤記為穩定通過。

## 13. O1 第一次硬體證據與 O3 同板對照

```text
docs/evidence/bananapi-m4zero-opi-ddr/O1-hardware-1116-20260813.md
docs/evidence/bananapi-m4zero-opi-ddr/O3-1116-parameter-comparison-20260813.tsv
docs/evidence/bananapi-m4zero-opi-ddr/hardware/O1-1116-20260813
```

`1116` 的單次 O1 診斷完成 4 GiB geometry、TF-A、U-Boot 與 kernel
handoff，但核心回報 initramfs 解包錯誤，未進入使用者空間。原始 UART 與
解析 JSON 已納入 Git；本結果不算 792 MHz 穩定通過，也不能納入 O5 統計。

## 14. M4ZLAB2 單一 SPL 實驗器

```text
docs/evidence/bananapi-m4zero-opi-ddr/M4ZLAB2-build-20260813.md
docs/bananapi-m4zero-ddr-lab-guide-20260813.md
```

實驗器在同一份 SPL 內接受全部 DDR 候選參數，提供 watchdog 復原、M0／M1／
M2、JSONL 續跑與三類候選排名。三次完整建置的 SPL、ELF 與組合二進位逐位元
一致，其中一次直接由已推送的程式提交建置；0438 實機參數矩陣結果見第 16 節。

## 15. M4ZLAB2 SD 卡寫入證據

```text
docs/evidence/bananapi-m4zero-opi-ddr/M4ZLAB2-sd-write-20260813.md
```

正式 SPL 已寫入本機辨識為 `/dev/mmcblk0` 的 59.5 GiB SD 卡 8 KiB 偏移；
來源與回讀逐位元一致，原區段已備份；後續已在 0438 讀取 UART 並執行矩陣。

## 16. M4ZLAB2 在 0438 板的參數掃描

```text
docs/evidence/bananapi-m4zero-opi-ddr/M4ZLAB2-hardware-0438-20260813.md
docs/evidence/bananapi-m4zero-opi-ddr/hardware/M4ZLAB2-0438-20260813
tools/bpi-m4zero-ddr-lab-profile-0438-candidate-792.json
```

0438 已完成 531 組執行期實驗。480 MHz 保險設定與 792 MHz 單板中心候選
各完成 `M2 10/10`；壓縮後的乾淨 JSONL 與 UART 已納入 Git。這些都是同次
上電後的熱重設結果，第二片板、冷開機及 Linux 壓力測試仍待執行。

## 17. M4ZLAB2 在 1116 板的跨板驗證

```text
docs/evidence/bananapi-m4zero-opi-ddr/M4ZLAB2-hardware-1116-20260813.md
docs/evidence/bananapi-m4zero-opi-ddr/hardware/M4ZLAB2-1116-20260813
tools/bpi-m4zero-ddr-lab-profile-cross-board-candidate-792.json
```

1116 的 480 與 792 MHz 候選各完成 `M2 10/10`。與 0438 的結果合併後，
792 MHz 跨板候選為 `20/20`；`TPR6` 共同已觀察零失敗區間為
`0x2e..0x44`。以上仍是熱重設實驗，冷開機與 Linux 壓力 gate 尚未完成。

## 18. X2 標準建置與可燒錄映像

```text
docs/evidence/bananapi-m4zero-opi-ddr/X2-cross-board-792-build-image-20260813.md
docs/bananapi-m4zero-cross-batch-x2-hardware-plan-20260813.md
```

X2 已完成可重現的標準 U-Boot、TF-A、套件與完整 Jammy 映像建置。套件、
工作樹及映像內 bootloader 逐位元一致，且一般 SPL 載入功能已恢復；
`M4ZLAB2` 已停用。本節記錄文件建立當時的狀態；當時四片 M4 Zero 的跨批次
冷啟動與 Linux 壓力仍待執行，後續結果已記錄於第 20 至 24 節。
兩片 BPI-M4B 採獨立板級工作流，不直接使用 X2 映像。

## 19. 三星 DDR 樣本照片與料號盤點

```text
docs/evidence/bananapi-m4zero-opi-ddr/M4Z-Samsung-DDR-inventory-20260813.md
docs/evidence/bananapi-m4zero-opi-ddr/hardware/M4Z-Samsung-20260813/IMG_3687.jpg
docs/evidence/bananapi-m4zero-opi-ddr/hardware/M4Z-Samsung-20260813/IMG_3686.jpg
```

兩片新找到的 M4 Zero 均確認採用三星 `K4F6E3S4HM-MGCJ`，頂部追溯碼分別
為 `SEC 337` 與 `SEC 322`。外部相容清單顯示該料號為 16 Gb、x32、1 Rank
LPDDR4，單顆容量相當於 2 GiB。本節記錄盤點當時的狀態；當時實機
geometry、冷開機與 Linux 壓力仍待完成，不納入 X2 已通過統計。後續結果
已記錄於第 21、22 與 24 節。

## 20. X2 在 1116 的標準啟動 G1

```text
docs/evidence/bananapi-m4zero-opi-ddr/X2-hardware-1116-G1-20260813.md
docs/evidence/bananapi-m4zero-opi-ddr/hardware/X2-1116-20260813/uart-1116-x2-cold-01.log.gz
```

`1116` 已用固定 X2 映像完成一次完全斷電冷啟動。792 MHz、4 GiB 雙 Rank
geometry、TF-A、U-Boot、initramfs、rootfs 與使用者空間全部通過，systemd
為 `running` 且失敗服務為零。此結果通過 G1 並計入 G2 `1/10`，尚未通過
冷啟動統計、記憶體測試及並行壓力 Gate。

## 21. X2 在三星 S337 的標準啟動 G1

```text
docs/evidence/bananapi-m4zero-opi-ddr/X2-hardware-S337-G1-20260813.md
docs/evidence/bananapi-m4zero-opi-ddr/hardware/X2-S337-20260813/uart-s337-x2-cold-01.log.gz
```

`S337` 正確偵測三星 2 GiB、x32、1 Rank geometry，並完成 X2 792 MHz
完整啟動。systemd 為 `running`，失敗服務為零；180 秒、1.4 GiB 記憶體與
4 CPU 冒煙測試結束碼為 `0`。SDIO 與 Bluetooth 另有初始化錯誤，因此本次
只通過 DDR 標準啟動 G1，不宣稱無線周邊正常，也不把短壓力算成完整 G3。

## 22. X2 在三星 S322 的標準啟動 G1

```text
docs/evidence/bananapi-m4zero-opi-ddr/X2-hardware-S322-G1-20260813.md
docs/evidence/bananapi-m4zero-opi-ddr/hardware/X2-S322-20260813/uart-s322-x2-cold-01.log.gz
```

`S322` 同樣正確偵測三星 2 GiB、x32、1 Rank geometry，並完成 X2 792 MHz
完整啟動與 180 秒冒煙測試。rootfs 顯示為 `/dev/mmcblk2p1`，經名稱、容量、
CID、控制器及 UUID 核對後確認仍是同一張 `SR64G` SD 卡，不是 eMMC。
SDIO／Bluetooth 初始化失敗使系統沒有網路介面，`vnstat.service` 因此失敗、
systemd 為 `degraded`；DDR G1 通過，但周邊功能仍未通過。

## 23. X2 在 0438 的標準啟動 G1

```text
docs/evidence/bananapi-m4zero-opi-ddr/X2-hardware-0438-G1-20260813.md
docs/evidence/bananapi-m4zero-opi-ddr/hardware/X2-0438-20260813/uart-0438-x2-cold-01.log.gz
```

`0438` 正確偵測 Rayson 4 GiB、x32、2 Rank geometry，並完成 X2 792 MHz
完整啟動。systemd 為 `running`、失敗服務為零，3.0 GiB 記憶體與 4 CPU、
180 秒冒煙測試結束碼為 `0`，沒有核心或資料錯誤。

## 24. X2 四板 G1 摘要

```text
docs/evidence/bananapi-m4zero-opi-ddr/X2-four-board-G1-summary-20260813.md
```

固定 X2 映像已在 0438、1116、S337、S322 四片實物完成首次完全斷電冷啟動，
涵蓋 4 GiB 雙 Rank Rayson 與 2 GiB 單 Rank Samsung。四片 G1 均通過，但
每片 G2 仍只有 `1/10`，G3、G4、G5 未通過；三星無線周邊問題另列處理。

## 25. X2 完整作業系統映像矩陣

```text
docs/bananapi-m4zero-x2-792-image-matrix-delivery-20260814.md
docs/evidence/bananapi-m4zero-opi-ddr/X2-mass-validation-record-template.tsv
```

五個發行版的 CLI 與 XFCE 共十套 IMG/XZ 已完成。每套都鎖定四板 G1 使用的
`P1f88` bootloader，並通過全檔雜湊、XZ 串流、分割表與內嵌 bootloader
回讀。這些映像供大量硬體驗證，不代表已完成量產認證。
