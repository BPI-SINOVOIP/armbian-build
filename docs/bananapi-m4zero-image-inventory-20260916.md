# M4 Zero 十映像唯讀定位與盤點

日期：2026-09-16。對應 [免 SD 切換器計畫](bananapi-m4zero-ten-image-no-mux-plan-20260916.md) 的 T0。

## 目前狀態

主代理依使用者「客戶使用 EMAC」的既有要求，確認候選 B 為本輪最新待交付十套：
上傳區 `bpi-m4z-emac`，核心 `6.18.49`，五個 OS 各一個 `minimal` 及 `xfce_desktop`。
已於 `2026-09-16T13:44:09Z` 啟動完整唯讀串流驗證，於 `2026-09-16T13:50:04Z` 完成，
十套全數通過、零失敗，退出碼 `0`。候選 A 普通 Zero 只做小型盤點，
不讀映像大檔；候選 C 舊 EMAC `6.18.48` 保留為歷史對照，不混入本輪十套，也不重驗大檔。
本輪 T0 的來源索引及離線完整性部分已完成，不是硬體通過；開機組件仍未解析，
不宣稱涵蓋計畫內全部開機組件盤點或實板驗收。

主代理提供 0845 eMMC 精確容量 `31289507840` bytes，僅作離線容量比較，並不授權寫入。
Wi-Fi 啟用後 eMMC 從 `mmcblk1` 改為 `mmcblk2`，SD 容量 `63864569856` bytes 不變；
本工具不使用這些裝置名稱，也不開啟任何裝置、UART、網路、掛載或硬體控制介面。
0845 仍是普通 Zero，尚無 EMAC 外接硬體，EMAC 介面未測。選定 EMAC 客戶映像不會消除這項板型及測試範圍限制。

## 三批候選

| 候選 | 完整目錄 | 檔數 | 檔名核心 | 板型 |
| --- | --- | ---: | --- | --- |
| A | `/media/pi/SMCI/bpi/google-drive-upload/2026/2026.08/bpi-m4z` | 10 | `6.18.49` | `bananapim4zero` |
| B | `/media/pi/SMCI/bpi/google-drive-upload/2026/2026.08/bpi-m4z-emac` | 10 | `6.18.49` | `bananapim4zeroemac` |
| C | `/media/pi/SMCI/armbian/bpi-v26.2.1-bananapi-optimize/output/images/2026.08/bpi-m4zero-emac-a1-h618-optimized-792-matrix` | 10 | `6.18.48` | `bananapim4zeroemac` |

三批皆有 `bookworm`、`jammy`、`noble`、`resolute`、`trixie`，各含檔名原樣的
`minimal` 與 `xfce_desktop`。C 的 TSV 另以 `cli`／`xfce` 描述角色，保留該來源字樣，
不將 `CLI`、`cli`、`minimal` 或缺少角色的檔名自動當作相同值。
`bpi-m4b` 是 Berry，不在本次候選。初始曾因 0845 是普通 Zero 而提請考慮 A，
但主代理已確認客戶使用 EMAC，故本輪改定 B；不能把 B 的宣告板型改寫成普通 Zero。

### 來源紀錄

- A、B 各自的 `Release-Notes-zh-TW.md` 記錄來源提交
  `c8673931c96c23c510dd29a28440e77f0b03286f`，BSP 基準則是
  `8893355b34efc97a1e7677c6541beb177ec014e1`，不得混稱兩者。
  倉內 `docs/evidence/bananapi-release-deduplication/final-20260907/完整性稽核/候選處置.tsv`
  也逐映像記錄上述最終來源與壓縮雜湊。上傳區 `2026.08/README.md` 明確說明目錄名稱不代表全部映像建於八月。
- C 的 `BUILD_PROVENANCE.tsv` 記錄 Bookworm、Trixie 四套來源
  `b2e663bb8afbde54307b1ee8334ed602293d70f0`；Jammy、Noble、Resolute 六套來源
  `61bed876ebd608626b1d729c3cac43280d7449ae`。`userpatches_sha256` 原值是 `unrecorded`，不能補猜。
  另有 `IMAGE_MANIFEST.tsv`、`VALIDATION_REPORT.txt` 及
  [8/30 工程交付紀錄](bananapi-m4zero-emac-image-matrix-delivery-20260830.md)。
- [8/20 普通 Zero A1 紀錄](bananapi-m4zero-a1-792-image-matrix-delivery-20260820.md)
  曾列出普通 Zero `6.18.32` 十套，來源 `6e05b3313317936d8e6abbd32a49dbcd9f4e0109`；
  指定輸出路徑的 `*m4zero*` 本次只找到 C，因此不把舊文件中的 A1 當作已找到的現存候選，也未另掃全碟。

這些都是發布或建置程序紀錄，不是密碼學來源認證，也不是本輪硬體通過證明。

## 本輪候選 B 十個完整檔名

| 完整檔名 | 壓縮 bytes | 實算解壓 bytes |
| --- | ---: | ---: |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_bookworm_current_6.18.49_minimal.img.xz` | 328413172 | 1543503872 |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_bookworm_current_6.18.49_xfce_desktop.img.xz` | 1016372620 | 5091885056 |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_jammy_current_6.18.49_minimal.img.xz` | 335672848 | 1514143744 |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_jammy_current_6.18.49_xfce_desktop.img.xz` | 961758396 | 4638900224 |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_noble_current_6.18.49_minimal.img.xz` | 327007440 | 1547698176 |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_noble_current_6.18.49_xfce_desktop.img.xz` | 954581880 | 4995416064 |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_resolute_current_6.18.49_minimal.img.xz` | 343624288 | 1539309568 |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_resolute_current_6.18.49_xfce_desktop.img.xz` | 1045781612 | 5532286976 |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_trixie_current_6.18.49_minimal.img.xz` | 345341952 | 1556086784 |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_trixie_current_6.18.49_xfce_desktop.img.xz` | 1112874504 | 5691670528 |

本輪新的盤點清單、雜湊及逐檔證據目錄：

```text
/media/pi/SMCI/bpi/evidence-20260916T134400Z-737ad9c22dc44db3a1f8c4e9dcb649d3/inventory.json
SHA-256：3b3bba0de68d79e7a87d6737e61008ad1b0058d14babf0ef546e7e8ee3872bd4
run-20260916T134409Z-1f9bd85e4f414605b8531017663b7e94/
```

B 的發布說明記錄來源 `c8673931c96c23c510dd29a28440e77f0b03286f`，
盤點同時固定該說明檔的 SHA-256，不以目前工作分支 HEAD 取代來源。
B 沒有同目錄的原始 IMG 雜湊清單，本輪已實算解壓大小及 SHA-256，
但不能宣稱與未知的可信原始 IMG 雜湊比對通過；壓縮 SHA-256 則逐套與發布旁檔比對。

### 完整驗證結果

- 十套的 XZ 完整性及壓縮 SHA-256 比對全部通過；每套均為單一 XZ 串流，具有 CRC64 檢查碼。
- 合計讀取壓縮內容 `6771428712` bytes，串流解碼 `33650900992` bytes；未產生完整 `.img`。
- 每套各自小於 `31289507840` bytes；最大 Trixie XFCE 為 `5691670528` bytes。
  容量判斷以一次一套為準，不要求十套合計同時容納於 eMMC。
- 每套均有一個型別 `0x83` 的 MBR 主分割區，起點 LBA `8192`，未越界或重疊。
  這是分割表初檢，未藉型別值推定 ext4 內容或檔案系統已驗證。
- 十筆原檔 `dev/ino/size/mtime/ctime` 前後一致；未修改原檔、掛載、操作硬體或寫入媒體。

完整逐檔壓縮／解壓大小、SHA-256、MBR 與限制旗標位於首次執行的 `summary.json`：

```text
run-20260916T134409Z-1f9bd85e4f414605b8531017663b7e94/summary.json
SHA-256：2d599719b331924b4395299f61e256a5b567316305b61bd7ce436a0574f0464c
```

`2026-09-16T13:50:34Z` 以相同命令實測續作，退出碼 `0`，十套全部 `resumed: true`，
只核對原檔身分及成功證據，不重讀映像大檔；此次紀錄另存：

```text
run-20260916T135034Z-c3291157a5524805bdb278323b251167/summary.json
```

### 證據整理交接

本次已完成的盤點與兩輪執行證據維持上述 `/media/pi/SMCI/bpi/evidence-*` 原路徑，
未搬動、改寫或重新產生。主代理可於交接後一次整理至專案
`output/evidence/bpi-h618-no-mux/T0-...`，並同步更新本文件路徑及核對既有 SHA-256；
不要只搬部分紀錄而切斷續作相對引用。
後續新建證據改以專案 `output/evidence` 為父目錄，不再新增於 `/media/pi/SMCI/bpi/` 根目錄。
原始客戶映像位置及內容維持不變。

## 候選 A 僅盤點保留

| 完整檔名 | 壓縮 bytes |
| --- | ---: |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zero_bookworm_current_6.18.49_minimal.img.xz` | 326782720 |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zero_bookworm_current_6.18.49_xfce_desktop.img.xz` | 1018180148 |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zero_jammy_current_6.18.49_minimal.img.xz` | 332948020 |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zero_jammy_current_6.18.49_xfce_desktop.img.xz` | 933445120 |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zero_noble_current_6.18.49_minimal.img.xz` | 330841580 |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zero_noble_current_6.18.49_xfce_desktop.img.xz` | 950563772 |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zero_resolute_current_6.18.49_minimal.img.xz` | 340936148 |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zero_resolute_current_6.18.49_xfce_desktop.img.xz` | 1041267060 |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zero_trixie_current_6.18.49_minimal.img.xz` | 341818300 |
| `Armbian-unofficial_26.11.0-trunk_Bananapim4zero_trixie_current_6.18.49_xfce_desktop.img.xz` | 1120607804 |

旁檔宣告的壓縮 SHA-256、完整檔名、來源紀錄 SHA-256 與 `dev/ino/size/mtime/ctime`
已存於下列獨立證據，不在原產物目錄新增任何檔案：

```text
/media/pi/SMCI/bpi/evidence-20260916T133959Z-b4ef8ec236b3433ca06b3882ea044afb/inventory.json
SHA-256：367bba24e1cb6e8d08187c2d2eb186661e90f7d537e73af8b7bb2cf99beb3063
```

候選 A 的壓縮 SHA-256 仍是旁檔宣告，未實算，也沒有執行解壓、容量或 MBR 檢查。

## 工具及續作

工具為 `tools/bpi_h618_image_matrix.py`，只用 Python 標準函式庫，沿用
`tools/bpi_h618_artifacts.py` 的安全路徑、一般檔案檢查及中文 CLI。
盤點必須指定單一根目錄、確切板型及兩個原始角色；不遞迴搜碟。

本輪實際執行的命令如下，工作目錄為
`/media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-sram-supervisor-plan`：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/bpi_h618_image_matrix.py verify \
  --evidence /media/pi/SMCI/bpi/evidence-20260916T134400Z-737ad9c22dc44db3a1f8c4e9dcb649d3 \
  --inventory-sha256 3b3bba0de68d79e7a87d6737e61008ad1b0058d14babf0ef546e7e8ee3872bd4 \
  --capacity-bytes 31289507840
```

每次驗證只新增 `run-*` 子目錄。完整逐檔紀錄先同步再以不覆寫的方式發布，
包含工具及安全讀檔模組 SHA-256、盤點 SHA-256、容量、解壓上限、完成時間與原檔身分。
中斷留下的 `partial-*` 不算成功；先前完整成功檔不重解壓。失敗或損壞紀錄重試，
工具或容量等條件改變會使舊結果失效；原檔身分改變則拒絕，要求重新盤點。

續作依賴受控本機證據與檔案系統身分，不能偵測繞過檔案系統的外部修改或蓄意偽造證據。
XZ 使用固定大小輸入／輸出區塊及解碼記憶體上限，核對多串流、檢查碼、尾端填補與截斷；
不產生完整 `.img`。解壓長度及 SHA-256、壓縮 SHA-256、主分割區邊界與重疊皆逐檔記錄。
GPT、延伸分割、超容量或無效 MBR 不算初檢通過；本工具不解析檔案系統，
也不將 SPL、TF-A、U-Boot、核心、DTB 或 initramfs 標成已驗證。

離線回歸命令：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_bpi_h618_image_matrix.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_bpi_h618_artifacts.py
```

本輪重跑結果：矩陣工具 `39` 項、安全讀檔 `28` 項全部通過；這些是合成小型映像的離線回歸，不計入硬體測試數。

只修改工具、對應測試與本文件；不提交、不推送。實板測試、最終批次選定及後續整合由主代理負責。
