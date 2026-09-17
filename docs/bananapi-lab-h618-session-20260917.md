# 同次登入與 UART／SSH 綁定

`tools/bpi_lab_h618_session.py` 將 0845 原型的首次登入、RAM Wi-Fi、測試公鑰及 UART hostkey 整理為有界 API。ignored 原型只供閱讀，不在執行時匯入。沒有硬編帳密、預設密碼探測、SSH 降級或秘密自動產生。

## 可復用介面

以下四個函式不執行 H618 媒體判定，其他平台可匯入；保留在本模組，不修改其他代理的檔案。匯入本身不開裝置或執行命令。

```python
private_file(path, maximum=16384) -> bytes
public_key(blob) -> str
write_ssh_config(output, *, address, username, identity_file,
                 host_key, alias="lab-session") -> dict
ssh_json(ssh, command, deadline) -> dict
```

- `private_file` 只讀目前使用者私有、單一連結、有界一般檔案，拒絕祖先符號連結、裝置及讀取期間變動。秘密留在呼叫端記憶體，不得記錄或放入 argv。
- `public_key` 驗證 Ed25519 Base64 與 SSH wire 結構，回傳不含註解的公鑰；目前不支援 RSA／ECDSA。
- `write_ssh_config` 接受已存在私有目錄、明確 IPv4、使用者、私鑰路徑及本次可信通道取得的 hostkey。排他建立 `known_hosts`、`ssh-config`，不覆寫。回傳 `{config:{path,sha256}, known_hosts:{path,sha256}, alias}`；函式不會自行證明 hostkey 來源可信。
- `ssh_json` 接受上述結果、程式明確產生的固定命令及 deadline；deadline 提供 `end`、`clock()`、`remaining()`。強制嚴格 hostkey、公鑰認證、新連線、無代理／轉送，前後重驗檔案摘要，限制 64 KiB JSON、要求正常退出。呼叫端仍須驗 nonce、boot_id、媒體及同次綁定，解析成功不等於身分通過。

不可把日誌當命令，或用別次 SSH 設定代替新綁定。憑證來源、配對核定及板級政策由各平台維持，不由低階函式推測。

## 共用概念

1. 在已配對、排他的同次 UART 核對 Linux，取得新挑戰 nonce、boot_id、路由與 hostkey。
2. 用本次 hostkey 建立新私有 known_hosts，不用 `accept-new`、`ssh-keyscan` 取代 UART 信任，禁止舊 ControlMaster 或代理主機。
3. SSH 使用相同挑戰回讀相同 Linux 身分；H618 比對整份 UART／SSH 採樣，收集前後再驗，排除途中重啟。
4. 保存完整工作綁定；resume 與後續階段重新經 UART 取 hostkey，不只讀舊 session JSON，非預期 boot_id 變更停止。
5. 合成證據不是實板資格；session 的 `hardware_validated=false`，外部資格流程仍須另行核定。

## H618 設定

`bpi-lab-h618-session-v1` 設定以 `{path, sha256}` 固定在 stage 中。

| 欄位 | 要求 |
| --- | --- |
| `login.username` | 明示帳號；登入後須 `id -u=0`，不內建 sudo 升權 |
| `login.password_file` | 本次登入憑證的絕對私有路徑或 null；缺少但遠端要求密碼就停止 |
| `login.new_password_file` | 首次初始化的新憑證來源；`initialize=true` 時必須提供 |
| `login.initialize`、`login.skip_user_creation` | 獨立布林授權，不猜測新密碼或默默略過使用者建立 |
| `peer_ipv4` | 測試主機明確 IPv4；用 `bpi_lab_network.select_local_ipv4` 核對唯一直接路由與介面位址 |
| `identity_file` | 私有 SSH 私鑰絕對路徑，拒絕展開字元，不保存內容或產生新金鑰 |
| `public_key` | 測試 Ed25519 公鑰 `{path, sha256}` |
| `rescue_network`、`customer_network` | 各為 `{mode:"existing"}`，或 `{mode:"wifi", interface, ssid, secret_file}`；介面與 SSID 明示 |
| `dt_compatible.rescue` | 救援 DT 的核定完整、有序 compatible 清單，救援 UART 與 SSH 均須精確相等 |
| `dt_compatible.customer` | 當次原配客戶 DT 的核定完整、有序 compatible 清單；UART、SSH 與 Linux 完整採樣共用此預期 |

`dt_compatible` 必須同時包含 `rescue` 與 `customer`，不接受原先共用的單一清單、遺漏模式或額外模式。兩個模式各自核對完整順序，不把清單合併成可任選的白名單，也不只核對共同 SoC 字串。

例如本倉 M4 Zero 救援 DTS 的 compatible 為 `["sinovoip,bpi-m4-zero", "allwinner,sun50i-h618"]`；既有 EMAC 客戶 DTB 離線證據則為 `["sinovoip,bpi-m4-zero-emac", "sinovoip,bpi-m4-zero", "allwinner,sun50i-h618"]`。必須分別填在對應模式，不能用其中一份代替另一份。這只說明設定形狀，不是其他映像的預設身分或新增實板證明。

舊設定須依核定救援及當次原配 DT 重新填入兩份清單，更新 session、adapter 設定及相依檔摘要；不得只換摘要就沿用原硬體資格。`write_ssh_config`／`ssh_json` 等低階共用函式的介面不變。

離線檢查不讀密碼、Wi-Fi 憑證或私鑰內容，執行時才讀私有檔案。帳密限有界 ASCII 單行；Wi-Fi 支援 8 至 63 字元密語或 64 位十六進位 PSK，來源最多容許尾端單一換行。

首次登入接續 boot 已消耗的 login 提示，支援原型的密碼登入、到期密碼變更、建立／重複 root 密碼、已確認的 bash 選項與經核定略過普通使用者建立。重複密碼提示、殘留確認、未知交握或逾時停止，不自動重送。仍須各映像實板驗收，不代表桌面登入通過。

Wi-Fi 只有確認 UART `ECHO`／`ECHONL` 關閉才送秘密，不放入 shell 命令、環境或報告。設定只寫 `/run`；救援用 `bpi-rescue wifi`，客戶僅支援既有 `systemd-networkd`，不安裝或切換網路管理器。既有 RAM 設定不覆寫，中途中斷不靠重試掩蓋。

客戶公鑰追加到 `/root/.ssh/authorized_keys`，救援追加到 RAM 根 `/etc/ssh/rescue_authorized_keys`，使用 `from=<測試主機>,restrict`。拒絕符號連結、多重硬連結、過大檔案、不完整末行，不刪既有公鑰。客戶首次登入與公鑰設定是明示的持久差異，不能宣稱無寫入。

## Linux 與證據

UART 唯讀程式核對 root 裝置號與 sysfs、直接 MMC 第一分割區、無堆疊、CID、控制器、容量、UUID、架構、核心、DT；SSH 必須完全一致。客戶再呼叫 `bpi_lab_linux.collect/validate`，保存原始 collection 與 validation；只有失敗服務交給 smoke，不能略過任何身分欄位。

救援另驗核定核心、身分檔摘要、RAM 根、媒體、無掛載／swap；stage 另驗 SD 前 4 MiB 及無媒體可寫描述符。這不是整片 SD 或客戶原始開機鏈驗證。私有原始 UART RX 仍可能含遠端自行回顯的秘密，不可公開。

## 本輪離線命令

```sh
env PYTHONPATH=tests output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest \
  test_bpi_lab_h618 test_bpi_lab_h618_lifecycle test_bpi_lab_h618_session test_bpi_lab_h618_stages \
  test_bpi_h618_customer_boot test_bpi_h618_rescue_boot test_bpi_lab_console \
  test_bpi_lab_network test_bpi_lab_linux test_bpi_h618_emmc_deploy test_bpi_h618_customer_smoke \
  test_bpi_lab_station test_bpi_lab_queue test_bpi_lab_queue_review

output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m ruff check --no-cache \
  tools/bpi_lab_h618.py tools/bpi_lab_h618_lifecycle.py tools/bpi_lab_h618_session.py \
  tests/test_bpi_lab_h618.py tests/test_bpi_lab_h618_lifecycle.py \
  tests/test_bpi_lab_h618_session.py tests/test_bpi_lab_h618_stages.py
```

上述回歸合計 404 項通過，指定七個 Python 檔案的 Ruff 通過，指定修改檔案的 `git diff --check` 通過；`python3 -B tools/bpi_lab_h618.py --help` 亦正常返回。未執行全專案回歸。

仍缺實體配對、核定憑證、實板完整五階段及故障／中斷恢復驗收。本輪全部是軟體回歸，不能把 fixture 或舊十套轉成新資格文件。
