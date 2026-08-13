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
