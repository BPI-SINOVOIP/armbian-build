# BPI-W2 RTD1296 L2 正式建置證據

更新日期：2026-08-28

## 結論

`bananapiw2` 已從已推送提交 `7882ba85da55ad5a8096321811a8c2ff531b4c01`，使用 W2 專用 OverlayFS 與固定來源完整建立 Debian Trixie legacy minimal CLI。IMG、XZ、MBR 雙分割區、唯讀 FAT／ext4、vendor boot 資產、W2 DTB、最終核心設定與 40 KiB 位置的 U-Boot 載荷均通過守門，因此證據層級提升為 L2 內部軟體候選。

本結果沒有實體板開機、介面、效能、穩定性或量產證據。四個 U-Boot 靜態庫、`bluecore.audio`、內含工具鏈與外部文件的再散布授權尚未閉合，因此不得公開發布組合映像。

## 固定來源

| 項目 | 固定身分 |
| --- | --- |
| Armbian 來源提交 | `7882ba85da55ad5a8096321811a8c2ff531b4c01` |
| Armbian 來源 tree | `10ccab5ed21a148cb33d3693490d80fdbfc48b38` |
| W2 BSP | `6e6aefc35dc50b1b8231cdb03a995d088f29eb21` |
| Armbian firmware | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| `SOURCE_DATE_EPOCH` | `1571768256` |
| 建置／驗證契約 SHA-256 | `77c712d668959ac7aa96f537fae7a31dedfe3e63a1c6fbb667b5923775c0a4b0` |
| 來源契約投影 SHA-256 | `13dcf92c40e1d19161da68adf834f45bbe56926e35782de20585bd2bbbf5335d` |

## 正式產物

固定目錄：`output/images/2026.08/bananapi-realtek-rtd1296-w2-trixie-legacy-cli`

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| `Armbian-unofficial_26.05.0-trunk_Bananapiw2_trixie_legacy_4.9.119_minimal.img` | 2,088,763,392 | `37d28132a24e0944112097caf66ce714ee589e6b8317351e861a6ff0c85a34fe` |
| `Armbian-unofficial_26.05.0-trunk_Bananapiw2_trixie_legacy_4.9.119_minimal.img.xz` | 393,632,040 | `ae74b820d3b3e540d79bf8a60d2d92210f1e41090e7c3ef14b28d0504072b116` |

XZ 完整性與解壓串流同一性均通過。正式 IMG 與 XZ 保留，不列入空間回收。

## 內容守門

- MBR 簽章有效；FAT 分割區自 LBA 8192 起、長度 524,288 sectors，ext4 根分割區自 LBA 532480 起。
- FAT 標籤為 `BPI-BOOT`，ext4 標籤為 `BPI-ROOT`；兩個檔案系統均以唯讀方式檢查。
- vendor boot 目錄包含 `uEnv.txt`、`bluecore.audio`、`uImage`、`uInitrd` 與 W2 DTB，且沒有封裝被禁止的 `spirom-bpi-w2.bin` 或原廠舊 `uInitrd`。
- W2 DTB SHA-256 為 `e2f0d51977310ecd06a8b72088a3ee3fbcec439b850ceacd9887c9b557d1c420`。
- 最終核心設定 SHA-256 為 `0bcd9fdd4e4dcbb1dbe5bd2702ad08171e425c8abf1f9e30e05f6fe4301ec6a3`。
- U-Boot 位於映像位移 40,960 bytes，大小 432,240 bytes，SHA-256 為 `d4d425862ded2334d354b421ff2df8cdb965041b3b3b2c903fbeddd29ab23890`。
- 候選矩陣、完成狀態、驗證清單、U-Boot 載荷清單與最終核心設定清單的 SHA-256 已寫入機器契約；完成時間為 `2026-08-28T01:26:39Z`。

## 證據限制

- W2 仍為 `.wip`；L2 只證明固定來源完整映像符合本機軟體契約。
- 未驗證 SD、eMMC、SATA、PCIe、乙太網路、HDMI TX、DisplayPort TX、USB host／gadget、音訊或 40-pin。
- 正式建置保留 229 筆舊 vendor 程式碼警告；Linux 4.9.119 與 U-Boot 2015.07 仍有維護及安全風險。
- U-Boot 時間已固定，但 initramfs 與 APT 套件輸入仍會隨建置環境改變，不宣稱整體映像逐位元可重現。
- W2 沒有板載 Wi-Fi；本映像存在共用 firmware 不構成板載無線功能證據。

## 重驗命令

```bash
python3 tools/check-bananapi-realtek-w2-source-policy.py
python3 tools/check-bananapi-realtek-w2-source-policy.py --verify-historical-image
python3 -m unittest tests.test_bananapi_realtek_w2_candidate
python3 tools/bananapi-board-audit.py --check
```

本文件、機器契約及中央狀態已由提交 `a4f40542f` 推送，歷史重驗通過後才移除專用 OverlayFS；實際可用空間增加 3,007,361,024 bytes。清理後歷史重驗再次通過，共用 `/media/pi/SMCI/armbian/bpi-v26.2.1/cache` 仍為 device `66306`、inode `96224797`，並始終只作唯讀 lower。
