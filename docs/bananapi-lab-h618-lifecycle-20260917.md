# 0845 生命週期與持久隔離

`tools/bpi_lab_h618_lifecycle.py` 延續既有有界冷循環、客戶 boot 與救援原型。C2 已將它接入五階段軟體契約，但未操作硬體，也不以舊十套或合成測試宣稱新實測。

## API

`cold_cycle()`、`boot_customer()`、`recover_normal()`、`recover_fault()` 仍委派 `run()`，動作為 `cold-cycle`、`boot-customer`、`recover-normal`、`recover-fault`。

共同參數維持 `config_path`、`config_sha256`、`session_id`、`output`；故障返回另需 `fault_evidence={path, sha256}`。獨立 v1 仍止於登入提示或 UART 核對的 RAM 救援，不宣稱 SSH 或整機成功。

stage 新增使用以下參數：

| 參數 | 契約 |
| --- | --- |
| `components_reference` | 本次映像的固定原配組件 |
| `receipt_reference` | 同次 deploy 的正式收據；客戶 boot 不得缺少 |
| `binding` | 工作鍵、attempt、站點、硬體、映像、設定、測試版本、模式的完整綁定 |
| `guard` | `resource_lock()` context 回傳的活躍鎖作用域；核對設定、鎖根與綁定，離開 context 後不可再用 |
| `establish` | `establish(console, mode, output, deadline, at_login)`；在同次開啟的 UART 執行登入與嚴格 SSH，回傳綁定須一致 |
| `deadline` | stage 的絕對期限；lifecycle 只能縮短，不能重開計時 |

`session_id` 由完整工作綁定計算，不只取映像摘要。`load_config(path, sha256, *, components_reference=None, receipt_reference=None)` 可離線讀取 v2 板級模板；沒有動態組件時回傳的 components／receipt 為 `None`，不操作裝置。

## 板級設定

`bpi-lab-h618-lifecycle-v2` 移除 v1 的固定 `components`、`deploy_receipt`，由同次 stage 提供，避免與未來收據形成循環摘要或借用舊收據。

| 欄位 | 規則 |
| --- | --- |
| `schema`、`hardware_id` | `bpi-lab-h618-lifecycle-v2`、`bpi-m4zero-0845` |
| `station_id` | 與 stage 一致的核定識別符 |
| `power` | `{"name":"bpi-pw-1","ip":"192.168.50.245","mac":"EC:B9:31:24:F7:D1"}` |
| `uart` | 明示 `/dev/serial/by-id/…` 或 `/dev/serial/by-path/…` 的 `stable_path`，`device=/dev/ttyUSB0`、`baud=115200` |
| `pairing` | `approved=true`、核定 `record`，完全一致的 `hardware_id`、`uart`、`power`、既有 `customer.EXPECTED` 的 `emmc`、`customer.PROTECTED_SD` 的 `sd` |
| `authorization` | 核定 `record` 與三個獨立布林值：`normal_shutdown`、`fault_poweroff`、`customer_boot_may_write_emmc` |
| `bridge`、`rescue_inputs` | `{path, sha256}`；bridge 另核對 `BRIDGE_SHA`，救援清單恰有四個固定組件及 SD 前綴 |
| `rescue_identity_sha256` | 核定救援身分檔摘要，與 stage 一致 |
| `dependencies` | 完整 `DEPENDENCIES` 檔名與摘要 |
| `timeout_seconds`、`off_seconds` | 60 至 1800 秒、10 至 60 秒整數 |
| `ssh_policy` | `{"hostkey_source":"same-session-uart","strict_host_key_checking":true,"reuse_previous_session":false}` |

UART 開啟前後核對穩定路徑、`ttyUSB0` 及描述符裝置編號，不猜測配對或改用 `ttyUSB1`。電源每次命令均核對名稱、IP、MAC、狀態回讀。既有 `bpi-pw` CLI 與資產設定仍須外部核定，工具不讀取或保存其認證及原始診斷。

## 執行界線

正常關機前即時核對 Linux 身分及沒有壓測，等待完整 `reboot: Power down`，再確認斷電、至少十秒間隔、上電、SRAM ready，才進 A1／ABI3 交握。客戶 boot 是一次性原配核心／initrd／DTB 引導，不是原始開機鏈驗證。

每一步先保存 `event-NNN.json` 意圖，完成再存核對。客戶 Linux 可能寫入 eMMC，所以 `customer_boot_may_write_emmc` 必須獨立授權；服務遮罩不代表防寫。工具不執行媒體燒錄、擦除或持久 U-Boot 環境命令。

stage callback 完成後可回報 `system_verified`、`strict_ssh_verified`、`network_verified`、`recovery_verified`，但只描述有限的本次執行。lifecycle 的 `hardware_validated`、`whole_adapter_ready`、`original_boot_chain_verified` 仍為 false；獨立 v1 不因新增 callback 而自動升級。

`uart.bin` 是私有原始 RX；本機 TX 不記錄，但遠端自行回顯的秘密仍可能保留，不可公開。錯誤摘要不輸出例外原文。`boot-trace.json` 只保存受控命令及布林結果，不複製任意遠端文字。

## 鎖、發布與中斷

使用 `/var/tmp/bpi-lab-locks` 的站點、硬體、資源鎖；父佇列持鎖時須核對實際鎖及同次 SQLite 租約，另有不可借用的執行鎖。獨立 API 若看見未完成 stage、stage pending 或其他佇列租約，不能繞過。

`h618-0845-lifecycle-state.json` 在動作前保存 `running`；客戶成功為 `customer`，確認救援後為 `rescue`，失敗為 `failed` 並固定報告摘要。沒有已完成返回證據時，不允許換映像。

最終發布前建立 `h618-0845-lifecycle-publication.pending`。報告、摘要、狀態、最後期限全部確認後才移除，且移除是最後發布操作，不再同步刪除。只要 pending 存在，即使可見 `rescue` 也不得放行；同步失敗、報告保存失敗、逾時或連 failure 都存不下，仍須保持隔離。

`reconcile_interrupted(config, config_sha256, binding, guard, output, deadline)` 只供已取得排他的 stage 恢復：要求同次真實 `running`、沒有報告、沒有 pending，並另行核定 `fault_poweroff=true`。新報告明示 `hardware_state_unknown=true`、`hardware_access_started=false`、`error_code=interrupted`，不覆寫原證據或假稱完成。之後才由 `recover_fault()` 以固定中斷證據完整返回。

未知發布結果不適用自動中斷分類。故障返回也不代表斷電後檔案系統完整性驗證；deploy 中斷有更保守的人工核對邊界，見[五階段適配器](bananapi-lab-h618-adapter-20260917.md)。

## 離線回歸

保留既有 58 項 lifecycle 回歸，新增真實本機父佇列借鎖、跨 attempt／摘要拒絕、持久租約、過期作用域與 stage 中斷接線。真實模式不接受注入 runtime；獨立模擬須 `simulated=True`、替身及獨立暫存鎖根，禁止 NativeRuntime。

本輪測試不驗證實體 UART、電源、Linux、Wi-Fi 或 SSH 登入。測試及 Ruff 命令見[登入模組文件](bananapi-lab-h618-session-20260917.md)，仍需核定實板完整循環。
