# 0845 有界生命週期 runtime

日期：2026-09-17。對應[跨板計畫 B4](bananapi-multiboard-lab-plan-20260917.md)；計畫提交 `30d711a8d` 已由主代理推送。本交付只有新工具、測試及本文件，未修改 `bpi_lab_h618.py`、平台設定或既有證據，未提交或推送。

## 本輪範圍

已將五份指定原型的電源、冷循環及引導流程整理為正式 Python API。執行時匯入既有 `bpi_h618_customer_boot`、`bpi_h618_rescue_boot`、`bpi_lab_console` 與串口實作，**不匯入或執行 ignored 原型**。

本輪沒有操作 UART、SSH、電源或媒體，沒有讀取密碼、Wi-Fi 認證或私鑰。所有新增測試均使用替身；測試通過只代表程式回歸，不是硬體通過。

本工具仍是**獨立 runtime**，舊 stage 的 `boot`／`recovery`／`resume` 沒有接入，也沒有新增 CLI。不得因此啟用完整自動佇列。

## 可呼叫 API

模組：`tools/bpi_lab_h618_lifecycle.py`。四個入口皆接受相同的關鍵字參數，委派給 `run()`：

| API | 起始條件 | 完成界線 |
| --- | --- | --- |
| `cold_cycle()` | 已登入、可執行命令的 RAM 救援 shell | 核對救援、關機、斷電至少十秒、SRAM／A1 引導、再次核對 RAM 根與 SD 前綴 |
| `boot_customer()` | 同上，且固定部署收據與原配組件相符 | 一次性啟動客戶核心，觀察核心版號及登入提示；不登入 |
| `recover_normal()` | 已登入的客戶 root shell | 即時核對核心、根 UUID、CID、控制器及無壓測；正常關機後回 RAM 救援 |
| `recover_fault()` | 當前同一工作已有持久化、固定摘要的失敗報告，另有故障斷電授權 | 不假稱已正常關機；受控斷電後回 RAM 救援，包括前次已斷電但上電失敗的情形 |

共同參數為 `config_path`、`config_sha256`、`session_id`、`output`。故障入口另須 `fault_evidence={"path": ..., "sha256": ...}`。每次 `output` 都須為不存在的絕對路徑，其父目錄須先存在且不得含符號連結。同一工作引導與返回使用同一 `session_id` 及完全相同的固定設定；更換設定、程式或輸入不是原工作的續作。

以下只示範 API，**本輪未執行**；不得在未取得實板授權時照抄執行：

```python
from tools.bpi_lab_h618_lifecycle import boot_customer

report = boot_customer(
    config_path=approved_config_path,
    config_sha256=approved_config_sha256,
    session_id=work_session_id,
    output=new_private_output_directory,
)
```

`run(action=...)` 的動作值依序為 `cold-cycle`、`boot-customer`、`recover-normal`、`recover-fault`。`load_config(path, sha256)` 可離線檢查設定、固定組件及部署收據，不開啟裝置；完整救援清單格式檢查在 `run()` 建立私有副本後、取得硬體入口前執行。

## 設定契約

設定採固定 SHA-256 的 JSON，拒絕未知欄位、重複欄位、符號連結、裝置檔與超界檔案。這是受信本機程式的輸入守門，不是執行沙箱或簽章認證系統。

| 欄位 | 必要值或格式 |
| --- | --- |
| `schema`、`hardware_id` | `bpi-lab-h618-lifecycle-v1`、`bpi-m4zero-0845` |
| `station_id` | ASCII 識別符，最多 128 字元 |
| `power` | `{"name":"bpi-pw-1","ip":"192.168.50.245","mac":"EC:B9:31:24:F7:D1"}` |
| `uart` | `stable_path` 須為明示的 `/dev/serial/by-id/…` 或 `/dev/serial/by-path/…`；`device` 固定 `/dev/ttyUSB0`，`baud` 固定 `115200` |
| `pairing` | `approved:true`、`record` 核定紀錄識別符，以及與本設定完全一致的 `hardware_id`、`uart`、`power`；`emmc` 必須等於既有 `customer.EXPECTED`，`sd` 必須等於 `customer.PROTECTED_SD` |
| `authorization` | `record` 識別符及三個獨立布林值：`normal_shutdown`、`fault_poweroff`、`customer_boot_may_write_emmc` |
| `bridge`、`rescue_inputs`、`components`、`deploy_receipt` | 每項皆須 `{"path":絕對路徑,"sha256":外部核定摘要}`，一般清單上限 64 KiB；bridge 上限 256 KiB，另須符合既有 `BRIDGE_SHA`；部署收據檔名必須為 `receipt.json` |
| `rescue_identity_sha256` | `/etc/bpi-rescue.json` 的核定摘要，回救援時重新經 UART 核對 |
| `dependencies` | 以模組 `DEPENDENCIES` 的所有檔名為鍵、實際核定 SHA-256 為值，不得缺少或新增項目 |
| `timeout_seconds`、`off_seconds` | 分別為 `60..1800`、`10..60` 秒整數 |
| `ssh_policy` | `{"hostkey_source":"same-session-uart","strict_host_key_checking":true,"reuse_previous_session":false}`；本版只約束後續接入，並沒有執行 SSH |

UART 在開啟前、開啟後都核對穩定路徑確實對應 `ttyUSB0`，並核對開啟描述符的裝置編號；對應 `ttyUSB1` 立即拒絕，不自動搜尋或重新配對。電源每次命令回覆均須包含核定名稱、IP、MAC、身分核對及期望狀態；初始 `status` 不符時，不送 UART 關機或電源切換命令。

本工具呼叫環境中既有 `bpi-pw --device bpi-pw-1 …`，不讀取其認證設定，也不保存其原始 stdout／stderr；部署環境仍須另外核定並保護這個 CLI 及其資產設定。配對紀錄識別符不代替實際接線驗證或操作者授權。

## 期限、鎖與失敗

期限從 `run()` 的前置核對開始計算，電源串流、UART 讀寫、shell、交握、斷電間隔及最終成功發布共用同一截止時間。完成事件、發布標記、成功報告、報告摘要與最終狀態落盤前後都檢查期限，不能只在開始保存結果之前檢查一次。電源單次最多 60 秒，輸出每流最多 64 KiB；超界即終止並回收子程序。原救援的破碎盤點重試在本工具中改為立即停止，不另增加重試等待。逾時不自動重啟、不自動斷電。

本機檔案同步仍受作業系統排程影響，無法強制在截止瞬間中止；同步返回後若已逾時，不得發布成功或放行下一個工作。失敗證據仍會持久保存，這部分及必要的清理可能超出期限，但不會因此將失敗改為成功；既有子程序終止／回收最多另等五秒。遠端唯讀命令逾時不代表遠端程序已被殺死；保留未知狀態，不繼續下一個硬體動作。

實際資源鎖放在 `/var/tmp/bpi-lab-locks`，使用非等待式 `flock`。`hardware:bpi-m4zero-0845` 鍵與既有佇列相同，因此換站名、換輸出目錄仍不能同時操作同一板。鎖目錄必須由目前使用者擁有且為私有，鎖檔拒絕符號連結、非一般檔案及硬連結。不同作業系統使用者間仍需站點管理者統一帳號／權限。

**父佇列已持有該鎖時不可直接重入本 API。** 主代理後續須整合鎖的擁有權與排程，不得以取消鎖或新增繞過開關解決。本工具另有 `h618-0845-lifecycle-state.json`；舊佇列目前不理解這份持久狀態，故尚不能與舊 stage 混跑。

每個硬體步驟先以 `event-NNN.json` 保存意圖並 `fsync`，完成後另保存核對結果。每次開始前持久狀態先標記 `running`；客戶登入提示完成後保留為 `customer`，只有核對 RAM 救援完成才成為 `rescue`。失敗保留 `failed` 及正式報告摘要，阻擋下一套映像。

最終發布前，先持久建立 `h618-0845-lifecycle-publication.pending`。讀取站點狀態時優先檢查這個標記；只要它存在，即使狀態檔已可見 `rescue`，也一律拒絕新 session 及續作，不讀取標記可能指向的符號連結。這個阻擋不依賴 `failure.json` 能否成功保存。

只有報告、摘要及最終狀態全部成功持久化，且成功發布的最後期限檢查通過後，才移除阻擋標記。移除是最後一個發布操作，**刻意不再同步該刪除**，避免重現「已移除阻擋，但後續目錄同步才失敗」的缺口；程序／主機崩潰至多讓舊阻擋標記重現，須人工核對，不會把不確定狀態放行。已確定持久化的 `failed` 狀態仍阻擋其他工作，只允許另外核定的同工作故障返回。

一般例外與 `KeyboardInterrupt` 會保存失敗。程序遭不可捕捉的終止、磁碟失效或最後狀態落盤失敗時，可能只剩 `running`，也可能是可見的 `rescue` 加上未解除的發布標記；兩者都須人工核對，不能只用狀態字串或舊收據自動續跑。沒有提供自動解鎖或覆蓋既有輸出的 API。

故障返回只接受當前持久狀態指向的同 `session_id`、設定摘要及模擬模式的已結束失敗報告。舊報告、其他板、其他工作、未知中斷或一般客戶成功報告，都不能授權斷電。故障返回完成後不宣稱檔案系統完整性已驗證。

## 報告解讀

- `ok:true` 只表示該 API 的有限目標完成；客戶結果為 `login_observed`，救援結果為 `ram_rescue_verified`。
- `hardware_validated`、`whole_adapter_ready`、`system_verified`、`strict_ssh_verified`、`network_verified`、`recovery_verified` 均保持 `false`，不將 UART 可用冒稱完整網路救援成功。
- `report.json` 是單次操作紀錄；`failure.json` 表示前置拒絕或更晚的持久化失敗。若兩者並存，以失敗為準；發布標記未解除也必須視為阻擋。呼叫者還須檢查回傳值及完整持久狀態，不能只看報告存在或 `status:rescue`。
- `boot-trace.json` 保存既有工具產生的命令及布林結果，不複製任意遠端文字。實際執行的 `uart.bin` 由共用 console 保存為私有原始 RX；它可能含遠端自行輸出的敏感資料，不可公開。
- 不輸出例外原文、電源原始診斷或設定內容，只留下 `error_code` 與 `last_step`；不讀取、不產生或傳送 secret。

## 限制與後續

1. 未實作 UART 首次登入、密碼初始化、測試公鑰部署、救援 Wi-Fi 認證注入或新 SSH hostkey 綁定。沒有 secret provider，也不借用舊 SSH 身分。下一階段若接入 SSH，須以本次 UART 取得公鑰，建立本次私有 known_hosts，強制嚴格核對並用同一總期限驗證；現在不能宣稱全 ready。
2. 正常客戶返回要求外部已登入 root shell。客戶 boot 完成後不能直接自動接 normal recovery；尚缺上述登入接線。故障斷電也不能被用來繞過正常登入缺口，仍須真實失敗及另外核定。
3. 保留既有 A1／ABI3 槽 3、FIT LBA 2048、限定原配組件與 RAM DTB 變更；未驗證客戶原開機鏈、原生 SD、桌面登入或全系統功能。
4. bridge 與所有輸入清單以 SHA-256 固定，組件沿用既有 U-Boot 長度／CRC32 核對；不是新增的遠端逐組件 SHA-256 認證。SD 只核對受保護的前 4 MiB，不代表整片 SD 已比對。
5. 工具不送媒體燒錄／擦除或持久 U-Boot 環境命令。**未來實際啟動客戶 Linux 仍可能寫入 eMMC 根系統**，因此另須 `customer_boot_may_write_emmc:true`；不得把本輪未操作硬體誤寫為 runtime 的硬體防寫保證。
6. 未實作一般 `resume`：不能根據部分 boot 日誌接續 SRAM／U-Boot，也不能接續未結束的 `running` 工作。`recover-fault()` 是有新授權及固定失敗證據的完整救援嘗試，不是中途續作或自動解鎖。

## 離線驗證

指定解譯器：`output/evidence/bpi-sram-supervisor/model-venv/bin/python`。新增測試禁止真實 `open_serial` 與 `subprocess.Popen`，使用假的 UART／電源與暫存私有鎖；同時直接執行既有 customer boot／rescue boot 函式，檢查真正接線而非只 mock 其成功結果。

```sh
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest discover -s tests -p test_bpi_lab_h618_lifecycle.py
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m ruff check --no-cache tools/bpi_lab_h618_lifecycle.py tests/test_bpi_lab_h618_lifecycle.py
env PYTHONPATH=tests output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest test_bpi_lab_h618_lifecycle test_bpi_lab_h618 test_bpi_h618_customer_boot test_bpi_h618_rescue_boot test_bpi_lab_console test_bpi_h618_emmc_deploy
```

替身注入僅接受 `simulated=True`，且必須提供獨立 `runtime.lock_root`；真實模式拒絕注入 runtime，模擬拒絕使用實際鎖根目錄。這是程式驗證入口，不是可提交硬體驗收的模式。

限定修正後結果：新增工具的 58 項測試及上述既有相關回歸合計 186 項通過，Ruff 通過。覆蓋電源／UART 錯配、固定輸入變動、正常關機與故障斷電分流、RAM 根與 SD 核對、跨站排他、故障報告過期／跨工作拒絕、共同期限、`KeyboardInterrupt`、事件／報告／最終持久狀態落盤失敗，以及直接執行既有 boot／rescue 的接線。未執行完整專案回歸，主代理後續統一整合。

## Wegener 限定修正

兩個回報均先以測試重現原程式失敗，再修正：

1. P1：真正執行 `os.replace` 發布 `rescue` 後，注入其後的目錄 `fsync` 失敗。修正後狀態檔仍可能可見 `rescue`，但持久發布標記必須讓不同 session 在任何 UART／電源操作前被拒絕；即使連 `failure.json` 都無法保存也保持阻擋。
2. P2：600 秒總期限下，在完成事件保存後注入額外 600 秒，必須回報期限失敗而非 `ok:true`／`rescue`。另測報告保存、報告摘要、發布標記及最終狀態保存各自超時，以及標記移除失敗；不確定的成功發布不得放行下一個 session。

新增 11 項限定回歸；包含正常成功可跨工作使用、標記移除後不得再有發布同步、崩潰後舊標記重現仍阻擋等反向檢查。以上僅是離線重現與修正驗證，等待 Wegener 限定複審，不宣稱已通過獨立審查。
