# 外部媒體 D1／D2

本版提供 USB／原生 PCIe NVMe 結構化身分、固定 SSH 整碟備份、唯讀前檢及有限部署。
尚未進行實板驗證；不選取開機、不操作 UART／電源、不改 SD、原 XZ 或 U-Boot 環境。
本輪測試只使用合成 sysfs、記憶體及暫存一般檔，沒有建立或開啟設備節點。

## 穩定介面

```python
media.validate_expected(target)
media.media_identity(target)
media.inspect(expected, *, sysroot="/sys", procroot="/proc", devroot="/dev",
              ops=None, require_idle=True)
media.inspect_sd(expected, *, sysroot="/sys", procroot="/proc", devroot="/dev",
                 ops=None, require_idle=True)
media.inventory(*, sysroot="/sys", procroot="/proc", devroot="/dev", ops=None)
media.check_fd(fd, observation, *, ops=None)
external.validate_contract(contract)
external.source_digest(source)
external.zero_digest(size, *, check=None)
external.backup(contract, output, *, timeout=21600, transport=None, monotonic=time.monotonic)
external.preflight(contract, source, output, *, timeout=21600, transport=None, monotonic=time.monotonic)
external.deploy(contract, source, output, *, confirm_overwrite=False,
                timeout=21600, transport=None, monotonic=time.monotonic)
external.validate_result(report, contract, source=None)
```

`validate_expected`、`validate_contract` 是純結構驗證。`validate_result` 不連線或讀取設備，
但必須讀取主機原始收據、完成憑證及目前隔離狀態，不能只傳入獨立 JSON 副本。
`inspect` 預設拒絕任何掛載、swap、holders／slaves；D2 不提供略過此限制的選項。
D3 已掛載客戶根採樣可用 `require_idle=False`，觀測仍回報真實使用狀態，並拒絕 slaves 堆疊。
`inventory` 列出所有非零大小區塊裝置，包含 SD、USB、NVMe、分割及虛擬裝置，
不等同可寫白名單或 UUID／LABEL 唯一證明；D3 須完成盤點前後一致性與直接 superblock 探測。

`inspect` 回傳固定期望欄位，另含 `device/name/devnum/sysfs_path/device_path/diskseq/partitions`、
`mounted/swap/holders/slaves`。分割項目為 `index/devnum/start_lba/sectors/sysfs_path`。
`inventory` 每項為 `name/device/devnum/sysfs_path/parent/bytes/partition_index`。
`sysfs_path` 與 `parent` 正規化為 `/sys/devices/...`；測試注入目錄不會進入永久配對。

## 契約欄位

根物件必須精確包含：

```text
schema = bpi-lab-external-v1
hardware_id, target, protected_sd, root, rescue,
backup, write_plan, authorization, ssh, isolation_dir
```

- `target`：`kind/identity/bytes/logical_block_size/physical_block_size/topology`。
- USB `identity`：`wwid/serial/vid/pid/lun`；`topology`：`controller/port`。
  WWID 來自 SCSI 磁碟，serial／VID／PID 來自 USB 裝置，另核對 storage／UAS、LUN 與實體埠鏈。
  不以橋接器 serial 代替磁碟 WWID，不提供 CID 回退。VID／PID 使用四位小寫十六進位。
- NVMe `identity`：`wwid/serial/model/nsid`；`topology`：`controller`。
  控制器必須為 PCIe；namespace WWID 與 filesystem UUID 是不同識別。
  `nvme.*` 回退 WWID 另交叉核對內含 serial／model／NSID。
- `protected_sd`：`cid/bytes/controller/full_sha256`，摘要涵蓋整張 SD。
- `root`：`uuid/partition_index/start_lba/sectors`，UUID 必須正規化。
- `rescue`：`schema/kernel/identity_sha256`；遠端核對一般檔 `/etc/bpi-rescue.json`、執行核心與直接 RAM 根。
- `backup`：初次備份可為 `null`；其餘操作須為 `{path,sha256}`，指向成功備份的 `manifest.json`。
- `ssh`：`host/port/user/identity/known_hosts`；固定 IP、`user=root`，兩個檔案參照各為 `{path,sha256}`。
- `isolation_dir`：主機共用隔離目錄；所有操作同一媒體的站點必須使用同一處，不能每工作換目錄規避隔離。

`media_identity(target)` 以種類與 WWID 產生 `media:<sha256>`；換埠、容量改變仍取得同一把鎖，
但不因此放寬完整身分與拓撲核對。`bytes` 上限為 16 TiB；首版 logical sector 固定 512 位元組。
physical sector 必須明示，且與 sysfs／描述符 ioctl 相同。

## 授權與來源

`authorization` 精確欄位：

```text
record, hardware_id, media_identity, backup_read, write,
backup_sha256, source_sha256, write_plan_sha256
```

初次備份使用 `backup_read=true`、`write=false`，`backup/write_plan` 與三個授權摘要均為 `null`。
成功備份後才補上參照與寫入計畫；前檢可保持 `write=false`，部署另需 `write=true` 及函式確認參數。
`source` 為 `{path,compressed:{bytes,sha256},raw:{bytes,sha256}}`，不修改原始 XZ。
授權 `source_sha256` 使用 `external.source_digest(source)`，綁定壓縮與原始內容，排除主機路徑。

```text
write_plan.schema = bpi-lab-external-write-v1
write_plan.source_sha256 = external.source_digest(source)
write_plan.ranges = [{offset: 0, bytes: source.raw.bytes, sha256: source.raw.sha256}]
write_plan.tail_policy = preserve-zero
authorization.write_plan_sha256 = media.digest(write_plan)
authorization.backup_sha256 = backup.sha256
```

主機在 SSH 前完整解碼備份與來源，核對 MBR 範圍、分割重疊及指定 ext superblock 的 UUID／容量。
這不是完整 filesystem 健康檢查。MBR 支援 `0x83/0x0b/0x0c/0x0e/0xea`；`0xea` 對應已擷取 Sunplus 原配分割型別。
不接受 GPT、延伸分割或未知型別。上述舊契約的前檢及部署仍要求目標現況完整摘要等於備份，
尾端須已為零；不明示授權時，客戶執行或先前部署已改變內容仍會被拒絕。

### 連續映像重用

專用測試媒體可在保留同板、同完整媒體身分、同 SD／救援及同隔離目錄的初始整碟備份下，
另加 `authorization.scope="reusable-test-area"`。此授權允許本次開始時資料不同於初始備份，
不放寬備份原檔重讀、來源 SHA、媒體配對、閒置狀態、描述符、SD 或期限核對。

```python
write_plan = {
    "schema": "bpi-lab-external-write-v1",
    "source_sha256": external.source_digest(source),
    "tail_policy": "zero",
    "ranges": [
        {"offset": 0, **source["raw"]},
        {"offset": source["raw"]["bytes"],
         **external.zero_digest(target["bytes"] - source["raw"]["bytes"])},
    ],
}
```

每套映像更新 `root`、來源摘要及計畫授權摘要，`backup` 仍指向同一份初始成功備份。
來源段精確為 `[0, rawbytes)`，尾端精確為 `[rawbytes, targetbytes)`，後者以有界零資料實際寫入，
不用 discard；兩段各完整回讀。即使尾端長度為零，也須提供空內容 SHA 的第二段。
主機連線前及遠端開啟描述符前均自行核對全零 SHA，不能拿任意摘要冒充歸零授權。
只有 scope、只有尾端範圍、範圍不連續或尾端 SHA 錯誤均拒絕。
重用前檢僅觀測，不歸零；`baseline_match=false` 明示目前內容不同於初始備份。
此模式涵蓋客戶改寫／擴根後由大映像換小映像，不自動重新備份或改寫原 XZ。

## 執行與證據

正式路徑建立嚴格 SSH 設定與金鑰／known_hosts 固定副本，禁止 Include、代理命令及任意遠端程式。
遠端程式由本版兩模組與既有 XZ 純函式產生，記錄來源及程式摘要，不需遠端安裝本倉工具。
注入的 `transport(argv, chunks, deadline, monotonic)` 必須產生 `stdout/stderr/sent/exit` 事件；
預設使用既有 `upload_stream` 的真正子程序管線。受控 `ops` 必須自行隔離設備，沒有生產模式略過核對旗標。

遠端先訂閱核心媒體事件，再核對配對與各 mount namespace 的掛載、swap、holders／slaves。
描述符取得後重驗裝置號、容量、磁區與可用的 `diskseq`；舊核心的 `diskseq=null` 不冒稱世代已核對，
仍必須持續監看核心事件、固定描述符並作前後完整身分核對。事件溢位、拔插或拓撲事件均失敗。
受保護 SD 始終只開唯讀；目標寫入僅允許計畫範圍。舊契約不清理尾端；
明示重用契約才歸零整個授權尾段，包含可能殘留的舊 GPT 備份表或 RAID 資料。

輸出為新目錄；備份失敗保留 `disk.img.gz.partial`，成功才發布 `disk.img.gz` 與 `manifest.json`。
`request.json`、`stderr.log`、`stdout.log` 保存固定請求及原始線路證據，結果附摘要參照。
結果 schema 固定 `bpi-lab-external-result-v1`，共用欄位包含：

```text
operation, status, hardware_validated=false, root_uuid, blockers,
media_identity, contract_sha256, contract, source, backup, remote,
writer_stopped, quarantined, full_readback_verified, publication
```

部署成功必須有精確 `remote.bytes_written/attempted_end/range/ranges`、完整 `source`、
`readback == source.raw`、MBR／ext 根解析、
等於固定 SD 整碟摘要的 `sd_before/sd_after`，以及 `descriptors_closed=true`、`io_drained=true`。
`source_bytes_written` 與 `tail_bytes_written` 分別計數，總和必須等於 `bytes_written`。
舊模式總量等於來源長度，尾段零寫入且 `tail_before == tail_after`；重用部署總量等於媒體容量，
`tail_readback == tail_after` 必須符合第二段精確全零摘要。`baseline_match` 必須與實測整碟／備份比較一致。
前檢所有寫入計數為零、尾端前後相同，`tail_readback=null`，不得冒稱完整部署回讀。
`validate_result` 逐項核對，不以 `full_readback_verified` 或 `status` 單一欄位放行。
備份產物則由主機重新解碼 gzip，核對精確完整容量及 raw／compressed SHA-256。

遠端媒體鎖持有到 I/O 排空與描述符關閉；失敗寫入亦嘗試在剩餘期限內排空。
主機在操作連線前持久建立 `.pending.json`；沒有完整 writer 終止證據、排空失敗或收據發布失敗時維持隔離。
沒有自動解除、還原、重試或切電入口。
`status=failed` 不代表可開機，即使 `writer_stopped=true` 也仍須 D4 的故障流程。

### 正式收據發布

`publication={schema:"bpi-lab-external-publication-v1",nonce,directory}` 指向原始輸出目錄。
發布順序為原始線路證據、manifest 檔案與目錄同步、隔離目錄內
`<media hash>.<nonce>.complete.json` 憑證及目錄同步，最後才移除 pending 並同步隔離目錄。
憑證綁定 manifest 完整 SHA、檔案身分、原輸出目錄及隔離目錄的裝置／inode，不是無來源布林值。
解除 pending 的同步失敗會保守重建 pending。

`validate_result` 持有同媒體鎖，拒絕任何 pending、缺少完成憑證或搬移／重新綁定的副本，
重讀原始請求與終止紀錄，並核對固定備份來源及部署時的備份檔案身分。
只在這些條件成立且隔離目錄同步成功後返回。內部 `_validate_result_data` 只是預發布資料核對，
不能供 D4／approve 放行。發布前的 `verified` JSON 即使完整留在磁碟，也不等於已完成發布。
此證據依賴受控主機、固定共用隔離目錄及檔案系統同步語意；不能抵抗有權改寫整套證據的管理者，
也不宣稱儲存裝置在斷電下必然遵守 flush。

## 驗證與限制

2026-09-18 新版回歸：D1 13 項、D2 47 項、既有 H618 備份／部署 77 項通過。
當時共享工作樹的 external 系列共 115 項亦通過，包含 D2 與其他模組的對接測試，不重複加總。
`ruff` 與 CLI 說明入口通過；先前初輪數字不作為本次新功能的驗證證明。

```sh
python3 -B -m unittest discover -s tests -p test_bpi_lab_media.py -v
python3 -B -m unittest discover -s tests -p test_bpi_lab_external.py -v
python3 -B -m unittest discover -s tests -p 'test_bpi_lab_external*.py'
python3 -B -m unittest discover -s tests -p 'test_bpi_h618_emmc_*.py'
ruff check tools/bpi_lab_media.py tools/bpi_lab_external.py tests/test_bpi_lab_media.py tests/test_bpi_lab_external.py
```

包含合成 USB／NVMe、壞 WWID、換埠／重複身分、不同 mount namespace、swap／holders、ioctl 漂移、
完整備份／前檢／部署、大映像換小映像及客戶擴根、短寫、超長 XZ、尾段回讀錯誤／逾時、
拔插、排空、SSH 中斷隔離，以及 manifest／目錄／完成憑證同步期間的例外與程序硬退出。
子程序測試使用正式串流與產生的遠端程式，對一般暫存檔執行真正 pread／pwrite／fsync；
區塊型別與 ioctl 明示為替身，不能據此宣稱實板、驅動或真 SSH 網路已驗證。

本版沒有還原 API；備份可供另行人工核定還原。D3／D4 負責根 UUID／LABEL 完整盤點、
固定 SD 暫時唯讀、客戶登入、UART／SSH 同次身分、冷循環與返回救援，D2 不宣稱已完成這些工作。
serial／WWID 不是防偽認證，核心唯讀不是硬體防寫，回讀一致不是斷電持久性或原始 ROM 開機資格。

來源：本倉 `bpi_h618_emmc_backup.py` 的安全檔案／gzip／SSH 純函式、
`bpi_h618_emmc_deploy.py` 的有界 XZ 與串流程序；本機 Q654 kernel 的
`drivers/scsi/scsi_sysfs.c`、`drivers/nvme/host/sysfs.c`、`drivers/usb/core/sysfs.c`、`block/ioctl.c`；
以及 [Linux 區塊 ABI](https://www.kernel.org/doc/html/latest/admin-guide/abi-stable.html)。
Sunplus `0xea` 來源為 `output/evidence/bpi-multiboard-integrate-20260917-f2p-002/extraction/extraction.json`，
本輪沒有重新解壓該原映像或把擷取證據當成實板媒體證據。
