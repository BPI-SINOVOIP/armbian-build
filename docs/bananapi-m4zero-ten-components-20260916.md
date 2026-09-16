# M4 Zero EMAC 十套系統組件清單

日期：2026-09-16。僅固定候選 B：`bananapim4zeroemac`、`6.18.49`、五個 OS 各 `minimal`／`xfce_desktop`。十套靜態檢查完成，不代表十套已在實板啟動。
原始證據不入 Git；工具、測試與文件由主代理審查後提交。本工具未操作 UART、SSH、電源、物理塊裝置、掛載或 loop，未執行映像內腳本，未重編或重打包。

## 交付位置

私有證據根目錄：

```text
/media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-sram-supervisor-plan/output/evidence/bpi-h618-no-mux/T0-customer-components-001
```

總索引 `components-index.json`：SHA-256 `57c7216efdee5dded8186d749c6aad51bff1ad39bc4066885f657bc839b574d2`。
每套使用主代理指定的 `bpi-h618-customer-components-v1` schema；必填欄位未改名。
`files.initrd.path` 指向原 `/boot/uInitrd-6.18.49-current-sunxi64`，不是裸 gzip initrd。
五個組件均記錄原映像內路徑、位元組數、SHA-256、小寫八位 CRC32，以及新增的本機 `evidence_path`。
`original_env` 僅拆鍵值字串，不做 `source`、`eval`、引號解譯或變數展開。

| 清單檔名 | SHA-256 |
| --- | --- |
| `bookworm-minimal.json` | `232c6c6a197adc477c28007d8ea7b0d977a1e69277a1122c1d2b998cd279253b` |
| `bookworm-xfce_desktop.json` | `c95d2cac2282a3ce6bc0ff531e5cb645602b6a294aa6b230c37c0478165b384f` |
| `jammy-minimal.json` | `5b1cbd6af4c9a09e84b8f9c9c65ad21ff02cd659c2888b1e5b3564aab13535f2` |
| `jammy-xfce_desktop.json` | `f3b5e79cbb3a6008cafa9c85ea73632333aadffd12fea4fda54a095440cdd2de` |
| `noble-minimal.json` | `c7312a8dc7275a323156ab2117c9f82c2264fc7c413a44be65fabdca86a257cc` |
| `noble-xfce_desktop.json` | `4a77c960456b4b8e90effbc7b8c39474b44d1e434fc808040ef6324b7189058d` |
| `resolute-minimal.json` | `2eede7b21f52b89a6d88e53ce19071f1c29f317b92510cfabda9af36196a250b` |
| `resolute-xfce_desktop.json` | `766cede0d0ca0688144f1ba776a1433ff0aae0308c46109e09e0d383f277b923` |
| `trixie-minimal.json` | `0a67f65aee5821b81fe9d3545b46104f376d0063a790ebca12dbf070bd1c872d` |
| `trixie-xfce_desktop.json` | `c7bebb8a0df53a05ce214a301871c04314e6b00588c1d5ca6806b8b611760865` |

首套已先交付，成功證據在 `bookworm-minimal-002/`。較早的 `bookworm-minimal/` 是校對 `/lib/modules` 擷取路徑時保留的未完成資料，沒有通過清單，不是額外候選或映像損壞結論。

## 來源與唯讀驗證

- 來源目錄固定為 `/media/pi/SMCI/bpi/google-drive-upload/2026/2026.08/bpi-m4z-emac`，未擴大其他板型或批次。
- 沿用 T0 inventory SHA-256 `3b3bba0de68d79e7a87d6737e61008ad1b0058d14babf0ef546e7e8ee3872bd4` 與成功 summary SHA-256 `2d599719b331924b4395299f61e256a5b567316305b61bd7ce436a0574f0464c`。
- Bookworm minimal 重新核對既有證據索引 `27362d92990f4fd424bde5fb60c50fd8329796ef63cd96c9ad4f60a5a12b7d5d` 及所用檔案，未重讀原 XZ，未改動舊 `root-partition.img`。
- 其餘九套各以標準 `lzma` 串流完整原 IMG，同時計算 XZ SHA-256、raw SHA-256 與長度；全部符合可信 T0。MBR 亦逐套相符，容量上限使用 `31289507840` bytes。
- 僅寫出 MBR 指定的根分割範圍及開機前綴，沒有完整 IMG 落地。每次只有一份本次暫存分割；各套組件與證據保存後刪除自身衍生分割，最後剩餘零份。本輪不刪舊 Bookworm 分割。
- `debugfs -R` 透過唯讀普通檔描述符查詢；保存實際命令、標準輸出、錯誤輸出與雜湊。缺檔不以退出碼 `0` 當成功；只有明確允許缺少的狀態標記可列為不存在。
- 十套映像內 `BUILD_REPOSITORY_COMMIT` 均空白。發布紀錄宣告的來源提交為 `c8673931c96c23c510dd29a28440e77f0b03286f`，不是內部獨立來源證明，也不以目前分支 HEAD 補值。

## 配套結果

十套 `source_verified`、`legacy_initrd_verified`、`mmc_support_verified` 均為 `true`；`hardware_validated` 固定為 `false`。

| 項目 | 結果 |
| --- | --- |
| 核心／根系統模組／initramfs 模組 | 唯一版本 `6.18.49-current-sunxi64`；核對 ARM64 Image 內嵌版本 |
| 核心、EMAC DTB、SDIO overlay、fixup | 各自只有一種 SHA-256，十套逐位元相同 |
| uInitrd | 十種不同 SHA-256，必須使用各套自己的檔案，不共用首套 initrd |
| uInitrd 封裝 | 標頭／資料 CRC 通過，payload 與原 `initrd.img-*` 逐位元一致 |
| boot.scr／fixup | 傳統映像 CRC 通過；boot.scr 腳本內容等於原 boot.cmd，沒有執行 |
| 根 UUID／PARTUUID | 各十個唯一值；根 UUID 與 ext4 超級區塊、fstab、armbianEnv 一致，PARTUUID 由原 MBR 推導 |
| MMC2/eMMC | 原 EMAC DTB 已啟用八位元 MMC2；指定 overlay 可離線套用並啟用 MMC1 SDIO，MMC2 保持啟用 |
| 必要驅動 | MMC、MMC block、Sunxi MMC、eMMC power sequence、ext4 與 gzip initrd 支援內建；rootfs／initramfs 的 modules.builtin 相同 |
| initramfs 擴容 | 十套列舉均無 growroot／resize 路徑；不代表系統層 resize service 已停用 |

## 首開與安裝器差異

十套 `firstrun`、`firstlogin`、resize 腳本、相關 service、`armbian-install` 入口、`platform_install.sh`、U-Boot 與 BSP postinst 的 SHA-256 相同。
其餘九套逐套確認 resize service、firstrun service、APT daily／upgrade timers 啟用，首次登入標記存在、`.no_rootfs_resize` 不存在；Bookworm minimal 狀態沿用[首套預檢](bananapi-m4zero-bookworm-emmc-preflight-20260916.md)。

Trixie 的 `config.functions.sh`／`config.system.sh` 整檔與其他 OS 不同。限定比較安裝器區段後，`module_partitioner` 僅尾端空白不同；`module_install_engine` 的差異是後續串接的 service helper，安裝器本體未見差異。位元組範圍及原始差異保存在 `installer-section-comparison.json` 與兩份 `*-comparison.diff`；不宣稱整套 armbian-config 相同。

共同風險仍是：原 boot.cmd 的 `part uuid mmc 0:1 partuuid`；擴容依目前根分割父盤而寫入；安裝器可依明確 target 寫入 bootloader；U-Boot postinst 在 `FORCE_UBOOT_UPDATE=yes` 時依 `root=` 找父盤。本批該強制更新設定均空白，不能誤報為首開必定自動裸寫 SD。BSP 的 bootscript 強制更新可能覆蓋引導修正。

## 主代理載入差異

1. 每套依自己的 JSON 從核對過的 eMMC 分割區載入原 Image、uInitrd、DTB、overlay、fixup；保留自己的根 UUID，不硬編 Linux MMC 編號，也不假設與 U-Boot 編號相同。
2. 修正或完全省略原 `ubootpart=`。一次性遮蔽擴容與 APT，禁止安裝器及 bootloader 更新，不掛載救援 SD 為 `/boot`。
3. 主代理計畫在傳給核心的 RAM DTB 停用 SD host，必須另存該 DTB 雜湊與修正紀錄。本清單的 `dtb.sha256` 始終指未修改的原 EMAC DTB，不能拿 RAM 修改後雜湊冒充原檔。
4. 主代理獨立保存部署回讀、實際載入雜湊、核心版本、根 UUID／CID 及功能測試。固定救援引導、停用 SD host、mask 與首次登入調整皆不是全原樣執行；只換 rootfs 不算測到客戶核心，系統層不代表原 SPL／TF-A／U-Boot 通過。

## 命令與測試

實際執行順序如下；已交付清單拒絕覆寫，不需重跑前兩個命令。重核對使用第三個命令，不讀原大映像。

```bash
env PYTHONDONTWRITEBYTECODE=1 python3 tools/bpi_h618_customer_components.py --only bookworm-minimal
env PYTHONDONTWRITEBYTECODE=1 python3 tools/bpi_h618_customer_components.py --remaining
env PYTHONDONTWRITEBYTECODE=1 python3 tools/bpi_h618_customer_components.py --verify-existing
env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_bpi_h618_customer_components.py
env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_bpi_h618_image_matrix.py
env PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_bpi_h618_artifacts.py
```

新測試 46 項，涵蓋 CRC／截斷／配套用途、UUID、內建 MMC、唯一版本、串流分割範圍及雜湊、普通檔安全、唯讀 debugfs、缺檔診斷、共同 schema、拒絕覆寫及錯誤暫存檔清理。既有矩陣 39 項與安全產物 28 項也通過，合計 113 項。私有 `tests-results.json` 保存命令與退出碼；測試只證明離線工具行為。
