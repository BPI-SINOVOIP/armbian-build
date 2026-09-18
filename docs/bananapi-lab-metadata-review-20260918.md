# E3：F2P／F2S 核心版本 metadata 逐筆審閱

本工具只產生完整候選清單及固定審閱證據，不更新既有硬體 SQLite、不啟用站點，
不修改原始 XZ、檔名、旁檔或原映像內容，也不授予實板、整合或發布資格。
新增實作為 `tools/bpi_lab_metadata_review.py`，專用回歸為
`tests/test_bpi_lab_metadata_review.py`。

## 固定範圍

- 輸入必須為固定 SHA-256 的原始 45 板、444 筆完整 catalog，不接受只含 20 筆的子集。
- 僅接受 F2P／F2S，五個發行版 `bookworm`、`jammy`、`noble`、`resolute`、`trixie`，
  各含 `minimal`、`xfce_desktop`，合計恰為 20 筆。
- 每筆原 `kernel` 必須為字串 `0`，且唯一問題須為原檔名產生的 `unknown_kernel`。
  板型、架構、家族、分支、OS／角色、來源身分、旁檔與 `image_id` 均須一致。
- 未知問題、重複來源、範圍擴張、來源或旁檔變更、路徑跳轉與符號連結均拒絕。
- 全部新輸出只能位於 `output/evidence/bpi-metadata-review-20260918/` 的新子目錄；
  既有輸出不覆寫。工具沒有修改原硬體 DB 的入口。

## 三個入口

### 逐筆準備

`prepare` 呼叫既有 `bpi_lab_prepare.prepare()`，以 `sunplus`、原板名與
`kernel_release=0` 核對各自的 uImage、CRC、內嵌核心版本、initrd modules、DTB、
原環境及根身分。無固定擷取者完整核對及讀取自己的 XZ；有固定完整擷取者才重播。
原檔身分與旁檔在準備前後均須維持固定盤點值。

重播索引 schema 為 `bpi-metadata-replays-v1`，逐筆列出 `image_id` 與
`extraction: {path, sha256}`。摘要是 extraction JSON 的摘要，不是 XZ 摘要。
每層重播都必須回到同一原始路徑、壓縮長度及 XZ SHA-256，最多八層。
重播的 `source_reread=false`，不能宣稱本次重新讀取原 XZ。

`request.json` 固定每筆原 metadata 摘要、板名、OS、角色、來源摘要、準備引用及重讀狀態。
來源失敗逐筆記錄，不以另一份抽樣代替，也不因失敗跳過其他來源。

### 重審與候選

`review` 要求全部 20 份獨立準備證據。它重新呼叫既有 prepare，從固定 extraction
重播原核心及組件，再比對原 preparation 的派生結果、組件摘要、根身分與完整版本。
不是只相信 `status=prepared`、人工填寫版號或候選 JSON 內部互相比對。

只有全部 20 筆通過才寫出 `catalog-candidate.json`；有任一阻擋則只寫審閱紀錄，
不輸出部分放行候選。固定證據關係如下：

1. 原 catalog 及 request 以實際檔案 SHA-256 固定。
2. `review-record.json` 保存逐筆 proof、審閱者、原因及離線限制，沒有候選摘要，避免循環引用。
3. 候選頂層及逐筆追溯固定同一份 catalog、request、review-record、審閱者與原因。
4. `review.json` 另列候選摘要及完成狀態。

候選保留原順序、全部 444 筆、原 `image_id`、原檔名、原來源摘要及身分。
只有這 20 筆的 `kernel`、`issues`、新增 `metadata_review` 可變動；板級與總問題
僅清除這 20 個已核對問題。其餘 424 筆內容不變。
原盤點的 `metadata_only`／`source_verified` 旗標不重寫；本次來源核對及重讀範圍
明確保存在新增追溯中，不能解讀成已重新驗證所有 444 份內容。

所有追溯層嚴格檢查 schema、欄位集合及型別。新核心必須等於逐筆 proof 的原核心解析值，
並綁定原 entry 摘要、來源摘要、來源身分、板名／OS／角色。
`integration_approved=false`、`hardware_validated=false` 不得改為真。

### 暫存資料庫示範

`demonstrate` 只接受固定摘要、沒有 `-wal`、`-shm`、`-journal` 的靜止 DB。
這不是 live WAL 讀取器；有活動旁檔時拒絕，不以 `immutable` 忽略 WAL。

示範前先重讀實際固定 catalog／request／review-record，核對候選與審閱紀錄逐筆一致，
再對每筆原 preparation 及當時的 revalidation 各呼叫 `verify_preparation()` 重播。
即使同時手改候選核心、proof、審閱紀錄並重新計算摘要，仍須符合真組件重新解析結果。

隨後以 `mode=ro&immutable=1` 開啟原靜止 DB，只寫獨立 SQLite 副本。
在副本匯入之前，全部 444 筆都經 `queue.get_job()` 核對工作鍵、來源 metadata 摘要、
映像摘要與站點摘要，並另比對原 catalog 完整 entry、來源根目錄、現行站點設定、
板名、硬體識別、引導設定及測試版本。不能接受「前後保持同樣篡改」作為安全證明。
工作列與 body 的模式一致仍不充分，另強制 `body.mode == station.mode == hardware`；
即使將舊阻擋工作的列與 body 同步改為 `simulation` 並重算工作鍵，仍須拒絕。

整份候選重匯入後必須符合：舊 20 筆保留為 `superseded` 且 body 不變；其他 424 筆
整列不變；新增恰好 20 筆 `queued`；45 個站點設定整列不變且維持停用；
`attempts=0`、`reports=0`。原 DB 前後 SHA-256 與旁檔狀態另行核對。

## 本次執行

根目錄為 `output/evidence/bpi-metadata-review-20260918/`。

| 階段 | 證據 |
| --- | --- |
| 固定重播索引 | `replays.json` |
| 20 筆真來源準備 | `sources-001/request.json`、`prepare-001.log` |
| 20 筆固定組件重審 | `review-001/review-record.json`、`review-001/review.json` |
| 完整 444 筆候選 | `review-001/catalog-candidate.json` |
| 副本重匯入與再次證據核對 | `demo-001/demonstration.json`、`demo-001/evidence-check/` |
| 專用回歸 | `unit-005.log`，46 項全部通過、零跳過；包含同步篡改工作模式拒絕案例 |
| 最終交付摘要 | `completion-002.json`，固定模式補強後的工具、測試、文件與證據摘要 |

`completion.json` 與 `unit-004.log` 保留為前一版紀錄，不代替最後模式補強的結果。
`demo-001` 保留原真來源示範；此次補強不重讀 XZ，另以新專用回歸驗證副本流程，
並以唯讀方式重新核對原 DB 的 444 筆工作符合新模式契約。

20 筆真來源準備全數通過，來源阻擋為零。兩板各一份 Bookworm minimal 使用原固定
擷取重播，`source_reread=false`；其餘 18 筆完整讀取各自 XZ。
20 筆均從原核心解析出 `5.4.35-legacy-sunplus-sp7021-bpi`，後續重審只重播擷取，
不再讀取 XZ。單元測試使用的合成核心、DTB、擷取及 DB 不混入這 20 份真來源證據。

嚴格副本示範已通過：444 筆原工作契約有效、舊 20 筆保留為 `superseded`、新增 20 筆
`queued`，其他 424 筆及 45 個停用站點整列不變，嘗試與報告皆為零。
示範前逐筆重新核對原準備及固定重驗證，原 DB 前後摘要相同。

已執行的主要參數：

```text
prepare --catalog output/evidence/bpi-multiboard-lab-20260917/catalog-001.json
        --catalog-sha256 7ec027496d0e9215ce673f41570875647df077b1766e6190cf6b0b5e41df0fe0
        --replays output/evidence/bpi-metadata-review-20260918/replays.json
        --replays-sha256 198add597a7450d53b4f4b0023ee892603055a6e52d5712345dbac88713ad078
        --output output/evidence/bpi-metadata-review-20260918/sources-001
review  --request output/evidence/bpi-metadata-review-20260918/sources-001/request.json
        --request-sha256 dd05e330c3e688588576091715ad6f7569521524bae51d3c63908c7c86e5e240
        --reviewer Codex-E3
        --reason 逐筆離線來源與原核心證據核對；待主代理整合審閱，不授予硬體或發布資格
        --output output/evidence/bpi-metadata-review-20260918/review-001
demonstrate --candidate output/evidence/bpi-metadata-review-20260918/review-001/catalog-candidate.json
            --candidate-sha256 c6d39f8caa27c10b7bb65702042a0aa2d320741ac470482a087c5f18df282fe5
            --db output/evidence/bpi-multiboard-lab-20260917/hardware.sqlite3
            --db-sha256 1b94dde10232cf4028e919c65fae98e0f1946ccb351abe9435012f364ff96e13
            --output output/evidence/bpi-metadata-review-20260918/demo-001
```

三個命令均由 `python3 -B tools/bpi_lab_metadata_review.py` 呼叫；`review` 及
`demonstrate` 也必須帶相同的 `--catalog`／`--catalog-sha256`。
上列是既有執行紀錄，不可覆寫重跑；後續審閱只使用固定 extraction 與新的證據子目錄。

專用驗證命令：

```bash
python3 -B -m unittest discover -s tests -p test_bpi_lab_metadata_review.py -v
ruff check --no-cache tools/bpi_lab_metadata_review.py tests/test_bpi_lab_metadata_review.py
```

## 限制

OS／角色由原固定 catalog、完整 XZ 摘要與精確來源檔名逐筆配對；原 preparation
沒有獨立的 rootfs OS／桌面安裝證據，本工具不宣稱已驗證那些內容。
SHA-256 是來源一致性與追溯，不是來源發布者真實性或數位簽章。
R2 不在 E3 範圍，原缺 DTB 問題及來源保持原狀。
本工具不修改其他工具、計畫或交付文件，不自行加入 Git 暫存、提交或推送。
