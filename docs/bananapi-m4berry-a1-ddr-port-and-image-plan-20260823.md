# BPI-M4 Berry A1 DDR 移植與全映像計畫

## 目標

將 BPI-M4 Zero A1 在 792 MHz 收斂的開源 DDR PHY 候選參數移植到
BPI-M4 Berry 的 U-Boot `v2025.04`，產生可供 2 GiB 與 4 GiB 實板共同
驗證的 current 全映像矩陣。

這是候選參數移植，不把 BPI-M4 Zero 的測試結果宣稱為 BPI-M4 Berry
硬體已通過。M4 Berry 必須重新完成容量辨識、冷啟動、Linux 記憶體壓力、
eMMC 與周邊測試。

## 變更邊界

| 項目 | 作法 |
| --- | --- |
| 板級目標 | `bananapim4berry` |
| U-Boot | 保留 `v2025.04` 與 `bananapi_m4_berry_defconfig` |
| DDR 類型 | 保留 LPDDR4 |
| DDR 頻率 | 保留 792 MHz |
| 容量 | 不硬編碼，由 H616/H618 SPL geometry 探測 2 GiB／4 GiB |
| 移植欄位 | `CA_DRI`、`TPR6`、`TPR11`、`TPR12` |
| 不移植內容 | M4 Zero DTS、板名、eMMC 設定、診斷命令、掃描器與 LAB SPL |
| Kernel／根檔案系統 | 沿用既有 M4 Berry current 6.18.32 映像內容 |

候選值如下：

```text
CA_DRI=0x0d0d
TPR6=0x3a808080
TPR11=0x25252523
TPR12=0x110f0f10
DRAM_CLK=792
```

## 證據來源與限制

- M4 Zero A1 的 0845 板在 SPL 記憶體測試中完成 64 MiB M2 20/20，並有
  Linux `memtester 3000M` 三輪通過紀錄。
- M4 Zero A1 的多板 UART 證據可證明候選參數曾在同系列 H618 設計上完成
  geometry、Kernel、使用者空間與關機流程。
- 不同 PCB 走線、DDR 顆粒、電源與 U-Boot 版本會改變訓練邊界，因此上述
  證據只能支持「值得移植驗證」，不能支持 M4 Berry 量產放行。
- 2 GiB 與 4 GiB 必須各有實板證據；SPL 顯示正確容量不等於全容量穩定。

## 建置矩陣

每個發行版各產生 CLI 與 XFCE 桌面映像，全部使用 current 6.18.32：

| 發行版 | CLI | XFCE |
| --- | --- | --- |
| Bookworm | 是 | 是 |
| Jammy | 是 | 是 |
| Noble | 是 | 是 |
| Resolute | 是 | 是 |
| Trixie | 是 | 是 |

總計十個 `.img`、十個 `.img.xz`、逐映像中繼資料、矩陣清單與 SHA-256。
只替換映像第一分割區前的 M4 Berry bootloader，並以雜湊確認 bootloader
範圍外資料與鎖定來源映像一致。

## 執行與守門

1. 靜態測試確認只改四項 DDR 欄位，板名、U-Boot 系列、792 MHz 與容量
   自動探測均未改變。
2. 強制從本分支重編 M4 Berry U-Boot，不接受舊快取。
3. 驗證套件內 defconfig、bootloader Build ID、二進位雜湊與映像寫入偏移。
4. 先建立並完整驗證一個 Noble CLI 基準映像。
5. 基準通過後展開十種映像，保留未壓縮及壓縮檔。
6. 每個映像執行 `xz -t`、解壓雜湊、分割區起點、內嵌 bootloader 與
   bootloader 範圍外不變檢查。
7. 產生測試紀錄模板，交由外部測試者按板號與 DDR 容量回填。

## 實機驗證 Gate

2 GiB 與 4 GiB 每一種硬體至少完成：

- 五十次完全斷電冷啟動，逐次保存 UART 與 Build ID。
- SPL 容量必須分別穩定顯示 2048 MiB 與 4096 MiB。
- `memtester` 使用至少可用記憶體的 80%，連續三輪無錯誤。
- `stress-ng` CPU、VM 與 I/O 組合測試至少八小時。
- SD 啟動、eMMC 安裝與 eMMC 啟動各十次。
- HDMI、GPU、四個 USB 2.0、Wi-Fi、Bluetooth、GPIO 與正常關機測試。
- `dmesg` 不得有記憶體、MMC、檔案系統或未處理例外錯誤。

完成前狀態一律標示為 `M4B_A1_候選_待實機驗證`，不得標示為量產通過。

## 回復方式

若任一容量發生冷啟動或記憶體錯誤，改回未套用本候選補丁的 M4 Berry
bootloader 即可；Kernel 與根檔案系統未被此移植改動。失敗證據必須保留
板號、DDR 完整料號、PCB 版本、映像 SHA-256、UART 與測試命令。
