# BPI-M4 Zero DDR 移植工作日誌

本文件只追加已執行操作、取得的證據、失敗與決策。計畫與驗收規格見
`docs/bananapi-m4zero-opi-zero3-ddr-port-plan-20260813.md`。

## 目前狀態

| 欄位 | 內容 |
| --- | --- |
| 日期 | 2026-08-13 |
| 階段 | B：O0 乾淨基線 |
| 分支 | `bpi-m4zero-opi-ddr-port-20260813` |
| worktree | `/media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-opi-ddr` |
| 起點 | `052955507` |
| 最近完成 | 文件基線以 `89645a409` 提交並推送 |
| 下一步 | 建置 O0 U-Boot，保存產物、設定、日誌與雜湊 |
| 硬體需求 | O0/O1 編譯完成後才需要三片弱板 UART 驗證 |

## 2026-08-13：架構重新評估

### 使用者問題

確認能否參考 Orange Pi Zero 3 的開源 H618 啟動鏈，避免繼續在原廠
bootloader 與主線核心之間累積不可控的相容性修補。

### 公開資料結論

1. Orange Pi Zero 3 有 4 GiB LPDDR4 版本。
2. upstream U-Boot LPDDR4 支援明確記錄開發顆粒為
   `RS1G32LO4D2BDS-53BT`。
3. Orange Pi Zero 3 upstream 設定使用 792 MHz。
4. upstream H618 的成熟路線是 SPL、TF-A、U-Boot、Linux 全開源；沒有找到
   成熟的 H618「只保留 vendor boot0、任意替換所有後段」參考專案。
5. Orange Pi 支援初期也曾出現容量誤判，經實板測試、記憶體屏障與時序
   修正後才進入 upstream。

### 本機證據結論

1. BPI-M4 Zero V2 已相當於 Orange Pi static profile 的 792 MHz 控制組，
   不是尚未嘗試直接套用。
2. 同 payload 比較結果：U0 480 MHz `8/8` 到登入；V2 792 MHz `5/8`
   通過、`3/8` 失敗。
3. 失敗表現是資料 CRC、initramfs 損壞、Oops 與 panic，支持 DDR margin
   不足或訓練不完整的判讀。
4. 原廠 boot0 會更新最終 `PARA1/PARA2/TPR13`、使用 RTC 調校資訊並執行
   DST；既有 clean-room eye scan 未正確重現該行為。
5. vendor boot0 在弱板 `450600826` 也曾出現 DST `8/10`，所以 792 MHz
   不能只假設是軟體問題。

### 啟動鏈稽核更正

V14 實際映像使用：

```text
BROM
  -> vendor boot0 V0.651
  -> vendor BL31
  -> vendor OP-TEE
  -> vendor 預編譯 U-Boot 2018.07
  -> Linux 6.18.32
```

V14 並未使用文件原先標示的來源版 U-Boot V6。此錯誤證明後續所有映像都
必須以映像回讀及雜湊確認元件，不可只依建置腳本參數或檔名判斷。

### 決策 D001：停止擴大混合鏈修補

原因：vendor BL31／OP-TEE 掌控 PSCI、CPU 電源、GIC 與暖重啟；boot0
只負責早期 DDR 與載入。V13 在 DDR 測試與 rootfs 掛載成功後發生 RCU
stall，不能再以修改 boot0 或核心參數混合處理。

### 決策 D002：Orange Pi 為乾淨基線，不是直接答案

原因：兩板使用相同 SoC 及 D2 顆粒，足以證明技術路線可行；V2 硬體矩陣
則證明 Orange Pi 固定參數沒有覆蓋全部 M4 Zero 弱板。

### 決策 D003：反組譯用於移植，不永久保留閉源 boot0

原廠 boot0 反組譯與暫存器對照的產出必須轉化為 upstream U-Boot SPL 的
可審查程式與測試；不能把修改後的閉源映像當成最終維護方案。

## 2026-08-13：工作樹隔離

### 原工作樹檢查

執行：

```bash
cd /media/pi/SMCI/armbian/bpi-v26.2.1
git status --short --branch
git branch --show-current
git remote -v
```

結果摘要：

- 分支為 `bpi-v26.8.0-trunk`，追蹤 `origin/bpi-v26.8.0-trunk`。
- HEAD 為 `052955507`。
- 原工作樹有大量 M4 Zero、M5、Jinsonic 及其他未提交內容。
- 不得切換原工作樹分支、清除或覆蓋這些內容。

### 建立獨立 worktree

執行：

```bash
git worktree add \
  -b bpi-m4zero-opi-ddr-port-20260813 \
  /media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-opi-ddr \
  052955507
```

結果：成功建立乾淨工作樹，初始 `git status` 無修改。

### 決策 D004：原工作樹只讀

後續不複製整個未提交補丁堆疊。只把經重新審查、具有單一目的及證據支持
的內容，以新補丁加入隔離分支。

## 2026-08-13：乾淨分支初始補丁盤點

乾淨分支現有 `board_bananapim4zero` 補丁：

```text
001-Add-board-BananaPi-BPI-M4-Zero.patch
002-Add-board-KickPi-K2B.patch
010-HACK-sunxi-h616-gpu-enable.patch
011-sunxi-h616-ths-workaround.patch
012-mach-sunxi-dram_helpers-add-delay-to-steady-dram-detection.patch
```

初始 M4 Zero defconfig 為 792 MHz，但 TPR6／11／12 並非目前 upstream
Orange Pi Zero 3 profile。O0 必須把這個差異放進獨立補丁，不能直接重寫
`001`，以保留來源與審查歷史。

`012` 在 `mctl_mem_matches_base()` 增加 150 us delay，會影響容量探測，
因此必須列為已存在的 M4 差異；O0 建置清單與日誌要明確標示它，後續另設
無 delay 控制組才能判斷是否仍有必要。

## 2026-08-13：階段 A 提交與推送

執行：

```bash
git commit -m '文件：建立 M4 Zero DDR 移植計畫與工作紀錄'
git push -u origin bpi-m4zero-opi-ddr-port-20260813
```

結果：

- 提交：`89645a409`。
- 推送：成功。
- 遠端分支：`origin/bpi-m4zero-opi-ddr-port-20260813`。
- 此提交只有計畫書、工作日誌與證據索引，沒有程式變更。

## 2026-08-13：O0 變因重新收斂

### 發現

乾淨分支原有 `012` 會在 upstream `mctl_mem_matches_base()` 的 `dsb()` 後
額外等待 150 us。upstream U-Boot `v2026.01` 與 Orange Pi Zero 3 不包含
此延遲；若 O0 同時修改 TPR 並保留延遲，就不是精確的 Orange Pi DDR
控制組。

### 決策 D005：O0 移除額外延遲

O0 移除 `012`，只用獨立 `013` 補丁把 BPI-M4 Zero 的 TPR6／11／12
對齊 Orange Pi Zero 3。若實機出現容量誤判，另建立 O0b，只加回 150 us
延遲，不同時修改其他參數。

### 建置來源警告

原工作樹的 U-Boot cache 已有多個板子的未提交補丁，狀態為 dirty，不能
直接當成 O0 產物來源。O0 必須由隔離 worktree 的 Armbian artifact 流程
重新準備來源、套用補丁及建置，並由套件回讀確認結果。

## 2026-08-13：O0 靜態實作與補丁驗證

### 程式變更

1. 移除會增加 150 us 容量探測延遲的 `012`。
2. 新增 `013-bananapi-m4zero-use-orangepi-zero3-ddr-baseline.patch`。
3. `013` 只修改 TPR6、TPR11、TPR12，其他 DDR profile 與 792 MHz 不變。
4. 新增 `tools/build-bpi-m4zero-opi-ddr-o0.sh`，負責建置、回讀套件、驗證
   `.config`、比對原始碼產物並產生 manifest 與 SHA-256。

### 第一次補丁驗證失敗

原先嘗試從既有 U-Boot cache 建立額外 Git worktree，Git 回報共用 bare
metadata 沒有寫入權限：

```text
fatal: could not create directory ... Permission denied
```

這次操作沒有產生驗證結果，也沒有修改原 U-Boot 工作樹。

### 替代驗證

改由 upstream 提交 `127a42c7257a6ffbbd1575ed1cbaa8f5408a44b3` 執行
`git archive`，在一次性目錄依名稱順序套用完整 M4 Zero 補丁堆疊。

結果：

- `001`、`002`、`010`、`011`、`013` 全部成功套用。
- 最終 `CONFIG_DRAM_CLK=792`。
- TPR6／10／11／12 為
  `0x44000000/0x402f6663/0x24242624/0x0f0f100f`。
- 原始碼沒有 `udelay(150)`。
- `bash -n`、`shellcheck` 與 `git diff --check` 通過。
- 尚未執行編譯與實機驗證。

## 日誌追加規則

每次實質操作後追加：

1. 時間與階段。
2. 完整命令或可重現腳本路徑。
3. Git 提交、來源版本與輸入雜湊。
4. exit code 與關鍵輸出。
5. 產物路徑及 SHA-256。
6. 成功、失敗、尚未驗證的明確分類。
7. 由結果產生的下一個單變因決策。

未插板、未讀 UART 或未完成壓力測試時，一律記為「尚未實機驗證」。
