# 0845 的 SPL1 最小升級計畫與紀錄

## 授權與範圍

2026-09-16，使用者同意[前階段提出的升級方案](bananapi-m4zero-uart-ddr-offline-20260915.md)：一次更新救援入口，保留第 0 號槽及原系統。板子為 BPI-M4 Zero `0845`，UART 為 `/dev/ttyUSB0`；電源僅限 `bpi-pw-1`，預期 MAC `EC-B9-31-24-F7-D1`。

唯一允許寫入的 SD 位元組區間為 `[8192,49152)`，共 40,960 B。不寫分割表、第 0 號槽、其他槽、Linux 分割區或 eMMC，不修改原本 `bpi_sram_sd_minimal.py` 的固定雜湊守門。

| 產物 | 固定 SHA-256 |
| --- | --- |
| 舊 `build-009/spl1-egon.bin` | `aef7a1a8c4eb84eb73b4fee519b93561ef5722fc0fc442049e308836695f7f76` |
| 新 `loader-v2-build-003/spl1-egon.bin` | `80e67d7ebaacb58d8b2b64a6a01a94b4d4329c5f11f8f9cb35e917d060f7e33d` |
| 不變的第 0 號槽 `spl2-smoke.sram` | `8f98a2e2f612341b0d108f147cd6c9dcfbb957c4c0577c5d6e171c6e898bd062` |

新版 SPL1 位於 `output/evidence/bpi-h618-recovery-network/P4A-0845-20260915/loader-v2-build-003/`。舊產物及先前部署證據均保留，不覆寫。

## 執行順序

1. 先同步本計畫。重新確認串口占用、板子目前執行階段、具名插座身分。若仍停在 SRAM 救援而未進 Linux，可受控斷電；若已進 Linux，須先正常關機。不得依歷史狀態直接切電。
2. 建立專用單範圍升級工具與離線反例測試；另一獨立工作準備嚴格 ABI 2 的 smoke 主機工具，不放寬 ABI 1 的相容檢查。
3. 等 SD 插回主機，核對 CID、精確容量、裝置號、512 B 扇區、SD 類型、分割區布局、掛載、swap 與 holders。任何不符即不寫入。
4. 唯讀備份當下前 4 MiB，確認卡上舊 SPL1 與槽 0 雜湊。保存原始前綴、預期前綴、候選檔案與可信備份清單雜湊；首尾各 1 MiB 的 Linux 分割區抽查另記錄。這不是整卡備份。
5. 寫前重驗身分與內容，只更新唯一白名單範圍，同步後完整回讀前 4 MiB；核對槽 0、分割表、其他前綴位元組及分割區抽查未變。失敗保留證據，不自動重寫或拔卡試跑。
6. SD 插回 `0845` 後，先測 ABI 2 救援、UART smoke、第 0 號槽 smoke、無負載逾時回復及 DDR 負載的就緒／封鎖行為。每次單次負載結束需受控重新供電，不假設可返回 SPL1。
7. 同步操作紀錄、驗證結果與限制；只有實際回讀及上板測項通過才更新對應狀態。

## 復原與限制

- 復原使用本次新備份，還原舊的已驗證 SPL1；不拿最初原系統備份混作本次回復基準。
- 復原仍只可寫同一個 40,960 B 範圍；若白名單外、媒體身分或分割區抽查已變，停止自動操作。
- 更新 SPL1 自身失敗時，UART 救援不一定存在，仍可能需要人工將 SD 插回主機。
- 本輪不啟用 DDR 訓練或 792 MHz 掃描。PMIC／幾何驗證仍封鎖，DDR 負載回報 `blocked` 不等於 DDR 測試通過。
- 分割區首尾抽查不是全分割區雜湊驗證；只能結合寫入範圍審查及已回讀範圍敘述證據，不誇稱完整系統已重新驗證。

## 可續作狀態

| 階段 | 狀態 | 證據與待辦 |
| --- | --- | --- |
| 使用者授權 | 已取得 | 本輪同意 40 KiB 最小升級 |
| 初始主機盤點 | 已完成 | 無 SD 區塊裝置；`ttyUSB0` 無其他持有者；不操作其他磁碟 |
| 計畫同步 | 已完成 | `78372c72e` 已推送至本工作分支 |
| 工具與離線驗證 | 已完成 | 428 項測試、8 組稽核通過；輸入未變，零失敗 |
| SD 備份及寫入 | 已完成 | 新備份、唯一 40 KiB 寫入、完整前 4 MiB 回讀一致 |
| 新版實機驗證 | 未執行 | 已通知使用者將 SD 插回 `0845`，插座仍關閉 |

## 已完成的現場核對

證據目錄為 `output/evidence/bpi-h618-recovery-network/P4B-0845-20260916/`，每份新證據使用新檔名，不覆寫前輪產物。

- `initial-probe.json`：新 nonce 交握回報 ABI 1、板型 `06180001` 與三項舊版能力，確認仍在 SRAM 救援，未交接負載。
- `power-status.json`：首次檢查因預期 MAC 分隔符使用連字號、工具輸出使用冒號而停止，沒有送出關閉。改用工具既定格式後重新核對同一設備。
- `power-verified-1-status.json`、`power-verified-2-off.json`、`power-verified-3-status.json`：核對 IP、MAC、具名設備與型號後關閉並回讀，均退出 0；最後 `device.on=false`、身分核對成功。
- 使用者回覆「已插回」後，主機辨識到同一 SD，精確容量 63,864,569,856 B，分割區起始扇區 8192、長度 123461632 扇區。主機曾自動掛載 `/media/pi/armbi_root`，確認無使用者程序占用後正常卸載，沒有使用強制卸載。
- `sd-readonly-inspection.json`：前 4 MiB SHA-256 仍為 `3d88fc15f1d86ddb23e86285d844dcce6ae210e76fba166ee8ec1ab5e4965596`，舊 SPL1 與槽 0 也吻合。分割區尾端抽查不變、首端抽查與前輪不同；已觀察到本次自動掛載，但不能僅憑此歸因所有差異。因此本次會重新備份及建立新抽查基準，不沿用舊清單宣稱系統逐位元組未變。
- `storage-and-writers-preflight.json`：以 `findmnt` 的 JSON 與目錄裝置號確認證據位於 `/dev/nvme0n1p2`、`ext4`、`259:2`，不是目標 SD 或 tmpfs；可用空間足夠。以 root 盤點 `/proc` 的區塊裝置描述符，沒有其他程序持有 SD 整卡或分割區；`fuser` 核對也無持有者。

獨立審查未見白名單越界，但通用工具未自行限制備份檔案系統，且 Linux 的區塊裝置 `O_EXCL` 不能排除所有不遵循排他的原始寫入者。本次部署須額外執行上述持久儲存核對，寫入前及完成後再盤點持有者；所有子代理明確禁止操作實機。這是受信任、單一操作者主機的操作前提，不是對其他惡意特權程式的強制隔離保證。

## 操作工具

新增 `tools/bpi_sram_spl1_upgrade.py`，重用原工具的媒體身分、掛載與抽查檢查，但沒有呼叫原本兩範圍寫入函式。其唯一 `pwrite` 位置只能落在本次 SPL1 範圍；清單也固定用途與產物，不接受其他升級版本。

執行位置為本工作樹。下列變數須由本次身分核對及新備份結果設定，不得猜測或拿另一張卡的清單代入：

```bash
sudo python3 -B tools/bpi_sram_spl1_upgrade.py prepare \
  --device /dev/mmcblk0 --cid "$VERIFIED_CID" \
  --bytes "$VERIFIED_BYTES" --devnum "$VERIFIED_DEVNUM" \
  --board-label 0845 --evidence-dir "$NEW_BACKUP" \
  --spl1 output/evidence/bpi-h618-recovery-network/P4A-0845-20260915/loader-v2-build-003/spl1-egon.bin

sudo python3 -B tools/bpi_sram_spl1_upgrade.py apply \
  --device /dev/mmcblk0 --cid "$VERIFIED_CID" \
  --bytes "$VERIFIED_BYTES" --devnum "$VERIFIED_DEVNUM" \
  --board-label 0845 --evidence-dir "$NEW_BACKUP" \
  --prepared-sha256 "$TRUSTED_PREPARED_SHA256" --confirm-write
```

`prepare` 只讀卡並新建主機備份，目錄已存在就停止。`apply` 必須通過清單外部雜湊及備份回讀；相同內容已存在時記錄為 `already_matches`，不再寫卡。復原使用相同已核對變數，把動作改成 `restore`；允許修復只在 SPL1 範圍內的部分寫入，每次嘗試各自保存，不覆蓋原備份。

新版 `tools/bpi_sram_v2_uart.py` 專用於 ABI 2 與固定 smoke。上載及讀槽都要求本機固定封包作為摘要比對來源，成功交接後該工作階段不可重用。原 ABI 1 工具維持原樣，新工具沒有 DDR 執行入口。

```bash
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B tools/bpi_sram_v2_uart.py probe \
  --port /dev/ttyUSB0

output/evidence/bpi-sram-supervisor/model-venv/bin/python -B tools/bpi_sram_v2_uart.py upload \
  --port /dev/ttyUSB0 --input output/evidence/bpi-sram-supervisor/build-009/spl2-smoke.sram --run

output/evidence/bpi-sram-supervisor/model-venv/bin/python -B tools/bpi_sram_v2_uart.py load-slot \
  --port /dev/ttyUSB0 --slot 0 \
  --input output/evidence/bpi-sram-supervisor/build-009/spl2-smoke.sram --run
```

兩次 `--run` 之間必須確認前一負載已停在 SRAM，再受控重啟並重新交握。上述是已部署後的程序，不可在卡仍插主機時給板子上電。

## 本次部署結果

使用者要求停止追加檢查、直接燒錄時，必要新版整體回歸已完成，隨即執行 `prepare` 與 `apply`；未再安排另一輪完整舊版測試。未執行的測項不計入本輪通過數。

- `validation-v2-001/validation-report.json`：428 項測試及 8 組稽核通過，17 個驗證命令皆退出 0，輸入在驗證期間未變。包含原 SD 24 項、新升級 54 項與新 ABI 2 smoke 38 項。
- 驗證報告 SHA-256：`c419052ee70cd1a8660f4a977b9456fe4df5c0d9c3a4f83203cbd2fb64419819`。
- 本次備份目錄：`output/evidence/bpi-h618-recovery-network/P4B-0845-20260916/deployment/`。`prepared.json` 的外部可信 SHA-256：`481d9c332811b74b927be70e79d128ddd653aecf43f2179928f3c5ddd603ae10`。
- `apply` 使用上述精確雜湊，退出 0，只寫 `[8192,49152)`；`apply-0001/result.json` SHA-256：`86eb575de7dab7dd9b9973ffba8527487325a0f073fa0d9eda8f78e46c0d4946`。
- 回讀完整前 4 MiB SHA-256：`59a8e09244f38ac4134944e23212e4ca69d754171b0ac830694b910a457012f2`，等於預期。SPL1 為固定新版雜湊，槽 0 為固定舊 smoke 雜湊。
- 白名單外所有前綴位元組與本次備份一致；分割區首尾各 1 MiB 抽查一致。未寫原系統分割區，不宣稱做過全分割區驗證。
- 寫入期間由工具重驗身分及布局；寫前與寫後的 `fuser` 無其他持有者，並非連續原始描述符隔離。沒有執行實卡故障注入、復原或第二次重複寫入。
- 最後媒體描述符已關閉，UART 已釋放，`bpi-pw-1` 保持關閉；等待使用者插回板子。現在只確認部署及回讀成功，尚不能宣稱新版已啟動，更不能宣稱 DDR 已通過。
