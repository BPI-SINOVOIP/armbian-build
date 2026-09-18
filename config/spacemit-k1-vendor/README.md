# K1 官方 SD／Titan 格式參考

此目錄固定 SpacemiT 官方 `Bianbu-LXQt-K1-V2.3.0-20251212104943.zip` 的封裝契約。原始小檔、二進位元件與擷取紀錄保存在本次作業的 `reference/vendor-v2.3/`；二進位不加入主倉。來源與逐檔 SHA-256 見 `sources.lock.json`，唯讀比對結果見 `reference-validation.json`。

為遵循本專案中文規則，設定檔的說明註解已翻成繁體中文，技術欄位與命令保留。來源鎖同時記錄官方原檔與本地範本的雜湊，原始檔保存在作業參考目錄。此處的 `partition_2M.json` 與 `partition_flash.json` 僅供格式追溯，不能直接收進 eMMC 專用候選。

## 來源與驗證界線

- [官方 Titan 封裝](https://archive.spacemit.com/image/k1/version/bianbu/v2.3/Bianbu-LXQt-K1-V2.3.0-20251212104943.zip) 使用 HTTP Range 取得 ZIP 中央目錄與小型成員；每個擷取成員經 ZIP CRC32 與 SHA-256 檢查。未下載整份封裝，不宣稱已驗證整包 SHA-256。
- 官方 v2.1 最小系統的 `fastboot.yaml`、`partition_universal.json`、`genimage.cfg` 與上述 v2.3 完全相同。這只證明格式一致，不表示兩版二進位配套可混用。
- 本機官方 CM6 v2.3 SD 映像的 bootinfo、FSBL、環境、OpenSBI、U-Boot 均與 Titan 封裝逐位元一致。這是內容證據，尚未包含 Titan 匯入、USB 燒錄或實機開機。
- U-Boot 二進位內的提交前綴對應 [db67f9dc4d26a45a29e3be085eb5b783aae8e965](https://github.com/spacemit-com/uboot-2022.10/tree/db67f9dc4d26a45a29e3be085eb5b783aae8e965)，但版本標示含 `dirty`。尚缺官方建置時的未提交差異，不能把該提交聲稱為完整可重現來源。U-Boot／FSBL 的授權依其 [授權說明](https://github.com/spacemit-com/uboot-2022.10/blob/db67f9dc4d26a45a29e3be085eb5b783aae8e965/Licenses/README) 處理；公開再散布前須補齊完整對應原始碼。
- `fw_dynamic.itb` 內版本為 `k1-bl-v2.2.7`，官方 OpenSBI 對應標籤提交是 [05479f5228f3fab2a4221fe0745f3703171ace58](https://github.com/spacemit-com/opensbi/tree/05479f5228f3fab2a4221fe0745f3703171ace58)，授權為 `BSD-2-Clause`。固定成品雜湊不代表已完成由原始碼重建比對。

## SD 佈局

使用 GPT、512 位元組磁區；`bootinfo` 是 LBA 0 前 80 位元組的媒體資訊，並非 GPT 分區。`holes = {"(80;512)"}` 讓 GPT 的保護 MBR 使用同一磁區的其餘空間。不能把整個 LBA 0 清掉後再補入 bootinfo。

| 項目 | 位元組偏移 | 分配大小 | 官方檔案 |
| --- | ---: | ---: | --- |
| bootinfo | 0 | 前 80 位元組 | `factory/bootinfo_sd.bin` |
| fsbl | 131072 | 262144 | `factory/FSBL.bin` |
| env | 393216 | 65536 | `env.bin` |
| opensbi | 1048576 | 1048576 | `fw_dynamic.itb` |
| uboot | 2097152 | 2097152 | `u-boot.itb` |
| bootfs | 4194304 | 268435456 | `bootfs.ext4` |
| rootfs | 272629760 | 後續可用空間 | `rootfs.ext4` |

官方 `env.bin` 本體是 16384 位元組，前四位元組為小端序 CRC32，涵蓋其餘 16380 位元組。分區保留 64 KiB 不代表應把環境改做 64 KiB。一般覆寫開機行為應使用 `bootfs` 根目錄的 `env_k1-x.txt`。

早先查得的 `buildroot-ext` 通用範本使用 448 KiB 的 OpenSBI 偏移，與這份官方發布映像不同；此目錄採上述已實讀的 1 MiB 偏移。

## Titan 暫載與 eMMC 寫入

官方 `fastboot.yaml` 先探測 `version-brom`，在 BootROM 階段暫載 `factory/FSBL.bin` 並繼續，再暫載 `u-boot.itb` 並繼續，最後以分區描述執行 `multi_flash`。此發布套件只有一份 `u-boot.itb`，同時用於 USB 暫載與最終 `uboot` 分區。不能以現有 Armbian 修改版載荷替換後，仍假定其 USB 燒錄能力相同。

來源程式對 `bootinfo`、`fsbl` 有特殊處理：

- [fb_mmc.c](https://github.com/spacemit-com/uboot-2022.10/blob/db67f9dc4d26a45a29e3be085eb5b783aae8e965/drivers/fastboot/fb_mmc.c) 將 `flash bootinfo` 導向 `fastboot_oem_flash_bootinfo()`；後者覆寫下載緩衝區，重新產生 eMMC header，寫入硬體分區 1 的偏移 0，也就是 Linux 的 `boot0`。因此原廠描述雖引用 `bootinfo_sd.bin`，實際 eMMC 不會把 SD header 原樣寫入。
- `flash fsbl` 寫硬體分區 1 的偏移 512；`flash fsbl_1` 才會寫硬體分區 2 的偏移 0。原廠 `partition_universal.json` 沒有 `fsbl_1`，不能宣稱原廠 Titan 流程必然更新 `boot1`。
- GPT 的 `fsbl` 項目仍存在，但標準 `flash fsbl` 被上述特殊分支接走；不能把 eMMC 使用者區的 GPT 分區內容當成 ROM 開機載荷驗證。
- eMMC header 的 `spl0_offset` 是 512，SD 是 131072；兩者 CRC32 已分別通過。
- 原始碼中的 GPT JSON parser 不處理分區 UUID 欄位，會重建 GPT。根檔案系統應使用每份成品唯一的檔案系統 UUID，不能假設打包時的 PARTUUID 在 Titan 重建後仍存在。

## eMMC 專用候選的媒體限制

原廠 `multi_flash` 同時參考 `partition_{size0}.json` 與 `partition_{size1}.json`，前者來自 `mtd-size`，後者來自 `blk-size`。eMMC 專用包應移除 MTD 分區描述與 MTD 流程參考，僅保留區塊裝置分區描述；實際 Titan 版本仍須驗證匯入與執行行為。

**僅移除 MTD 描述不能把原廠 U-Boot 硬鎖在 eMMC。** [燒錄分派](https://github.com/spacemit-com/uboot-2022.10/blob/db67f9dc4d26a45a29e3be085eb5b783aae8e965/drivers/fastboot/fb_command.c) 與 [GPT 寫入](https://github.com/spacemit-com/uboot-2022.10/blob/db67f9dc4d26a45a29e3be085eb5b783aae8e965/drivers/fastboot/fb_spacemit.c) 依硬體開機選擇腳位決定媒體：

- eMMC／SD 模式使用固定 `CONFIG_FASTBOOT_FLASH_MMC_DEV=2`，即官方設定中的 eMMC。
- NOR／NAND 模式可改走 `get_available_blk_dev()`，包含其他區塊裝置；此時相同 GPT 描述不代表 eMMC。

目前沒有查得可證實支援的 `oem target emmc` 命令，也沒有能可靠拒絕錯誤腳位的 Titan YAML 條件契約。候選操作必須確認 eMMC 開機腳位及板型、移除 SD 與其他儲存裝置，並以 Titan／UART 記錄確認目標；若要求由包本身保證只能寫 eMMC，需另外建置並驗證有明確媒體限制的 USB 暫載 U-Boot。

## 使用明確的 Armbian extlinux

官方 `CONFIG_CMD_SYSBOOT=y`，且實際載荷含 `sysboot` 命令。開機時會尋找名為 `bootfs` 的 GPT 分區，載入根目錄 `env_k1-x.txt`，以 `env import -t` 匯入。此行為可覆寫正常 SD／eMMC 開機的 `bootcmd`。

`env_k1-x.txt` 範本如下；其中 `bootfs_devname`、`boot_devnum`、`bootfs_part` 由官方板級程式設定：

```text
kernel_addr_r=0x08000000
ramdisk_addr_r=0x21000000
fdt_addr_r=0x31000000
pxefile_addr_r=0x0c200000
bootcmd=sysboot ${bootfs_devname} ${boot_devnum}:${bootfs_part} any ${pxefile_addr_r} /extlinux/extlinux.conf
```

`extlinux.conf` 必須提供板卡專用的明確 `FDT`、核心、initrd 與唯一 `root=UUID=...`，避免執行官方 `detect_dtb` 的自動板型選擇。記憶體位址沿用官方載入配置，仍須確認實際核心、initrd 與 DTB 大小不互相覆蓋。USB 暫載階段另有 BootROM／fastboot 流程，不以此覆寫視為已完成 USB 開機測試。
