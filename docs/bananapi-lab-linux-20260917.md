# Banana Pi 跨平台 Linux 唯讀預檢

日期：2026-09-17。對應跨板後端 A3；補充計畫先推送 `8ed9d3f51`，再開始實作。

## 範圍

工具為 `tools/bpi_lab_linux.py`，離線回歸為 `tests/test_bpi_lab_linux.py`。支援 ARM32、ARM64、RISC-V 64 位元的 Linux 配置核對，不沿用 H618／0845 的固定核心、CID、MMC 編號、控制器或 64 GiB 容量限制。

本版是**唯讀預檢，不是完整短測或壓測**。不安裝軟體、不讀寫區塊媒體內容、不建立遠端暫存檔、不操作 UART／電源／引導，也不呼叫備份或部署函式。CPU／記憶體負載測試明列 `not_tested`；`hardware_validation`、`smoke_tested`、`stress_tested` 永遠為 `false`。

目前僅能核對具有 CID 的直接 MMC／SD 根媒體。跨架構支援不表示支援所有儲存協定、實體板型、救援路徑或啟動鏈，也不構成寫入授權。

## 介面

主機使用指定 Python：

```bash
PY=output/evidence/bpi-sram-supervisor/model-venv/bin/python
"$PY" -B tools/bpi_lab_linux.py --help
```

以下 `collect` 範例**會連線實體主機**；本次離線實作及測試沒有執行。路徑與別名須換成已核定站點的可信設定，不得直接套用測試假資料。

```bash
"$PY" -B tools/bpi_lab_linux.py collect \
  --ssh-config /absolute/path/ssh_config \
  --alias paired-board \
  --known-hosts /absolute/path/known_hosts \
  --timeout 20
```

收集 JSON 寫入 stdout，由呼叫端負責保存，工具本身不建立證據檔。`status=collected` 及退出碼 `0` **只表示收集程序完成**，不等於配置符合或預檢通過；必要欄位仍可能是 `unavailable`／`error`。

```bash
"$PY" -B tools/bpi_lab_linux.py validate \
  --report /absolute/path/collection.json \
  --expected /absolute/path/expected.json
```

`validate` 完全離線，不啟動 SSH、不讀取目前主機的 `/proc`／`sysfs`。輸入須為不超過 1 MiB 的一般 JSON 檔，拒絕最後一層符號連結、管線及裝置。JSON 重複鍵、非有限數值、截斷、追加物件及不合法 Unicode 均拒絕。

Python 呼叫介面：

```python
collect(*, ssh_config, alias, known_hosts, timeout=20)
validate(report, expected)
validate_expected(expected)
```

`collect` 回傳 `bpi-lab-linux-collection-v1`；`validate` 回傳 `bpi-lab-linux-validation-v1`，包含 `status`、`ok`、`scope=read-only-preflight`、逐項 `checks` 與限制說明。配置契約錯誤拋出 `LinuxError`，CLI 轉成 JSON 錯誤，不把錯誤資料當成空的成功結果。

| 結果 | 退出碼 | 意義 |
| --- | --- | --- |
| `collected` | 0 | 僅完成收集；尚未核對配置 |
| `passed` | 0 | 所有必要唯讀核對通過；非 systemd 的服務查詢可明示 `skipped` |
| `failed` | 1 | 已知配置不符或 systemd 有失敗服務 |
| `blocked` | 1 | 證據缺失、未知堆疊、型別錯誤或身分鏈不完整，不能宣稱通過 |
| `error` | 2 | CLI／配置／JSON 契約錯誤、SSH 失敗、逾時或收集不完整 |

多項不符會全部列出；存在 `failed` 時總結果為 `failed`，否則存在 `blocked` 時為 `blocked`。不以第一項成功或只有 CID 相符決定通過。

## 預期配置

所有下列欄位必填，拒絕未知欄位；以下只是離線格式範例，不代表已配對硬體。

```json
{
  "schema": "bpi-lab-linux-expected-v1",
  "architecture": "arm64",
  "kernel_release": "6.12.9-fixture",
  "dt_compatible": ["fixture,cross-platform-board", "fixture,soc"],
  "root": {
    "uuid": "11111111-2222-3333-4444-555555555555",
    "cid": "1234567890abcdef1234567890abcdef",
    "controller": "/sys/devices/platform/fixture-soc/12000000.mmc",
    "bytes": 137438953472,
    "media_type": "MMC"
  }
}
```

- `architecture` 正規化為 `arm32`、`arm64`、`riscv64`。接受 `arm`／`armhf`／`armel`、`armv5tel`／`armv5tejl`／`armv6l`／`armv6b`／`armv7l`／`armv7b`／`armv8l`／`armv8b`，以及 `aarch64`／`aarch64_be` 別名。只核對執行核心的架構，不證明使用者空間 ABI 或位元序相容性。
- `kernel_release` 完整相等，不用前綴或模糊比對。`dt_compatible` 是非空、不重複的完整有序清單，不只匹配其中一個 SoC 字串。DT `model` 必須存在但不拿來證明實體板號。
- `root.uuid` 完整相等；`cid` 為 32 位十六進位，大小寫正規化。CID 不能單獨證明根系統所在媒體。
- `controller` 是已解析的 `/sys/devices/...` 絕對路徑，止於 `/mmc_host/` 之前，**不含**會變動的 `mmcN` 編號。採完全相等比對，不接受相似前綴。
- `bytes` 是整顆父媒體容量，非根分割區大小。必須是正整數、512 位元組倍數且小於 `2**63`；布林值不是整數容量。
- `media_type` 須明示 `MMC` 或 `SD`，不因找到 CID 就把 SD 當成 eMMC。

## 收集與身分鏈

遠端以固定 `/usr/bin/python3 -I -B -` 執行純標準函式庫收集程式。程式經 stdin 傳入；命令引數只含固定路徑、隨機 nonce 及受限期限，不經任意 shell 指令、`PATH` 搜尋、安裝步驟或遠端腳本檔案。

收集包含 `uname`、有界 `/proc/cpuinfo` 原文與結構化欄位、DT `compatible`／`model`、結構化 `/proc/self/mountinfo`、MMC／SD 的 CID／控制器／容量／父裝置、根掛載的 `major:minor` 與解析後 sysfs、`/proc/swaps`、`/proc/meminfo`、失敗服務。DT 字串須有完整 NUL 終止；mountinfo 支援選用欄位及標準跳脫，不靠空白分割後猜根裝置名稱。

根媒體依下列順序判定：

1. 找到唯一 `/` 掛載，核對掛載 `major:minor` 與 `os.stat("/").st_dev`。
2. 嚴格解析 `/sys/dev/block/<major:minor>`，核對根 sysfs `dev`；依 `partition` 欄位找父媒體，不裁切固定 `mmcblkN` 字尾猜測。
3. 核對父媒體自己的 `dev`、sysfs 解析結果，以及是否仍有 `partition` 或 `slaves`。整顆媒體作為根裝置時，根與父媒體必須是同一路徑、同一裝置號。
4. 在 `/dev/disk/by-uuid` 找到唯一指向同一區塊裝置號的 UUID，再核對其 sysfs。缺少 udev 連結、權限不足或多重候選都阻擋，不把 `/proc/cmdline`、mount source 或配置中的 UUID 當作實際證據。
5. 用**根父媒體路徑**選出唯一媒體，再核對 CID、控制器、容量及 `MMC`／`SD` 類型。其他媒體即使有預期 CID 也不能取代根媒體。
6. 收集結束前重新採樣根身分及媒體資料；前後不一致不能通過。sysfs `size` 以 512 位元組扇區換算，與報告容量交叉核對。

分割區本身不要求存在 `slaves` 目錄；必須追到整顆父媒體再核對。此差異已對照 [Linux v6.12 分割區建立邏輯](https://github.com/torvalds/linux/blob/v6.12/block/partitions/core.c)與[整碟建立邏輯](https://github.com/torvalds/linux/blob/v6.12/block/genhd.c)，並以缺少分割區 `slaves` 的 fixture 覆蓋。

只支援直接媒體上的 `ext2`／`ext3`／`ext4`／`f2fs`／`xfs`／`vfat`。overlay、LVM／device-mapper、RAID／`slaves` 堆疊、NFS、Btrfs、子目錄根掛載、未知／虛擬裝置及非 MMC／SD 根媒體均明確阻擋。這是保守範圍限制，不是假造這些系統沒有儲存媒體。

UUID 是裝置管理器連結所宣告的身分；工具不開啟區塊裝置、不重新探測檔案系統超級區塊，因此不能把它說成磁碟內容的獨立驗證。前後採樣也不是鎖住遠端系統的原子快照。

## SSH 與資源界線

只借用既有 `bpi_h618_emmc_backup.ssh_command` 產生嚴格選項，以及 `config_fingerprint` 讀取設定指紋；不引入或呼叫備份、寫入、0845 預期身分。因既有 `ssh_stream` 不接受程式 stdin，本工具自行使用同型 selectors 有界傳輸。

- 本機固定 `/usr/bin/ssh`；`-F` 使用明確專用設定，限制 alias 格式。關閉密碼／互動登入、轉送、代理轉送、連線共用、hostkey 更新、代理命令、跳板與本機命令。
- 強制 `StrictHostKeyChecking=yes`，要求已有可信 `--known-hosts`，停用全域 hostkey 檔、`KnownHostsCommand` 及 DNS 驗證。未知或變更 hostkey 不會自動接受，不執行 `ssh-keyscan`。
- 設定及 hostkey 檔沿用 helper 的一般檔案／祖先無符號連結規則、各自 64 KiB 上限與前後指紋核對。hostkey 路徑不接受空白、SSH 展開字元或上層跳轉。
- 專用 SSH 設定屬可信輸入，應自包含，不含 `Include`、`Match exec` 或其他具副作用指令。本工具不把 SSH 設定解析器當成沙箱，也不宣稱指紋涵蓋其外部相依檔或私鑰。
- SSH 程式傳送、stdout／stderr 同時讀取及程序退出共用 2..60 秒期限，預設 20 秒；兩路輸出合計最多 1 MiB，超限立即失敗並回收本機程序群組。
- 遠端另有獨立期限，最多 55 秒；單檔與目錄分別限量，遠端 JSON 最多 768 KiB。`mountinfo`／`cpuinfo` 各 128 KiB、媒體最多 32 個、目錄最多 256 項、掛載最多 512 筆。
- PID 1 是 `systemd` 時，只查詢既有 `/usr/bin/systemctl` 或 `/bin/systemctl`，固定引數列出失敗服務，期限 3 秒、兩路輸出合計 32 KiB。查詢失敗、缺少程式、逾時或診斷訊息均阻擋，不當成零失敗服務。
- PID 1 明確不是 systemd 且無矛盾執行目錄時才標示 `skipped`，附中文原因；讀不到 PID 1 不算非 systemd。非 systemd 的其他服務管理器未受測。

本版**不保存原始 stdout／stderr 串流或部分報告檔**；SSH 失敗由 CLI 輸出 `status=error`、具體錯誤及 `raw_streams_saved=false`，不得稱為完整持久日誌。成功報告記錄 nonce、程式摘要、設定／hostkey 指紋及耗時。呼叫端須自行保存 JSON 與退出碼；離線檔案可被人修改，驗證器不能證明來源真實性或採樣時間可信。

「唯讀」表示工具不主動寫入遠端媒體；SSH 登入日誌、檔案讀取時間及系統自身活動不在工具的零寫入保證範圍內。只讀預檢通過也不代表備份有效、部署安全、媒體完整回讀或實體資格通過。

## 離線驗證

```bash
PY=output/evidence/bpi-sram-supervisor/model-venv/bin/python
"$PY" -B -m unittest discover -s tests -p test_bpi_lab_linux.py -v
```

測試使用合成 `/proc`／sysfs／UUID 資料與本機 Python 假程序，不啟動真 SSH、不操作 UART、電源或真實區塊媒體。涵蓋三種架構與 uname 別名、非固定 MMC 編號、超過 64 GiB 容量、SD／MMC 區別、多項配置不符、錯誤根父媒體、其他媒體具有預期 CID、未知根堆疊、前後身分變動、非 systemd 跳過、必要欄位缺失，以及逐層任意 JSON 型別替換。

傳輸回歸涵蓋固定 stdin 程式、嚴格 argv、注入拒絕、輸出合計超限、stdin 阻塞、輸出關閉後未退出、非零結束碼、程序回收及設定變動。這些離線通過結果只證明工具行為，不算任何板型已完成硬體預檢或壓測。
