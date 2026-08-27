# Banana Pi R2 MT7623 啟動載荷來源與發布政策

日期：2026-08-27

## 結論

Banana Pi R2 的 Linux 與 U-Boot 均已固定至可重建的公開原始碼提交；SD 與 eMMC 啟動鏈另需要四個原廠二進位載荷。四個載荷皆可追溯至 `BPI-SINOVOIP/BPI-files`，而且解壓後 SHA-256 與本倉內容完全一致。

該來源倉未提供足以明確授權二進位再散布的授權條款。因此本候選只可用於內部建置與驗證；在取得 Banana Pi 或 MediaTek 的書面再散布授權前，不得把包含這些載荷的映像標示為可對外發布版本。公開可下載不等於取得再散布權。

## 固定原始碼

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| Linux 6.6.153 | `https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git` | `dc6160265ffc795a1832bc1424f58291d152c7bb` |
| U-Boot 2024.07 | `https://github.com/u-boot/u-boot.git` | `3f772959501c99fbe5aa0b22a36efe3478d1ae1c` |
| Armbian firmware | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| R2 原廠載荷 | `https://github.com/BPI-SINOVOIP/BPI-files.git` | 各檔案依下表固定 |

## 原廠載荷證據

| 本倉檔名 | 固定來源路徑 | 固定提交 | 解壓後 SHA-256 |
| --- | --- | --- | --- |
| `BPI-R2-HEAD440-0k.img` | `SD/100MB/BPI-R2-HEAD440-0k.img.gz` | `e4c2b542f199346853febb240500c8071cdf3006` | `62490bba0931680b65146792b795748a30777b101a8437885dd195c28b868a6b` |
| `BPI-R2-HEAD1-512b.img` | `SD/100MB/BPI-R2-HEAD1-512b.img.gz` | `e4c2b542f199346853febb240500c8071cdf3006` | `2cf55689fdaecc72a7ca24929b6b79e419e7d9798cfb8e6d36e8c5ea3b96e049` |
| `BPI-R2-preloader-2k.img` | `SD/100MB/BPI-R2-preloader-DDR1600-20191024-2k.img.gz` | `1b7b6039b9d92c48193f38ac29dd2f71e303e280` | `b6a2cbead0b34fc243b9d8d9bde69496a0dbcf77dcdf0246adf0664fe4e07252` |
| `BPI-R2-EMMC-boot0-0K-0905.img` | `SD/100MB/BPI-R2-EMMC-boot0-DDR1600-0k-0905.img.gz` | `e28da06a8dd7d77d30b60942008c1255b5fda997` | `cc0dfb488bed14e23bce8302553714d29e6cba9fe8406cf8c3c0e97e9ae2b417` |

前兩個路徑最早可追溯至 `59fd3f6fe73a54944f0a504a310707bdd992cc7d`。eMMC 載荷內容最早可追溯至 `233dcc2bc117a36b832411feccb55ac2e19a5c30`，提交紀錄指出該 preloader 補丁來自 MediaTek；`e28da06a8dd7d77d30b60942008c1255b5fda997` 只固定目前小寫 `0k` 的來源路徑。SD preloader 內含時間戳記 `20191024-155141`，eMMC boot0 內含時間戳記 `20170905-120917`。

## 映像配置契約

| 載荷 | 位元組偏移 | 用途 |
| --- | ---: | --- |
| `BPI-R2-HEAD440-0k.img` | 0 | SD 映像前導區 |
| `BPI-R2-HEAD1-512b.img` | 512 | SD 映像前導區 |
| `BPI-R2-preloader-2k.img` | 2048 | SD preloader |
| `u-boot.bin` | 327680 | 由固定原始碼編譯的 U-Boot |

`BPI-R2-EMMC-boot0-0K-0905.img` 只放入 U-Boot 套件，不直接寫進 SD 映像。映像使用 `msdos` 分割表，根分割區自 sector 8192 開始；驗證器必須逐一比對固定載荷的大小、SHA-256 與位元組偏移。

## 建置與驗證邊界

- 扁平 DTB 路徑修正版 U-Boot 已單獨交叉編譯，該次 `u-boot.bin` 大小為 463696 bytes，SHA-256 為 `c0a88952f1f6fef0f28fa4f63975325d00d30c1b7af1f7d666f9ef923e64fb7b`；二進位只包含 `boot/dtb/mt7623n-bananapi-bpi-r2.dtb`，不含錯誤的 `boot/dtb/mediatek/` 路徑。
- 提交 `4f7241a7b09a4e6a40c2b3b70951df1be82ad747` 已完成一次預檢映像建置；因當時補丁中繼資料仍需重整，該輸出只保留為預檢證據，不升級為正式 L2 候選。
- 提交 `87099e8c1fa0c82ae06368ed9c1188fe1d365e21` 的第二次預檢已確認 U-Boot 補丁零問題，但唯讀映像檢查發現核心套件把 DTB 安裝為 `/boot/dtb/mt7623n-bananapi-bpi-r2.dtb`，當時 boot script 與 U-Boot 環境卻多加一層 `mediatek/`。該映像無法依設定載入 DTB，同樣不得升級為 L2。
- 修正版契約要求 `BOOT_FDT_FILE`、boot script、U-Boot 內建環境及驗證器全部使用扁平路徑 `mt7623n-bananapi-bpi-r2.dtb`，並拒絕重新出現 `mediatek/mt7623n-bananapi-bpi-r2.dtb`。
- 修正後提交 `a07ed672ad6250b1229c70aa4afc5dd76416d00b` 已使用全新專用 OverlayFS 完成正式候選建置，總執行時間為 16 分 59 秒；U-Boot 修補摘要為 10 個全部套用、零問題，核心來源沒有額外修補。
- 正式 IMG 大小為 1363148800 bytes，SHA-256 為 `6d954e65669eae7883e9acb1496ee6dc3aa551bf46a57241e2a68a72a58ded61`；XZ 大小為 332157056 bytes，SHA-256 為 `7a6962d6b95e029ced0e7ec95b4f7b33c60c7fdab7fe8fac3bd0e3008d282566`。
- 唯讀掛載確認 `armbianEnv.txt`、`boot.cmd` 與成品 U-Boot 都只使用扁平 DTB 路徑；DTB model 是 `Bananapi BPI-R2`，compatible 是 `bananapi,bpi-r2`、`mediatek,mt7623`，SHA-256 為 `55151de1694bb279e759498eb5f86253e0e90700408044c546b4310a2a81c796`。
- 成品 U-Boot 大小為 462560 bytes，SHA-256 為 `bc3dffced856d68219ae997e2faa0ee246e8ef64fb2930d65ce1795b34f90cf2`；四個原廠載荷的成品套件雜湊與固定來源完全一致。
- 建置包裝器在映像與候選中繼資料完成後，第一次卸載專用 OverlayFS 時因短暫忙碌回傳狀態 32；確認沒有殘留建置程序或子掛載後重新執行一般卸載即成功。這項收尾事件不改變映像內容，但已保留於操作紀錄，不能用來省略後續正式驗證。
- 驗證器提交 `cca7e35c6e7c7911bfa656248e1ae561e0e5940a` 已完成 IMG／XZ 同一性、分割表、五個啟動載荷、來源中繼資料、DTB、核心設定、套件及唯讀根檔案系統守門，結果為 L2。驗證器同時修正 root-only 受控檔案與 U-Boot 載荷的唯讀雜湊權限處理，沒有放寬成品檔案權限。
- 本次沒有 R2 實體板、SD、eMMC、SATA、PCIe、HDMI、USB、網路交換器、GPIO、I2C 或 SPI 測試，因此不得宣稱上述硬體功能已通過。

## 發布守門

機器可讀契約中的 `boot_blob_redistribution_authorized` 必須維持 `false`。只有在書面授權可被保存、審查並連結至具體載荷版本後，才能另行評估修改；不得因映像可編譯、可開機或來源為第一方倉庫而自行改為 `true`。
