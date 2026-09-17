# 首次單板核定

## 目的

`tools/bpi_lab_qualify.py` 解決「還沒測第一輪，卻先要求完整循環資格」的循環依賴。
它不放寬正式佇列資格，而使用另一份明確的首次試驗授權，沿用相同原生五階段。
首次救援系統必須已部署，UART、電源、SD、eMMC、RAM 配置及媒體覆寫授權仍須事先配對。
此工具不猜板號、不部署初始救援、不提供任意 shell 外掛、不把歷史 0845 配對套用到其他板。

## 固定輸入

- 共用後端設定 `bpi-lab-backend-v1`：可尚無 `qualification`，其他欄位全部必填。
- 工作請求 `bpi-lab-request-v1`：`mode=hardware`、`stage=preflight`，不能帶 `resume`。
- 首次授權 `bpi-lab-first-cycle-authorization-v1`：必填 `approved=true`、`record`、`scope_sha256`、`image_sha256`、`stages`、`allow_failure_recovery`、`driver_sha256`、`queue_sha256`。

`scope_sha256` 由 `bpi_lab_backend.scope_digest(config)` 計算；只排除尚未取得的 `qualification`。
`stages` 必須依序為 `preflight`、`deploy`、`boot`、`smoke`、`recovery`。
`driver_sha256` 是本工具本體摘要，`queue_sha256` 是實際載入的 `tools/bpi_lab_queue.py` 摘要；原生後端及其他相依項另由後端設定固定。
共用嚴格驗證器 `bpi_lab_evidence.py` 必須列入 backend 的 `config.dependencies` 並固定 SHA-256；缺少或摘要錯誤會在輸入檢查拒絕，不能開始首輪或 approve。沒有新增另一份首次授權欄位，也不自動補齊舊設定。
新增 queue 相依後，缺少其摘要的舊授權明確拒絕；應由操作者重新核定，不自動補欄位或升格舊證據。
每階段執行前後重新核對 queue 與 backend 相依摘要，不能在授權後偷偷替換資源隔離實作。
`request.attempt_id` 等於授權 `record`；工作鍵是 `deploy.encode` 序列化以下物件後的 SHA-256：

```json
{"scope":"設定範圍摘要","authorization":"首次授權檔摘要"}
```

這不是供直接套用的實板設定。媒體 CID、SSH 金鑰、RAM、UART 與電源身分不得以範例值補齊。
首次初始化帳號或安裝測試 SSH 金鑰仍需生命週期設定中的各別授權；不把登入失敗當成默許重設密碼。

## 操作順序

先執行純離線檢查：

```sh
python3 tools/bpi_lab_qualify.py check \
  --config /私有目錄/backend.json --config-sha256 設定摘要 \
  --authorization /私有目錄/first-auth.json --authorization-sha256 授權摘要 \
  --request /私有目錄/first-request.json --request-sha256 請求摘要
```

`check` 不建立狀態、不開 UART／SSH、不切電、不寫媒體。完整原映像及備份串流仍由真正預檢重新驗證。
上板時把子命令改為 `run` 並明示 `--confirm-hardware-test`；會依序執行五階段。
短測範圍與共用後端一致，不能把唯讀檢查宣稱為長期 DDR、GPU 或周邊壓測。

輸出在後端 `output_root/first-cycle-<record>/`，每階段分開保留請求、原生操作及階段報告。
開始硬體動作前持久化意圖並鎖定配對資源；成功報告落盤後才發布已完成狀態。
同授權不能再執行一輪，其他工作也不能越過尚未返回救援的狀態。

首次入口先使用 queue 的 `station_locks`，再使用 backend 的資源鎖，順序與正式佇列相同。
鎖鍵涵蓋站點、板號、UART、電源及媒體；即使站點名稱不同，共用 UART 或電源仍不能同時操作。
同時經 queue 的 `reservation_store` 在共用 `reservations.sqlite3` 建立持久占用；占用 owner 為原首輪 `intent.json` 的絕對路徑，work_key 為同份授權導出的工作鍵。
不建立佇列工作或已核定站點。既有其他 owner、不同工作鍵或不完整占用都拒絕，不覆蓋或刪除他人紀錄。
失敗或程序中斷後仍保留占用；只有原生完整返回救援並發布摘要後才釋放。純 `check` 與注入 runtime 的替身不碰鎖或 reservation。

失敗保留 `running`／`failed` 意圖，或最後一個已完成階段及持久占用，不自動重刷。
只有同份授權明示允許復原，才可把子命令改為 `recover`，附上相同三份固定輸入及 `--confirm-hardware-test`。
復原只執行 recovery，不重部署客戶映像；每次使用新證據目錄。復原成功不補成完整五階段成功。

部署中斷另保留 `writer_unresolved`，在未證明遠端寫入停止前禁止切電復原；即使有故障斷電授權也不能略過。重複要求復原不會清除該狀態，須先由操作者核對遠端寫入狀態。

### 階段交界與發布中斷

- 階段目錄、請求或意圖寫入失敗，不能把上一個完整完成階段假裝成未完成部署；`recover` 重驗上一階段操作、報告、boot_id 與所在系統後接手，不再執行 deploy。
- 完整部署收據已發布、但下一階段尚未開始時，可以在重驗收據後正常返回救援；deploy 的 `running`／`failed`、尚未完成發布的 deploy pending 或未知 writer 仍拒絕。
- 占用已提交但首個意圖尚未建立時，只准同工作 `recover` 接手；不准以 `run` 重試。此時無可沿用的開機身分，仍需生命週期獨立的故障斷電授權。
- `running`／`failed` 狀態的故障復原需生命週期 `fault_poweroff` 授權；已驗證階段交界則走正常關機與身分核對，不自動擴大故障斷電權限。
- 救援已完整發布，只剩摘要或占用釋放失敗時，`recover` 重驗既有救援收據後補發布及釋放，不再操作硬體；結果仍是 `recovered`，不補成完整核定。
- 如果持久狀態檔遺失但 pending 仍存在，不推定它是全新工作或已停止 writer；保持隔離，須人工核對。

### 期限與替身邊界

`runtime` 只以 `is not None` 決定是否注入；即使替身的 `__bool__` 回傳假，也必須使用該替身，不能回落至原生硬體入口。格式錯誤的假值替身只會失敗，不建立原生 runtime。

總期限涵蓋取得隔離、保存操作、階段發布及最終發布；每次發布完成後重新核對時間。
首輪目錄先保留 `publication.pending`，最終摘要的 `publication` 參照綁定 `publication.json` 完成憑證。
摘要、憑證及 pending 清除後都檢查期限，全部完成前持續保有 reservation；逾時撤銷完成憑證並保留 pending，租約從未釋放，不依賴例外處理重新取得占用。
釋放 reservation 是最後一步；此時完成憑證、pending 清除及期限判斷都已完成。釋放後不再保存發布資料、刪除 pending 或重新判定期限，不會反向撤銷已完成的發布。
釋放本身是發布完成後的資源清理。若釋放失敗且占用仍在，只能同授權 recover 補發布及釋放；若釋放交易已提交後回傳失敗，既有完成憑證仍有效，不補回租約或把它改成發布失敗。
最終發布失敗回傳 `failed`，另保存 `publication-failed.json`。先前已落盤的 `result.json` 即使仍寫有 `review-required`，也因缺少有效完成憑證而不能核定。
approve 同時要求完成憑證綁定摘要、路徑屬於原首輪，且沒有 pending 或發布失敗標記。已完整返回救援者只允許後續 recover 補發布，不重刷、不再次切電，也不追認原逾時循環。

## 人工審閱與資格

完整原生五階段通過後，`result.json` 狀態為 `review-required`，不是自動啟用站點。
由操作者檢查原始 UART、媒體、開機及返回證據後另建立：

```json
{
  "schema": "bpi-lab-first-cycle-review-v1",
  "approved": true,
  "record": "由操作者指定的審閱編號",
  "candidate": {"path": "/首輪目錄/result.json", "sha256": "結果摘要"},
  "scope_sha256": "相同設定範圍摘要"
}
```

再執行：

```sh
python3 tools/bpi_lab_qualify.py approve \
  --candidate /首輪目錄/result.json --candidate-sha256 結果摘要 \
  --review /私有目錄/review.json --review-sha256 審閱摘要 \
  --output /私有目錄/qualification.json
```

工具重新核對固定設定、授權、五份原生操作及五份階段報告；合成結果、單獨復原、缺件或摘要變動均拒絕。
原生 schema/action、完整身分、SSH、媒體契約、救援預檢與 Linux 採樣語意，統一呼叫 `backend.validate_result(..., config=config, current=current)`，由 `bpi_lab_evidence.py` 驗證；qualify 不維護第二份身分、採樣、媒體或相鄰身分連續性規則。
執行時 `current` 為前一階段持久狀態；approve 逐份重讀原始操作與報告，並以已核對前一份操作的參照、boot_id 與 phase 建立 `current`。復原時重驗已完成收據本身不把它誤當成自己的前一階段。
qualify 仍負責完整五階段順序、報告鏈、全歷史 boot_id 新值、首次授權、持久占用、發布憑證與人工審閱綁定；不能只因最後一份操作通過便核定整輪。
操作必須符合各階段原生 schema；boot／recovery 另核對 `action`，不可將救援操作放入預檢欄位。
預檢重新核對原映像、備份摘要、雙媒體及零寫入狀態；部署沿用完整回讀收據驗證。短測檢查名稱不得重複。
每份階段報告保存 `operation` 參照及 `previous_report` 參照；第一階段的前一報告為 `null`。
approve 依序重新推導完整報告，核對操作摘要、短測結果及報告鏈，拒絕重複路徑與內容不一致，即使候選及人工審閱摘要已重新計算也不放行。
每份 session 的 boot_id 必須格式正確且等於 UART identity；preflight／deploy 必須同次開機、boot／smoke 必須同次開機，boot 與最後 recovery 均須是未出現過的新 boot_id。
另重用 `bpi_lab_session.validate_identity` 核對完整 nonce、root 身分、架構、核心、DT、根 UUID、控制器及 eMMC／受保護 SD；不是只看 boot_id 或 UART／SSH 成功旗標。
同次開機的根、媒體清單及 hostkey 必須連續一致。SSH 的主機、埠、使用者與測試金鑰須等於固定配置，known_hosts 內容須等於該次 UART 公鑰。
boot 與 smoke 保存原生 `linux/collection.json` 的固定摘要參照，重新執行 `bpi_lab_linux.validate` 並要求全部檢查 passed、結果完整相等；單獨 `root_cid` 或刪除任何檢查均不接受。
Native 已附 `linux_collection` 時，保留並核對它與本階段固定檔案的路徑及摘要；錯誤參照直接拒絕，不以重新計算取代它。只有未附參照時，才從固定的 boot `cycle/linux/collection.json` 或 smoke `linux/collection.json` 擷取摘要；共用驗證器仍會重讀並核對完整原始內容。
Linux 採樣另核對同次 hostkey、處理器、根裝置鏈、檔案系統與完整媒體清單，拒絕與 session 身分不一致的採樣；真實採樣時間與來源仍須結合原生執行紀錄及人工審閱。recovery 必須附有完整救援唯讀預檢，不能只設 `rescue_verified=true`。
核定的 `source_evidence` 也納入完成憑證、兩份原始 collection 與五份同次 known_hosts，後續資格載入仍會重驗其摘要。
以上語意在原生執行時也會核對，不能先產生無效首輪再等待人工發現。舊報告缺少鏈結欄位時拒絕核定，不靠補旗標追認。
產出的資格只覆蓋此配對與設定範圍，不代表全部映像通過，更不代表 ROM／SPL 原開機鏈、EMAC 或長期穩定性已驗證。
完成後仍須人工核定站點登錄；本工具不修改既有 45 個停用範本、佇列站點或工作資料，只維護自身的共用資源占用。

## 本機測試

```sh
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest discover \
  -s tests -p test_bpi_lab_qualify.py -v
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m ruff check \
  tools/bpi_lab_qualify.py tests/test_bpi_lab_qualify.py
```

測試使用有界一般檔案、暫存 reservation 資料庫與替身，覆蓋授權錯配、雙向 queue 鎖與持久隔離、五階段、重複執行拒絕、全部階段交界、pending、writer 隔離、同工作復原、摘要／釋放失敗、原生預檢格式、證據錯置、重綁摘要、boot_id 循環與報告鏈。另逐項移除身分欄位及短測檢查、破壞原始 collection，並測試假值 runtime 及各發布步驟完成後逾時。
三個真實 `os.fork`／`os._exit(88)` 回歸分別停在 pending 清除前、租約釋放前與釋放後；父程序有 30 秒等待上限。前兩者保留五筆占用，新授權雖通過輸入檢查仍不得執行任何階段，同授權 recover 不再呼叫硬體入口；最後者只留下已完整發布且可供人工核定的摘要。pending 未清除的舊首輪即使完成後續 recover，仍不能追認核定。
另驗證釋放完成後才發生例外不撤銷憑證，以及釋放耗時跨過期限時，不再執行任何決定發布完成與否的寫入或判斷。硬體入口仍為替身，未執行實板五階段。
替身入口只能產生 `test-only`，不授予硬體資格。沒有本輪實板測試成果。

2026-09-18 前輪補強回歸結果：上述 Ruff 通過；qualify 64 項通過，八組另以同一程序組成測試集，共 299 項通過。這是 Q5 硬退出修正前的紀錄，不代表目前共享工作區整合已通過。單組可使用同一 Python 的 `-B -m unittest discover -s tests -p` 搭配下列模式重跑。

| 測試模式 | 通過項數 |
| --- | ---: |
| `test_bpi_lab_qualify.py` | 64 |
| `test_bpi_lab_backend*.py` | 34 |
| `test_bpi_lab_queue.py` | 59 |
| `test_bpi_lab_station.py` | 38 |
| `test_bpi_lab_lifecycle.py` | 16 |
| `test_bpi_lab_session.py` | 12 |
| `test_bpi_lab_deploy.py` | 20 |
| `test_bpi_lab_linux.py` | 56 |

首輪 64 項中，`test_stage_request_write_failure_can_recover` 曾失敗一次；該次斷言未保留足夠原因。加入失敗結果診斷後，單跑、64 項重跑、299 項整合及該案例連續 20 次均通過，尚未再現或證實原因，不將初次失敗歸因於其他代理。

命令列 `python -B tools/bpi_lab_qualify.py --help` 亦通過；這只核對平面匯入與 CLI 載入，不會操作硬體。

目前 Q5 補強後 qualify 為 72 項通過。主代理再合併共用後端、部署、登入、生命週期、原入口及
Realtek 救援共八組，212 項通過，Ruff 通過；紀錄為
`output/evidence/bpi-multiboard-integrate-20260917/shared-backend-main-reviewed.log`。
72 項已包含在 212 項內，不重複計數；這仍不是實板首輪結果。

### Q5 硬退出修正回歸

該次 qualify 單獨回歸 68 項通過，耗時 5.810 秒；Ruff 與 CLI 載入通過。首次重跑曾因新版 backend 嚴格 API 缺少固定 config 而使原生預檢案例失敗；當時已明確傳入 `config=config`，未修改 backend 或共用證據 helper，重複規則則於下節整合移除。

同一程序另跑上述八組，共 310 項，耗時 18.392 秒，結果為 2 個失敗及 11 個錯誤；qualify 68 項均通過。其他範圍尚未通過，未修改他人檔案：

- `test_bpi_lab_backend.BackendTests.test_offline_family_recipes_are_not_execution_configs`：預期 special `bootconfig` 被呼叫一次，實際未呼叫。
- `test_bpi_lab_backend_runtimes.RuntimeIntegrationTests.test_native_five_stages_publish_and_recover_for_both_runtime_families`：`bpi-r3` 的 preflight 原生 schema 不符；`bpi-ai2n` 的 LMB 保留區輸出不完整。
- 同組 `test_special_four_boards_from_preparation_to_real_runtime` 及 `test_special_transport_or_template_drift_rejected_before_runtime`：special 測試資料未通過 LMB 保留區數量核對。`test_realtek_customer_label_runner_and_missing_vendor_sd_rescue_are_distinct` 的 `bpi-m4`／`bpi-w2` 缺少唯一 LMB 保留區數量。
- `test_bpi_lab_lifecycle.LifecycleTests` 的 `test_authorized_fault_recovery_requires_same_work`、`test_customer_boot_runs_real_uboot_protocol_and_power_order`、`test_recovery_uses_sd_config_then_ram_and_sd_preflight`：LMB 與初始保留表及已核對實收範圍不同。

整合命令如下；輸入及硬體入口仍為離線替身：

```sh
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B - <<'PY'
import unittest
patterns = ('test_bpi_lab_qualify.py', 'test_bpi_lab_backend*.py', 'test_bpi_lab_queue.py',
            'test_bpi_lab_station.py', 'test_bpi_lab_lifecycle.py', 'test_bpi_lab_session.py',
            'test_bpi_lab_deploy.py', 'test_bpi_lab_linux.py')
loader = unittest.TestLoader()
suite = unittest.TestSuite(loader.discover('tests', pattern=pattern) for pattern in patterns)
result = unittest.TextTestRunner(verbosity=1).run(suite)
raise SystemExit(not result.wasSuccessful())
PY
```

### 共用嚴格驗證器整合

移除重複語意規則後，既有 68 項先重跑通過，耗時 5.808 秒；新增 Native collection 參照保留、缺項擷取、錯配拒絕及共用 helper 摘要固定四項回歸後，共 72 項通過，耗時 5.905 秒。原生五階段與 approve 的既有測試另追蹤共用 API，確認每份都傳入固定 config 與前一份原始操作。全歷史 boot_id、逐項身分／短測負例、Q5 三個真實硬退出及發布期限測試均保留通過。

同一指定 Python 的 Ruff 與 CLI 載入亦通過。另執行下列五項 backend 共用規則測試，全數通過，耗時 0.110 秒；未重跑前節 310 項整合，不推定其外部失敗已修復：

```sh
env PYTHONPATH=tests output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest \
  test_bpi_lab_backend.BackendTests.test_strict_boot_rejects_forged_identity_action_and_simulation \
  test_bpi_lab_backend.BackendTests.test_strict_smoke_requires_full_collection_and_unique_checks \
  test_bpi_lab_backend.BackendTests.test_resume_cannot_return_customer_when_next_stage_needs_rescue \
  test_bpi_lab_backend.BackendTests.test_receipt_cannot_only_agree_with_its_own_wrong_contract \
  test_bpi_lab_backend.BackendTests.test_all_five_stages_publish_bound_reports
```

供主代理轉告 Franklin 的整合界面：共用語意唯一來源為 `bpi_lab_evidence.py`，qualify 透過 backend 驗證 API 使用既有相依摘要固定機制；此輪未修改 backend、共用 helper、佇列或其他代理檔案，未操作硬體及原證據。
