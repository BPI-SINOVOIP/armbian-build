# 跨板測試交付與接板清單

本頁彙整軟體交付與後續集中實板驗證，不取代各模組的受控格式、媒體授權或引導資格。
本輪計畫 C／D／E 及補查 F 所列、能離線完成的受控軟體實作與整合已完成，包含 K3 與 USB／NVMe。
這不是「45 款板子已實測可用」或「444 套映像已通過」的宣告。
下方明列軟體交付、來源阻擋與接板後工作；沒有把未驗證項目算成完成。

分支為 `bpi-h618-recovery-network-20260915`，不是 `main`。
主要交付提交為 `a3c642601`（外部媒體）、`46dca7afe`（K3）、
`c28488ef5`（外部根與五階段）、`6c8042e50`（真實目標執行環境）。
續作提交為 `c2b023490`（ARM32／RISC-V 真實探測）、`4fa51404b`（唯讀查詢）、
`cfbc9652f`（Sunplus 20 份逐筆重審及副本續作）。
F 階段新增 `c88c949c7`（共用 eMMC 首輪到站點匯出）、`e8980a0ac`（候選匯入守門）、
`eab01ed88`（R2 current 路徑修正及內部衍生候選）。F 是後續操作路徑補查發現的缺口，
不以先前 C／D／E 回歸通過宣稱當時已驗證這些新增流程。

G 階段 `1da0d6399` 新增[完整映像離線續跑](bananapi-lab-matrix-20260918.md)。
正式 444 筆批次正在執行；既有 45 板抽樣仍保留歷史範圍，不提前改稱全部完成。

## 範圍

既有目錄共 444 筆映像工作、45 款板型；本輪不重建、替換或刪除原始 XZ。
另有一份 R2 內部衍生候選，不計入或取代這 444 筆原工作。
ARM32 為 16 款、ARM64 為 26 款、RISC-V 為 3 款。
這是目前已收錄的板型，不是所有歷代 Banana Pi 產品或所有硬體修訂的支援聲明。

| 家族 | 板型 |
| --- | --- |
| Allwinner，16 款 | `bpi-6204`、`bpi-m1`、`bpi-m1p`、`bpi-m2`、`bpi-m2b`、`bpi-m2m`、`bpi-m2p`、`bpi-m2u`、`bpi-m2z`、`bpi-m3`、`bpi-m4b`、`bpi-m4z`、`bpi-m4z-emac`、`bpi-m64`、`bpi-p2z`、`bpi-pro` |
| Amlogic，4 款 | `bpi-cm4io`、`bpi-m2pro`、`bpi-m2s`、`bpi-m5` |
| MediaTek，7 款 | `bpi-r2`、`bpi-r3`、`bpi-r3mini`、`bpi-r4`、`bpi-r4lite`、`bpi-r4pro`、`bpi-r64` |
| Rockchip，9 款 | `bpi-aim7`、`bpi-cm5pro`、`bpi-forge1`、`bpi-m1super`、`bpi-m5pro`、`bpi-m7`、`bpi-p2pro`、`bpi-r2pro`、`bpi-w3` |
| SpacemiT，3 款 | `bpi-cm6`、`bpi-f3`、`bpi-sm10` |
| Realtek，2 款 | `bpi-m4`、`bpi-w2` |
| Sunplus，2 款 | `bpi-f2p`、`bpi-f2s` |
| Renesas，1 款 | `bpi-ai2n` |
| Synaptics，1 款 | `bpi-m6` |

[逐板抽樣及來源摘要](evidence/bpi-multiboard-integrate-20260917/sample-audit.json)
記錄每板一份實際來源，共 44 份組件準備通過、一份阻擋。
有些使用已核對擷取的重播，不代表本輪重新讀取 444 份 XZ。
`prepared` 只表示原配組件已準備，不表示實板或完整原生開機鏈已通過。

## 已完成軟體

| 範圍 | 已交付內容 | 不能外推的範圍 |
| --- | --- | --- |
| 排程及來源 | 45 板／444 筆持久工作、已做與未做分列、去重、續作、失敗隔離 | 不代表全部映像內容或硬體通過 |
| Allwinner、Amlogic | 原配組件、DT／CMA、固定引導配置、eMMC 五階段與救援 | 逐板 RAM、控制器、DDR 及冷啟動仍須觀測 |
| Rockchip、MediaTek、SpacemiT K1 | 原入口／extlinux／受限 FIT、原環境與檔案摘要、專用執行路由 | 不接受未知腳本、未核定 overlay 或前置韌體 |
| Sunplus、Renesas、Realtek、Synaptics | 各家族原格式與執行器；Realtek M4／W2 合併救援命令真 BSP 編譯 | 合成配對產物不能直接燒錄 |
| SpacemiT K3 | SM10 原廠 SDK 真編譯、原入口與 SD RAM 救援、正式五階段接線 | FSBL／ESOS／SBI、簽署及安全鏈仍需實體資格 |
| 無 eMMC 板 | 固定 SD 加 USB／NVMe、完整備份、明示重用及歸零、回讀、首次核定 | 首版限 512-byte MBR 主分割／ext 根／已核定 mainline U-Boot |
| 客戶系統交接 | 固定 SD 暫時唯讀、根身分、有限末分割增長、同次 UART／SSH、授權公鑰安裝 | 不抵抗任意 root 程式，非整個原生開機鏈證明 |
| 執行環境 | ARM32／ARM64／RISC-V 封裝介面、原生救援建置，三架構均有真 QEMU 使用者空間探測 | 僅覆蓋明列來源組合，未做板級啟動，不等同各 OS 均通過 |

外部媒體操作文件為[媒體契約](bananapi-lab-external-media-20260918.md)、
[根保護及 Python 封裝](bananapi-lab-external-root-20260918.md)、
[五階段與首次核定](bananapi-lab-external-backend-20260918.md)。
一般原入口與特殊韌體的支援範圍以各模組文件為準；不能用同家族名稱取代格式核對。

## 最終離線驗證

**最新 G 階段：1,443 項全部通過，零失敗、零跳過。**
包含新增 62 項批次守門；F 的 1,381 項紀錄保留為歷史證據。命令如下：

```bash
BPI_LAB_REAL_C3=1 output/evidence/bpi-sram-supervisor/model-venv/bin/python \
  -B -m unittest discover -s tests -p 'test_bpi_lab*.py'
```

日誌為 `output/evidence/bpi-lab-handoff-F-20260918/lab-final-F.log`。
此結果包含首輪 83 項、候選守門六項及 R2 重封裝九項，不能再相加。
另啟用 `BPI_R2_DTB_REAL=1`，R2 板級與同版套件 13 項全部通過、零跳過。
所有 `bpi_lab*.py` 工具與測試，以及 R2 板級測試的 Ruff 亦通過；45 板／98 來源核對通過。
[F 階段最終摘要](evidence/bpi-lab-handoff-F-20260918/delivery-F-final.json)
保存固定命令、來源、候選及原庫摘要。原 registry 保留，新 registry 只更新 R2 板級來源。

E 階段 1,355 項的歷史日誌仍為
`output/evidence/bpi-crossarch-runtime-20260918/lab-final-E4-mode-fixed.log`。
[E 階段最終摘要](evidence/bpi-multiboard-integrate-20260917/delivery-E-final.json)
保留當時命令、日誌與程式摘要，不改成目前程式的測試證據。

以下保留 C／D 歷史驗證，日誌均在 `output/evidence/bpi-multiboard-integrate-20260917/`，
互有重複的測試組不相加；未修改的 H618／SRAM 產物沿用明列證據，不冒稱本輪重跑。

| 驗證 | 結果 | 日誌或證據 |
| --- | --- | --- |
| 跨板完整整合 | 執行 1,289 項，1,287 項通過、2 項依環境開關跳過，零失敗 | `lab-delivery-final.log` |
| 上列跳過的真組件組另行啟用 | `BPI_LAB_REAL_C3=1`，39 項全部通過，零跳過；包含 CM6 FIT 與跨板原組件重播 | `original-entry-delivery-final.log` |
| 舊 H618 回歸 | 362 項全部通過 | `h618-delivery-final.log` |
| SRAM 分版本回歸 | V1 為 249 項、V2 為 428 項、V3 為 188 項，全部通過、零跳過，輸入快照未變更 | `sram-v1-final/`、`sram-v2-final/`、`sram-v3-final/` 的 `validation-report.json` |
| 五個發行版真封裝 | Bookworm／Jammy／Noble／Trixie／Resolute 的唯讀擷取、入口摘要及硬連結保留通過 | [來源證據](evidence/bpi-multiboard-integrate-20260917/external-init-profiles.json) |
| 真 AArch64 使用者空間 | 原 shell、Python 固定模組、blkid、注入相依後 shell 四項通過；120 項相依，14 項保留原配 | [執行證據](evidence/bpi-multiboard-integrate-20260917/external-runtime-audit.json) |
| K3 真 SDK 建置 | 實際 ELF／binary 連結及主代理 21 筆來源／產物核對通過 | [建置證據](evidence/bpi-multiboard-integrate-20260917/k3-runtime-build-audit.json) |
| 靜態與入口 | 所有 `bpi_lab*.py` 工具與測試的 Ruff、差異空白檢查、七個主要 CLI `--help` 通過 | 最終交付摘要另存固定雜湊 |
| 來源快照 | 45 板、98 項本機來源一致性通過 | `bpi_lab_platforms.py validate --json` |

本輪另補查 SRAM 舊模型。第一次泛用 discovery 未指定各版本建置，產生六個設定錯誤及
三個跳過，保留在 `sram-delivery-final.log`，**不列為通過**。
後續改用既有 `validate_bpi_sram.py`，分別指定 V1／V2／V3 及其 DDR／更新器／橋接產物，
各次新證據獨立保存，不以混用快照或刪掉失敗日誌消除錯誤。

測試命令、結果、日誌路徑與 SHA-256 彙整於
[最終機器可讀交付紀錄](evidence/bpi-multiboard-integrate-20260917/delivery-final.json)。
該紀錄也保留首次 SRAM 設定失敗與硬體佇列快照，便於中斷後核對續作。

分工期間已有限定獨立審查；本次收尾修正與整合由主代理核對，沒有宣稱最後每一筆變更
都另經第二位審查者複審。單元模型、真組件重播、使用者空間模擬器與實板證據分開保存。

續作 E1／E2 已補上 ARM32 與 RISC-V 各四項真實 QEMU 探測，快取擷取新增 14 項回歸。
詳見[跨架構實證及使用方法](bananapi-lab-runtime-cache-20260918.md)。上表及
`delivery-final.json` 保留 C／D 完成時的快照；新證據不覆蓋前輪失敗或冒充實板通過。
E3 的 20 份來源已逐筆準備及重審通過，18 份完整重讀、兩份使用固定擷取重播。
[重審文件](bananapi-lab-metadata-review-20260918.md)及
[主代理核對紀錄](evidence/bpi-multiboard-integrate-20260917/metadata-review-audit.json)
保存完整候選、獨立副本示範與 46 項專用回歸，不自動修改原硬體資料庫。
最後的模式契約補強另有原 444 筆工作唯讀核對；沒有將此前真來源示範冒稱為修正後重跑。

## 固定測試流程

1. 唯讀核對原 XZ、分割表、根識別、核心、initrd、DTB 與原引導設定。
2. 以實際板號配對 UART、電源、固定 SD 與可覆寫媒體，完整備份並核對雜湊。
3. 在獨立救援中核對同次 UART／SSH、RAM 根、媒體身分與閒置狀態。
4. 依明示範圍寫入測試媒體，完整回讀；不寫 SD 救援、eMMC boot0／boot1 或 RPMB。
5. 單次引導原配客戶組件，核對 Linux、根裝置、核心與板型，再返回固定救援。
6. 第一套完整循環經具名審閱後才核定該站，之後由持久佇列依序測試其他映像。

eMMC 使用[共用後端](bananapi-lab-shared-backend-20260917.md)及
[首次核定](bananapi-lab-qualify-20260917.md)。
沒有 eMMC 的板子另採固定 SD 加 USB／NVMe，不能把外部磁碟偽裝成 MMC CID。
不同媒體、不同板號、不同引導 ABI 的核定互不通用。

正常流程可在完成初次部署後自動切換測試系統，不需要每套插拔 SD。
這不是永不需要人工介入的保證：SD／供電／UART 硬體故障、無法確認遠端 writer 已停止，
或救援入口本身失效時，保持隔離，不猜測切電或重刷。
Linux 暫時唯讀也不是 SD 的硬體防寫，不能承諾抵抗任意具有 root 權限的惡意映像。

## 接板時集中準備

| 項目 | 需要確認的內容 |
| --- | --- |
| 板子 | 正式板型、硬體修訂、板號、DDR 顆粒及容量；不沿用別片板的資料 |
| UART | 接線電壓、baud、穩定 `/dev/serial/by-id/` 路徑與實際連接板子 |
| 電源 | `bpi-pw` 名稱、IP、MAC、插座配對；正常關機與故障斷電分別授權 |
| 救援 SD | 可冷啟動的固定入口、CID、容量、控制器及受保護內容摘要 |
| 測試媒體 | eMMC CID，或 USB／NVMe 真實身分及拓撲；容量、完整備份、核定可覆寫範圍 |
| 救援軟體 | 正確架構的核心、DTB、initramfs、必要驅動、Python、SSH 與網路 |
| 引導介面 | 真 U-Boot 版本／命令、啟動來源、RAM／LMB、載入位址及前置韌體限制 |
| 登入 | 可用帳號，或明示可修改首次帳號；測試金鑰及 hostkey 綁定，憑證不提交 Git |
| 第一套來源 | 該板原始 XZ 及固定 SHA-256；不把另一個 OS 或板型的核定當作本套結果 |

這些資料可在接板時一次採集；使用者不必陪同每套映像逐次操作。
沒有先取得上述核定的站點仍保持停用。

接板後由工具完成各板原生救援建置／runtime 探測、首次備份及第一套完整循環；
使用者需提供實際板子、連線與媒體覆寫授權，不需要逐套陪同操作。
本輪未產生逐板可直接部署的真實配對與簽署套件，不能拿合成測試 JSON 代用。

原硬體資料庫最後唯讀盤點仍為：45 個停用站、414 筆 `queued`、20 筆 `metadata_blocked`、
10 筆 `review_required`；新硬體嘗試與新硬體報告均為零。
既有 0845 十套歷史證據仍保留待審，不算本輪重新測試。
E3 獨立示範副本則為 434 筆 `queued`、10 筆 `review_required`、20 筆 `superseded`；
舊 20 筆工作內容保留、其餘 424 筆及 45 個站點不變。副本不是正式執行佇列。

## 已知來源問題

R2 抽樣的 `6.6.153-current-mt7623` 原始環境指定
`/boot/dtb/mediatek/mt7623n-bananapi-bpi-r2`，但缺少該無副檔名檔案。
新的完整擷取再次確認，連 `mediatek` 子目錄也不存在，不是解析器找錯分割區或單純副檔名差異。
原始檔不修改、不自動改選 `.dtb`，也不將此筆標成通過；需另提供修正且固定摘要的來源。

F 階段已完成來源修正及一份內部衍生候選：`current` 改為平鋪的
`mt7623n-bananapi-bpi-r2.dtb`，`edge` 不變。已確認原映像本來含該 DTB，
候選只改環境的 141 位元組區間，保留原核心、initrd、DTB、腳本及其餘 raw 位元組。
候選的 `e2fsck` 與最終 XZ 完整組件準備皆通過；原來源仍為阻擋，不混成 45 份原映像全通過。
位置及固定摘要見[R2 修正說明](bananapi-r2-dtb-path-fix-20260918.md)。
這是重封裝而非重新編譯，僅供內部驗證，既有開機載荷的對外發布限制未解除。

F2P／F2S 原檔名的版號 `0` 已有全部 20 份原核心證據，實際均為
`5.4.35-legacy-sunplus-sp7021-bpi`。完整候選及副本續作驗證已完成，
不再列為「尚未逐筆核對」；原佇列因本輪明示不改寫而保留舊狀態。
接板前另經整合審閱才可採用候選；候選的 `integration_approved=false` 與
`hardware_validated=false` 保留，不藉資料修正授予實板或發布資格。

## 證據界線

- 單元、故障注入、模擬 UART、真實組件重播、真 BSP 編譯與實板結果分開記錄。
- Realtek 合併命令及原入口 U-Boot 的本機編譯產物含測試配對，不能直接燒錄給客戶。
- 同家族共用程式不是共用實板資格；救援啟動客戶核心也不等同 ROM／SPL 原始鏈通過。
- 基本短測不代表 DDR 長期壓測、GPU／影片硬解、40-pin、EMAC、USB 或其他周邊已完整驗證。
- 本輪不新增硬體通過結果，不自動啟用 45 個停用站點。

計畫與階段提交見[完整計畫及即時紀錄](bananapi-multiboard-lab-plan-20260917.md)，
操作入口見[總操作文件](bananapi-multiboard-lab-guide-20260917.md)。
