# 跨板測試交付與接板清單

本頁彙整軟體交付與後續集中實板驗證，不取代各模組的受控格式、媒體授權或引導資格。
目前 K3 及外部測試媒體仍在整合，尚未宣告本輪最終完成。

## 範圍

既有目錄共 444 筆映像工作、45 款板型；本輪不重建、替換或刪除原始 XZ。
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

## 已知來源問題

R2 抽樣的 `6.6.153-current-mt7623` 原始環境指定
`/boot/dtb/mediatek/mt7623n-bananapi-bpi-r2`，但缺少該無副檔名檔案。
新的完整擷取再次確認，不是解析器找錯分割區。
原始檔不修改、不自動改選 `.dtb`，也不將此筆標成通過；需另提供修正且固定摘要的來源。

F2P／F2S 已能從原核心內容解析版本，但舊佇列 20 筆 metadata 阻擋不因單板抽樣自動解除。
只有逐筆來源及 metadata 修正審閱後才能另行更新佇列；本輪保留原有工作狀態。

## 證據界線

- 單元、故障注入、模擬 UART、真實組件重播、真 BSP 編譯與實板結果分開記錄。
- Realtek 合併命令及原入口 U-Boot 的本機編譯產物含測試配對，不能直接燒錄給客戶。
- 同家族共用程式不是共用實板資格；救援啟動客戶核心也不等同 ROM／SPL 原始鏈通過。
- 基本短測不代表 DDR 長期壓測、GPU／影片硬解、40-pin、EMAC、USB 或其他周邊已完整驗證。
- 本輪不新增硬體通過結果，不自動啟用 45 個停用站點。

計畫與階段提交見[完整計畫及即時紀錄](bananapi-multiboard-lab-plan-20260917.md)，
操作入口見[總操作文件](bananapi-multiboard-lab-guide-20260917.md)。
