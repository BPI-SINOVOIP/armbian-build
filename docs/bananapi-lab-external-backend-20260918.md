# 外部媒體五階段與首次核定

日期：2026-09-18。範圍為 D4；沿用 D1／D2／D3 契約，不修改既有原映像，不借用 MMC CID。
本文件及離線回歸不構成實板資格；既有 0845 十套紀錄不計入本輪測試，停用站點不自動啟用。

## 入口與邊界

- `tools/bpi_lab_external_backend.py` 接受 stdin `bpi-lab-request-v1`，回傳 `bpi-lab-stage-v1`，供既有 queue `external-v1` 呼叫。
- `tools/bpi_lab_external_qualify.py` 提供 `check`、`run`、`recover`、`approve`、`station`。
- `check` 只讀固定配置與來源證據，不要求已完成首次循環的資格，不建立實板狀態或連線。
- `run` 是獨立首次單映像五階段試驗；完成後僅成為待審候選，不自動核定批次或登記站點。
- `approve` 重播完整原始證據與人工審閱，才產生 D4 專用 qualification。
- `station` 重驗 qualification，匯出通用資格封套及站點設定，不操作硬體、不登記佇列；預設 `enabled: false`。
- `recover` 只接續同一首次授權、工作及持久租約，不重新部署、不產生首次循環資格。

固定入口不接受任意命令、shell hook、vendor 或 extlinux 原開機鏈替身。首版只接受已核定
mainline U-Boot 的 raw／uImage 核心、原 DTB 與 D3 衍生 guard initrd。載入來源為事先固定的
TFTP 檔案或 SD 預佈置檔案，仍由 U-Boot 實際核對大小及 SHA-256；D4 不在執行時改寫 SD。

`derived_initramfs: true` 與 `original_boot_chain_verified: false` 必須保留。
短測只核對固定 Linux 身分與媒體狀態，不等同壓力、效能、原 ROM／SPL 或整個後端資格。

## 固定配置

所有參照都是正規化絕對路徑的 `{path, sha256}`，未知或缺失欄位拒絕；不能使用可變的猜測預設值。
秘密來自既有受限本機檔案及 SSH 身分參照，不在配置範例硬編帳密，也不降級 SSH 驗證。

`bpi-lab-external-backend-v1`：

| 欄位 | 契約 |
| --- | --- |
| `station_id/hardware_id/test_version` | 明示固定身分；不借用既有 SRAM 原型資產 |
| `resources` | `uart/power/media`；媒體鍵使用 `media.media_identity(target)` |
| `dependencies` | 完整固定模組摘要，包含 C 核心與 D1..D4；不是只固定一個入口 |
| `pairing` | 已核定的本板、固定 UART、電源、外部媒體與保護 SD 配對 |
| `target` | D1 正式 USB／NVMe 身分、容量、磁區及拓撲 |
| `protected_sd` | D1 SD CID、容量、控制器及整碟 SHA-256 |
| `rescue` | D2 固定救援 schema、kernel、身分檔 SHA-256；根必須為 RAM |
| `lifecycle` | 本配對的受限正常關機、冷循環、登入及 SSH 設定 |
| `rescue_source` | 獨立救援載入來源，不與外部 target 或客戶根混用 |
| `images` | 原始壓縮映像 SHA-256 到每映像 bundle 的參照 |
| `output_root/timeout_seconds` | 私有證據目錄與有界期限 |
| `qualification` | 首次配置可省略；批次必須有已審閱資格 |

`bpi-lab-external-pairing-v1` 包含 `approved/record/hardware_id/resources/target/protected_sd/rescue`，
以及 `uart: {stable_path, baud}`、`power: {driver, name, ip, mac}`、`sd_device`。
UART 只接受固定 `/dev/serial/by-id/...`；電源只使用固定 `bpi-pw` 協定與配對資產。
未知、缺少實體配對或配對摘要不符都保持待驗，不製作通用假配對。

`bpi-lab-external-lifecycle-v1` 綁定 `hardware_id/pairing_sha256/uart_device`，以及
`power_program/power_dependencies/autoboot/login/shutdown_marker/off_seconds/authorization/ssh_setup/rescue_expected`。
`login` 與 `ssh_setup` 都分 `customer/rescue`；客戶與救援 DT 預期互不借用。
`authorization` 明示正常關機、冷循環、客戶根可寫、故障斷電、首次帳號變更與金鑰安裝權限。
登入沿用 C 的 `root-shell/password/initial-setup`，首次帳號變更必須另有授權。
固定救援須預先具備 root 公鑰登入及 Ed25519 hostkey。客戶可明示
`wait_for: armbian-firstrun`，在有界等待 SSH 就緒後先做完整 UART／guard 根媒體核對；
另有 `install_test_ssh_key` 授權時，才允許 `install_key: true` 寫入
`/root/.ssh/authorized_keys`。使用受限來源 IP 的測試公鑰，不修改 SSH 伺服器設定或原 XZ。
另一個掛載、符號連結、不同 boot_id／根裝置或缺少授權均拒絕；安裝後重驗同次身分，
記錄 `ssh-setup.json`，再以同次 UART 公鑰建立嚴格 SSH 連線。
金鑰安裝沿用 `core.session.install_key` 的受限路徑與 boot／root 核對。
等待服務與 hostkey 就緒的過程只讀狀態，不以缺少 hostkey 的前置觀測替代完整客戶核對。
逾時、guard 缺失或錯根時，尚未開始安裝測試金鑰。

每映像 `bpi-lab-external-image-v1` 包含：

- `board/image/deployment`：D2 原始 XZ 與部署契約；source 路徑、壓縮大小、摘要須與 queue 工作一致。
- `preparation/original_components`：原映像擷取證據與核心、DTB、原 initrd 路徑；不重新打包原 XZ。
- `boot_source`：`bpi-lab-external-boot-source-v1`，欄位 `kind/uboot/qualification/artifact_root`。
- `guard/linux_expected`：直接使用 D3 `validate_manifest` 與 `expected_from_contract`／固定觀測契約，沒有 D4 假 guard metadata。
- `customer_ssh`：固定端點與私鑰參照；每次連線前由同次已配對 UART 建立新的嚴格 known_hosts。

boot source 的 `kind` 僅有 `sd-prepositioned/tftp-fixed`。客戶根只使用原配 `root=UUID=...`，
拒絕 sdX 猜測及 LABEL 替代；救援使用獨立 RAM 根。原核心、DTB、原 initrd 與衍生 initrd 摘要分開保存。

## 連續映像重用

部署契約與來源綁定由 D2 正式 validator 判定，D4 不硬編單一 range，也不自行複製成功判斷。
預設舊契約仍要求目前內容符合初始備份且尾端已為零；不能自動升級成可覆寫測試區。

重用須明示 `authorization.scope: reusable-test-area`、`tail_policy: zero` 及兩段精確範圍：
原始映像從 offset 0 開始，其後至核定媒體末端為全零範圍。授權綁定同一實體 target、完整初始
backup、source 及整份 write plan 摘要。每映像使用不同 deployment，但可以共用初始完整備份。

客戶改寫後不要求重新備份或換 SD。D2 每次重驗來源、初始備份、目前媒體、閒置及授權，
完整寫入並分開核對原映像回讀與全零尾端回讀。D4 state 與 approve 都呼叫
`external.validate_result`，包含原始 manifest、請求、遠端終止紀錄、發布完成憑證及即時隔離狀態。
只有 `verified` JSON、缺完成憑證或仍有 pending 都不接受，不能藉複製收據洗掉 writer 隔離。

D3 已提供明示 `root_growth` 策略，核對原始完整 MBR 配置，只允許最後一個根分割
在相同 UUID／起點下向後增大。D4 在配置及實際 D2 來源收據再次核對配置；
未明示策略仍維持精確大小。離線兩套正例涵蓋完整五階段、擴根觀測、客戶資料與非零尾端改寫，
再部署較小映像；這是軟體回歸，不是已在實板執行客戶擴根服務。

## 五階段與中斷

| 階段 | 必要結果 |
| --- | --- |
| `preflight` | 固定 RAM 救援、同次 UART→SSH、完整 SD 摘要、D2 來源／備份／目標預檢 |
| `deploy` | 同次救援身分、完整 D2 寫入及讀回、正式發布完成與 writer 結束證據 |
| `boot` | 正常關機、電源身分與冷循環、已核定 U-Boot 載入、全新 boot_id、D3 客戶根與 guard |
| `smoke` | 同一客戶 boot_id 的固定唯讀觀測；完整根／父媒體／SD／guard／UART→SSH 核對 |
| `recovery` | 正常關機或另行授權故障斷電、冷循環、新 boot_id 與獨立 RAM 救援身分 |

每個 session 綁定本工作、attempt、映像、配置及測試版本，UART 與 SSH 必須使用同一 nonce、
boot_id、hostkey 與媒體身分。SSH 快照逐項核對，強制 `StrictHostKeyChecking yes`，
拒絕 ProxyCommand、額外設定、替換私鑰或非本次 UART 取得的 known_hosts。

批次沿用 C `resource_lock/transition/StateStore`；D4 僅覆寫 `validate_deploy_receipt`，
不能以 MMC 或偽造 CID 收據清除 writer 隔離。父 queue 已持有 station 鎖時不重入該鎖。
意圖先落盤，遠端 writer 未解析時禁止切電；重複 recovery 不能覆蓋或洗掉 unresolved 狀態。

純配置前檢尚未建立本次操作狀態時，`blocked`／`needs_recovery: false` 不要求復原。
操作或意圖發布不確定時回 `needs_recovery: true`；queue 依契約復原，無法證明安全則持久隔離。
resume 重播有序既有報告，再觀測當前救援／客戶系統，不重刷已完成映像。
復原重試使用新證據目錄，不覆蓋前次故障；完成復原後摘要發布中斷，只補發布、不再切電。

首次試驗同時使用 queue station 鎖、完整持久租約及後端鎖。租約先於首個目錄／intent，
同授權 recover 能接續意圖前窗口。最終發布沿用已回歸的 Q5 順序，完成憑證與 pending 處理
先於最後釋放租約；不得以硬停或重試製造第二份復原。

## 核定與批次操作順序

以下為入口格式，並非本輪執行硬體的命令紀錄。

```sh
python3 -B tools/bpi_lab_external_qualify.py check \
  --config "$CONFIG" --config-sha256 "$CONFIG_SHA256" \
  --authorization "$AUTH" --authorization-sha256 "$AUTH_SHA256" \
  --request "$REQUEST" --request-sha256 "$REQUEST_SHA256"
```

首次授權 schema 為 `bpi-lab-external-first-authorization-v1`，包含
`approved/record/scope_sha256/image_sha256/stages/allow_failure_recovery/driver_sha256/queue_sha256`。
`stages` 必須完整五階段，`attempt_id` 等於授權 record，工作鍵為
`media.digest({scope: scope_sha256, authorization: 授權參照的sha256})`。
同配置範圍摘要排除後加的 qualification，避免首次核定的循環依賴。

經獨立實板授權才使用同樣參數呼叫 `run --confirm-hardware-test`；失敗只可依同授權使用
`recover --confirm-hardware-test`。不完整首輪、復原摘要及注入替身的 `test-only` 都不能 approve。

人工審閱 schema 為 `bpi-lab-external-first-review-v1`，包含
`approved/record/candidate/scope_sha256/approved_images`。approved_images 明示批次允許的原始
映像摘要，須包含已實測首套且屬於該固定配置；不表示其他映像已完成實板測試。

```sh
python3 -B tools/bpi_lab_external_qualify.py approve \
  --candidate "$CANDIDATE" --candidate-sha256 "$CANDIDATE_SHA256" \
  --review "$REVIEW" --review-sha256 "$REVIEW_SHA256" --output "$QUALIFICATION"
```

approve 重驗五份不同階段原始 operation、有序 report、action／collection／身分、部署發布及
boot_id 序列。產物為 `bpi-lab-external-qualification-v1`；新增參照到另一份不可變批次配置，
不修改首次配置或原證據。任何固定內容改變都需新範圍與新審閱。

```sh
python3 -B tools/bpi_lab_external_qualify.py station \
  --config "$BATCH_CONFIG" --config-sha256 "$BATCH_CONFIG_SHA256" \
  --interpreter "$FIXED_PYTHON_BINARY" --output "$STATION_OUTPUT"
```

匯出包括 `station.json` 與 `qualification.json`。明示 `--enable-reviewed-station` 才產生
啟用設定；後續仍需主代理核准登記既有 queue，工具不代做登記。
直譯器須解析至真正一般可執行檔，入口摘要固定其內容，argv 固定 `-B`、本工具、配置路徑與摘要。
完整 D4 依賴另外固定，不把直譯器摘要誤當模組完整性證明。

## 離線驗證與待驗

```sh
env PYTHONPATH=tests output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest -v \
  test_bpi_lab_external_backend test_bpi_lab_external_qualify
/home/pi/.local/bin/ruff check --no-cache \
  tools/bpi_lab_external_backend.py tools/bpi_lab_external_qualify.py \
  tests/test_bpi_lab_external_backend.py tests/test_bpi_lab_external_qualify.py
```

測試使用假 sysfs／區塊 I/O／UART／SSH／電源，執行真正 D2 串流與發布、D3 證據重播、
U-Boot 固定步驟及 D4 原生控制流程；guard 的合成 init allowlist／ELF probe 只在測試替換。
測試內用來驗證實板分支的核定封套一律留在暫存目錄，不能發布成板級資格。
兩套回歸經 queue／station／D4 完整五階段，確認只一次初始 backup、原 XZ 不變、SD 不變、
較大到較小仍各自完整來源與尾端回讀。故障覆蓋發布同步、缺完成憑證、writer、resume、
復原重試、原始證據重綁及 Q5 真正 fork／硬停窗口。

有限末分割增長、較大映像到較小映像的完整控制流程，以及原映像授權公鑰安裝
均已接入正式 API。回歸包含缺授權、錯根、額外掛載覆蓋金鑰路徑、服務等待逾時、
採樣中擴根與來源分割清單不符等拒絕情況；不是實板擴根或 SSH 成功證明。

仍需實板驗證：各板固定實體配對、原生／救援 USB 或 NVMe 驅動、實體埠與電源讀回、RAM 救援、
受支援 initramfs profile/runtime 的板級驗證、首次登入與授權金鑰路徑的實板覆驗、guard 實際執行、
以及兩套不同 OS／核心的真正實板循環。未知或缺失時保持待驗與隔離。
