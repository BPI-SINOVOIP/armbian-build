# 0845 五階段適配器

`tools/bpi_lab_h618.py` 已接入五階段軟體契約。本輪只做離線回歸，沒有操作 UART、SSH、電源或媒體，沒有提交或推送。既有十套原型只供流程參考，不重新計為本適配器實測。

## 階段與綁定

標準輸入維持 `bpi-lab-request-v1`，標準輸出維持單一 `bpi-lab-stage-v1`。共用 queue／station 僅擴充下述救援旗標與中斷處理；不修改 prepare、platforms 或計畫文件。

| 階段 | 必要核對與完成界線 |
| --- | --- |
| `preflight` | 從已登入 RAM 救援開始；固定備份、媒體、救援身分及 SD 前綴，本次 UART 取得 hostkey、路由、boot_id，嚴格 SSH 核對；不切電、不燒錄 |
| `deploy` | 只能接同次成功 preflight；重驗救援及 boot_id，呼叫既有部署工具核對來源、完整寫入範圍回讀、SD 前後比較，保存同次正式收據 |
| `boot` | 只用同次 deploy 所指向且摘要相符的 `receipt.json`；正常關機、冷循環、SRAM／A1 引導、首次登入、UART／SSH 同 boot_id 與 Linux 根身分驗證；只有 login 提示不算通過 |
| `smoke` | 同次成功 boot；重新經 UART 綁定 SSH，執行原配核心／根媒體、CPU、暫存檔完整回讀、失敗服務檢查，之後再驗同 boot_id |
| `recovery` | 依同次租約及 lifecycle 狀態選正常關機、另行核定的故障返回或既有救援唯讀核對；RAM 根、核定救援、媒體未使用、SD 前綴、新 UART／SSH 綁定均須通過 |

所有階段完整綁定 `work_key`、`attempt_id`、`station_id`、`hardware_id`、`image_sha256`、`boot_config_sha256`、`test_version`、`mode`。已完成的 attempt 不能重新開始，須建立新 attempt；不得跨嘗試引用部署或 boot 證據。

`hardware_validated=true` 是既有 station 契約的單階段真實執行宣告，不是資格文件。`whole_adapter_ready=false`、`hardware_qualification=awaiting_hardware`、`original_boot_chain_verified=false` 保留。單元測試替身即使通過同一契約，也不能作實板資格。

## 私有設定

執行使用 `bpi-lab-h618-v2`；`v1` 僅能離線檢查，不能沿用靜態 `rescue_ssh`／`customer_ssh` 執行。設定 SHA-256 必須等於工作 `boot_config_sha256`，變更設定須重新建立工作及授權／資格綁定。

| 欄位 | 要求 |
| --- | --- |
| `schema` | `bpi-lab-h618-v2` |
| `station_id`、`hardware_id`、`test_version` | 與請求一致；只接受 `bpi-m4zero-0845` |
| `dependencies` | 本模組 `DEPENDENCIES` 的完整檔名與核定摘要，包含 lifecycle、session、network、linux |
| `backup` | 可信備份清單 `{path, sha256}`；同目錄保留完整 gzip 備份，執行時重驗 |
| `authorization` | `userarea_write=true`、相同 `hardware_id`、`media_identity=cid:<核定CID>`、人工授權 `record` |
| `sd_prefix`、`rescue_identity_sha256` | `{bytes:4194304, sha256:<核定摘要>}` 與核定救援身分檔摘要 |
| `lifecycle` | 板級 `bpi-lab-h618-lifecycle-v2` 的 `{path, sha256}`，見生命週期文件 |
| `session` | `bpi-lab-h618-session-v1` 的 `{path, sha256}`，見登入文件 |
| `components` | XZ SHA-256 對應原配組件 `{path, sha256}`，限 `bpi-m4z-emac`，最多十套；不固定舊部署收據 |
| `output_root` | 已存在、目前使用者私有的絕對目錄，不得含祖先符號連結 |
| `timeout_seconds` | 60 至 86400 秒，包含前置設定核對；外層站點期限另需涵蓋必要清理 |

板級設定與 stage 的站點、救援身分、SD 前綴須一致。秘密只經明示私有檔案來源取得，不提交 Git、不放進設定值或命令參數。原型假摘要與測試假授權不可搬入實際站點。

以下只讀本機設定及固定參照，不開 UART、不連 SSH、不呼叫電源，也不讀憑證內容：

```sh
python3 -B tools/bpi_lab_h618.py \
  --config /absolute/private/h618.json \
  --config-sha256 <設定摘要> --inspect-config
```

stage 從標準輸入接受完整請求。`external-v1` 會將可執行入口做成 memfd 快照，部署時應使用經核定且摘要固定的薄包裝入口，以 `exec` 呼叫絕對路徑的 Python 與原始 H618 模組。不要把需匯入相鄰模組的 `.py` 直接當作 memfd 腳本；包裝入口也不可另起常駐程序，否則直接父程序借鎖核對會拒絕。

## 持久隔離

所有 stage 使用 `/var/tmp/bpi-lab-locks` 排他，包含資料操作。父佇列持鎖時核對 `/proc/locks` 的直接父 PID、inode、完整排他範圍，再核對 `reservations.sqlite3` 及原佇列資料庫的工作鍵、attempt、站點、執行狀態與身分摘要。不提供布林或環境旗標略過鎖。

額外 `h618:execution` 鎖永遠不可借用，阻止同父程序同時啟動兩個 H618 子工作。`flock` 因程序結束釋放後，其他工作的持久租約仍會阻擋操作。lifecycle 的獨立 API 也不能繞過未完成 stage 狀態。

stage 狀態保存在 `h618-0845-stage-state.json`。成功發布前先建立 `h618-0845-stage-publication.pending`，完成報告、摘要、狀態與期限檢查後才移除；移除是最後發布操作，刻意不再同步刪除。崩潰至多讓舊阻擋重現，不會放行未知結果。

任一 stage／lifecycle pending 存在時，即使可見狀態是 `rescue`，也不能開始新工作、資料階段或自動恢復，須人工核對。不可只刪標記、換目錄或借舊報告解鎖。`failure.json` 與成功報告並存時，以失敗與持久隔離為準；摘要只留 `error_code`，不輸出例外原文。

## 續作與中斷

`resume` 沿用 preflight 請求的 `{next_stage, previous_reports}`，只允許目前租約支持的成功邊界。重驗每份佇列報告摘要及全部綁定，且內容必須與適配器保存的同次報告一致。

- 接 `deploy`：重驗 RAM 救援、媒體、備份及同 boot_id。
- 接 `boot`：除上述核對，再以唯讀排他描述符完整回讀已部署範圍並比對 raw SHA-256，不重寫映像。
- 接 `smoke`／`recovery`：重驗目前客戶核心、根 UUID、CID、控制器、容量、DT、先前 boot_id，並重新 UART／SSH 綁定。
- 可捕捉錯誤及 `KeyboardInterrupt` 保存失敗，不重送密碼、不重試部署、不把正常關機失敗默默改為故障斷電。
- lifecycle 只剩同次 `running`、沒有報告且沒有 pending 時，可在重新取得排他與另行核定 `fault_poweroff` 後保存「硬體現況未知」中斷證據，再完整故障返回；不假稱原動作已完成或已確定開始硬體操作。
- deploy 寫入中被不可捕捉終止，遠端是否停止仍未知：保持隔離、須人工核對，不直接續寫。救援唯讀核對另盤點 MMC 可寫描述符，避免把仍在寫入的媒體視為靜止救援。

### 佇列預檢失敗與救援

原先 queue 不分原因直接結束 preflight 失敗工作，會遺留 H618 持久租約，且終態工作不能再使用 queue `recover`。目前以選填 `needs_recovery: bool` 明示是否需要同次救援，H618 與通用後端共用此欄位定義：

- 純本機設定、摘要、授權或配對核對遭拒，尚未建立本次 adapter 狀態、未開始操作：`needs_recovery=false`，不因 preflight 阻擋而切電或執行 recovery。
- 已建立本次尚未完成的 adapter 狀態，或本次 operation 開始後失敗：`needs_recovery=true`。即使 UART 尚未開啟，已落盤的狀態仍須同工作救援後才能釋放；狀態替換後同步失敗也不能當成未開始。
- 既有 stage／lifecycle pending 會在讀取狀態前就保留 `needs_recovery=true`，不因狀態讀取遭拒而退回純本機阻擋。queue 會嘗試同次 recovery 並在仍受 pending 阻擋時保留隔離與租約；旗標不允許讀取標記的連結目標、刪除標記、跨工作接續或切電中止未知 writer。同次尚未結束的 lifecycle operation 也保留救援需求。
- 所有成功報告為 `needs_recovery=false`。station 拒絕非布林值及 `passed` 搭配 `true`；失敗／阻擋可依是否已開始回報 `true` 或 `false`。旗標不代表實板資格，也不授予額外切電權限。
- 未提供旗標的舊 adapter 保留一般 preflight 失敗不自動救援的既有行為；其餘階段不因 `false` 而略過原本必要的救援。resume preflight 失敗可能涉及先前已開始的工作，無論旗標缺省或 `false`，仍回救援，不執行後續 deploy／boot。

queue 在發布收據前將救援決策及失敗分支持久化，並綁定同一 `work_key`／`attempt_id`。收據發布失敗、收據入庫前中斷或資料庫重開後，`resume` 只接續 recovery，不會把工作誤算成五階段通過。明示 `recover` 的成功收據已落盤但尚未入庫時，`resume` 或再次 `recover` 都先核對同次 recovery 意圖與收據，完成入庫後只提交終態，不重做救援。錯誤 attempt 或不可信收據保持隔離；此對帳不允許重跑未知 deploy。新的 attempt 不繼承舊救援旗標。

station 的 `StageExecutionError` 攜帶 `report` 與 `adapter_started`；`StagePublicationError` 繼承此介面並保留原本兩參數呼叫方式。只有程序確實啟動後才設定 `adapter_started=true`；`report` 僅保留已驗證回報，未驗證內容為 `None`。例如 stdout 寫入失敗、逾時、非零退出或無法驗證回應時，已啟動但結果未知不能當成純前檢失敗，queue 必須持久保存同次救援需求，救援無法通過則保留隔離與租約。此規則也適用於尚未取得旗標的舊 adapter，不推測原始 stdout 是成功證據。

已驗證回報則維持原有旗標契約：queue 重驗本次綁定後只用例外附帶回報決定救援，不作成功收據。明示新旗標的 adapter 即使回報成功，若 station 或 queue 的發布失敗，仍須返回救援；合法 `blocked/false`、合法缺省旗標的舊回報，以及未啟動程序的純前檢失敗，不因新增啟動資訊而觸發 recovery。H618 連 `failure.json` 都無法保存時仍回傳失敗與救援旗標，保留原 pending 隔離。

救援成功後保留原失敗／阻擋結果並釋放 queue 資源；救援失敗則保留跨資料庫持久占用並隔離站點，之後由 queue `recover` 以同次綁定再核對，不必繞過 queue 操作 adapter。既有 stage／lifecycle lost-publication 或遠端部署寫入狀態未知，仍保持隔離，不因這個旗標自動清除或強行切電。

## 尚缺驗收

仍缺核定實體配對、外部憑證、實板單映像完整循環與故障／中斷返回驗收。RCU／LZMA 間歇失敗、Bookworm 服務失敗及 minimal 空間限制保留；不調小測試檔、不關閉失敗服務。客戶 Linux 可能寫入 eMMC，不能冒稱防寫；其他板不繼承 0845 的媒體、電源或 SRAM 位址。
