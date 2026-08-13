# BPI-M4 Zero 參考 Orange Pi Zero 3 的 DDR 移植計畫

日期：2026-08-13  
狀態：執行中  
分支：`bpi-m4zero-opi-ddr-port-20260813`  
工作目錄：`/media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-opi-ddr`

## 1. 目標

以 upstream U-Boot 的 Orange Pi Zero 3 H618 LPDDR4 實作為乾淨基線，為
BPI-M4 Zero 建立可重現、可稽核且能涵蓋 Rayson 2 GiB／4 GiB 顆粒的開源
DDR 初始化流程。最終目標是使用：

```text
H618 BROM
  -> upstream U-Boot SPL 與開源 DDR 初始化
  -> upstream TF-A BL31
  -> upstream U-Boot proper
  -> Armbian Linux
```

客戶要求的最終 DDR 時脈是 792 MHz，但任何 792 MHz 候選版本必須先通過
弱板硬體矩陣，不能只以單片開機成功或 SPL 顯示容量正確作為完成證據。

## 2. 已確認事實

1. Orange Pi Zero 3 4 GiB 使用 Rayson `RS1G32LO4D2BDS-53BT`，與
   BPI-M4 Zero 的 D2 4 GiB BOM 相同。
2. upstream U-Boot 的 H616 LPDDR4 timing 最初即依該顆粒及原廠 `boot0`
   行為加入。
3. Orange Pi Zero 3 upstream defconfig 固定 `CONFIG_DRAM_CLK=792`，並使用：

```text
DX_ODT = 0x07070707
DX_DRI = 0x0e0e0e0e
CA_DRI = 0x00000e0e
ODT_EN = 0xaaaaeeee
TPR6   = 0x44000000
TPR10  = 0x402f6663
TPR11  = 0x24242624
TPR12  = 0x0f0f100f
```

4. BPI-M4 Zero V2 已使用上述 Orange Pi PHY profile 與 792 MHz。相同
   Noble XFCE payload 的配對矩陣結果為 U0 480 MHz `8/8` 到登入、V2
   792 MHz `5/8` 通過及 `3/8` 失敗。
5. V2 失敗包含 initrd `Bad Data CRC`、資料損壞、核心 Oops 及 panic，
   顯示部分 M4 Zero 板在 792 MHz 的資料眼圖裕量不足或訓練不完整。
6. 原廠 `boot0 V0.651` 會探測 geometry、重試訓練、更新最終參數、使用
   RTC 調校資料並執行 DST；目前 upstream SPL 未完整重現此流程。
7. 弱板 `450600826` 使用原廠 boot0 也曾只有 `8/10` 通過 DST，因此
   792 MHz 可能同時受到軟體訓練與實體板級裕量限制。
8. vendor boot0 完成 792 MHz DDR 初始化後出現的暖重啟 RCU stall，是
   vendor BL31／OP-TEE／PSCI 邊界的另一個問題，不得與 SPL DDR 訓練混為
   同一根因。

## 3. 不採用的作法

1. 不繼續在同一補丁堆疊中同時修改 DDR、TF-A、U-Boot、核心重啟與
   watchdog 行為。
2. 不以檔名、編譯成功、容量輸出或一次登入宣告穩定。
3. 不把 Orange Pi 的固定 TPR 視為所有 M4 Zero PCB 與批次的通用答案。
4. 不把破解後的閉源 boot0 永久視為無後遺症的產品方案。
5. 不在三片最弱板通過前建立完整作業系統映像矩陣。
6. 不讓實驗版補丁覆蓋 U0 480 MHz 已知保守版本與原廠 Android 映像。

## 4. 分支與證據隔離

原工作樹 `/media/pi/SMCI/armbian/bpi-v26.2.1` 有大量未提交的既有修改與
實驗檔案。為避免覆蓋使用者內容，本計畫使用獨立 Git worktree：

```text
/media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-opi-ddr
```

新分支從提交 `052955507` 建立。原工作樹只作唯讀證據來源；所有新程式、
文件、建置工具與提交都在新 worktree 完成。

## 5. 實驗代號

| 代號 | 用途 | 唯一主要變因 | 發布資格 |
| --- | --- | --- | --- |
| O0 | Orange Pi 乾淨控制組 | M4 DTS／周邊加 Orange Pi DDR profile；不加容量探測延遲 | 無，僅基線 |
| O0b | 容量延遲控制組 | O0 只增加 150 us 容量探測延遲 | 無，僅診斷 |
| O1 | 訓練診斷版 | O0 加入唯讀訓練與 PHY 暫存器輸出 | 無，僅診斷 |
| O2 | geometry 控制組 | O1 加入已知 geometry 與 Rank 選擇策略 | 無，僅診斷 |
| O3 | 原廠狀態對照組 | 比對 boot0 與 O1/O2 最終 PHY 狀態 | 無，僅診斷 |
| O4 | 動態訓練候選 | 只移植已由 O3 證明缺少的訓練步驟 | 候選 |
| O5 | 792 MHz 驗收候選 | O4 固定功能後執行完整弱板矩陣 | 通過後才可評估 |

同一代號不得同時變更頻率、ODT、Vref、lane delay、geometry 與記憶體
測試演算法。需要多個變因時必須拆成新的子版本並在工作日誌說明。

## 6. 執行階段

### 階段 A：證據凍結與可交接文件

交付項目：

- 本計畫書。
- 持續追加的工作日誌。
- 公開來源、本機原始證據與產物雜湊索引。
- 原工作樹狀態與新 worktree 起點紀錄。

完成條件：其他 Codex 只讀取這三份文件，就能知道目前假設、已驗證事實、
禁止外推事項、下一個命令與待測硬體。

### 階段 B：O0 乾淨基線

以 upstream U-Boot `v2026.01` 的 `orangepi_zero3_defconfig` 為 DDR 參數
基準，只保留 BPI-M4 Zero 必須的 DTS、eMMC 與周邊差異。

必做項目：

1. 產生可審查的 Orange Pi 與 M4 Zero defconfig 差異。
2. 將 DDR profile 差異限制在獨立補丁。
3. 不帶入先前 V0/V1 自製 eye scan，也不帶入額外 150 us 容量探測延遲。
4. 只有 O0 實機出現容量偵測問題時，才建立 O0b 單變因控制組。
5. 編譯 O0 U-Boot，保存 `.config`、SPL、U-Boot、TF-A、雜湊與建置日誌。
6. 回讀映像中的 U-Boot，確認與建置產物一致。

### 階段 C：O1 唯讀診斷

在不改變控制器決策的前提下輸出：

- 輸入時脈、geometry、bus width 與 Rank。
- write leveling、read calibration、read training、write training 的每次結果。
- 各 data lane 的關鍵狀態與錯誤碼。
- 初始化前後的 DDR controller／PHY 白名單暫存器。
- 最終 TPR、Mode Register、容量及快速資料完整性測試結果。

診斷輸出必須有固定版本與欄位名稱，讓 UART 日誌可由腳本解析；不能用
無邊界的完整 MMIO 掃描影響時序。

### 階段 D：O2 geometry 與 Rank

已知 BOM：

| 顆粒 | 容量 | 匯流排 | Rank | Rows | Columns |
| --- | ---: | ---: | ---: | ---: | ---: |
| `RS512M32LO4D1BDS-53BT` | 2 GiB | x32 | 1 | 16 | 10 |
| `RS1G32LO4D2BDS-53BT` | 4 GiB | x32 | 2 | 16 | 10 |
| `RS1G32LX4D4BNR-53BT` | 4 GiB | x32 | 2 | 16 | 10 |

先驗證 upstream 自動偵測，不預設自製 Rank fallback 一定正確。若自動
偵測不穩，才以 O1 輸出及原廠最終 `PARA1/PARA2` 設計單獨的 O2 補丁。

### 階段 E：O3 原廠 boot0 對照

在同一片板、同一次完全斷電條件下取得：

1. 原廠 boot0 最終 DDR controller／PHY 暫存器。
2. O1/O2 最終 DDR controller／PHY 暫存器。
3. 原廠與開源流程的 Rank、Vref、lane delay、training error、RTC 狀態。
4. 兩邊相同與不同暫存器的機器可讀差異。

只移植可由原始證據與硬體結果支持的行為。反組譯成果的目標是改善 GPL
U-Boot SPL，不是長期散布修改後的閉源 boot0。

### 階段 F：O4 動態訓練

移植優先順序：

1. 原廠訓練失敗重試與控制器完整重置順序。
2. 正確的 Rank／geometry 探測。
3. read／write calibration 與 lane 結果判定。
4. Vref 與 lane delay 搜尋。
5. RTC 調校資料格式與有效性檢查；未理解前不得直接沿用。
6. 訓練後的破壞性快速記憶體測試與失敗處理。

### 階段 G：O5 硬體驗收

第一道 gate 使用三片已知弱板：

```text
450600146
450600826
450601075
```

每片最低要求：

- 30 次完全斷電冷開機。
- 30 次系統暖重啟。
- 每次容量、Rank、時脈與 training 結果正確。
- U-Boot 載入 kernel、DTB、initrd 的 checksum 全部正確。
- Linux 進入登入後執行 `memtester` 與 `stress-ng`。
- SD、eMMC、USB、網路、HDMI 及溫度檢查沒有新增錯誤。
- UART 不得出現 SPL 重訓無限循環、EL3 exception、資料 CRC、Oops、panic
  或 RCU stall。

三片弱板全數通過後，再擴大到 D1、D2、D4 每種 BOM 與完整作業系統矩陣。

## 7. 頻率與參數搜尋順序

需要頻率階梯時固定使用：

```text
480 -> 528 -> 600 -> 672 -> 720 -> 744 -> 768 -> 792 MHz
```

每個階梯先保持 ODT、Vref、lane delay 與 geometry 不變。若在某頻率失敗，
先由 O1 診斷確認失敗階段，再依序只調整一類：

1. ODT／drive strength。
2. CA／DQ Vref。
3. read lane delay。
4. write lane delay。
5. Mode Register 與控制器時序。

不得用「降低頻率加改 TPR」的組合結果判定其中任何單一變因有效。

## 8. 產物與命名

每個實驗產物目錄至少包含：

```text
manifest.tsv
build-command.txt
git-state.txt
u-boot.config
u-boot-sunxi-with-spl.bin
sunxi-spl.bin
bl31.bin
sha256sums.txt
build.log
patches.sha256
```

映像檔名必須包含實驗代號、頻率與 Git 短提交碼，不得只使用 `latest`。

## 9. 提交策略

每個階段單獨提交並推送：

1. 計畫書、工作日誌與證據索引。
2. O0 乾淨基線。
3. O1 診斷工具。
4. O2 geometry／Rank；只有需要時才提交。
5. O3 對照工具與資料格式。
6. O4 訓練演算法的每個獨立步驟。
7. O5 實機結果與是否取得發布資格。

提交訊息、文件與人工可讀輸出使用繁體中文。每次提交前執行本機格式、
補丁套用、編譯及離線雜湊檢查；不以 GitHub Actions 取代本機證據。

## 10. 停止條件

遇到以下情況立即停止擴大建置，只保留診斷產物：

- 三片弱板任一片出現資料損壞。
- 同一參數在冷開機結果不確定。
- UART build ID 與待測映像不符。
- 原廠或開源啟動鏈元件被錯誤標示。
- 不能由雜湊證明 SD 卡中的 bootloader 與建置產物一致。
- 需要同時修改兩個以上尚未隔離的變因才能繼續。

## 11. 續作入口

後續 Codex 開始工作前依序執行：

```bash
cd /media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-opi-ddr
git status --short --branch
git log -5 --oneline --decorate
sed -n '1,260p' docs/bananapi-m4zero-opi-zero3-ddr-port-plan-20260813.md
sed -n '1,260p' docs/bananapi-m4zero-opi-zero3-ddr-worklog-20260813.md
sed -n '1,260p' docs/evidence/bananapi-m4zero-opi-ddr/README.md
```

先確認工作日誌的「目前狀態」與「下一步」，再執行新命令；不得重新建立
另一套未記錄的實驗編號。
