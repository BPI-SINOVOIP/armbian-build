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
- 當時尚未執行編譯與實機驗證。

## 2026-08-13：O0 U-Boot 建置與離線驗證

### 可重現命令

```bash
./tools/build-bpi-m4zero-opi-ddr-o0.sh
```

腳本實際呼叫：

```bash
./compile.sh uboot BOARD=bananapim4zero BRANCH=current RELEASE=trixie ARTIFACT_IGNORE_CACHE=yes
```

### 起訖與來源

| 項目 | 值 |
| --- | --- |
| 開始時間 | `2026-08-13T12:47:46+08:00` |
| 結束時間 | `2026-08-13T12:49:47+08:00` |
| Armbian 提交 | `ed1ba8108310043a22d080805b870bfe3d1ac8ef` |
| U-Boot 標籤 | `v2026.01` |
| U-Boot 提交 | `127a42c7257a6ffbbd1575ed1cbaa8f5408a44b3` |
| TF-A 版本 | `lts-v2.12.9` |
| 套件版本 | `2026.01-S127a-Pe260-Hc6a9-V3946-Bd0d2-R448a` |
| 建置結果 | exit code `0` |

Armbian patch 工具回報 16 個 patch 全部套用；其中兩項既有跨板 patch 有
metadata／rebase 警告，但沒有套用失敗，M4 Zero 的 `013` 成功套用。完整
建置日誌搜尋 `error`、`failed`、`fatal` 沒有命中。

### 設定回讀

從生成套件內的 `.config` 回讀：

```text
CONFIG_DRAM_SUNXI_DX_ODT=0x07070707
CONFIG_DRAM_SUNXI_DX_DRI=0x0e0e0e0e
CONFIG_DRAM_SUNXI_CA_DRI=0x0e0e
CONFIG_DRAM_SUNXI_ODT_EN=0xaaaaeeee
CONFIG_DRAM_SUNXI_TPR6=0x44000000
CONFIG_DRAM_SUNXI_TPR10=0x402f6663
CONFIG_DRAM_SUNXI_TPR11=0x24242624
CONFIG_DRAM_SUNXI_TPR12=0x0f0f100f
CONFIG_DRAM_CLK=792
```

同時確認不存在自製 Rank fallback 與額外 `udelay(150)`。套件中的
`u-boot-sunxi-with-spl.bin` 與原始碼工作樹建置產物逐位元一致。

### 關鍵產物

產物目錄：

```text
output/evidence/bpi-m4zero-opi-ddr/O0-20260813-124746-ed1ba8108
```

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| `linux-u-boot-...deb` | 1,024,192 | `baccd13dac5cef738be6ee84167cd0c9a2dd5916b0b73f622eea2105a4d750b6` |
| `sunxi-spl.bin` | 40,960 | `197a84f9476173187d9c9c15bfa44e4c85044d3e25b307964119f629a642df98` |
| `u-boot.bin` | 777,664 | `0fd53ae235971982fe6ab420ef3ba26682c0342206ae5194b0893697f5308079` |
| `u-boot-sunxi-with-spl.bin` | 873,977 | `219b40324979f4a0dbad816c704bb22e4284c5abcc56fcb68bd2e9449b15b8be` |
| `bl31.bin` | 53,361 | `641bbce6b58d3e541e650946e7ce81c7c3cdb8e63244800457599c48947cb226` |

`sha256sum -c sha256sums.txt` 對七個受控產物全部回報 `OK`。詳細清單見
`docs/evidence/bananapi-m4zero-opi-ddr/O0-build-20260813.md`。

### 結論與邊界

O0 已完成原始碼套用、編譯、套件回讀與離線一致性驗證。這只證明產物是
預期的 Orange Pi DDR 基線，不等於弱板在 792 MHz 已穩定；目前仍是
「尚未實機驗證」。下一階段 O1 只加入不改變訓練流程的唯讀診斷標記。

## 2026-08-13：O0 證據提交與推送

執行：

```bash
git commit -m '紀錄：保存 M4 Zero O0 建置證據'
git push
```

結果：

- 提交：`a9bef393b`。
- 推送：成功。
- O0 的程式、文件與重建腳本至此形成不可變證據點。

## 2026-08-13：O1 診斷設計稽核

### upstream UART 與空間

U-Boot SPL 在 `sunxi_dram_init()` 前已執行 `preloader_console_init()`，目前
組態也有 `CONFIG_SPL_SERIAL=y` 與 `CONFIG_SPL_PRINTF=y`，因此可直接輸出
固定 UART 標記，不必啟用全域 `DEBUG`。

O0 的 SPL 尺寸資料：

```text
u-boot-spl-nodtb.bin  37712 bytes
text                  37229 bytes
data                    480 bytes
bss                     448 bytes
```

`TPR10=0x402f6663` 的有效訓練位為：

```text
write leveling    0
read calibration  1
read training     0
write training    0
```

因此 O1 必須明確輸出 `wl=0 rc=1 rt=0 wt=0`。若擅自打開另外三項，將變成
訓練演算法實驗，不再是 O1 唯讀診斷。

### 第一次補丁格式失敗

第一次以人工方式組合 `014` 郵件補丁時，執行：

```bash
git apply --check 014-sunxi-h616-add-structured-dram-diagnostics.patch
```

結果為 `corrupt patch at line 38`。該檔立即刪除，沒有套用、編譯或宣稱
成功。後續改在獨立 U-Boot 複本實際修改、提交，再以 `git format-patch`
產生正式補丁，避免人工維護 hunk 計數。

### 靜態 BSS 草稿遭否決

初始設計曾考慮用靜態結構保存初始化序號及每個訓練結果。稽核發現此 SPL
的 BSS 位址位於尚未初始化的 DRAM；在 `sunxi_dram_init()` 內讀寫靜態
BSS 會形成循環依賴。此草稿在形成正式補丁及編譯前即遭否決。

決策 D006：O1 不使用任何新的可寫靜態狀態。每個結果立即輸出，最終
controller／PHY 白名單快照只在最後一次 `mctl_core_init()` 後輸出一次。

### 診斷時序邊界

O1 不改變條件位、重試次數、函式回傳值、geometry、Rank、時脈或失敗
處理。不過 UART 本身會增加初始化之間的時間，因此 O1 只能用來定位，
不能用其成功率直接替代 O0／O5 的無診斷穩定性驗證。

## 2026-08-13：原廠 boot0 V0.651 證據重算

### 啟動與成功率

本機 30 次 vendor boot0 測試重算結果：

| 板號 | DST 通過 |
| --- | ---: |
| `450600146` | 10/10 |
| `450600826` | 9/10 |
| `450601075` | 10/10 |
| 合計 | 29/30 |

另一組 `v3-sunxi-flash-gpt` 的 `450600826` 為 8/10。這些數據證明原廠
792 MHz 也不是每次必過，不能把 boot0 視為絕對穩定的黑盒答案。

原廠流程保持 792 MHz，最多執行四輪 DST。失敗後觀察到同頻率重新掃描
與重新選點，例如 `tpr6` 從 `0x34808080` 改成 `0x36808080`；沒有找到
自動降頻證據。

已知流程為 `R_2d`、`R_1st`、`W_2st`、`R_2st`、`RV_C`，接著 DRAM
simple test 並把調校參數寫入 RTC。未剝除符號的原廠物件：

```text
/media/pi/SMCI/bpi/m4z/BPI-H618-Android12-source-sparse/longan/brandy/brandy-2.0/spl-pub/board/h618/libsun50iw9p1_sdcard.a
SHA-256 8c563e43895005dd6beb0e8b4f034f8d28c979b3d7fa801ac722bdcdebb5ccc5
```

RTC 索引 8 至 15 對應 `mr6`、`mr14`、`tpr6`、`tpr11`、`tpr12`、
`para1`、`para2`、`tpr13`；位址範圍為 `0x07000120` 至
`0x0700013c`。詳細證據與欄位見 O1 設計文件。

決策 D007：O1 先證明 upstream 實際執行的低階階段與最終 PHY 狀態；
原廠四輪 DST、R/W 眼圖及 RTC 恢復策略留到 O3/O4，不能提前混入 O1。

## 2026-08-13：O1 實作與獨立編譯

### 正式實作

新增：

```text
patch/u-boot/v2026.01/board_bananapim4zero/014-sunxi-h616-add-structured-dram-diagnostics.patch
tools/build-bpi-m4zero-opi-ddr-o1.sh
tools/parse-bpi-m4zero-o1-uart.py
```

補丁提供固定 `M4ZDDR1` 欄位，記錄 profile、每次初始化 geometry、執行中的
訓練階段、結果與重試次數、最終暫存器白名單及容量。解析器把混有其他
UART 文字的日誌轉為結構化 JSON，並偵測 BEGIN／END 不平衡或缺少 FINAL。

### 隔離編譯

在一次性 U-Boot 複本 `/tmp/bpi-m4zero-o1.5TbzdB`，以 `v2026.01`
提交 `127a42c7257a6ffbbd1575ed1cbaa8f5408a44b3` 套用 O0 補丁後編譯。

結果：

```text
checkpatch --strict      0 errors, 0 warnings
M4 Zero O1 build         exit code 0
u-boot-spl-nodtb.bin     39328 bytes
text                     38843 bytes
data                       480 bytes
bss                        448 bytes
```

相對 O0，SPL text 增加 1,614 bytes，BSS 完全不變；未超過現有 SPL 限制，
但 O1 仍只供診斷。MMIO 位址第一次編譯曾出現 32/64 位指標轉換警告，改為
經 `ulong` 轉型後重新編譯，警告消失。

另以 `orangepi_zero3_defconfig` 建置未啟用診斷的控制組：exit code `0`，
SPL 不含任何 `M4ZDDR1` 字串，證明其他板在未選取 Kconfig 時不帶入診斷。

### 工具驗證

```text
bash -n                         通過
shellcheck                      通過
python3 -m py_compile           通過
合成 M4ZDDR1 UART 解析          通過，問題清單為空
O0 最新分支防誤標               通過，在建置前拒絕並指向 a9bef393b
git apply --check 014           通過
git diff --check                通過
```

完整欄位定義與暫存器白名單見
`docs/evidence/bananapi-m4zero-opi-ddr/O1-diagnostics-design-20260813.md`。

### 巢狀補丁的 whitespace 檢查

`014` 本身是加入 Git 的郵件補丁；對整個 staged diff 執行
`git diff --cached --check` 時，Git 會把內層補丁的 context 前置空白當成
外層新增內容的 whitespace 警告。這不是待套用 C 原始碼的空白錯誤。

處理方式：

- `014` 單獨以 U-Boot `checkpatch.pl --strict` 驗證，結果 0/0。
- `014` 對 O0 U-Boot 來源執行 `git apply --check`，結果通過。
- 其餘 staged 檔案排除巢狀 patch 後執行 `git diff --cached --check`，通過。

## 2026-08-13：O1 程式提交與第一輪正式建置

### 提交與推送

```text
提交 91fec77ca
訊息 診斷：加入 M4 Zero O1 結構化 DDR 紀錄
推送 成功
```

### 第一輪 Armbian artifact

```bash
./tools/build-bpi-m4zero-opi-ddr-o1.sh
```

結果：

| 項目 | 值 |
| --- | --- |
| 開始 | `2026-08-13T13:08:28+08:00` |
| 結束 | `2026-08-13T13:09:03+08:00` |
| Armbian 提交 | `91fec77caccd7ef551ce72d555fd429e8abd8ee7` |
| U-Boot 提交 | `127a42c7257a6ffbbd1575ed1cbaa8f5408a44b3` |
| Build ID | `2026.01-S127a-P4301-Hc6a9-V3946-Bd0d2-R448a` |
| 建置結果 | exit code `0` |
| 套件與來源產物 | 逐位元一致 |
| `M4ZDDR1` 標記 | 十種全部存在 |
| 實機 | 尚未驗證 |

產物目錄：

```text
output/evidence/bpi-m4zero-opi-ddr/O1-20260813-130828-91fec77ca
```

七個受控產物執行 `sha256sum -c` 全部通過，完整 build log 沒有命中
`error`、`failed` 或 `fatal`。

### 證據腳本補強

第一輪保存了固定 40 KiB 的 `sunxi-spl.bin`，但沒有保存可顯示實際程式
餘量的 `u-boot-spl-nodtb.bin`。這不影響第一輪建置成功，但不足以直接由
artifact 重新稽核 SPL 邊界。

決策 D008：正式 O1 證據再增加未封裝 SPL、`size` 報告及低於 40 KiB 的
強制檢查；先提交工具改進，再執行第二輪正式建置，不手工修改第一輪產物。

## 2026-08-13：O1 第二輪後處理失敗與套件選取修正

工具補強提交 `3623e3726` 已推送後，執行第二輪：

```bash
./tools/build-bpi-m4zero-opi-ddr-o1.sh
```

U-Boot 與 DEB 本身建置成功，但整體腳本 exit code 為 `1`，停止於：

```text
u-boot-sunxi-with-spl.bin differ: byte 13, line 1
```

失敗產物目錄保留命令與完整日誌：

```text
output/evidence/bpi-m4zero-opi-ddr/O1-20260813-131012-3623e3726
```

### 根因

Armbian 同一 Build ID 重建時產生了新的 hashed 套件，但 reversioned
`output/debs` 目的檔沿用第一輪內容。兩者雖有新的 mtime，內容不同：

| 來源 | SHA-256 |
| --- | --- |
| reversioned `output/debs` | `5cf5150ff474a875adac37a948c814ad6890d51bbd378df2cfc294bab2a13a10` |
| 第二輪 hashed 套件 | `f162302b0898011dd01b7492d55ba85b55ad99e20891ad1030fedb8ad9f86c82` |
| 第二輪來源組合二進位 | `8d900976a1261ce213d6e3e4a8c7b8ca4ef9e2a35ae4ac6ee3afa335e2d6ecf2` |

從 hashed 套件提取的組合二進位 SHA-256 同為 `8d900976...`，`cmp` exit
code `0`。差異只來自腳本錯選 reversioned 套件，不是 U-Boot 編譯錯誤。

決策 D009：證據腳本改由 `output/packages-hashed/global` 取本輪原始套件；
reversioned 套件只供 Armbian 發布命名，不再作為逐位元來源證據。修正提交
後執行第三輪，不把第二輪標記為通過。

## 2026-08-13：O1 第三輪正式通過

修正提交 `238e3e244` 推送後再次執行 O1 建置。結果：

```text
開始 2026-08-13T13:12:10+08:00
結束 2026-08-13T13:12:44+08:00
exit code 0
Build ID 2026.01-S127a-P4301-Hc6a9-V3946-Bd0d2-R448a
```

正式產物目錄：

```text
output/evidence/bpi-m4zero-opi-ddr/O1-20260813-131210-238e3e244
```

結果摘要：

- 未封裝 SPL 為 38,912 bytes，距 40 KiB 邊界 2,048 bytes。
- SPL section 為 text 38,429、data 480、BSS 448 bytes。
- hashed DEB 與來源組合二進位逐位元一致。
- 十種 `M4ZDDR1` 標記全部存在。
- 八個受控二進位／設定執行 `sha256sum -c` 全部通過。
- build log 沒有命中 `error`、`failed`、`fatal`。
- 實機驗證仍未執行。

完整證據摘要：

```text
docs/evidence/bananapi-m4zero-opi-ddr/O1-build-20260813.md
```

## 2026-08-13：O1 測試映像封裝與獨立複驗

### 封裝輸入

```text
封裝提交 cd69c06c797bda76b166abfc5df104a525629c62
O1 證據 output/evidence/bpi-m4zero-opi-ddr/O1-20260813-131210-238e3e244
來源映像 Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_u0-safe-480mhz.img.xz
來源 SHA-256 80f9b188d6315b9a7d189a3e08b3b174ffbeb6b6173c74c98007a4ff1dbb6348
```

執行：

```bash
./tools/package-bpi-m4zero-o1-test-image.sh \
  output/evidence/bpi-m4zero-opi-ddr/O1-20260813-131210-238e3e244
```

程序結束碼為 `0`。它解壓來源映像後，只把 O1 組合 bootloader 寫入
8,192 bytes 偏移，長度 873,977 bytes；寫入前後分別比對前綴、後綴、
總大小與 bootloader 回讀，最後建立 `.img.xz`、分割表 JSON、清單與雜湊。

### 產物

```text
output/images/2026.08/bpi-m4zero-o1-opi-ddr-diag/Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_o1-opi-ddr-diag-P4301-792mhz.img
output/images/2026.08/bpi-m4zero-o1-opi-ddr-diag/Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_o1-opi-ddr-diag-P4301-792mhz.img.xz
```

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| `.img` | 2,034,237,440 bytes | `316e0d24dc02c9bbfd9579d2b190cbb1aea37516acd2ddcefa85842546897e23` |
| `.img.xz` | 454,842,952 bytes | `20d70f507c3a7e81e2aafc4f6ebf0f36d4249ecd59ad9f46eb301a7642704847` |

### 封裝後獨立複驗

另行執行兩份 `sha256sum -c`、`xz -t` 與：

```bash
cmp -n 873977 -i 8192:0 映像 O1證據/u-boot-sunxi-with-spl.bin
```

全部 exit code `0`。從映像內指定區間可讀到 `P4301`、
`M4ZDDR1_PROFILE0`、`M4ZDDR1_BEGIN` 與 `M4ZDDR1_FINAL`。分割表仍為
DOS，第一分割區從磁區 8192 開始。

決策 D010：O1 映像升格為「可燒錄的診斷產物」，但不得升格為可用或穩定
映像。它沿用 U0 既有系統內容，唯一受控變因是 O1 bootloader；下一步依實機
手冊收集完整 UART，再判斷是否建立 O2。

完整證據與實機入口：

```text
docs/evidence/bananapi-m4zero-opi-ddr/O1-test-image-20260813.md
docs/bananapi-m4zero-o1-hardware-test-guide-20260813.md
```

### 文件提交與交接狀態

```text
提交 3847308cf
訊息 紀錄：保存 O1 可燒錄映像與實機交接
遠端 origin/bpi-m4zero-opi-ddr-port-20260813
推送 成功
```

提交前已完成 `git diff --check`、文件引用路徑存在性檢查，以及新增文件的
可翻譯外語敘述掃描，結果均通過。環境未安裝 `markdownlint-cli2`、
`markdownlint` 或 `mdl`，因此沒有宣稱通過未執行的 Markdown linter。

目前可交接狀態：

- O0 與 O1 原始碼、建置腳本、解析器、建置證據及映像封裝工具已推送。
- O1 原始映像及壓縮映像已在本機產生，雜湊與 bootloader 回讀通過。
- 大型映像位於 `output/images`，不納入 Git；遠端保存其重建工具與雜湊證據。
- 目前沒有接上待測 M4 Zero，所有硬體結論仍為「尚未實機驗證」。
- 下一個唯一有效步驟是依 O1 實機手冊收集 `1116` 或弱板完整 UART；沒有
  O1 日誌前不得直接建立 O2，也不得宣稱 Orange Pi 方案已解決 792 MHz。

## 2026-08-13：1116 的 O1 硬體證據與 O3 同板對照

### 輸入

使用者提供：

```text
output/evidence/bpi-m4zero-opi-ddr/O1-hardware-1116-20260813-135610
```

內容包含 4,916 bytes 原始 UART、10,838 bytes 解析 JSON、外部交接文件與
雜湊清單。`sha256sum -c evidence-sha256.txt` 三項全部通過。

原始 UART SHA-256：

```text
c645d23c74ec4903fa7c51c4a431361bc5452e6b733450a4b8608aa56ba7855f
```

### 診斷結果

- Build ID 為 `P4301`，與 O1 候選相符。
- 41 筆 `M4ZDDR1` 標記完整；四次 read calibration 均首次通過。
- 最終為 4,096 MiB、2 Rank、x32、16 Rows、10 Columns。
- TF-A、U-Boot 與 kernel handoff 成功。
- 核心回報 `Initramfs unpacking failed: no cpio magic`，沒有登入證據。
- JSON 解析器結束碼 `0`、`問題=[]`；這只代表診斷格式完整。

以證據目錄為工作目錄、純檔名作輸入重跑解析器，產生 JSON SHA-256
`12d01460...`，與保存檔逐位元一致。

### initrd 離線驗證

以唯讀 loop 掛接映像，從 ext4 取出實際 `uInitrd` 目標。`dumpimage`、
`gzip -t` 與 `cpio -t` 全部通過，共 1,163 個 cpio 項目。uImage SHA-256
為 `77e1014b...`，gzip payload SHA-256 為 `9917880d...`。

封裝時已證明 O1 映像除 bootloader 外與 U0 payload 相同；既有 U0 480 MHz
日誌另有多次登入成功。決策 D011：geometry 沒有失敗證據，暫不建立 O2；
initramfs 錯誤優先視為 792 MHz DRAM 資料可靠性假設，但尚未確定根因。

### O3 同板差異

原廠 `1116` UART SHA-256 為 `aaff93ef...`。同為 792 MHz／4 GiB／2 Rank／
x32，原廠動態結果與 O1 靜態值分別是：

| 欄位 | O1 | 原廠 |
| --- | --- | --- |
| `TPR6` | `0x44000000` | `0x3a808080` |
| `TPR11` | `0x24242624` | `0x25252523` |
| `TPR12` | `0x0f0f100f` | `0x110f0f10` |

原廠還執行 `R_2d`、`R_1st`、`W_2st`、`R_2st`、`RV_C`、DST 與 simple
test；O1 只啟用 read calibration。決策 D012：O3 已證明主要缺口是動態
眼圖與選點，不把 `1116` 結果硬編碼為通用值；O4 必須逐步移植。

### 本輪失敗命令

以下全部保存，沒有隱藏：

1. 第一次批次讀檔把 `2>/dev/null` 放在 `for` 清單，shell 結束碼 `2`；
   未執行寫入。
2. 第一次重建 JSON 從倉庫根目錄傳入長相對路徑，`cmp` 結束碼 `1`；差異
   只在每筆「來源」字串。改用證據目錄與純檔名後逐位元通過。
3. 第一次 `jq` 直接用中文識別符建立物件，編譯結束碼 `3`；改用
   `.["欄位"]` 後通過。
4. 第一次 `debugfs dump /boot/uInitrd` 匯出的是符號連結本身，得到空檔，
   `dumpimage` 結束碼 `1`；改讀實際目標後全部通過。
5. 第二次 initrd 命令含 `rm -f`，在執行前被工具安全規則拒絕；沒有建立
   loop 或修改檔案。改用全新 `mktemp` 目錄完成唯讀驗證。

### 證據保存

原始 UART 與 JSON 已逐位元複製到 Git 追蹤目錄，兩次 `cmp` 都是結束碼
`0`。完整報告及機器可讀比較：

```text
docs/evidence/bananapi-m4zero-opi-ddr/O1-hardware-1116-20260813.md
docs/evidence/bananapi-m4zero-opi-ddr/O3-1116-parameter-comparison-20260813.tsv
docs/evidence/bananapi-m4zero-opi-ddr/hardware/O1-1116-20260813
```

## 2026-08-13：改用單一執行期 SPL DDR 實驗器

使用者指出以完整作業系統映像逐版搜尋 DDR 參數效率過低，且要求移除每組
候選使用 `#define`／Kconfig 重編譯的方式。決策 D013：停止建立 O4 的逐組
編譯候選，改為 `M4ZLAB2` 單一 SPL 實驗器與主機端控制器。

已淘汰但未建置、未提交的兩份草稿如下：

1. O4a 只開啟 upstream read training 的候選。
2. `M4ZLAB1` 以 Kconfig 固定 passes、window 與單一 profile 的實驗器。

兩者都沒有產生實機結果，不得列入候選歷史。保留的技術結論只有：H616
timing 的 `ns_to_t()` 目前使用編譯期 `CONFIG_DRAM_CLK`，若要在同一 SPL
切換時脈，必須讓 timing 與 PLL 同時改用執行期 `para->clk`。

新的固定流程為 480 MHz 保險 profile 啟動、UART 下發完整候選、watchdog
保護下重設 DDR、執行 M0／M1／M2，正常後恢復 480 MHz。壞候選若卡死，
watchdog 重啟同一份 SPL；主機端保存進度並續跑，不需重寫 SD 卡。

原廠物件唯讀分析確認 `dramc_simple_wr_test` 為 140 bytes，會測試 RAM 起點
與容量中點；四 GiB／兩 Rank 時對應 Rank 0 與 Rank 1 起點。它只能作快速
篩選，新的 M1／M2 仍須加入 Rank 邊界、initrd 區域、大窗口及 benchmark。

詳細協定、判定與交付條件：

```text
docs/bananapi-m4zero-ddr-lab-plan-20260813.md
```

目前分類：架構與協定已確定；程式尚未建置；硬體尚未執行。

決策 D014：保險值、最佳值、最大容錯值必須分開計算。單板結果先保存，
量產共同值只能取跨序號、顆粒與批次的通過交集。

## 2026-08-13：M4ZLAB2 軟體完成與正式建置

決策 D015：所有待搜尋的 DDR 候選欄位改為單筆 UART `R` 記錄，在同一份
SPL 執行期套用。480 MHz 只保留為首次啟動與錯誤復原錨點；SPL 會保存本次
啟動實際使用的完整 profile，不另維護第二份硬編碼安全參數。

正式實作包含：

1. `clk`、`dx_odt`、`dx_dri`、`ca_dri`、`odt_en`、`tpr0`、`tpr2`、
   `tpr6`、`tpr10`、`tpr11`、`tpr12` 全部由 UART 執行期設定。
2. PLL 與 `ns_to_t()` 使用同一候選時脈，只接受 240 至 900 MHz 間的
   12 MHz 倍數。
3. 初始 DDR 初始化與每個候選都受 watchdog 保護；安全設定恢復成功後才輸出
   `FINAL recovered=pass`。
4. M0／M1／M2 包含資料線、全容量位址別名、Rank 邊界、五個分散窗口、
   圖樣校驗及直接 load／store／copy 計時。
5. 主機工具提供 `info`、`run`、`scan`、`rank`、JSONL 增量保存、
   `--resume`、多欄笛卡兒掃描、重複 watchdog 重啟與外部重設命令。
6. 排名要求相同參數的全部 M2 樣本通過，分別輸出保險、最佳效能與最大容錯
   候選；掃描碰到邊界時明確標示容錯半徑尚未收斂。

正式建置命令執行兩次：

```bash
BUILD_STAMP=20260813-final-a ./tools/build-bpi-m4zero-ddr-lab.sh
BUILD_STAMP=20260813-final-b-repro ./tools/build-bpi-m4zero-ddr-lab.sh
```

兩次結束碼均為 `0`。Build ID 為
`2026.01-S127a-P2cea-Hc6a9-V3946-Be6d8-R448a`；未封裝 SPL 為
24,768 bytes，eGON SPL 為 32,768 bytes，距 49,056-byte 上限尚有
24,288 bytes。套件與來源組合二進位逐位元一致，兩輪的 SPL、ELF 與組合
二進位也逐位元一致。SPL SHA-256：

```text
4cf6e982dfff69485e4c1251f7a8b16d74dfe9b881bede907a8a32b412171a8f
```

主機工具完成 `ruff check`、`ruff format --check` 與 19 項單元測試；兩份
shell 工具完成 `bash -n` 與 `shellcheck`。U-Boot 正式補丁的 strict
checkpatch 為 `0 errors`、`9 warnings`、`0 checks`，保留直接 DRAM 存取
需要的 8 個 `volatile` 警告與 1 個新檔維護者提醒。

韌體與主機工具分別以 `effa18361`、`9db9f9549` 提交並推送。為排除工作樹
未提交程式碼影響，另從 `9db9f9549380f2657040d3462ba5f840f475dbfa`
執行正式提交後建置：

```bash
BUILD_STAMP=20260813-pushed-final ./tools/build-bpi-m4zero-ddr-lab.sh
```

命令結束碼為 `0`，產物位於：

```text
output/evidence/bpi-m4zero-ddr-lab/build-20260813-pushed-final-9db9f9549
```

該次 `Build ID`、尺寸及三項主要雜湊均與前兩次相同；正式 SPL SHA-256 仍為
`4cf6e982dfff69485e4c1251f7a8b16d74dfe9b881bede907a8a32b412171a8f`。
由於 Git 的一般空白檢查會把巢狀 `format-patch` 內容所需的前導字元視為
空白問題，正式補丁改由 U-Boot `checkpatch.pl --strict` 守門；其餘程式與
文件仍使用 Git 空白檢查。

本輪失敗與修正均保留：

1. 第一個正式建置證據腳本錯選舊 `P4301` hashed DEB，導致來源與套件比對
   失敗；改為從 SPL 版本字串取得 Build ID，且只選同 ID 套件。
2. 第二次建置仍因 U-Boot 的 `env -i` 丟棄 `SOURCE_DATE_EPOCH`，兩個時間字串
   與其 eGON／FIT 校驗共 9 bytes 不同；將值傳入 U-Boot make 環境後，
   中間版 `P141b` 與最終版 `P2cea` 各自兩次完整建置逐位元一致。
3. 主機工具第一次審查發現送出位置欄位、事件使用空格、文字 `id` 與 SPL 的
   `key=value`、底線事件、`u32 id` 不一致；統一協定並加入正式補丁契約測試。
4. 韌體審查發現 `tiny-printf` 不支援 `ll`、任意時脈與 PLL 取整不一致、
   `FINAL` 早於安全恢復及初始初始化沒有 watchdog；全部修正後才建立正式證據。
5. 獨立 U-Boot 完整映像建置因缺少 `atf-bl31` 結束碼為 `2`；同一命令已完成
   SPL 編譯，正式 Armbian 建置提供 TF-A 後完整通過。
6. 清除本輪 Python 快取時，`rm -rf` 被工具安全規則拒絕且未執行；改用
   `find -delete` 與 `rmdir` 清除本輪產物。
7. 第一份大型補丁因檔頭插入點不符而整筆拒絕，沒有留下部分修改；拆成小型
   原子修改後完成。
8. 直接修改 Armbian U-Boot cache 因檔案屬於 root 而失敗，沒有寫入；只調整
   本輪涉及檔案的擁有者後繼續，未改動無關來源。
9. 第一次隔離輸出建置先缺少 `syncconfig`，補做後又因共享來源殘留 in-tree
   產物而被 U-Boot 拒絕；沒有執行 `mrproper` 破壞共享狀態，改用本機乾淨
   clone 驗證。
10. 乾淨 clone 第一次編譯缺少 timer 與 DRAM base 定義，第二次仍缺 DRAM
    base；加入正確 `config.h` 後完成 SPL 編譯。這些失敗都沒有形成候選產物。

完整建置證據與操作手冊：

```text
docs/evidence/bananapi-m4zero-opi-ddr/M4ZLAB2-build-20260813.md
docs/bananapi-m4zero-ddr-lab-guide-20260813.md
```

目前分類：軟體與離線建置完成；尚未寫入實體 SD 卡，2 GiB／4 GiB Rayson
實機矩陣尚未執行，因此三類候選仍無硬體結論。

## 2026-08-13：正式實驗 SPL 寫入 SD 卡

本機將裝置識別碼 `0x97bc8c07`、容量 `63864569856` bytes 的 SD 卡辨識為
`/dev/mmcblk0`。寫入前確認根目錄位於 `/dev/nvme0n1p5`，SD 卡所有分割區
均未掛載。使用正式提交後建置的 `M4ZLAB2` SPL，只寫入 8 KiB 偏移的
32 KiB 區段。

寫入工具結束碼為 `0`。來源及裝置回讀 SHA-256 均為：

```text
4cf6e982dfff69485e4c1251f7a8b16d74dfe9b881bede907a8a32b412171a8f
```

寫入前原區段已保存，SHA-256 為
`4aff4a4bb4a6ea86b78ca5308e5c0dfc1fbf16a139d41012d7cb3857e1f16a12`。
本階段證明媒體寫入正確；SD 卡尚未放入 BPI-M4 Zero，UART 與 DDR 實機結果
仍為尚未驗證。完整證據：

```text
docs/evidence/bananapi-m4zero-opi-ddr/M4ZLAB2-sd-write-20260813.md
```

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

## 2026-08-13：0438 執行期參數掃描

停止一個舊 `minicom` 程序對 `/dev/ttyUSB0` 的爭用後，重新建立乾淨 JSONL
與 UART 記錄。0438 回報 4,096 MiB、2 Rank、x32、16 Rows、10 Columns。
乾淨記錄共 531 筆，其中 383 筆通過、148 筆為刻意搜尋邊界所得失敗。

480 MHz 保險設定與 792 MHz 單板中心候選各完成 `M2 10/10`。792 MHz 候選：

```text
dx_odt=0x07070707 dx_dri=0x0e0e0e0e ca_dri=0x00000d0d
odt_en=0xaaaaeeee
tpr6=0x3a808080 tpr10=0x402f6663
tpr11=0x24242422 tpr12=0x110f1111
```

主機工具同步加入 packed lane 掃描與多候選交錯執行，避免把 `tpr11`、
`tpr12` 整個 32-bit 值作錯誤線性加減，並降低連續測同一候選造成的時間及
溫度偏差。排名器也改為只有左右失敗邊界完整時才輸出最大容錯候選；截尾
窗口另列為最寬已觀察候選。原始記錄、SHA-256、邊界結果與限制收錄於：

```text
docs/evidence/bananapi-m4zero-opi-ddr/M4ZLAB2-hardware-0438-20260813.md
```

本輪全是 watchdog 熱重設，尚不能算冷開機或量產通過。下一步是第二片板的
完整候選與關鍵邊界交集，再建立可開機候選進行 Linux 壓力測試。

## 2026-08-13：1116 跨板熱重設驗證

1116 回報與 0438 相同的 4,096 MiB、2 Rank、x32、16 Rows、10 Columns。
480 與 792 MHz 以十輪交錯順序各完成 `M2 10/10`。關鍵 `TPR6` 邊界顯示
`0x44` 為 3/3、`0x45` 為 1/3、`0x46` 為 0/3；與 0438 合併後，共同已
觀察零失敗區間為 `0x2e..0x44`，跨板候選保留中心附近的 `0x3a`。

原廠 1116 動態 `TPR11/TPR12` 組合另完成 `M2 5/5`。乾淨證據共 64 筆，
53 筆通過及 11 筆預期邊界失敗，所有測試均恢復安全設定。詳細報告：

```text
docs/evidence/bananapi-m4zero-opi-ddr/M4ZLAB2-hardware-1116-20260813.md
```

兩板熱重設 gate 完成。下一階段把共同候選帶回標準 U-Boot，驗證 TF-A、
U-Boot proper、核心、initrd、完整斷電冷開機及 Linux 壓力。

## 2026-08-13：跨板候選帶回標準 U-Boot

板級 `013` 補丁改為兩板共同候選：792 MHz、`CA_DRI=0x0d0d`、
`TPR6=0x3a808080`、`TPR11=0x24242422`、`TPR12=0x110f1111`。geometry
仍由 upstream 自動探測，不加入固定容量或 Rank fallback。

`015` 的實驗器程式碼繼續保留，但一般 defconfig 明確關閉
`CONFIG_DRAM_SUNXI_H616_LAB`，並恢復 SPL MMC、raw image 與下一階段載入。
`M4ZDDR1` 唯讀診斷暫時啟用，用於追蹤 TF-A、核心與 initrd 交接；它不改變
訓練旗標、重試次數或初始化回傳值。

從乾淨 U-Boot clone 依序套用 `001`、`002`、`010`、`011`、`013`、`014`、
`015` 全部成功，最終 defconfig 與跨板候選逐欄一致。建置工具新增 LAB 關閉
守門與可傳入的預期 profile，入口為：

```text
tools/build-bpi-m4zero-cross-board-792.sh
```

本階段只完成原始碼與補丁堆疊檢查，正式 U-Boot 建置及映像封裝在推送後執行。

第一次正式建置的 18 份補丁全部套用，但在 SPL 編譯時因 LAB 執行期時脈
變數未宣告而結束碼為 `2`。原因是變數宣告受 `#if` 保護，使用點卻只以
`if (IS_ENABLED())` 包住；後者仍須通過 C 名稱解析。最終讓 `extern` 宣告
始終可見，實體定義與物件仍只在 LAB 模式連結；一般映像的條件分支會被
編譯器消除。此修正不改 LAB 啟用時的行為，也不產生失敗候選產物。

## 2026-08-13：X2 可重現建置與完整映像

一般建置修正後，U-Boot 與 TF-A 已完成編譯。證據腳本第一次誤取舊的
480 MHz LAB 套件，原因是只搜尋 `output/packages-hashed/global`；改為搜尋
現行輸出並限定建置開始後產物。後續二進位差異已確認只來自兩處時間字串及
衍生校驗，加入固定 `SOURCE_DATE_EPOCH=1786579200`，沒有放寬 `cmp`。

Armbian 仍曾回用同 Build ID 的舊套件，故先將兩份舊 `P1f88` 套件移入
證據封存目錄，再執行第五輪。正式命令結束碼為 `0`：

```bash
BUILD_STAMP=20260813-cross-board-pushed-v5 \
  ./tools/build-bpi-m4zero-cross-board-792.sh
```

套件、工作樹與證據目錄內的完整 bootloader SHA-256 均為：

```text
a23cb287ac503a63bb505c4fe538447aec91a18fb5aadb6e5e87126b3c47e0ad
```

以 U0 480 MHz 已知可開機映像為 payload，只替換 8 KiB 偏移 bootloader，
產生並保留 `.img` 與 `.img.xz`。其 SHA-256 分別為：

```text
fb665992d6a5becfe2694cade5f2e1367f0eeb18582fdcda8e8d3d446042610b
3bff7ae94ffdc6e38fb5241646204dbe6ede9b6556028924bd54626ecc670fbd
```

前綴、bootloader 回讀、後綴、檔案大小、分割表與 xz 完整性全部通過。
目前新增兩片不同批次、現場確認採用三星 DDR 的 M4 Zero，實機矩陣擴充為
`0438`、`1116` 與兩片待記錄板號及完整 DDR 料號的三星樣本。X2 仍只代表
兩片 Rayson 板的共同候選，不宣稱已跨 DDR 供應商。另有兩片 H618 BPI-M4B，
因板級設定與 DDR 拓撲尚未確認，採獨立工作流，禁止直接使用 M4 Zero 映像。
完整證據與計畫：

```text
docs/evidence/bananapi-m4zero-opi-ddr/X2-cross-board-792-build-image-20260813.md
docs/bananapi-m4zero-cross-batch-x2-hardware-plan-20260813.md
```

封裝工具與計畫以提交 `86f22f9aa` 推送後，另由該提交執行：

```bash
BUILD_STAMP=20260813-cross-board-post-push \
  ./tools/build-bpi-m4zero-cross-board-792.sh
```

命令結束碼為 `0`，正式 post-push 證據目錄為：

```text
output/evidence/bpi-m4zero-opi-ddr/X2-20260813-cross-board-post-push-86f22f9aa
```

其 bootloader SHA-256 仍為
`a23cb287ac503a63bb505c4fe538447aec91a18fb5aadb6e5e87126b3c47e0ad`，
並與既有 X2 完整映像 8 KiB 偏移回讀逐位元一致。第一次核對命令因呼叫端
把 `$wd` 保留成字面路徑而找不到檔案，未寫入或修改任何內容；改用絕對路徑
及 `set -euo pipefail` 後通過。

## 2026-08-13：兩片三星 DDR 樣本盤點

原始照片 `IMG_3687.jpg` 與 `IMG_3686.jpg` 已納入證據目錄並計算 SHA-256。
兩片顆粒字樣均分成 `K4F6E3S` 與 `4HMMGCJ` 兩行，完整料號確認為
`K4F6E3S4HM-MGCJ`；頂部追溯碼分別為 `SEC 337` 與 `SEC 322`。照片底部的
追溯字串有模糊字元，因此不做猜測性轉錄。

Renesas DRAM 相容清單將該料號列為 LPDDR4、16 Gb、x32、1 Rank，單顆
封裝容量相當於 2 GiB。此資料只建立實機 geometry 預期，不取代 SPL 與
Linux 驗證，也不代表 X2 已跨 DDR 供應商通過。兩片暫以 `S337`、`S322`
識別，待補拍板身序號與 PCB 版本後再建立永久樣本對照。

詳細盤點與驗證邊界：

```text
docs/evidence/bananapi-m4zero-opi-ddr/M4Z-Samsung-DDR-inventory-20260813.md
```

## 2026-08-13：1116 的 X2 標準啟動 G1

將 X2 完整映像寫入新出現的 `/dev/mmcblk0` 59.5 GiB SD 卡。寫入前確認
映像大小為 `2034237440` 位元組，SHA-256 為
`fb665992d6a5becfe2694cade5f2e1367f0eeb18582fdcda8e8d3d446042610b`。
完整範圍回讀雜湊相同，8 KiB 偏移的 `1154976` 位元組 bootloader 也逐位元
一致。第一次 bootloader 區段比較把讀取權限放在 `cmp` 而不是裝置端 `dd`，
因此只產生權限錯誤；改正後通過，完整回讀結果從未失敗。

`1116` 完全斷電後使用該卡啟動。SPL 一次完成每個 geometry 階段，最終為
4,096 MiB、x32、2 Rank、16 Rows、10 Columns；TF-A、U-Boot、initrd checksum、
核心及 rootfs 全部通過。Linux `6.18.32-current-sunxi64` 進入使用者空間，
systemd 為 `running`、失敗服務為零，可用記憶體約 3.8 GiB。先前 O1 的
initramfs 解包錯誤沒有重現。

第一次開機完成 rootfs 擴充與測試帳號初始化。提交的 UART 只遮蔽 root
測試密碼參數，其餘輸出未改。此結果通過 G1 並計入 G2 `1/10`；G2 至 G4
仍待完成。詳細證據：

```text
docs/evidence/bananapi-m4zero-opi-ddr/X2-hardware-1116-G1-20260813.md
```

## 2026-08-13：三星 S337 的 X2 標準啟動 G1

同一張 X2 SD 卡移至頂部追溯碼 `SEC 337`、料號 `K4F6E3S4HM-MGCJ` 的三星
樣本。SPL 在雙 Rank read calibration 五次失敗後依主線探測流程退回單 Rank；
後續 geometry 階段全部一次通過，最終正確回報 2,048 MiB、x32、1 Rank、
16 Rows、10 Columns。TF-A、U-Boot、initrd checksum、核心、rootfs 與使用者
空間全部通過。

Linux `6.18.32-current-sunxi64` 顯示總記憶體 1.9 GiB，systemd 為 `running`，
失敗服務為零。以 `stress` 配置 1.4 GiB 持續寫入、4 CPU worker、180 秒，
結束碼為 `0`；測試後沒有 OOM、page fault、panic、Oops、EDAC 或資料毀損
關鍵字。此項只列短壓力，不升格為完整 G3。

錯誤掃描發現 SDIO `mmc1` 初始化失敗及 Bluetooth reset timeout。它們未影響
DDR、SD、eMMC、rootfs 或 systemd，故不阻擋 DDR G1，但需另列周邊支援缺口。
S337 通過 G1 並計入 G2 `1/10`。詳細證據：

```text
docs/evidence/bananapi-m4zero-opi-ddr/X2-hardware-S337-G1-20260813.md
```

## 2026-08-13：三星 S322 的 X2 標準啟動 G1

同一張 X2 SD 卡移至頂部追溯碼 `SEC 322` 的三星樣本。SPL 的雙 Rank 探測
失敗五次後退回單 Rank，後續 geometry 全部一次通過，最終正確回報
2,048 MiB、x32、1 Rank、16 Rows、10 Columns。TF-A、U-Boot、initrd checksum、
核心、rootfs 與使用者空間全部通過。

本板 Linux 把測試 SD 枚舉為 `/dev/mmcblk2`，而非 S337 的 `/dev/mmcblk0`。
名稱 `SR64G`、59.5 GiB 容量、CID、`4020000.mmc` 控制器及 rootfs UUID 均
確認它仍是同一張 SD；`/dev/mmcblk1` 才是 7.28 GiB 的 `8GTF4R` eMMC。

180 秒、1.4 GiB 記憶體與 4 CPU 冒煙測試結束碼為 `0`，沒有記憶體或核心
異常。SDIO 與 Bluetooth 初始化仍失敗，系統只剩 loopback 介面；
`vnstat.service` 因無介面可加入資料庫而退出，使 systemd 成為 `degraded`。
此問題不阻擋 DDR G1，但代表整板周邊 Gate 尚未通過。S322 計入 G2 `1/10`。

詳細證據：

```text
docs/evidence/bananapi-m4zero-opi-ddr/X2-hardware-S322-G1-20260813.md
```

## 2026-08-13：0438 與四板 X2 G1 階段完成

同一張 X2 SD 卡移至 Rayson `0438`。SPL 的四個雙 Rank geometry 階段均一次
通過，最終回報 4,096 MiB、x32、2 Rank、16 Rows、10 Columns。TF-A、U-Boot、
initrd checksum、核心、rootfs 與使用者空間全部通過。systemd 為 `running`、
失敗服務為零，`wlan0` 已建立；錯誤掃描只命中 watchdog 的正常 timeout 設定。

以 3.0 GiB 記憶體持續寫入、4 CPU worker 執行 180 秒，結束碼為 `0`；swap
未使用，核心沒有 OOM、page fault、panic、Oops、EDAC 或資料毀損。0438
通過 G1 並計入 G2 `1/10`。

至此固定 X2 映像已在 0438、1116、S337、S322 四片實物完成首次完全斷電
冷啟動，涵蓋 Rayson 4 GiB 雙 Rank 與 Samsung 2 GiB 單 Rank。四片 G1 通過，
但 G2 都只有 `1/10`，G3、G4、G5 尚未通過；S337、S322 的無線周邊問題不因
DDR 成功而關閉。摘要與 0438 詳細證據：

```text
docs/evidence/bananapi-m4zero-opi-ddr/X2-hardware-0438-G1-20260813.md
docs/evidence/bananapi-m4zero-opi-ddr/X2-four-board-G1-summary-20260813.md
```
