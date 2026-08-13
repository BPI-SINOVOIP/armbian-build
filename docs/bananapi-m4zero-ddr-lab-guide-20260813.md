# BPI-M4 Zero 單一 SPL DDR 實驗器操作手冊

日期：2026-08-13

協定：`M4ZLAB2`

## 1. 交付範圍

這套工具只需建立並寫入一份 SPL。後續候選不再修改 `#define`、Kconfig、
U-Boot、核心或完整映像，也不再重新編譯。主機工具透過 UART 一次下發完整的
`clk`、`dx_odt`、`dx_dri`、`ca_dri`、`odt_en`、`tpr0`、`tpr2`、`tpr6`、
`tpr10`、`tpr11`、`tpr12`、測試層級、輪數及窗口。

SPL 在 UART 可用前仍必須先完成一次 DDR 初始化，因此保留 480 MHz 啟動設定。
這不是待掃描的編譯期候選，而是固定復原錨點。SPL 會保存本次啟動實際使用的
完整設定，測完每個候選後先恢復該設定，成功後才送出 `FINAL`。

目前只處理 BPI-M4 Zero 使用的 LPDDR4；記憶體種類固定為 LPDDR4，容量、Rank、
位寬、Rows 與 Columns 由 480 MHz 啟動流程偵測，不作為任意掃描參數。

## 2. 安全界線

- 測試會破壞 DRAM 內容，SPL 不會啟動 U-Boot proper、TF-A、核心或作業系統。
- 寫入工具只接受整顆磁碟，會拒絕分割區、根磁碟及含掛載分割區的裝置。
- 寫入前仍應人工確認 `/dev/sdX`，錯誤裝置名稱會破壞該裝置既有開機區。
- 單板結果只能稱為單板候選；量產值必須通過不同序號、顆粒與批次交集。

## 3. 建立與寫入

在 Armbian 倉庫根目錄執行：

```bash
./tools/build-bpi-m4zero-ddr-lab.sh
```

通過後，工具會在下列目錄建立 SPL、ELF、設定、DEB、建置日誌、符號、反組譯、
驗證表及 SHA-256 清單：

```text
output/evidence/bpi-m4zero-ddr-lab/build-時間-提交/
```

把 SPL 寫到測試 SD 卡的 8 KiB 偏移：

```bash
sudo ./tools/write-bpi-m4zero-ddr-lab.sh \
  --device /dev/sdX \
  --spl output/evidence/bpi-m4zero-ddr-lab/build-時間-提交/sunxi-spl-ddr-lab.bin \
  --evidence-dir output/evidence/bpi-m4zero-ddr-lab/write-板號-時間 \
  --confirm-write
```

寫入工具會保存原區段備份、寫入前後雜湊及逐位元回讀結果。它不會修改分割表。

## 4. UART 與裝置資訊

連接 3.3 V UART，設定為 `115200 8N1`，不要連接 UART 轉接器的 5 V。開機後
應出現 `M4ZLAB2_READY`。也可由工具主動查詢：

```bash
python3 -B tools/bpi-m4zero-ddr-lab.py info \
  --tty /dev/ttyUSB0 \
  --uart-log output/evidence/bpi-m4zero-ddr-lab/1116-info-uart.log
```

`READY` 會回報容量、Rank、位寬、Rows、Columns、復原時脈與實際 timer 頻率。

## 5. 執行單一設定

基準設定檔為：

```text
tools/bpi-m4zero-ddr-lab-profile-safe-480.json
```

連續執行三次 M2；每次候選完成後都先恢復 480 MHz，再執行下一次：

```bash
python3 -B tools/bpi-m4zero-ddr-lab.py run \
  --tty /dev/ttyUSB0 \
  --profile tools/bpi-m4zero-ddr-lab-profile-safe-480.json \
  --repeat 3 \
  --timeout 120 \
  --jsonl output/evidence/bpi-m4zero-ddr-lab/1116-results.jsonl \
  --uart-log output/evidence/bpi-m4zero-ddr-lab/1116-uart.log
```

`--set` 可覆寫任一候選欄位，例如 `--set clk=792 --set level=M1`。時脈只接受
240 至 900 MHz 間的 12 MHz 倍數，確保要求值、PLL 實際值與 timing 換算一致。

## 6. 執行參數掃描

先用 M0 掃描頻率階梯：

```bash
python3 -B tools/bpi-m4zero-ddr-lab.py scan \
  --tty /dev/ttyUSB0 \
  --profile tools/bpi-m4zero-ddr-lab-profile-safe-480.json \
  --set level=M0 \
  --field clk=480,528,600,672,720,744,768,792 \
  --repeat 3 \
  --timeout 40 \
  --jsonl output/evidence/bpi-m4zero-ddr-lab/1116-results.jsonl \
  --uart-log output/evidence/bpi-m4zero-ddr-lab/1116-uart.log \
  --resume
```

再固定已通過時脈，以 M1 分開掃描驅動、ODT 與 TPR。逗號表示離散值；
`起點:終點:步進` 表示含終點範圍。多個 `--field` 會建立笛卡兒組合：

```bash
python3 -B tools/bpi-m4zero-ddr-lab.py scan \
  --tty /dev/ttyUSB0 \
  --profile tools/bpi-m4zero-ddr-lab-profile-safe-480.json \
  --set clk=792 --set level=M1 --set passes=2 --set window=8 \
  --field tpr6=0x3a808080,0x44000000 \
  --field tpr11=0x24242624,0x25252523 \
  --field tpr12=0x0f0f100f,0x110f0f10 \
  --repeat 3 \
  --timeout 120 \
  --jsonl output/evidence/bpi-m4zero-ddr-lab/1116-results.jsonl \
  --uart-log output/evidence/bpi-m4zero-ddr-lab/1116-uart.log \
  --resume
```

候選開始後若 DDR 初始化卡死，硬體 watchdog 會重啟同一份 SPL。工具把
`START` 後重新出現 `READY` 記為 `watchdog_reset`，將結果立即附加到 JSONL，
再繼續下一組。`--resume` 會按每組參數所需重複次數續跑，不會把第一次結果
誤當成全部重複測試已完成。

## 7. M0、M1、M2

| 層級 | 內容 | 用途 |
| --- | --- | --- |
| `M0` | 資料線 walking-one／zero、全容量位址別名、Rank 邊界 | 快速淘汰 |
| `M1` | M0 加五個分散窗口、多輪寫入與逐字校驗 | 找連續通過區間 |
| `M2` | M1 加直接 load／store／copy 計時與搬移後校驗 | 最終候選比較 |

M2 的計時區段不重算圖樣，也不在計時內 reload watchdog；SPL 由硬體
`get_tbclk()` 回報 timer 頻率。吞吐量只可在相同 SPL、板子、窗口與測試條件
下比較。

## 8. 產生三類候選

```bash
python3 -B tools/bpi-m4zero-ddr-lab.py rank \
  output/evidence/bpi-m4zero-ddr-lab/1116-results.jsonl \
  --min-samples 3 \
  --output output/evidence/bpi-m4zero-ddr-lab/1116-ranking.json
```

排名只接受相同參數的全部 M2 樣本通過，且樣本數達 `--min-samples`：

- 保險候選：先取最低通過時脈，同時脈再比較連續通過半徑與效能。
- 最佳效能候選：以讀、寫、複製三項中最差吞吐量最高者為優先。
- 最大容錯候選：以每個掃描維度的最小連續通過半徑為優先。

若通過區間碰到掃描邊界，結果會標示 `boundary_truncated=true`，表示尚未找到
真實失敗邊界，不能把目前半徑當成完整容錯範圍。任何一次失敗、逾時、
watchdog 重啟或安全設定恢復失敗，都會讓該組參數退出候選。

## 9. 實機升格條件

每片 2 GiB 與 4 GiB Rayson 板至少完成：

1. 480 MHz 基準 M2 連續三次，另做 watchdog 復原與完全斷電測試。
2. 頻率 M0 階梯三次。
3. 單維 M1 找出每個參數的通過區間與失敗邊界。
4. 區間中心及邊界 M2 至少五次。
5. 完全斷電冷開機、完整 Armbian 映像、長時間記憶體壓力測試。

完成跨板交集前，工具輸出的三類結果仍是實驗候選，不是量產設定。
