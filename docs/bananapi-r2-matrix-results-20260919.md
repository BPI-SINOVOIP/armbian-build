# R2 十套內部候選交付紀錄

日期：2026-09-19。分支：`bpi-h618-recovery-network-20260915`。
十套候選皆已準備通過，批次退出碼為 0；獨立交付核對亦以退出碼 0 完成。

## 結果與範圍

| 發行版 | CLI／minimal | XFCE |
| --- | --- | --- |
| Bookworm | 既有 F 候選重驗通過 | 前批候選重驗通過 |
| Jammy | 新候選準備通過 | 新候選準備通過 |
| Noble | 新候選準備通過 | 新候選準備通過 |
| Resolute | 新候選準備通過 | 新候選準備通過 |
| Trixie | 新候選準備通過 | 新候選準備通過 |

共十個唯一候選：重用 F 的一套，I 階段新建九套。其中 Bookworm XFCE 已在
`batch-002` 完成，`batch-003` 核對其完整收據後直接引用，不複製、不重做、不重新壓縮。
這是路徑修正與重封裝，不是重新編譯核心、U-Boot 或引導載荷。

原 G 結論仍為 **444 套原映像已檢查，434 套準備通過、10 套 R2 原來源阻擋**。
修正候選不回填原映像通過數，不覆蓋原 Google Drive 上傳目錄，不更動原硬體資料庫。
每套候選只供內部測試，仍未實板驗證、未核定整合、未取得引導載荷對外再散布授權。

## 實際變更

原環境指定的 `mediatek/mt7623n-bananapi-bpi-r2` 不存在；實際原配檔案是平鋪的
`mt7623n-bananapi-bpi-r2.dtb`。工具先逐套核對該 DTB 與原核心的固定摘要，再修正
`/boot/armbianEnv.txt` 中的 `fdtfile`，以註解補足原長度。

每套只修改自身的 141 位元組環境區間，保留原根 UUID、分割表、inode、核心、initrd、
DTB 及開機腳本。另對候選 raw 以原環境在記憶體逆向替換，重算整份原 raw 摘要，
證明區間外沒有變更；不只比較抽樣檔案。

驗收包括唯讀 `e2fsck -f -n`、原配組件回讀、最終 XZ 完整解析、根身分核對，及
XZ／raw／擷取報告的交叉綁定。只有全部相符才發布該套 `receipt.json`。

## 檔案與續作

工作目錄：`/media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-sram-supervisor-plan`。
主批次為 `output/evidence/bpi-r2-matrix-I-20260918/batch-003/`；
各 `jobs/<image_id>/receipt.json` 指向確切候選、準備報告及前批重用來源。
內部 raw 保留供完整逆向核對，不是要求上傳 raw 或改寫原發布目錄。

[十套完整檔案與證據索引](evidence/bpi-r2-matrix-I-20260918/delivery-I-final.json)
逐套列出 `candidate.path`、壓縮檔 SHA-256、校驗旁檔、原來源、修改區間、raw 摘要及重用收據。
索引 SHA-256：`0fea283742bce8c31ec99a9abdbc9aa559ad3908b42fd856416087dcd5c03242`。
`batch-003/summary.json` SHA-256：
`edcae3782e9919d7e3e6c39ea5612b87c88859deb4491316efda12480cd363d3`。

計畫 SHA-256：`53b7394fb0182ee58c212bc679b25f5b23697b7e57cba8b937632d6921352cb4`。
中斷時按[計畫的續作命令](bananapi-r2-matrix-plan-20260918.md)重新核對已完成收據，
只繼續缺少的工作，不刪除舊結果。`batch-002` 的中斷日誌保留，不冒充當時十套已完成。

## 回歸與最終核對

工具提交為 `92fd3d32f637b4573d1bce846eb8b8ab1180a9ef`。
新增 74 項候選矩陣測試，加原九項 R2 測試，共 83 項專用回歸全部通過。
完整 `BPI_LAB_REAL_C3=1` 回歸為 **1,535 項全部通過，零失敗、零跳過，173.278 秒**。
Ruff、45 板／98 項來源一致性，以及限定工具複審通過。
最終 JSON 另經限定獨立複審，十個唯一來源、五 OS 各兩個變體、重用計數及小型引用皆一致；
大型映像核對以主代理實際執行的完整核對輸出為證，不冒稱複審另行重讀映像。

完整回歸日誌為 `output/evidence/bpi-r2-matrix-I-20260918/lab-final-I-001.log`，
SHA-256 為 `d02c874cc17d865fbda36638c2f1a15abdcf5639704eaea9958234adb2719622`。

主代理已以本機 Python 3.10 執行以下獨立的唯讀交付核對，全部通過：

```bash
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B \
  docs/evidence/bpi-r2-matrix-I-20260918/final-audit.py
```

此命令重新核對十套完整收據、校驗旁檔與重用鏈，並核對原 444 套來源身分及原硬體資料庫摘要。
它只輸出報告，不建候選、不操作 UART、電源或實體媒體。
執行輸出保留於 `output/evidence/bpi-r2-matrix-I-20260918/final-audit-001.json`，
與上述 Git 索引逐位元組相同。原硬體資料庫 SHA-256 仍為
`1b94dde10232cf4028e919c65fae98e0f1946ccb351abe9435012f364ff96e13`。

## 尚待接板

本輪離線證據不代表原生冷啟動、RAM、eMMC、網路、GPU、桌面或周邊實測通過。
依[集中接板清單](bananapi-multiboard-lab-handoff-20260918.md)完成板號、UART、電源、
救援 SD、測試媒體與覆寫授權配對後，才進行備份、首次資格循環及完整硬體批次。
