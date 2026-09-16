# 實驗進度輔助函式

`tools/bpi_h618_lab_progress.py` 提供純 JSON 資料函式，沒有 CLI、檔案讀寫、硬體操作、排程或既有 T3 報告自動匯入。不取代主代理批次流程，也沒有建立或修改本輪實驗進度清單。

## 工作鍵

`register` 接受 components JSON 位元組及主代理固定的 SHA-256，核對靜態前提後，以以下欄位的穩定 JSON 雜湊作為工作鍵：

- `board_model`：操作人員明示的實際板型；原映像的 `artifact_board` 另存，不把普通 Zero 與 EMAC 映像混為同一宣告。
- `hardware_id`：實體板號。
- `image_raw_sha256`：取自固定 components 清單。
- `boot_sha256`：實際載入組件雜湊；必含 kernel、initrd、dtb、overlay、fixup，可加 U-Boot 等雜湊。kernel／initrd 必須與原映像相同；停用 SD host 的 RAM DTB 應提供實際修改後雜湊，不能沿用原 DTB 值。
- `test_version`：包含測試條件、引導參數及略過 fixup 等差異的明確版本；任一工作鍵欄位變更都形成新工作。

## 狀態與續作

分項為 `deployment`、`customer_kernel`、`identity`、`cpu`、`fs`、`mem`、`network`、`recovery`、`services`。未有結果一律 `pending`，不由上一分項推定通過。

主代理先解讀原始 T3 證據，再提供明確綁定的分項 JSON，包含 `work_key`、`attempt_id`、`stage`、`status`；`status` 只接受 `passed`、`failed`、`not_applicable`。服務分項另須明示 `failed_services` 清單，非空時不得填 `passed`。可在 JSON 額外保留原 T3 報告路徑、固定雜湊及觀察值。

此輔助函式只驗 JSON 位元組雜湊、工作綁定及狀態一致性，**不判讀硬體報告內容是否足以通過**。例如部署必須是完整回讀 `verified`，客戶核心須是原核心實際執行，不能把原始報告單一 `ok` 欄位或 `switch_root` 自動轉成通過。

1. `new_ledger`／`register` 建立記憶體工作資料；相同工作鍵不重複建立，來源清單更換不得悄悄覆蓋。
2. `begin_attempt` 開始新的嘗試；續作直接沿用目前嘗試，不重新呼叫。新嘗試清空目前分項，保留舊結果與失敗歷史。
3. `record_report` 接收報告位元組、固定 SHA-256 與參照路徑；錯工作鍵、錯嘗試及不同結果覆寫都拒絕。同一份報告可重送。
4. `can_skip` 僅對目前工作與嘗試中，雜湊完整且明確通過的該分項回傳 true。缺測、失敗、不適用不算可略過；它不重新讀取原證據檔，呼叫方仍須核對原證據的存續與完整性。
5. `summarize` 分別列待部署、部署已核對、客戶核心已觀察、未完成、失敗、全部通過或重試後通過。九個分項全通過且明示服務無失敗，才可能是整體通過。
6. `dumps`／`loads` 僅序列化位元組及重新核對所存報告，不落地檔案。需要保存時由主代理自行管理新證據位置、排他及原子寫入。

## 驗證

```bash
env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_bpi_h618_lab_progress.py
ruff check --no-cache tools/bpi_h618_lab_progress.py tests/test_bpi_h618_lab_progress.py
```

19 項單元測試通過，涵蓋工作鍵、變更失效、450 個純記憶體工作鍵、分項缺測、服務失敗、重試歷史、固定雜湊、重送及序列化續作。沒有掃描 450 套映像或產生實驗通過聲明。文件及程式由主代理審查後整合，本代理不提交或推送。
