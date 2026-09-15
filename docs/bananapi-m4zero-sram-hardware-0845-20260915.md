# M4 Zero 0845：SRAM 救援實板首驗

日期：2026-09-15。分支：`bpi-h618-recovery-network-20260915`。
執行基準提交：`19483e8b1d2451b9b92809f3b399e7b775087e26`。韌體仍使用既有 `build-009`，未重新編譯或改動 DDR 參數。

## 結論

**0845 的縮限 SRAM 首驗通過。** SD 上的 SPL1 可啟動、接受 UART 傳輸，並交接執行固定 SPL2 冒煙程式；SD 槽 0 的同一封包也能載入並執行。兩種執行路徑完成後，均經插座斷電約 10 秒再上電，重新取得救援入口。

這不是完整多系統、DDR、Linux、SD 更新交易或量產驗收。N2 維持「部分完成」；完整專用多槽布局、實機復原及開機後媒體保留區複核仍未驗證。

## 設備與授權

- 板號 **0845**、UART `/dev/ttyUSB0` 由使用者確認；CH340 的實體路徑另存本機證據，沒有靠重複的 `by-id` 猜測。
- 使用者另指示以具名設備 `bpi-pw-1` 自行上電。先讀取 `/home/pi/log/bpi-pw-1/docs/M4-Zero-Codex交接-20260915.md` 與操作手冊，核對共用 `bpi-pw 0.4.0` 的新介面。
- 電源控制由持續供電的主機執行。每筆均核對指定插座名稱、IP、MAC、退出碼及回讀狀態；沒有操作另一顆插座，也沒有使用 `discover`、`toggle` 或批次查詢。
- UART 使用 115200、8N1、無流量控制；錄製先於首次上電。各階段由單一程序持有串口。
- 原卡的系統、備份及唯一允許的兩個寫入範圍見[最小部署紀錄](bananapi-m4zero-sram-minimal-sd-0845-20260915.md)。本輪板上測試未新增媒體寫入。

## 執行結果

| 順序 | 實際動作 | 證據與結果 |
| --- | --- | --- |
| 1 | 具名狀態查詢、上電 | 初態關閉；MAC 核對及 `on` 回讀通過 |
| 2 | 首次開機錄製 | `BPI-SUP1 event=ready abi=1 board=06180001 ddr=off sd_write=off` |
| 3 | UART 交握 | nonce `226320664`，ABI 與能力清單符合 |
| 4 | UART 上載，不執行 | nonce `1097355747`，`loaded result=0` |
| 5 | UART 重新上載並執行 | nonce `1657366668`／`62c9688c`，交接與 smoke 通過 |
| 6 | 狀態查詢、斷電約 10 秒、上電 | 回讀均通過，第二次出現 `ready` |
| 7 | SD 槽 0 載入並執行 | nonce `526718993`／`1f651811`，交接與 smoke 通過 |
| 8 | 斷電約 10 秒、上電 | 回讀均通過，第三次出現 `ready` |
| 9 | 未載入先要求執行 | 收到 `event=reject reason=command_or_state`；未交接 |
| 10 | 拒絕後重新交握 | 新 nonce `1430007763` 成功；保留救援等待狀態 |

UART 及 SD 兩次 smoke 均為 `sp=00047ff0 el=3 ddr=off result=pass`。主代理從實際控制 RX／TX 核對 `info → loading → loaded → handoff → smoke` 的順序、同 nonce、入口 `00030000` 與堆疊範圍，不只採信主機結果 JSON。

兩次斷電前皆已收到固定 smoke 成功，來源顯示其後停於遮罩中斷的 `wfe` 迴圈；沒有 Linux 或掛載檔案系統可供 `systemctl poweroff`。此判斷**只適用這個固定 SRAM 程式**；其他映像、未知狀態或已啟動 Linux 的板子仍須先正常關機，不得直接套用此程序。

## 可重現命令

下列為本次使用的既有工具介面，不是整段不分狀態直接執行的腳本。每次 power 操作與上一節對應；先開 UART 錄製，再上電，保存 `ready` 後才交給交握工具。`probe` 會清除接收緩衝，不能取代開機錄製。

```bash
bpi-pw --version
bpi-pw --device bpi-pw-1 status
bpi-pw --device bpi-pw-1 on

PY=output/evidence/bpi-sram-supervisor/model-venv/bin/python
SPL2=output/evidence/bpi-sram-supervisor/build-009/spl2-smoke.sram
"$PY" -B tools/bpi_sram_uart.py probe --port "$BPI_CONFIRMED_UART" --timeout 15
"$PY" -B tools/bpi_sram_uart.py upload --port "$BPI_CONFIRMED_UART" \
  --timeout 15 --input "$SPL2" --transfer-timeout 30
"$PY" -B tools/bpi_sram_uart.py upload --port "$BPI_CONFIRMED_UART" \
  --timeout 15 --input "$SPL2" --transfer-timeout 30 --run
```

`BPI_CONFIRMED_UART` 須先設成當次已核對的串口實體路徑。smoke 執行後停止，須完成前述狀態核對、受控斷電至少 10 秒與重啟錄製，才執行下一次載入：

```bash
"$PY" -B tools/bpi_sram_uart.py load-slot --port "$BPI_CONFIRMED_UART" \
  --slot 0 --timeout 15 --run
```

未載入執行的拒絕測試是在第三次開機的新交握後，傳送 `R <本次nonce>`，核對拒絕訊息，再用新 nonce 查詢。沒有讀取或寫入槽 1 至 4。

## 證據保存

本機根目錄：

```text
output/evidence/bpi-h618-recovery-network/N2-m4zero-0845-20260915/
```

- `cold-0845-20260915T142513Z/`：首次開機原始位元組及時間。
- `uart-flow-20260915T142742Z/`：UART 三階段結果與控制追蹤。
- `cold-slot0-20260915T142922Z/`：第一次斷電重啟、槽 0 控制追蹤與結果。
- `cold-return-20260915T143106Z/`：第二次斷電重啟、未載入執行拒絕與最後交握。
- `hardware-validation.json`：八組證據一致性核對、27 個證據檔案大小／SHA-256、七筆具名電源命令摘要。八組核對不是另八次硬體測試。

整合報告 SHA-256：`194eda6d076439fe695b5747efb594c83b28d77a5c8f89a6426e8c911eb1adf7`。

原始證據含內網設備資料，保留本機且由 Git 忽略；公開文件只保存必要摘要與雜湊，不推送帳密、IP／MAC 對照或卡片 CID。獨立只讀審查分別核對控制證據與固定韌體停止／寫入範圍；不由子代理操作硬體。

審查確認本輪控制證據相符，並指出主機工具未檢查 SP 的 16 位元組對齊。後續已補拒絕判定及離線反例；本輪實收 `00047ff0` 正常。硬體執行使用本文開頭的原提交，不能把後續離線修正冒充再次實機測試。未載入執行的拒絕回應沒有 nonce 欄位，其主機命令 nonce 不可說成裝置回傳。

修正後 **162 項原型回歸、24 項媒體工具回歸與 8 組稽核通過，零失敗、零跳過**。第一次直接呼叫泛用測試探索缺少 `BPI_SRAM_BUILD`，造成一個設定錯誤及一組跳過，該次未計為通過；失敗後段留存 `regression-invocation-failed-tail.json`，再改用既有驗證器帶入固定建置完整重跑。

```bash
"$PY" -B tools/validate_bpi_sram.py \
  --build-dir output/evidence/bpi-sram-supervisor/build-009 \
  --output-dir output/evidence/bpi-h618-recovery-network/N2-m4zero-0845-20260915/post-hardware-validation
"$PY" -B -m unittest discover -s tests -p 'test_bpi_sram_sd_minimal.py' -v
```

上述 `--output-dir` 已存在，重跑時必須用新的證據目錄，不能覆寫。`post-hardware-validation/validation-report.json` SHA-256 為 `4721ee072771f71ea98303bd73804f8648a22e6a5af22df1d03bd61e6c1831b4`；獨立媒體工具報告 `post-hardware-sd-minimal-tests.json` 為 `03b4ac52c2b761008afdc96df320c5f91e6a88169f8574947b2e9e943fd54b21`。離線驗證器的未實板驗證字樣只描述該次命令不操作硬體，不能取代另存的本輪實板報告。

## 限制與交接

1. 樣本僅一片板、一張卡、固定 1536 B 封包及兩次插座斷電重啟，沒有長時間或大量樣本證據。
2. `board=06180001` 是 ABI 標籤，不是 HW_ID；沒有驗證自動辨識 M4 Zero／EMAC／Berry。
3. `ddr=off` 是固定程式宣告，結合來源可確認本路徑沒有 DDR 初始化；未量測 DDR 控制器或電氣狀態，更沒有證明 792 MHz 穩定。
4. 插座回讀及救援重入不等於量測所有電源軌已歸零；UART 或其他線路是否造成回灌未作電氣量測。
5. 首次 UART 前導一個 NUL 原樣保留，之後取得完整 `ready`；未把 NUL 當成成功證據，也未斷言其來源。控制追蹤不包含 `sx` 直接傳輸的封包本體。
6. 首次錄製報告的 `power_control_performed=false` 僅指錄製程序未控制電源；整輪實際操作以另存的具名電源證據及整合報告為準。
7. 未回插主機重讀開機後的保留區／整卡；未在實卡執行 `restore`。原部署回讀證據不能冒充開機後再次核對。
8. 沒有 Linux、完整 DDR SPL2、寫卡更新器、eMMC／PXE、多槽更新交易或失敗程式自動復原功能。任意載入程式也沒有硬體寫入隔離保證。

最後狀態：`bpi-pw-1` 保持開啟，0845 停在 SPL1 等待命令；`ttyUSB0` 已關閉並確認沒有其他持有者。接續者先讀本報告，不重刷已部署的入口；下一階段須另行審查 DDR SPL2 與受控復原設計。
