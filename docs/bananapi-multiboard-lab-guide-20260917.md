# 跨板映像測試工具操作說明

## 最新整合入口

本節記錄 2026-09-18 續作；下方盤點與模擬數字仍是原始 P 階段證據，不是新硬體結果。
完整板型範圍、已知來源問題與接板需求集中於[交付與接板清單](bananapi-multiboard-lab-handoff-20260918.md)。
目前固定流程為「核對客戶原映像、準備原配組件、核定救援與媒體、單套首次循環、審閱、批次排程」。
不要求為測試而重建全部客戶映像，也不以既有檔名或模擬通過跳過實板資格。

| 工作 | 文件與工具 |
| --- | --- |
| XZ／原映像的 MBR／GPT、多分割與原配組件核對 | [準備入口](bananapi-lab-prepare-20260917.md)，`tools/bpi_lab_prepare.py` |
| 全部 444 筆逐套準備、去重與中斷續跑 | [完整離線批次](bananapi-lab-matrix-20260918.md)，`tools/bpi_lab_matrix.py`；不修改硬體佇列或升格資格 |
| Allwinner 與 Amlogic 組件、DTB、CMA 核對 | [Allwinner](bananapi-lab-allwinner-20260917.md)、[Amlogic](bananapi-lab-amlogic-20260917.md) |
| Rockchip、MediaTek、SpacemiT 原始引導設定 | [原入口](bananapi-lab-original-entry-20260917.md)、[extlinux](bananapi-lab-extlinux-20260917.md) |
| Sunplus、Renesas、Realtek、Synaptics 差異 | [特殊平台](bananapi-lab-special-20260917.md) |
| 在同架構原 Linux 建置獨立救援 | [跨架構救援](bananapi-lab-rescue-build-20260917.md)，`tools/build_bpi_lab_rescue.py` |
| H618 的既有 0845 配對及五階段 | [H618 適配器](bananapi-lab-h618-adapter-20260917.md) |
| 其他家族的固定配置、完整部署、登入及返回 | [共用後端](bananapi-lab-shared-backend-20260917.md)，`tools/bpi_lab_backend.py` |
| 第一片板的單套授權、證據審閱與資格檔 | [首次核定](bananapi-lab-qualify-20260917.md)，`tools/bpi_lab_qualify.py` |
| 共用 eMMC 首輪後接正式批次 | 同一工具的 `station` 子命令，重驗首輪並匯出兩層資格及站點，預設停用 |
| K3 原廠入口、獨立救援 U-Boot 與真實 SDK 編譯 | [K3 執行器](bananapi-lab-k3-runtime-20260918.md)，`tools/bpi_lab_k3_runtime.py` |
| 固定 SD 加 USB／NVMe 測試區 | [外部媒體](bananapi-lab-external-media-20260918.md)、[五階段與核定](bananapi-lab-external-backend-20260918.md) |
| 外部根保護與目標 Python 封裝 | [根保護](bananapi-lab-external-root-20260918.md)，`tools/bpi_lab_external_bundle.py`、`tools/bpi_lab_external_guard.py` |
| F2P／F2S 原版號 `0` 的逐筆核對與完整候選 | [重審工具](bananapi-lab-metadata-review-20260918.md)，只產生候選及獨立副本，不直接匯入正式佇列 |

首次核定與已核定佇列是兩個入口，避免「尚未測第一套卻先要求全部通過」的循環依賴。
首次核定仍須有實際配對、備份、可覆寫範圍及引導資格，不能用它繞過媒體保護。
各入口的格式、韌體 ABI 與儲存媒體限制以其文件為準；有程式入口不等於 45 板皆已可用。

### 接板時一次準備的資料

1. 板型、修訂、板號及 DDR；UART 的穩定裝置路徑和 baud。
2. 電源設備名稱、IP、MAC 與實際插座配對，正常關機及故障切電分別授權。
3. 固定救援 SD 與可覆寫 eMMC 的 CID、容量、控制器；完整備份、雜湊及該媒體授權。
4. 已能開機的救援核心、initramfs、DTB、必要網路驅動，以及 U-Boot 版本、命令、RAM／保留區觀測。
5. 同板的第一套客戶 XZ 及固定 SHA-256；專用 SSH 公鑰，私有帳密另存，不提交 Git。

沒有 eMMC 的板型使用獨立 USB／NVMe 契約與外部後端，不能套用雙 MMC 身分。
外部後端首版限 512-byte MBR 主分割、直接 ext 根與明示 mainline U-Boot 載入；
網路只作固定組件傳輸，不將 NFS 根、未知原廠容器或 GPT 外部根視為已支援。
實板接入後先驗證一套的正常循環及失敗返回，再批次跑 OS；不要求使用者陪同每套操作。
若遠端 writer 是否停止不明，保留隔離，不以自動斷電或重刷解決。

## 目前可以做什麼

以下保存 P 階段的盤點與模擬紀錄。現行軟體交付及限制請以上方入口、交付清單為準。
`bpi_lab_platforms.py validate` 檢查的是 A 階段來源快照，45 板／98 項來源核對通過
不會將歷史 `backend_complete`、`execution_ready` 或任何硬體資格欄位自動升格。

`tools/bpi_lab.py` 提供映像盤點、站點登記、持久排程、分階段執行、續作、故障返回及結果彙整。執行環境為 Linux、Python 3.10 以上，僅使用標準函式庫；沒有新增服務或雲端 CI。本輪不操作任何實體板。

已盤點 45 板、444 個壓縮映像，來源原檔不修改。依本工作樹的板型設定靜態分類為 arm32 16 板／160 套、arm64 26 板／260 套、riscv64 3 板／24 套；不是從映像內執行檔驗證的架構，也不是映像建置來源提交證明。

- F2P／F2S 的 20 套檔名核心版號為 `0`，保持 `metadata_blocked`，不猜測版本、不啟動適配器。
- CM6／F3／SM10 各有八套；目前目錄未提供各自的兩套 Bookworm，不列為已存在映像。
- 424 筆已完成五階段**模擬**，有 2,120 份階段收據。沒有解壓或讀取這些映像內容，沒有硬體通過聲明。
- 再次匯入新增零筆，再次執行模擬完成零筆，證明不重做相同工作。
- 舊 0845 十套證據以固定摘要引用。相關停用硬體工作先列 `review_required`，不從頭重跑，也不當成目前 eMMC 仍有那些映像。

## 本機位置

專案：`/media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-sram-supervisor-plan`。

工作資料：`output/evidence/bpi-multiboard-lab-20260917/`。

| 路徑 | 用途 |
| --- | --- |
| `catalog-001.json` | 444 筆唯讀盤點，含旁檔宣告與檔案身分 |
| `hardware.sqlite3` | 真實工作準備清單；45 個站點全部停用 |
| `stations/` | 各板待配對設定，不是可燒錄設定 |
| `simulation.sqlite3` | 完全分開的模擬資料庫 |
| `simulation-stations/` | 純模擬站點 |
| `simulation-runs/` | 模擬請求、結果、收據與輸出 |

所有命令在專案根目錄執行。以下 `python3` 亦可換成本機既有的 `output/evidence/bpi-sram-supervisor/model-venv/bin/python`。

## 查看進度

```bash
python3 -B tools/bpi_lab.py status --db output/evidence/bpi-multiboard-lab-20260917/hardware.sqlite3
python3 -B tools/bpi_lab.py jobs --db output/evidence/bpi-multiboard-lab-20260917/hardware.sqlite3 --state review_required
python3 -B tools/bpi_lab.py status --db output/evidence/bpi-multiboard-lab-20260917/simulation.sqlite3
```

`status`／`jobs` 使用 `mode=ro` 與 `query_only`，不建立資料庫、不初始化表格或改寫
`journal_mode`／`user_version`；未知或未初始化版本直接拒絕。
查詢保留 SQLite 對有效 WAL 的讀取，不以 `immutable=1` 略過尚未 checkpoint 的進度。
這是資料庫唯讀語意，SQLite 仍可能使用讀取協調旁檔，不宣稱檔案系統零操作。

硬體準備清單初始為 414 筆 `queued`、20 筆 `metadata_blocked`、10 筆 `review_required`。`queued` 僅代表已排程，**不代表站點已配對、適配器存在或允許寫入**。

`collected` 表示本次五階段契約報告通過，不代表桌面、GPIO、GPU 或長期穩定性全通過。`hardware` 與 `simulation` 分開統計；`all_hardware_tests_passed` 不會因短測或模擬變成真。歷史失敗與例外另列，不能只看最後成功數。

`historical_nonpassing_reports` 與 `historical_execution_errors` 只統計新佇列的嘗試，不涵蓋引用進來的舊報告。舊 0845 的 RCU／LZMA、服務失敗及缺測仍在[第一輪總結](bananapi-m4zero-ten-image-first-pass-20260917.md)與原證據；新資料庫計數為零不表示舊失敗已消失。

## 重新盤點與排程

```bash
python3 -B tools/bpi_lab.py catalog \
  --root /media/pi/SMCI/bpi/google-drive-upload/2026/2026.08 \
  --build-root /media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-sram-supervisor-plan \
  --output output/evidence/bpi-multiboard-lab-20260917/catalog-002.json

python3 -B tools/bpi_lab.py prepare \
  --catalog output/evidence/bpi-multiboard-lab-20260917/catalog-002.json \
  --db output/evidence/bpi-multiboard-lab-20260917/hardware.sqlite3 \
  --stations-dir output/evidence/bpi-multiboard-lab-20260917/stations
```

新輸出檔名不可覆寫既有檔案。清單按完整來源目錄快照匯入；已移除或條件更換的待跑工作改為 `superseded`，歷史紀錄仍保留。已配對站點請另存設定，用 `register` 更新，不修改產生用的 `pending-*` 範本。新資料庫若要避免重跑舊十套，須引用歷史：

普通 `prepare`／`queue.import_catalog()` 拒絕頂層或逐筆含 `metadata_review` 的重審候選；
CLI 在開啟或建立資料庫之前即拒絕，`--simulation` 也不繞過這個限制。
手改 `integration_approved=true` 不是整合審閱，不會讓候選獲准匯入。
E3 工具只在核對原來源、完整候選及全部原工作後，對新建立的獨立副本示範遷移；
副本不是正式佇列，原庫保持不變。這是正常工具入口的工作流程限制，不是防禦任意
Python 呼叫、刪除追溯欄位或手改 SQLite 的沙箱；不得以這些方式繞過整合審閱。

```bash
python3 -B tools/bpi_lab.py history \
  --db output/evidence/bpi-multiboard-lab-20260917/hardware.sqlite3 \
  --report output/evidence/bpi-h618-no-mux/T6-first-pass-001/summary.json \
  --sha256 d5dab342aec0bce1fae3bc7f22b53b9bdf6d6a823f1daaa7e84c8714a8f9577a
```

歷史引用只建立人工審查保留，不憑舊成功收據跳過目前媒體核對。原報告未明示的板號仍保持未知；不同實板不可承接另一片的通過結論。`release --work-key <工作鍵> --reason <補測原因>` 可釋出已審工作，但不會自動將缺測改成通過。

## 純離線演練

```bash
python3 -B tools/bpi_lab.py prepare \
  --catalog output/evidence/bpi-multiboard-lab-20260917/catalog-001.json \
  --db output/evidence/bpi-multiboard-lab-20260917/simulation.sqlite3 \
  --stations-dir output/evidence/bpi-multiboard-lab-20260917/simulation-stations \
  --simulation

python3 -B tools/bpi_lab.py simulate \
  --db output/evidence/bpi-multiboard-lab-20260917/simulation.sqlite3 \
  --evidence output/evidence/bpi-multiboard-lab-20260917/simulation-runs
```

`simulate` 只接受內建模擬器，拒絕外部程式及硬體站點。結束碼 `0` 表示所列工作無待處理問題；`2` 為輸入或契約錯誤；`3` 表示仍有失敗、阻擋或未收集工作。本輪因 20 筆 `metadata_blocked`，結束碼為 `3`，不是程序崩潰。再次執行不重跑已收集項目。

## 實板加入流程

1. 提供板型／硬體修訂、板號、DDR、UART 穩定身分、電源配對及可覆寫媒體。先確定正常救援與資料傳輸路徑。
2. 複製相應停用範本到獨立站點檔，填入實際身分、引導配置 SHA-256、測試版本、適配器入口與摘要。不得保留 `pending-*` 身分或只把 `enabled` 改成真。
3. 平台適配器實作 `preflight → deploy → boot → smoke → recovery`，有界返回結構化報告。`deploy` 必須核對媒體、來源及完整回讀；`boot` 必須證明是候選核心與根系統，不是救援核心。
4. 每片媒體完成備份與核對，取得該片的 userarea 覆寫授權。其他片的備份或授權不可移用。
5. 首次平台適配由工程流程受控驗證一套完整循環及返回救援；先前 H618 原型可參考，但不能自動核定新站點。產生綁定站點／硬體／媒體／引導／適配器／測試版本的資格 JSON，再啟用批次站點。

共用 eMMC 後端完成 `approve` 後，先把產生的後端資格參照加入一份新的後端設定，
再用 `bpi_lab_qualify.py station` 匯出站點。它會重驗完整首輪，不是把後端資格檔
直接填入佇列要求的另一種 schema。預設 `enabled=false`；只在明示
`--enable-reviewed-station` 時匯出啟用設定，仍不自動 `register` 或寫入原資料庫。
完整命令與設定摘要的更換方式見[站點匯出](bananapi-lab-qualify-20260917.md)。

```bash
python3 -B tools/bpi_lab.py register --db <資料庫> --station <已核定站點.json>
python3 -B tools/bpi_lab.py schedule --db <資料庫> --station-id <站點代號>
python3 -B tools/bpi_lab.py run --db <資料庫> --station-id <站點代號> --evidence <新證據目錄> --limit 10
```

共用排程及已明示格式的家族適配器已有實作，支援界線以本頁最新整合入口及交付清單為準；
**各板真正的引導／燒錄／返回救援仍須實測核定**。不能把建立 45 個範本說成 45 板已實測。
沒有 eMMC 的板型採已授權 USB／NVMe 契約，不能直接套用 0845 的儲存路徑，
也不能將尚未支援的網路根視為已完成後端。

## 中斷與失敗

```bash
python3 -B tools/bpi_lab.py resume --db <資料庫> --work-key <工作鍵> --evidence <證據目錄>
python3 -B tools/bpi_lab.py recover --db <資料庫> --work-key <工作鍵> --evidence <證據目錄>
python3 -B tools/bpi_lab.py retry --db <資料庫> --work-key <工作鍵> --reason <限定重試原因>
```

- 先核對執行意圖及已落盤收據；若收據已完成而資料庫尚未更新，恢復紀錄，不重送部署。
- 沒有部署收據的未知寫入不自動重刷；先明示返回救援，再決定新嘗試。
- 可以續作時仍先要求適配器核對目前板上狀態。適配器不支援現況核對就停止，不以歷史成功推定媒體仍正確。
- 失敗階段不能推進為通過；故障返回失敗就隔離站點，連共用資源的其他站點也不能接手。
- 同一主機共用 `/var/tmp/bpi-lab-locks/` 的程序鎖與持久占用，跨資料庫也維持占用。程序被殺死不會自動解除不明媒體狀態。不同主機沒有分散式鎖，不能共同控制同一實體站點。
- 不手動刪鎖目錄來解決忙碌。若工作已安全結束、僅中斷於鎖清理，可用 `unlock --db <資料庫> --work-key <工作鍵>`；未安全結束者必須先救援。

## 適配器與證據限制

外部適配器經 stdin 接收 `bpi-lab-request-v1`，stdout 回傳 `bpi-lab-stage-v1`。必須回映工作鍵、嘗試、階段、站點／硬體身分、映像與引導摘要、測試版本及模式；任何錯配都拒絕。結果為 `passed`／`failed`／`blocked`。資格格式及各階段必要宣告見 `tests/test_bpi_lab_station.py` 的資格與報告 fixture。

每階段保存 `request.json`、`stdout.log`、`stderr.log`、`response.json`；核對後再保存 `queue-receipt.json` 並更新資料庫。命令不使用 shell，限制執行時間、輸出大小，逾時終止程序群組。入口固定摘要與不可變副本不能代替整個執行環境核定；直譯器、相依套件及 argv 引用檔案仍由平台適配負責。這不是惡意程式沙箱。

資格 JSON 與報告契約不是獨立實物鑑定器；操作人員仍須審查原始證據。不能自行填真值來宣稱測通。平台長測、桌面登入、GPU、GPIO／I2C／SPI、影音與周邊須有真正測項，不能由單一 `smoke` 標籤推定。

## 本機回歸

```bash
python3 -B -m unittest discover -s tests -p 'test_bpi_lab*.py'
python3 -B -m unittest discover -s tests -p 'test_bpi_h618*.py'
```

測試使用臨時目錄與假資料；不與實體 UART、電源或 eMMC 互動。進度及驗收範圍見[計畫](bananapi-multiboard-lab-plan-20260917.md)及[板型清單](evidence/bpi-multiboard-lab-20260917/board-registry.json)。

本輪驗收為 168 項主控台／網路／跨板工具測試及 356 項 H618 回歸，共 524 項通過，Ruff 通過。424 筆模擬批次證明基本排程可運作；其後的中斷與競態修正由故障回歸、原收據重核對及零重做續作確認，沒有把模擬升格為實板測試。
