# BPI-M4 Zero 0845 DDR 完整 Benchmark 與參數收斂計畫

## 目標

以板號 `450600845` 作為目前第一優先弱板，先區分已觀察到的 PID 1 kernel
panic 是否由 DDR margin、SD/rootfs、供電或其他硬體問題造成，再使用同一份
`M4ZLAB2` SPL 找出 792 MHz 的保險值、效能值與最大已觀察容錯值。

0845 的單板最佳值不能直接升格為共同設定。最終候選必須與 0438、1116、
2 GiB 單 Rank 樣本及舊 V2 三片弱板交叉驗證。

## 已知基線

| 項目 | 值 |
| --- | --- |
| 0845 geometry | 4,096 MiB、x32、2 Rank、16 Rows、10 Columns |
| X2 Build ID | `P1f88` |
| X2 時脈 | 792 MHz |
| X2 bootloader SHA-256 | `a23cb287ac503a63bb505c4fe538447aec91a18fb5aadb6e5e87126b3c47e0ad` |
| X2 Noble XFCE XZ SHA-256 | `e148b33abc2ca4384bb40f8269d9cd99ae1d863f59f94ee05b89b663d5f97443` |
| U0 Noble XFCE XZ SHA-256 | `b60b3e874540a58e7a46b220a2821f02f5b5fa9ae369e9b42cd122a86a2052ce` |
| 已知失敗點 | initrd checksum 通過；kernel handoff 後 PID 1 以狀態 1 結束 |

X2 與 U0 使用同一份 Noble OS、kernel、initramfs、DTB 與 rootfs payload，
差異只在 bootloader 區域。這讓兩者可用來判斷 0845 是否具有頻率相關差異。

## 階段 A：實物與輸入鎖定

收到 0845 後先完成：

1. 拍攝板號、PCB 版本、DDR、eMMC、PMIC 與可能缺件區域。
2. 記錄 DDR、eMMC、SD 卡、電源供應器與 UART 轉接器的識別資料。
3. 對 U0、X2、M4ZLAB2 SPL 及 SD 寫入回讀區段計算 SHA-256。
4. 使用固定 3.3 V UART `115200 8N1`，排除其他程序同時讀取序列埠。
5. 從完全斷電開始記錄，每輪標示冷啟動或 watchdog 熱重設，不得混算。

證據目錄固定使用：

```text
output/evidence/bpi-m4zero-ddr-lab/0845-日期時間/
```

## 階段 B：先排除非 DDR 根因

1. U0 480 MHz 完全斷電啟動 10 次。
2. X2 792 MHz 完全斷電啟動 10 次，與 U0 輪次交錯。
3. 每輪記錄 SPL、initrd checksum、kernel、PID 1、rootfs 裝置與最後階段。
4. U0 能進 Linux 時執行記憶體、CPU 與 SD I/O 冒煙測試，保存完整 `dmesg`。

| 結果 | 判斷與下一步 |
| --- | --- |
| U0 與 X2 都失敗 | 先處理硬體、供電、SD、rootfs；停止調 DDR 參數 |
| U0 通過、X2 可重現失敗 | 進入 M4ZLAB2 792 MHz margin 掃描 |
| U0、X2 都通過 | 把 X2 擴增到至少 30 次冷啟動，再決定是否需要調參數 |
| M4ZLAB2 480 MHz 不穩 | 視為硬體或基礎環境問題，不進行 792 MHz 最佳化 |

X2 若只出現 PID 1 panic，而 M4ZLAB2 792 MHz 長測完全通過，應優先增加 kernel
輸出並核對 initramfs/rootfs，不因單一 panic 直接改寫 DDR profile。

## 階段 C：M4ZLAB2 基準

使用既有單一 SPL 實驗器，不修改 `#define`、不重編譯每組參數：

```bash
EVIDENCE=output/evidence/bpi-m4zero-ddr-lab/0845-日期時間

python3 -B tools/bpi-m4zero-ddr-lab.py info \
  --tty /dev/ttyUSB0 \
  --uart-log "$EVIDENCE/info-uart.log"

python3 -B tools/bpi-m4zero-ddr-lab.py run \
  --tty /dev/ttyUSB0 \
  --profile tools/bpi-m4zero-ddr-lab-profile-safe-480.json \
  --set level=M2 --set passes=5 --set window=64 \
  --repeat 20 --timeout 600 \
  --jsonl "$EVIDENCE/results.jsonl" \
  --uart-log "$EVIDENCE/uart.log"

python3 -B tools/bpi-m4zero-ddr-lab.py run \
  --tty /dev/ttyUSB0 \
  --profile tools/bpi-m4zero-ddr-lab-profile-cross-board-candidate-792.json \
  --set level=M2 --set passes=5 --set window=64 \
  --repeat 20 --timeout 600 \
  --jsonl "$EVIDENCE/results.jsonl" \
  --uart-log "$EVIDENCE/uart.log"
```

480 與 792 MHz 應交錯執行；若單次 64 MiB M2 超過目前 timeout，再依實測時間
調整 timeout，不縮小測試內容來換取表面通過。

## 階段 D：頻率失敗邊界

先用 M0 找出頻率 cliff：

```bash
python3 -B tools/bpi-m4zero-ddr-lab.py scan \
  --tty /dev/ttyUSB0 \
  --profile tools/bpi-m4zero-ddr-lab-profile-cross-board-candidate-792.json \
  --set level=M0 --set passes=3 --set window=16 \
  --field clk=480,528,600,672,720,744,756,768,780,792 \
  --repeat 5 --timeout 120 \
  --jsonl "$EVIDENCE/results.jsonl" \
  --uart-log "$EVIDENCE/uart.log" --resume
```

再對第一個間歇失敗時脈及其前後兩級執行 M1，每組至少五次。若 792 MHz
完全通過，仍繼續掃參數邊界；目標是量出 margin，不是只證明中心值偶爾能跑。

## 階段 E：792 MHz 單變因掃描

所有掃描一次只改一個欄位，先取得左右失敗邊界，再縮小到通過區間中心。
禁止一開始建立大型笛卡兒組合，避免無法判斷是哪個參數造成結果。

第一優先掃描 `TPR6`：

```bash
python3 -B tools/bpi-m4zero-ddr-lab.py scan \
  --tty /dev/ttyUSB0 \
  --profile tools/bpi-m4zero-ddr-lab-profile-cross-board-candidate-792.json \
  --set clk=792 --set level=M1 --set passes=3 --set window=32 \
  --field tpr6.b3=0x28:0x4c:1 \
  --repeat 3 --timeout 300 \
  --jsonl "$EVIDENCE/results.jsonl" \
  --uart-log "$EVIDENCE/uart.log" --resume
```

後續依序執行：

1. `tpr11.b0..b3`、`tpr12.b0..b3` 各自從 X2 基準值正負四格開始，未出現
   左右失敗邊界就逐步向外擴張；每次只掃一個 lane。
2. `dx_odt`、`dx_dri`、`ca_dri` 先作離散粗掃，再對通過區間逐格細掃。
3. `odt_en` 比較 `0xaaaaeeee`、`0x9988eeee` 與原始來源中的離散候選。
4. `tpr10` 先保持 `0x402f6663`；只有其他欄位仍無法形成共同窗口時才單獨掃描。

任一候選只要出現一次資料錯誤、timeout、watchdog reset 或安全設定恢復失敗，
就不能列入最終候選。通過區間碰到掃描端點時標示為截尾，不宣稱已找到最大
容錯值。

## 階段 F：候選排名與複驗

```bash
python3 -B tools/bpi-m4zero-ddr-lab.py rank \
  "$EVIDENCE/results.jsonl" \
  --min-samples 5 \
  --output "$EVIDENCE/ranking.json"
```

產生三類候選：

- 保險候選：最低可靠時脈及最大共同 margin。
- 792 MHz 效能候選：以讀、寫、複製三項中最差者比較，不看單一最高值。
- 792 MHz 最大容錯候選：每個已量邊界的最小半徑最大者。

每類候選在 0845 執行 `M2 20/20`、`passes=10`、`window=64`。接著在 0438、
1116 與至少一片 2 GiB 單 Rank 板重跑相同候選；最終值取所有板共同通過窗口
中心，不取 0845 單板中心。

## 階段 G：正式映像 Gate

只有 SPL benchmark 通過後才建立新的標準 U-Boot 與完整映像。每個正式候選
至少完成：

1. 0845 完全斷電冷啟動 `30/30`。
2. `450600146`、`450600826`、`450601075` 各 `10/10`。
3. 0438、1116 與 2 GiB 單 Rank 樣本各 `10/10`。
4. 每片確認 initrd checksum、完整 kernel boot、rootfs 與登入。
5. 0845 執行至少八小時記憶體、CPU、MMC 並行壓力，無資料錯誤、Oops、panic
   或非預期重設。
6. 另做冷／熱環境與供電角落；未完成前只標示工程候選。

## 交付物

0845 完成後必須提交：

- 原始 UART、JSONL、排名 JSON 與 SHA-256。
- 每個階段的命令、時間、結束碼、板號、電源與 SD 卡資訊。
- U0/X2 的冷啟動矩陣及失敗邊界。
- 三類候選與 0438、1116、2 GiB 樣本的窗口交集。
- 最終 bootloader、完整映像、建置提交與回讀雜湊。
- 明確列出通過 Gate、未通過 Gate 及不得外推的範圍。

## 2026-08-19 執行進度

已完成 0845 的 M4ZLAB2 geometry 確認、原 X2 反證、頻率粗掃、TPR6 上下
失敗邊界與 TPR11／TPR12 配對收斂。原 X2 792 MHz M2 出現真實資料位元
錯誤；A1 候選改用 `tpr11=0x25252523`、`tpr12=0x110f0f10`，保留
`tpr6=0x3a808080`。A1 標準 bootloader 與 Jammy IMG/XZ 已建立並通過離線
封裝驗證。

尚未完成的 Gate 是斷電冷啟動、Linux 全容量壓力、跨 0438／1116／2 GiB
單 Rank／舊 V2 弱板的共同驗證，以及溫度與供電角落。進度與限制不得解讀
為 A1 已可量產。
