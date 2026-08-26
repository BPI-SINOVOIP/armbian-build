# MT7622 網路與藍牙韌體來源

這個目錄只保存 BPI-R64 的 MT7622 核心驅動在開機或啟用裝置時會要求的三個韌體，以及其原始授權文件。

## 固定來源

- 儲存庫：`https://gitlab.com/kernel-firmware/linux-firmware.git`
- 提交：`01205307636157a12c29e6a774bf83b218732050`
- 來源索引：該提交的 `WHENCE`
- 授權：該提交的 `LICENSES/LICENCE.mediatek`

授權原文因法律與來源追溯要求保留為 `LICENCE.mediatek`，不翻譯或改寫。官方 `WHENCE` 將下列檔案列為可再散布，並指向該授權文件。

## 檔案雜湊

| 檔案 | 用途 | SHA-256 |
| --- | --- | --- |
| `mt7622pr2h.bin` | MediaTek 藍牙 UART 韌體 | `48c919e6ea243485f5092e63fd5558d03a5b9075e79c14447e3705ca42c14b53` |
| `mt7622_n9.bin` | MT7622 內建無線網路 N9 韌體 | `f1b21fced7344006e029b291ed1edacddd41eaf2571c7a31e2207903ddd111a3` |
| `mt7622_rom_patch.bin` | MT7622 內建無線網路 ROM 修補韌體 | `b7ad5bab333b2dffe31dcb4cc911a15060ee16f661de38139e66f0804a74ba26` |
| `LICENCE.mediatek` | MediaTek 韌體授權原文 | `a90d3f66704d85889945fec5525ea77622549da83aced1aac99828383f8f1805` |

這些雜湊同時列入候選映像驗證契約；來源檔或安裝後檔案任一位元改變都會使守門失敗。
