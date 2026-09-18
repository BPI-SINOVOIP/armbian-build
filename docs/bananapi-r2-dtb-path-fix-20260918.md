# BPI-R2 current DTB 路徑修正

## 範圍與結論

板級修正在 [current hook](../config/boards/bananapir2.csc) 覆寫
`BOOT_FDT_FILE="mt7623n-bananapi-bpi-r2.dtb"`；另有下節記錄的內部衍生候選。
頂層設定及 `edge` 分支不變；不修改解析器、原 XZ、資料庫、硬體或既有證據。
這是來源配置修正，並非新映像、實板開機、eMMC、量產或對外發布通過的證明。

板級來源變更會觸發 `config/bpi-lab/platforms.json` 的來源摘要守門。
整合已更新唯一受影響來源，並核對全 45 板／98 份來源。
舊 registry 不變；新版保存於
[F 階段來源清單](evidence/bpi-lab-handoff-F-20260918/board-registry.json)，僅更新 R2 的來源摘要。
修正後板級檔案的 SHA-256 為
`1300075172a9738650a7f2e9de89076c0274cb4cd23bed5e63bfd166eee06a84`。
更新來源摘要只接納新配置，不能回溯改寫舊映像的失敗結論。

原錯誤是環境指定的 DTB 路徑與安裝位置不一致，不能擴大解讀成原映像完全沒有 R2 DTB。
前段來源調查使用既有 R2-004 快照及本機套件，不讀完整 XZ；
後續衍生候選階段另完整核對及解壓原 XZ，兩個階段分開記錄。

## 原入口與缺檔證據

證據目錄為
[`output/evidence/bpi-multiboard-integrate-20260917-allboard-bpi-r2-004/`](../output/evidence/bpi-multiboard-integrate-20260917-allboard-bpi-r2-004/)。
以下相對路徑均以該目錄為基準。

- `extraction/file-0008.bin` 是原 `/boot/armbianEnv.txt`，指定
  `fdtfile=mediatek/mt7623n-bananapi-bpi-r2`。
- `extraction/file-0001.bin` 是原 `/boot/boot.cmd`；先設定平鋪 `.dtb` 預設值，
  再匯入環境，然後由 `ext4load` 讀取 `${prefix}dtb/${fdtfile}`。
  它不補副檔名、不刪除子目錄、不回退至預設 DTB；最後以獨立 DTB、uInitrd、zImage 執行 `bootz`。
- `extraction/file-0002.bin` 是原 `/boot/boot.scr`，為未壓縮單項 legacy script，並非 FIT。
  驗證標頭、資料 CRC 及長度表後，其內容與原 `boot.cmd` 完全相同。
- `extraction/volume-01/query-0040.stat` 記錄 `/boot/dtb` 是指向
  `dtb-6.6.153-current-mt7623` 的符號連結；`query-0041.stat` 確認目標目錄存在。
  `extraction/query-0004.stderr` 記錄該目錄下的 `mediatek` 不存在。
  因此單純補 `.dtb` 仍不能修復原路徑。
- `preparation.json` 記錄 `blocked`／`missing_file`。較早 R2-003 的 `dtb_name`
  是解析器限制，而重播的 `reader_failed` 是快照缺證，均不能單獨證明原檔缺失。
  詳見 [MediaTek 原配組件記錄](bananapi-lab-mediatek-20260917.md)。

原 XZ 的來源路徑為
`/media/pi/SMCI/bpi/google-drive-upload/2026/2026.08/bpi-r2/Armbian-unofficial_26.11.0-trunk_Bananapir2_bookworm_current_6.6.153_minimal.img.xz`。
下表為原證據摘要；候選階段另外重新計算原 XZ 及 raw，均與表內一致。

| 證據 | SHA-256 |
| --- | --- |
| 原 XZ | `38a736cbc41c21cb7969e18d0f7954a5ea217af70f347beadb0883ef6e2d58e5` |
| 原 raw，沿用既有擷取紀錄 | `8f0335848b6868e89b2edc21600e26aa85d0c338637d1a62241128e40d3b4f6e` |
| `extraction/extraction.json` | `aa5f8b065ee20e178bfed723f85e589b283ba984feb94bdb57b309fcae30c274` |
| 原 `boot.cmd` | `822a4afad456b46be41657860fb474a565d06f476ab60fe99fbd802216149c57` |
| 原 `boot.scr` | `ced34b5acb06c06838fe950f256bdf5caa53a76e95bd6e58e6672ebb0cac6ce3` |
| 原 `armbianEnv.txt` | `ee6b078261398fdaa857d5c9a3343d773ca9bb79fb407a282cfd82feebd735ad` |
| `extraction/query-0004.stderr` | `d4c8d65cc8ce1aa52bbfe3aee03c2d8411a4350bc2473f051ac14bb50e579f2c` |

## 同版套件與來源

本機套件位於兄弟工作樹
`/media/pi/SMCI/armbian/bpi-v26.2.1-bananapi-parallel/output/debs/`。
兩個檔名分別由 `linux-image-current-mt7623`、`linux-dtb-current-mt7623`
加上共同尾碼
`_26.11.0-trunk_armhf__6.6.153-Sdc61-D0000-P0000-Cdcf3-H8075-HK01ba-V014b-Bf00c-R448a.deb`
組成。

| 證據 | SHA-256 |
| --- | --- |
| kernel 套件 | `b6ee041c3854a2a151411c25c5acccd5d7fdca80da5737ecfeaca2e9dfc1988e` |
| DTB 套件 | `ce5280141c393fddb7251102085b745ab41696ec47d94ff10d1ee98174f23a9c` |
| kernel 本體，與原配擷取檔相同 | `020f92c6a93f0f06f94963f9d535959885beaad49ffc716e480567c8f92b1bec` |
| kernel config，與原配擷取檔相同 | `cc85863073c38f1d52147e479e4e60ea81f9941d110e925bbc8fbb6298eee04c` |
| 套件中的 R2 DTB，34,525 位元組 | `55151de1694bb279e759498eb5f86253e0e90700408044c546b4310a2a81c796` |

DTB 套件提供 `/boot/dtb-6.6.153-current-mt7623/mt7623n-bananapi-bpi-r2.dtb`，
不是 `mediatek/` 子目錄。kernel config 的 `CONFIG_ARCH_WANT_FLAT_DTB_INSTALL=y`
與此相符。DTB 的 `model` 為 `Bananapi BPI-R2`，有序 `compatible` 為
`bananapi,bpi-r2`、`mediatek,mt7623`；這些是板型識別技術字串。

同一工作樹的 `cache/sources/linux-kernel-worktree/6.6__mt7623__armhf/`
保留 DTS、已編譯 DTB、zImage 與 `.config`，產物雜湊亦相符。
板級來源固定 `dc6160265ffc795a1832bc1424f58291d152c7bb`；
本次未因 Git 中繼資料權限限制而宣告已獨立確認該工作樹的 HEAD。

## 聚焦測試

測試位於 [test_bananapi_mt7623_r2_candidate.py](../tests/test_bananapi_mt7623_r2_candidate.py)。
一般測試以乾淨 shell 環境載入板級與家族設定，依分支呼叫 hook，
核對 `current` 的有效路徑、固定來源與 `edge` 不變。
另以無硬體副作用的命令替身執行原 `boot.cmd`，檢查環境匯入後的載入位址、
路徑、順序及 `bootz` 參數；舊錯誤值仍照原值載入，不被測試工具偷偷修補。
此測試不是完整 U-Boot 模擬器，也不執行映像或操作媒體。

```bash
python3 -B -m unittest discover -s tests -p 'test_bananapi_mt7623_r2_candidate.py' -v
BPI_R2_DTB_REAL=1 python3 -B -m unittest discover -s tests -p 'test_bananapi_mt7623_r2_candidate.py' -v
PYTHONDONTWRITEBYTECODE=1 output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest discover -s tests -p 'test_bpi_lab_mediatek.py' -v
```

未設定 `BPI_R2_DTB_REAL=1` 時明示略過本機證據測試；啟用後若固定快照、套件、
`dpkg-deb` 或 `fdtget` 不可用即失敗，不把缺證當通過。
此測試核對快照及套件雜湊、原腳本封裝、缺檔查詢、套件內的平鋪路徑與 DTB 身分，
並比對 kernel、config 及 kernel 套件內的同份 DTB；全程只讀取或在記憶體處理，
不安裝套件、不擷取至磁碟、不讀原 XZ，也不修改證據。

本次執行結果：

- 修改板級設定前，新增的有效 `current` 配置測試失敗，另兩項分支與原腳本測試通過。
- 修正後一般模式共 13 項，12 項通過、1 項明示略過。
- 啟用本機原證據與套件核對後，13 項全通過。
- MediaTek 10 項回歸初次因板級來源摘要失配阻擋；主代理更新摘要後重跑全通過，
  包含原錯誤 DTB 路徑仍回報 `missing_file`、不改載腳本預設檔的反例。

## 衍生候選驗證

同版套件只供交叉比對。後續已在固定原映像內讀到正確平鋪 DTB，與上述套件摘要相同，
才只修改衍生副本的環境；沒有從套件移入 DTB，沒有覆寫 R2-004 的阻擋結果。

相關程式為 [固定樣本衍生工具](../tools/bpi_lab_r2_repack.py) 及
[位元組界線測試](../tests/test_bpi_lab_r2_repack.py)。
該流程須先確認固定原 XZ／raw 與原映像內的平鋪 DTB、kernel 摘要，
再透過 ext 區塊映射定位原本 141 位元組的環境檔，只在新 raw 副本替換同長度內容。
修正路徑省下的 5 位元組以 `#F3` 註解及換行補齊，不更動 inode 或其他位元組。
本次已完成候選回讀、七項原配組件不變核對、唯讀 `e2fsck`、家族準備與根 UUID 綁定，
再壓縮並留下 `candidate.json`。最終 XZ 又由正式準備入口完整讀取、解壓及解析，
結果為 `prepared`、`source_reread=true`、`hardware_validated=false`。

證據根目錄為 `output/evidence/bpi-lab-handoff-F-20260918/`：

| 項目 | 結果或位置 |
| --- | --- |
| 衍生候選 | `r2-candidate-002/Armbian-unofficial_26.11.0-trunk_Bananapir2_bookworm_current_6.6.153_minimal_f3-dtb-path.img.xz` |
| 候選 XZ 摘要 | `f2e4516e1007d1e71119a4077f5465ff3588f37af3730a75786c6a4540db9788` |
| 候選 raw 摘要 | `20051c2d8dbdfb171784cdbdfdf24d444418d8df9477108df797efab0b4cc945` |
| 唯一可改範圍 | raw 偏移 `1228353536` 起的 141 位元組，其餘位元組保持原樣 |
| 檔案系統 | `r2-candidate-002/e2fsck.stdout`，唯讀檢查結束碼 0 |
| 最終 XZ 重驗 | `r2-final-xz-check-001/preparation.json`，`prepared`、零阻擋 |

`r2-candidate-001` 在寫入副本前因原環境權限是 `0600`、不是初始假設的 `0644`
而拒絕；保留該失敗擷取。工具已按實際固定樣本核對 `0600`、單連結、141 位元組與
extents，新增回歸，不放寬成任意 inode，也不改寫原權限。

此候選是**內部重封裝實驗，不是重新編譯或客戶發布版**。
既有 `boot_blob_redistribution_authorized=false` 限制未變，不因修正路徑而獲得對外發布資格。
原客戶 XZ 與 444 筆原佇列未替換；其他九套 R2 映像未重封裝，不能由本樣本外推通過。
實板開機、eMMC 與完整恢復循環仍須另行測試。
