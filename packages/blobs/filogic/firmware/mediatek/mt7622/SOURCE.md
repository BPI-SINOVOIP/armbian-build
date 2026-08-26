# MT7622 與共享 Filogic 韌體來源

這個目錄保存 BPI-R64 的 MT7622 核心驅動會要求的三個韌體，以及共享 Filogic 內建 Ethernet 驅動會宣告的 MT7981／MT7986 WED 韌體。後三個檔案不是 R64 硬體功能，但必須存在才能讓共享核心建立無缺檔警告的 initramfs。MT7988 的三個共享韌體由相鄰的 `mt7988` 受控目錄提供。

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
| `mt7981_wo.bin` | 共享 Filogic 驅動宣告的 MT7981 WED 韌體 | `2d69d4cb56d4808727e8ab1bf9a9abfc61657f9803c284bf39017f1872af9dd1` |
| `mt7986_wo_0.bin` | 共享 Filogic 驅動宣告的 MT7986 WED 韌體 0 | `4c268aed7c9ebd7fdd9afc6d2f93e64e108e335626b7b025d7ab7c80704684d8` |
| `mt7986_wo_1.bin` | 共享 Filogic 驅動宣告的 MT7986 WED 韌體 1 | `b60e9930e507b9e8228ba229c3ba6d1e4736d34720c744aeb2f85a9c8e5d3f29` |
| `LICENCE.mediatek` | MediaTek 韌體授權原文 | `a90d3f66704d85889945fec5525ea77622549da83aced1aac99828383f8f1805` |

這些雜湊同時列入候選映像驗證契約；來源檔或安裝後檔案任一位元改變都會使守門失敗。
