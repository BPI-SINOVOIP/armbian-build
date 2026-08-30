# Banana Pi BPI-CM6 legacy 候選映像 L2 建置證據

更新日期：2026-08-27

## 結論

`bananapicm6` 已使用專用 OverlayFS 隔離快取，由固定來源完整建置 Debian Trixie legacy minimal CLI，並通過 SpacemiT K1 專用 L2 唯讀守門。來源、IMG／XZ 同一性、MBR、六項 U-Boot 載荷、extlinux、CM6 專用 DTB、核心設定、預編譯韌體追溯與標準診斷工具均符合本次受控政策。

此結果只證明可重現建置與映像內容。尚無實體 CM6 的 UART、冷啟動、SD、eMMC、網路、USB、PCIe、顯示、音訊、無線網路或 I/O 證據，因此板卡保留 `.wip` 且最高維持 L2。

## 建置身分

| 項目 | 值 |
| --- | --- |
| 板卡 | `bananapicm6` |
| 發行版 | Debian 13 `trixie` |
| 核心目標 | `legacy` |
| 映像型態 | minimal CLI |
| 映像來源提交 | `98d320d8e754692b8fa0fb9ad2f8970f7a129cb5` |
| 驗證器提交 | `162fb25b1b737e38bd6984b2efa2573d590ac3fd` |
| 建置時驗證政策 SHA-256 | `3a5dc6fc22860c7101f559ba31605d57fb5820cc279374277c4e1a1f20be1c38` |
| 最終驗證政策 SHA-256 | `77d86f43f8288184f0b7e057dd752cdb81804bc2a1deeb7f90187f80e28f63e5` |
| Linux | `6.6.36`，來源提交 `0d0af0d895251383baee939d44e523699e31889f` |
| U-Boot | `2022.10`，來源提交 `066cccd77f35e57d13363fea524a439759196dca` |
| OpenSBI | 來源提交 `05479f5228f3fab2a4221fe0745f3703171ace58` |
| 完整建置時間 | 17 分 48 秒 |
| 輸出目錄 | `output/images/2026.08/bananapi-spacemit-k1-cm6-trixie-legacy-cli/` |

完整建置重新產生 U-Boot、OpenSBI、核心、DTB、套件、根檔案系統、IMG 與 XZ，不是由既有映像改名或只替換啟動載荷。共用快取只作唯讀下層，CM6 的變更寫入專用上層。

## 映像與分割表

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| IMG | 1329594368 | `fa0b6ebb3b1dfa5dc6aa6836f6ef53369856a39780d6c1d829f6e27b5f3681e2` |
| XZ | 351283320 | `d49201af0d6f5b99ab733ba6862c01816930f528ba5adbed2ff6a791ada45687` |

XZ 通過完整串流解壓，解壓後大小與 SHA-256 均和 IMG 相同。映像採 MBR／`msdos` 分割表，第一個根檔案系統分割區固定由 sector 8192 開始。

## 啟動鏈證據

| 載荷 | 放置方式 | 位元組偏移 | 大小 | SHA-256 |
| --- | --- | ---: | ---: | --- |
| `bootinfo_emmc.bin` | 寫入映像 | 0 | 80 | `5433619a573ec7935450f303168b87c97dc5511905291328df40a2ff081b3d07` |
| `FSBL.bin` | 寫入映像 | 512 | 192384 | `5b1fc24c6ac78841e0ea60632713fcc334462184f087b573530e2b2a29d3f612` |
| `fw_dynamic.itb` | 寫入映像 | 655360 | 135679 | `182e49dff82b66af6b9be159316f8aff53fc954919b6a0deaa87e12a7564e8b9` |
| `u-boot.itb` | 寫入映像 | 1048576 | 2095738 | `7dd54041af79d44b4762d5684148fc563e0f31a39788c5b263f1c1a636229b8e` |
| `bootinfo_spinor.bin` | 僅保留於套件 | 不適用 | 80 | `49b63bcbcdcf2e0bbcaabbc1f2a01959ab56ed70c16a3594b25bc1183d92efae` |
| `u-boot-env-default.bin` | 僅保留於套件 | 不適用 | 16384 | `cba21a86eee13ad1e6701504a55bdab611e90fd4f1187fa199cf814775303e27` |

守門從安裝於映像內的 U-Boot 套件取出載荷，驗證套件 MD5、最低大小與 SHA-256，再逐位元比對四項映像內原始偏移。`u-boot.itb` 另逐字確認兩個 extlinux 路徑、K1-X 自動開機環境及 `product_name=k1-x_deb1`。U-Boot 載荷證據清單 SHA-256 為 `343692eb95d8c232b1c0b8775886391269fc89c0b210dee2dcfd2cb423d57d8e`。

## DTB、核心與韌體

映像內 `spacemit/k1-x_bpi_cm6.dtb` SHA-256 為 `6d8db2aa3dc0a106190052316a0839cf9dda6a64b41ff6fe8fcc317f96a46f67`。DTB 的 model、compatible、SD／SDIO／eMMC bus width、HS400 屬性及網路、PCIe、USB、HDMI、GPU、VPU、音訊、無線、GPIO、I2C、SPI、PWM fan、熱感測與遠端處理器節點均通過結構化檢查；供應商 `debug loglevel=8` 與 `rdinit=/init` 啟動參數不存在。

核心設定確認 SpacemiT K1X SoC、SDHCI、雙乙太網路、PCIe／NVMe、USB host、USB gadget mass storage、HDMI、IMG GPU、Linlon 視訊、ES8326 音訊、RTL8852BS、Bluetooth、GPIO、I2C、SPI、PWM fan、熱感測、watchdog、遠端處理器及硬體加密功能已納入。映像亦安裝 GPIO、I2C、SPI、PCIe、NVMe、USB、音訊與網路診斷工具。

`esos.elf` 是預編譯韌體；本次只驗證固定 SHA-256 `3b3ef5ba9b404c6500bfc0f7f1efc0cb7fdde818450b7beddac1c00f29898537`、授權原文與繁體中文來源追溯文件均正確安裝。這不代表其內部行為可由本倉庫來源重建或審計。

## 被拒絕的候選

第一次完整建置沒有產生 CM6 專用 DTB，因此即使映像封裝成功仍被拒絕，保留於：

`output/images/2026.08/bananapi-spacemit-k1-cm6-trixie-legacy-cli-rejected-missing-cm6-dtb-20260827/`

第二次候選已有 CM6 DTB，但 U-Boot artifact 中繼資料仍記錄預設 GPT，與實際 MBR 映像不一致，因此被拒絕，保留於：

`output/images/2026.08/bananapi-spacemit-k1-cm6-trixie-legacy-cli-rejected-uboot-partition-metadata-20260827/`

最終候選把分割表政策提前到家族分割設定前生效，使 artifact 中繼資料與實際映像一致。兩個被拒絕產物不得對外發布。

## L3 實機門檻

- 以 UART 保存多次冷啟動、重新啟動與斷電重啟記錄，確認 FSBL、OpenSBI、U-Boot、extlinux 與 Linux 啟動鏈穩定。
- 分別驗證 SD 開機與 eMMC `boot0` 安裝；原廠多分割區 GPT、SD 原始偏移及 eMMC 寫入流程不可互相代替。
- 驗證雙 Gigabit Ethernet、PCIe／NVMe、USB host、USB gadget、HDMI、GPU、VPU、音訊、Wi-Fi 與 Bluetooth。
- 驗證 40-pin GPIO、I2C、SPI、UART、PWM、fan、熱感測及外接電氣對照。
- 執行 CPU、記憶體、儲存與網路混合壓力，保存溫度、節流、重置、I/O 錯誤與測試時間。
