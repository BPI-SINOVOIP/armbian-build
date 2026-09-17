# 共用受限部署與五階段後端

本文件說明共用程式介面，不是任何板子的硬體資格、覆寫授權或啟用站點指示。本次實作及回歸沒有操作實板、改動原映像或啟用停用站點；程式及文件由主代理驗證後一同提交。

## 範圍

| 模組 | 責任 |
| --- | --- |
| `tools/bpi_lab_deploy.py` | 完整 gzip 備份與 XZ 來源重驗、RAM 救援唯讀預檢、受限 userarea 寫入及完整範圍回讀 |
| `tools/bpi_lab_session.py` | 同次 UART／SSH 的核心、架構、DT、根、雙媒體、hostkey 與 `boot_id` 交接；受控測試公鑰安裝 |
| `tools/bpi_lab_lifecycle.py` | 配對 UART 與電源冷循環、明示 autoboot 停止、固定 U-Boot 引導、首次登入與固定 SD 救援 |
| `tools/bpi_lab_backend.py` | 原配清單與家族範本核對、五階段報告、資源排他、持久意圖及續作守門 |
| `tools/bpi_lab_evidence.py` | 重播完整階段證據、UART 與 Linux 採樣交叉核對、外部部署契約綁定 |
| `tools/bpi_lab_realtek_rescue.py` | 同份 Realtek BSP 的固定 SD→RAM 救援來源、核定及實際 UART runner |

`bpi_h618_emmc_deploy.deploy` 新增可選 `rescue_schema` 與 `rescue_expected`。預設仍是 `bpi-h618-rescue-v1`，舊呼叫不增加請求欄位；其他 schema 必須提供 `{kernel, identity_sha256}`。若同時有 `pinned_preflight.rescue`，兩者必須完全相等。此參數不授權任何實板，也不繼承 0845 的配對或授權。

## 階段與限制

| 階段 | 實際操作與成功條件 |
| --- | --- |
| `preflight` | 在已登入的配對 RAM 救援 UART 重新建立 SSH；完整解壓核對備份與來源；驗證 eMMC CID／容量／控制器、未掛載狀態、RAM 根、固定 SD 身分與 4 MiB 前綴 |
| `deploy` | 要求同工作前檢；重新建立同次 UART／SSH 身分、重做前檢；呼叫既有受限寫入核心並核對完整來源、寫入範圍與回讀收據 |
| `boot` | 要求同工作完整部署收據；先在 RAM 救援以唯讀 `blkid` 核對根識別唯一性，LABEL 另盤點全部可見區塊媒體；正常關機、核對電源關閉、等待斷電間隔、重新啟動、停止 autoboot、逐步執行固定 U-Boot 配置；首次登入後再核對同次 UART、SSH 與 Linux |
| `smoke` | 要求同次客戶開機；前後各重新核對 UART／SSH，執行 Linux 唯讀採樣及完整預期配置比對；任何略過或失敗檢查均不通過 |
| `recovery` | 正常情況先核對來源系統並關機，再冷循環至固定 SD 救援配置；同次 UART／SSH 核對 RAM 身分，並重做受保護 SD 前綴及 eMMC 唯讀前檢 |

支援的生命週期 ABI 是 `mainline-console-v2025.01`；不猜 UART、U-Boot prompt、版本、MMC 編號、RAM、停止按鍵、電源名稱或 MAC。不使用 0845 SRAM／FEL／ROM 流程。

這是指定原配組件的受控引導，不代表 ROM／SPL／原生自動開機鏈已驗證。`original_boot_chain_verified`、`whole_backend_ready` 不因個別階段成功而自動升格。短測限 Linux 唯讀核對，不宣稱 CPU、記憶體壓力測試或周邊完整驗證。

## 固定輸入

參照統一為 `{path, sha256}`，使用無上層跳轉的絕對路徑。一般檔案與所有父路徑拒絕符號連結；資料採唯一 JSON 鍵及有限數值。所有新增私有目錄拒絕覆用既有路徑。

後端設定 `bpi-lab-backend-v1` 包含：

- `station_id`、`hardware_id`、`test_version`、`resources`、`output_root`、`timeout_seconds`。
- `dependencies`：必須恰好包含程式 `DEPENDENCIES` 列出的所有本倉模組及其 SHA-256；操作前後均核對。
- `pairing`：已配對 UART 穩定路徑與 baud、電源名稱／IP／MAC、eMMC、受保護 SD、救援身分與資源。
- `deploy`：共用部署契約參照。
- `lifecycle`：生命週期配置參照。
- `images`：以完整壓縮映像 SHA-256 索引原配 bundle。
- `qualification`：佇列必須提供的人工審閱核定參照；獨立首次核定載入介面可省略。

共用部署契約 `bpi-lab-deploy-v1` 必須明示 `hardware_id`、`expected`、`protected_sd`、`sd_prefix`、`rescue`、`backup`、`authorization`、`ssh`。兩個媒體均使用 `{cid, bytes, controller}`；禁止 CID 或控制器相同。授權使用 `{userarea_write, hardware_id, media_identity, backup_sha256, record}`，不能借用其他板子或備份。

`backup` 必須指向已發布的 `manifest.json`，備份格式沿用既有 `bpi-h618-emmc-userarea-backup` 名稱；這只是歷史格式識別。共用層除了驗證兩端紀錄與壓縮摘要，也會完整解壓 `emmc-userarea.img.gz`，核對原始容量與摘要。`.partial` 不能作成功證據。

來源檔案清單使用 `{path, compressed: {bytes, sha256}, raw: {bytes, sha256}}`。只支援完整磁區大小的普通 XZ 來源。完整來源核對與備份核對均先於遠端寫入；寫入只涵蓋 `[0, raw_size)`，不改 boot0、boot1、RPMB，不清除尾端、不自動重試。

## 原配與傳輸

映像 bundle `bpi-lab-backend-image-v1` 使用 `board`、`image`、`preparation`、`uboot_template`、`uboot`、`uboot_qualification`、`linux_expected`、`artifact_root`、`customer_ssh`、`transport`。

`preparation.json` 的 `source`、`raw`、板型、核心、實際檔案系統 UUID、`components` 與 `extraction` 參照必須與工作一致。UUID 引導仍要求 `root_uuid_verified=true`。Realtek LABEL 路徑則要求 `root_identity_verified=true`、`root_uuid_verified=false`、原配 `root_target=LABEL=BPI-ROOT` 與完整 `root_binding={method: "label", label: "BPI-ROOT", uuid: 實際檔案系統UUID, unique_in_image: true, unique_on_hardware: false}`；不得把 LABEL 冒充 UUID 核定。擷取證據還須包含完整分割標籤盤點及映像內唯一性。

LABEL 冷啟動前以固定唯讀程式盤點全部非零大小的 `/sys/class/block` 裝置，包含 USB／NVMe，逐一固定描述符核對裝置號並以 `blkid -p` 取得 UUID／LABEL；前後清單不一致、不可讀、逾時或名稱重複均拒絕。唯一 LABEL 與唯一 UUID 必須是同一個已配對 eMMC 分割，否則不關機、不操作電源、不進首次帳戶寫入。開機後 Linux 核對始終使用實際 UUID、CID、控制器及容量，不以 `root_label_scope_approved` 代替觀測。

後端不自行簡化 CMA 或保留記憶體條件，必須呼叫家族正式入口：

- Allwinner：`build_uboot_config(manifest, template=..., artifact_root=...)`。
- Amlogic：`validate_boot_config(artifact_root, template=...)`，取得回傳的 `config`。
- 共用原入口家族：`extlinux.validate_template(...)`。
- 特殊平台：`special_runtime.bootconfig(artifact_root, template=..., kernel_placement=config["execution"].get("kernel_placement", "original"))`；完整沿用家族核對及 W2 `direct-final` 限制。Realtek 原廠來源集合由 `special_runtime.vendor_files(board)` 核對，不固定沿用舊來源清單。

離線原入口證據／載入配方仍不是可執行配置。後端已接入兩個專用執行器，保留家族核對，不以 `execution_ready` 宣告代替執行資格：

- `bpi-lab-special-runtime-v1`：經 `special_runtime.validate_artifacts/validate_config` 核對完整配置，交由其 `boot` 執行。支援 F2P、F2S、AI2N、M6；AI2N 的 `opencva`、`codec` 載荷及 RAM 摘要不會因投影成三個核心載荷而遺失。
- `bpi-lab-original-entry-v1`：先核對 `extlinux.validate_template`，再經 `original_entry.build_uboot_config` 重播原始擷取、重跑家族解析及韌體資格核對，交由其 `boot` 執行原 `source`／`sysboot` 入口。實際支援範圍及拒絕條件沿用該執行器，不自行放寬 overlay、vendor-env 或安全鏈限制。

Realtek 客戶配方已接入 `execution.uboot.abi=realtek-lab-v1`，透過相同 special API 核對並將完整配置交給 `boot`，不降級成主線指令。固定 SD→RAM 救援使用 `bpi-lab-realtek-rescue-v1`，必須與客戶配置綁定同份配對、同份合併 U-Boot binary/config/map 及獨立救援核定；缺少任何一項仍在配置階段拒絕。詳見[救援入口與建置證據](bananapi-lab-realtek-rescue-20260918.md)。

H618 正式名稱 `bpi-m4z`、`bpi-m4b`、`bpi-m4z-emac` 可使用本共用後端，但須重新核對同板 Allwinner 原配組件，提供全新的主線客戶／SD RAM 引導配置、配對、備份、授權及循環核定；沒有任何 H618 實板因此自動 ready。既有 `0845`／`bpi-m4zero-0845` 板號、其 eMMC／SD CID、舊 `bpi-h618-rescue-v1` 身分，以及舊板名別名 `bpi-m4zero`／`bpi-m4berry` 仍拒絕。共用 H618 不呼叫 0845 SRAM／FEL 流程，也不把相同控制器位址誤當成相同實板。

`uboot_template` 是家族驗證原始輸入；`uboot` 是完成家族驗證與傳輸映射後的固定配置，或上述專用執行器的完整配置參照。兩者核對結果須完整相等，不只比較長度及摘要。Amlogic、special 與原入口的 `artifact_root/manifest.json` 必須與 prepare 的 `components` 文件內容完全相同。

`transport` 只有兩種：

1. `{kind: "mmc-original", image_paths: {kernel, initrd, dtb}}`：每個原映像路徑必須存在於擷取證據，其位元組數與摘要必須等於準備後載荷；分割編號與 PARTUUID 也須符合來源。實際 MMC 路徑使用擷取結果的分割內 `resolved` 路徑。衍生 DTB、解壓核心或只存在本機的 `files/*.bin` 不會被當成原 MMC 檔案。
2. `{kind: "tftp-published", root, serverip}`：向明示本機 TFTP 根目錄新增以完整準備配置摘要命名的產物目錄；只新增、不覆寫。檔案發布為唯讀，重用時重新核對完整內容。U-Boot 使用這些實際發布路徑，仍須通過 LMB 防護與板端載入後 SHA-256。操作者須先核定 TFTP 服務 IP、根目錄及可讀權限；程式不安裝或改寫 TFTP 服務。

專用執行器目前只接受其已核對的原 MMC 傳輸：special 的 bundle `transport` 須完整等於配置的 `execution.transport`，包含 `{kind, media, extraction, image_paths}`；`media` 必須是 `emmc`，`extraction` 必須與 prepare 指向同份摘要固定的擷取。Realtek 另須 `root_preparation` 精確指向 bundle 的準備清單。original-entry 沿用 `{kind, image_paths}`，逐角色核對原入口的原始路徑及位元組，原入口本身、連結、分割命名空間與額外工作區由專用執行器重播核對；不以解析後的檔名改寫原腳本語意。兩者都須使用與 backend 相同的 `pairing` 及 `uboot_qualification` 參照。

## 生命週期與登入

生命週期設定 `bpi-lab-lifecycle-v1` 明示 `abi`、`hardware_id`、`pairing_sha256`、`uart_device`、固定 `power_program` 及 `power_dependencies`、`autoboot`、`login`、`shutdown_marker`、`off_seconds`、`authorization`、`mmc`、`rescue_uboot`、`rescue_qualification`、`rescue_artifact_root`、`rescue_expected`、`ssh_setup`。

- `autoboot` 使用 `{stop_text, stop_key_hex}`；停止按鍵限 1..16 位元組。
- `mmc` 使用 `{emmc, sd}`。主線 MMC 編號必須不同；Realtek 原廠使用分離的 `mmc 0:1` 與 `sd 0:1` 命名空間，SD block descriptor 編號另須明示，不能猜測。救援只接受核對過的固定 SD 配置與 initrd，禁止持久根媒體 bootargs。
- `authorization` 分開核定 `normal_shutdown`、`cold_cycle`、`customer_boot_may_write_emmc`、`fault_poweroff`、`firstboot_account_changes`、`install_test_ssh_key`，並保存 `record`。
- `power_program` 只接受固定 `bpi-pw` 入口；命令僅 `status`、`off`、`on`。入口以不可變快照執行，另核對操作者列出的相依項及實際回覆資產身分。

`login.customer` 與 `login.rescue` 支援 `root-shell` 或既有 root 的 `password`；只有客戶端可選 `initial-setup`。

`password` 配置使用 `shell_prompt`、`login_prompt`、`password_prompt`、`username`、`password` 私有參照。`initial-setup` 另使用 `new_password` 私有參照及 `skip_user_creation`。必須明示授權 `firstboot_account_changes=true`，才會處理首次強制改密碼與 Armbian 首次登入。沒有預設密碼、猜測迴圈、帳號掃描或自動重送；每類提示只能出現核定次數，總交握最多十二次，未知流程即停止。

帳密檔案必須由執行使用者持有、權限不得開放群組或其他使用者、且只有一個連結。憑證內容不寫入報告或 TX 日誌。不得把真實帳密、私有設定或金鑰提交至倉庫。

`ssh_setup.customer` 與 `ssh_setup.rescue` 分別使用 `host_key_path`、`install_key`、`public_key`、`authorized_keys`、`peer_ipv4`、`wait_for`。新增公鑰必須另有 `install_test_ssh_key=true`。僅可寫受限的 `authorized_keys` 路徑，拒絕符號連結與硬連結，保留既有內容，新增公鑰限 Ed25519 且帶來源 IP 與 `restrict` 限制。寫入前再次核對目前根裝置與 `boot_id`。

公鑰安裝在 UART 核對實際 Linux／根／雙媒體之後、SSH 建立之前。`wait_for: "armbian-firstrun"` 可有界等待首次服務與 SSH 就緒，不會自行安裝服務或改網路。正式測試映像及救援需已具備 Python 3、SSH 服務及受支援的網路配置。

SSH 設定使用 `{host, port, user, identity, known_hosts}`，不接受自由命令、Include、ProxyCommand 或任意 argv。尚未建立的 hostkey 可在 bundle 使用 `known_hosts: null`；任何真正 SSH 都須先由同次已配對 UART 取得 Ed25519 公鑰，生成全新私有設定，並以同一固定身分程式核對兩端 `boot_id` 及媒體。直接使用部署 CLI 的遠端動作不能使用空 hostkey。

**私有執行目錄含 SSH 私鑰快照，不得整目錄對外發布。** 報告不包含私鑰內容；公開證據應另經操作者審閱與去識別，不能將執行目錄當作可直接提交的證據包。

## 狀態、續作與失敗

後端共用 `/var/tmp/bpi-lab-shared-locks`，依硬體及資源取得排他鎖，以媒體身分索引持久狀態。每個操作在硬體入口前先保存 `running` 意圖；完整報告與操作證據落盤後，才發布 `verified` 狀態。

新工作不能接手未完成救援的媒體；同工作不能跳過階段或重複部署。`boot` 會重新讀取固定摘要的前階段完整回讀收據。`resume` 僅接受有序、完整且逐份摘要相符的歷史前綴，游標須與本機持久狀態一致，再重新核對目前 UART／SSH 與 `boot_id`。續作前檢不重新部署、不重啟、不把舊 SSH 設定當作目前身分。

所有原生結果須符合階段 schema／action，拒絕 `simulated`、`synthetic`、`test_only` 真值。boot／smoke／客戶系統 resume 必須提供固定摘要的 `linux_collection`；共享 validator 重新執行完整 Linux 核對，再與 UART 的核心、DT、根 UUID、雙媒體及 SSH hostkey 逐項比對，不能以單項、重複檢查或 `ok=true` 代替。部署及救援預檢同時核對外部來源、備份、RAM 身分及受保護 SD 契約。

只有 `.pending` 而沒有狀態文件時一律隔離，不得開始新 preflight 或刪除標記。救援每次明示嘗試使用獨立輸出目錄，失敗後仍須經原有狀態及 writer 守門，並非自動重試。發布前後皆檢查總期限，跨期限的落盤不能回報成功。

中斷、逾時、發布不完整、媒體錯配或失敗狀態不允許續作。故障 `recovery` 必須有同工作保存的中斷意圖及獨立 `fault_poweroff` 授權；正常 `recovery` 必須先完成 UART 身分與正常關機標記核對。不在失敗處理中自動斷電、重試或復原。

階段報告固定包含布林 `needs_recovery`。本次已保存意圖後的原生階段失敗回 `true`，包括中途失敗的 preflight。意圖寫入拋錯也必須在資源鎖內重讀：若狀態已變更，或無法確認，仍回 `true`；涵蓋 rename 已完成但目錄 fsync 失敗、尚未進入 runtime 的窗口。事前資格／配置／板名／狀態阻擋、確認未改變原狀態的意圖寫入失敗，以及測試替身均為 `false`。此旗標不授權切電，也不解除 writer 隔離。

若操作後的失敗報告也因磁碟故障無法落盤，stdout 仍回傳原失敗狀態及 `needs_recovery`，另標示 `report_persistence_failed=true`，不降成遺失階段資訊的泛用 CLI 錯誤。成功操作但未完成報告／狀態發布也視為失敗，不宣稱硬體通過。強制終止或斷電不保證還有 stdout；此時須讀取持久意圖，不能把缺少回報解讀成未操作。

`deploy` 的 `running`／`failed` 一律隔離，不能靠故障斷電授權切電；此時尚未證明遠端 writer 停止。`writer_unresolved` 會跨後續失敗意圖持久保留，只有完整回讀收據才能清除。須由操作者另行核對遠端寫入生命週期；目前不提供猜測式遠端租約或自動解除隔離。

## 穩定程式介面

首次核定入口由主代理的 `bpi_lab_qualify.py` 負責，本模組不繞過佇列資格：

```python
config, contract = backend.load_inputs(config_reference)
bundle, boot, expected = backend.selected_image(config, contract, request)
context = (config, contract, bundle, boot, expected, request)
result = backend.NativeRuntime().execute(context, current_state, output, timeout)
backend.validate_result(request["stage"], result, request, contract, bundle,
                        config=config, current=current_state)
```

`load_inputs` 只核對固定程式、輸入、授權及配對，不讀取循環資格檔。`check_qualification(config)` 才核對人工審閱資格；佇列 `load_config(reference)` 永遠依序呼叫兩者。`scope_digest(config)` 排除 `qualification` 參照，避免自我摘要循環。

`selected_image` 回傳的 `boot` 必須原樣傳給 `NativeRuntime.execute`；不得取 `lifecycle.customer_view(boot)` 的投影取代原配置。`customer_view` 只供共同的核心、架構、配對與 MMC 身分核對，實際命令由 `lifecycle.boot_driver` 按固定 schema 分派。新增固定相依項包含 `bpi_lab_original_entry.py`、`bpi_lab_special_runtime.py`、`bpi_lab_realtek_rescue.py`、`bpi_lab_evidence.py`、`bpi_lab_image.py`、`bpi_lab_rockchip.py`；原設定須重新固定完整摘要並審閱 scope，不可沿用舊資格。

original-entry 的共同核對欄位透過 `original_entry.lifecycle_view(config)` 投影，支援其已核對的 CM6 FIT 路徑；實際執行仍保留 FIT 原配置與原 `sysboot`／`bootm`，不以投影中的 Image 欄位啟動共用 runner。

`validate_result` 原有位置參數不變，新增可選 `config`、`current`。`selected_image` 在記憶體 bundle 保留 `_validation_config` 供既有呼叫使用；自行建立 bundle 的核定驅動器應明示傳入 `config`，續作必須傳入 `current`。核心驗證實作位於 `bpi_lab_evidence`，不匯入 backend／qualify，兩種入口可共用而無循環匯入。

`load_inputs`、`scope_digest`、`NativeRuntime.execute` 與 `StateStore` 既有介面不變。`selected_image` 在記憶體 bundle 加入 `_root_binding`，原生後端傳給 `lifecycle.cycle(..., root_binding=...)` 的新增可選關鍵字；直接呼叫 LABEL 引導若省略綁定會拒絕。special dispatch 使用固定的 `validate_config(config)`、`validate_artifacts(config, artifact_root)`、`lifecycle_view(config)`、`boot(console, config, records, timeout=..., monotonic=...)`。

`StateStore(config)` 的 `read()`、`write(value)`、`publish(value, output, report)` 須由呼叫端在 `resource_lock(config)` 範圍內使用。首次核定驅動器仍須自己核對單次操作授權、階段順序與結果；不能把完成輸入驗證當成硬體授權。

`StateStore.validate_deploy_receipt(self, receipt)` 封裝原有 `status/ok` 與 MMC `core.validate_state(..., final=True)` 核對；`write` 在清除 writer 隔離前呼叫它，預設行為不變。未來外接媒體後端可由獨立子類別覆寫自己的嚴格收據驗證，不能把外接媒體偽裝成 CID 或以空驗證器解除隔離。

### 離線來源核對

```bash
python3 tools/bpi_lab_deploy.py verify \
  --contract "$CONTRACT" --contract-sha256 "$CONTRACT_SHA256" \
  --source "$SOURCE_RECORD" --source-sha256 "$SOURCE_RECORD_SHA256"
```

此命令只讀本機一般檔案，完整重驗備份與來源，不開 UART、電源或 SSH。`preflight` 與 `deploy` 才會接遠端；兩者須另指定全新 `--output`，其中 `deploy` 還須 `--confirm-overwrite`。

### 已核定站點適配器

```bash
python3 tools/bpi_lab_backend.py \
  --config "$CONFIG" --config-sha256 "$CONFIG_SHA256" < "$REQUEST"
```

stdin 使用 `bpi-lab-request-v1`，stdout 使用 `bpi-lab-stage-v1`，完整沿用 `station.BINDINGS`。若經 `external-v1` 執行，argv 的入口可使用解析至真正一般檔案的 Python 直譯器，後接 `-B` 與本工具絕對路徑；入口摘要與本倉完整模組摘要都需固定。不要以 `-I` 移除腳本目錄後又假設仍可匯入同目錄模組。

這些雜湊與快照不是對惡意本機 root、已遭入侵的 UART 端點、直譯器、系統函式庫或未列出的外部相依項之沙箱。板級配對及原始資格證據仍須由操作者審閱。

## 本機回歸

2026-09-18 整合測試對 AI2N、R3、CM6 FIT、Realtek M4／W2 及非 0845 H618 三板走完實際 `run_stage`、`NativeRuntime.execute`、引導 runner、Linux validator、持久狀態發布及固定 SD recovery，逐階段核對報告、根系統狀態、boot ID 與 writer 隔離旗標。H618 另重播真正 Allwinner 家族核對，準備產物經受控 TFTP 發布。UART、電源、遠端部署與採樣回覆是合成測試邊界，不是實板通過證據。

```bash
python3 -m unittest discover -s tests -p test_bpi_h618_emmc_deploy.py
python3 -m unittest discover -s tests -p test_bpi_lab_deploy.py
python3 -m unittest discover -s tests -p test_bpi_lab_backend.py
python3 -m unittest discover -s tests -p test_bpi_lab_lifecycle.py
python3 -m unittest discover -s tests -p test_bpi_lab_session.py
python3 -m unittest discover -s tests -p test_bpi_lab_backend_runtimes.py
python3 -m unittest discover -s tests -p test_bpi_lab_realtek_rescue.py
```

測試僅使用暫存資料、模擬 UART、傳輸替身及受控失敗注入。生命週期測試實際執行共用及專用 U-Boot runner；跨家族整合測試另允許本機 `dtc/fdtget` 編譯與唯讀解析合成 DTB，及 `ldconfig -p` 查詢 FIT 所需的 libfdt，其他子程序仍封鎖。沒有真實 UART、電源、SSH、區塊寫入或原映像變更。離線通過不是實板驗證，也不替代首次核定、人工審閱或站點啟用權限。

## 未實作與實板界線

- Realtek 客戶 LABEL 與同 vendor 固定 SD RAM 救援已接入五階段，合併來源亦完成兩板真 BSP 編譯、連結、大小檢查；尚無實板冷循環、救援 kernel／initrd 網路可用性及 RAM／ACPU 時序核定。W2 `direct-final` 不是保留區檢查豁免，不能以合成測資或編譯成功生成硬體資格。
- 共用部署只支援 MMC/CID 媒體。無 eMMC 的板子若沒有獨立可寫 MMC 測試媒體，仍缺 USB／NVMe 等獨立適配器；不得改常數把 WWN／serial 冒充 CID，也不能覆寫受保護救援 SD。新適配範圍須涵蓋穩定媒體與控制器身分、完整備份、mount／swap／holders 排除、固定描述符、完整回讀、Linux 根身分與共用排他。僅 RAM 引導不是完整五階段映像資格。
- 原入口目前包含已核對的 ARM32 zImage 與 CM6 單核心 FIT 分支；其他 FIT、TFTP、overlay 與 K3 vendor-env 限制沿用專用執行器，不能降級成共用 runner 來避開限制。
- 沒有未知遠端 writer 的自動租約核對或自動解除隔離；未完整發布的 deploy 不允許故障切電。
- smoke 是固定 Linux 唯讀預檢，不包含 CPU／記憶體壓力、周邊完整驗證或 ROM／SPL 原生開機鏈認證。
- 上板前仍須由操作者取得真實 UART／電源、CID／控制器、MMC 編號、RAM／保留區、U-Boot 命令與版本、救援身分及受保護 SD 的配對證據，完成原 userarea 備份並明示寫入／首次帳戶變更授權。首次單映像循環須走獨立 qualify 入口，所有證據通過後再人工 review；程式與離線測試均不啟用站點。
