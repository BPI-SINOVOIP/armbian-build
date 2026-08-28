# Banana Pi 2026.08 映像候選發布 Gate 稽核

日期：2026-08-29

## 結論

- 本批 45 個板型目前最高皆為 L2 軟體候選，尚未逐板通過 L3 實機 Gate。
- 45 板、444 個壓縮映像的矩陣建置與完整性整理仍在進行；完成後只能標示為內部驗證候選。
- 在 L3、授權與來源責任人 Gate 閉合以前，不得把整批候選描述為已核准的客戶或公開發布版本。
- 17 個板型另有明確公開發布或再散布阻擋，不能只靠 SHA-256、XZ 完整性或建置成功解除。

## 明確阻擋板型

| 板型 | 主要阻擋 | 依據 |
| --- | --- | --- |
| AI2N | DRP、Codec、相機與 RTL8821CU 資產缺授權及 ABI 契約 | `config/validation/bananapi-renesas-rzv2n-ai2n-legacy.json` |
| AIM7 | 韌體授權、載板差異及實機證據未閉合 | `config/validation/bananapi-rockchip-rk3588-aim7-vendor.json` |
| CM5 Pro | RTL8852BS 韌體授權、載板等同性及外部驅動稽核未閉合 | `config/validation/bananapi-rockchip-rk3576-cm5pro-vendor.json` |
| CM6 | 預建 `esos.elf` 的發布授權未確認 | `docs/evidence/bananapi-family-optimization/F-spacemit-cm6-source-policy-20260827.md` |
| F2P | `ISPBOOOT.BIN`、工具鏈再散布與 eMMC Gate 未閉合 | `config/validation/bananapi-sunplus-sp7021-f2p-legacy.json` |
| F2S | xboot 與工具鏈缺可審計來源或明確再散布授權 | `docs/evidence/bananapi-family-optimization/F-sunplus-f2s-source-policy-20260827.md` |
| Forge1 | RKBin DDR／TEE 授權責任與實機 Gate 未閉合 | `docs/evidence/bananapi-family-optimization/E-rockchip-forge1-source-policy-20260827.md` |
| M1 Super | 韌體逐檔再散布稽核未完成 | `config/validation/bananapi-rockchip-rk3528-m1super-vendor.json` |
| M4 | 不透明啟動／音訊載荷與工具鏈授權未閉合 | `config/validation/bananapi-realtek-rtd1395-m4-legacy.json` |
| M6 | TZK 與 `sm.bin` 缺來源、重建鏈及逐檔授權 | `config/validation/bananapi-vs680-m6-legacy.json` |
| R1 | EOS、目前不支援，且公開發布欄位為禁止 | `config/validation/bananapi-sunxi-a20-r1-archive.json` |
| R2 | MediaTek 啟動載荷未取得書面再散布授權 | `docs/evidence/bananapi-family-optimization/D-mt7623-r2-source-policy-20260827.md` |
| R3 Mini | MT7986 `dram.o` 再散布與 eMMC `boot0` 實測未閉合 | `config/validation/bananapi-filogic-mt7986-r3mini-current.json` |
| R4 Lite | MT7987 預建 DRAM／eFuse 物件授權未補齊 | `docs/evidence/bananapi-family-optimization/D-filogic-r4lite-source-policy-20260827.md` |
| R4 Pro | ATF 預建物件授權、非正式核心與實機 Gate 未閉合 | `config/validation/bananapi-filogic-mt7988-r4pro-current.json` |
| SM10 | ESOS、VPU、PowerVR 授權及量產金鑰隔離流程未閉合 | `config/validation/bananapi-spacemit-k3-sm10-current.json` |
| W2 | 靜態庫、`bluecore.audio` 與工具鏈授權未確認 | `config/validation/bananapi-realtek-rtd1296-w2-legacy.json` |

## 板型特有限制

下列 12 個板型未在已檢查欄位中出現直接的公開發布禁止，但仍須先通過全域 L3 Gate，並在候選說明揭露限制：

| 板型 | 必須揭露的限制 |
| --- | --- |
| F3 | RISC-V 不提供 Bookworm；含預建 ESOS 載荷 |
| M1 Plus | 不得宣稱 Bluetooth 已受支援 |
| M4 Berry、M4 Zero | DDR 尚缺跨樣本、冷啟動、長時間壓力及完整受控矩陣 |
| M5 Pro | 保留 RKBin DDR v1.08，且尚未完成實機驗證 |
| M7 | 保留 DDR v1.11 與 BL31 v1.38，載荷雜湊不代表量產通過 |
| P2 Pro、R2 Pro | 依賴外部預建 RKBin，尚未完成全介面實測 |
| R3、R4 | 目前候選只涵蓋 SD，不得沿用至 NOR、NAND 或 eMMC |
| R64 | 只定義 SD 映像；第二組 PCIe 與 SATA 互斥 |
| W3 | RKBin 只可隨 Rockchip 平台保持未修改散布，且須附授權副本 |

## 一般實機未驗證

其餘 16 個板型只有共同的 L3 實機缺口：6204、CM4 IO、M1、M2、M2 Berry、M2 Magic、M2 Plus、M2 Pro、M2S、M2 Ultra、M2 Zero、M3、M5、M64、P2 Zero 與 Pro。

## 候選整理守門

本機候選整理工具必須同時符合：

1. 固定 45 個英文板型目錄、444 個 `.img.xz`、444 個同名 SHA-256 校驗檔及 90 份雙語候選說明。
2. 一般板型為五個發行版的 CLI／XFCE 十映像；CM6、F3 與 SM10 因 RISC-V Bookworm 缺口為八映像。
3. 目錄不得含 raw `.img`、`.img.txt`、巢狀目錄、符號連結、特殊檔或根目錄散落檔。
4. 每個檔名必須符合板型 token、發行版、分支與 variant；來源須能回溯到成功摘要及含相同映像名稱的建置日誌。
5. 全部 SHA-256 與 XZ 串流必須重新讀取驗證；finalizer 不允許環境變數關閉完整 Gate。
6. 中央候選的 XZ 不得與外部來源共用 inode；刪除外部硬連結前必須保存逐檔路徑與 inode 證據。
7. 雙語說明必須標示 L2／L3 邊界，17 個硬阻擋板型另須明示僅供內部驗證。

## 升級條件

單一板型只有在下列條件全部閉合後，才能從內部候選升級：

- 完成 UART 冷啟動、登入、記憶體、儲存、網路、USB 與重新啟動實測。
- 依板型完成 GPU、VPU、無線、顯示與 40-pin 等實際功能 Gate。
- 完成預建載荷、工具鏈、韌體與授權的逐項再散布核准。
- 由產品、授權及發布責任人確認可交付範圍與必要授權附件。
- 重新產生不含內部候選警示、但仍保留已知限制的客戶發行說明。

本文件只記錄稽核與升級條件，不授予任何再散布權，也不取代實機、法律或產品發布核准。
