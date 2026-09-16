# M4 Zero EMAC Bookworm eMMC 靜態預檢

日期：2026-09-16。範圍僅為候選 B 第一套 `bookworm`／`minimal`，不是十套共同結論。
本次不使用 UART、SSH、電源、物理塊裝置、掛載或 loop，不執行映像內任何 shell、U-Boot 腳本或二進位。
原 XZ 沒有改動；只有私有證據目錄中的普通衍生檔及本文件新增。原始證據不入Git；文件由主代理審查後提交。

## 給主代理的結論

1. 靜態未發現缺少 eMMC 所需配套：原核心、initramfs、模組版本相符；Linux DTB 的 MMC2 已啟用，核心的 Sunxi MMC、MMC block、ext4 皆內建。因此目前沒有理由先換共用核心、重建 initramfs 或額外開啟 MMC2；這不等於原核心已在 0845 執行成功。
2. 保留 `root=UUID=62cd58de-498d-4325-8528-96a15da7d902`、`rootwait`、`rootfstype=ext4`，不要改成硬編 `/dev/mmcblk0p1` 或 `/dev/mmcblk2p1`。部署前主代理仍須核對救援 SD 是否有重複 UUID／PARTUUID；本次未讀實板，不能排除衝突。
3. 引導載入來源必須明確固定為已核對的 eMMC 分割區 1，包含原配 `Image`、`uInitrd`、EMAC DTB、指定 overlay 及對應 fixup 行為；不能只切換 rootfs。不得把 Linux 的 `mmcblk2` 當成 U-Boot 的 `mmc 2`。
4. 原 `boot.cmd:80` 的 `part uuid mmc 0:1 partuuid` 在「SD 救援入口、eMMC 客戶系統」下有選錯媒體風險。受控引導須改從核對過的 eMMC 分割區取得 `ubootpart`，或完全省略此參數；不能傳入救援 SD 的值，也不能只清空值後仍留下 `ubootpart=`。
5. 第一次受控系統層試跑先以一次性 `systemd.mask=armbian-resize-filesystem.service` 禁用擴容；本映像有支援此參數的 systemd generator。另封鎖安裝器／bootloader 更新與 APT 自動更新，保留原始首次啟動和受控調整的差異紀錄。這些保護不是硬體防寫。

## 證據與來源

私有證據目錄：

```text
/media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-sram-supervisor-plan/output/evidence/bpi-h618-no-mux/T0-bookworm-components-001
```

目錄權限為 `0700`。`root-partition.img` 保留至主代理檢查後再決定清理，本次未刪除。
`files/` 是逐檔擷取的原始位元組；其中外語註解、產品標示及程式碼屬雜湊固定的原始證據，未翻譯或執行。
`queries/` 保存每次 `debugfs -R` 的參數、退出碼與原始輸出；`analysis/` 保存解析輸出、命令及比對結果。
`debugfs` 缺檔查詢可能仍回傳 `0`，本次以原始錯誤輸出及實際擷取結果判斷，不把退出碼當作檔案存在證明。

| 證據 | SHA-256 |
| --- | --- |
| `evidence-index.json` | `27362d92990f4fd424bde5fb60c50fd8329796ef63cd96c9ad4f60a5a12b7d5d` |
| `extraction.json` | `9f7450dfd35433b0d89937396765d5fff6cfa5da5f279a680f33ebd72e30a8c1` |
| `analysis/components.json` | `133bc7384da9808e615c7782cca1131949f2c3c8d660de05ec91695a3b45dd1a` |
| `analysis/verification.json` | `e78d633ece72c893ba41a47c34f094e9aa1bdaaa372e7d984c47b32506b99ad7` |

來源完整路徑：

```text
/media/pi/SMCI/bpi/google-drive-upload/2026/2026.08/bpi-m4z-emac/Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_bookworm_current_6.18.49_minimal.img.xz
```

沿用 [T0 十套盤點](bananapi-m4zero-image-inventory-20260916.md) 已固定的清單與成功結果，先核對兩份 JSON 的 SHA-256 及原 XZ 的 `dev/ino/size/mtime/ctime`。
本次只串流這一套以取得分割範圍，同時實算解壓 IMG SHA-256，與 T0 一致；沒有再讀其餘九套大檔。

- 壓縮檔：`328413172` bytes；沿用已驗 SHA-256 `caea951ce80664ddb30802b4ece66ea0c2d01402695da455d854cfff73dbcfdc`。
- 解壓 IMG：`1543503872` bytes；本次實算 `0677fe8aa9624b07232e8257c3fe1f4686178402083fdb18d20ad410efb7c8e0`。
- 發布紀錄來源提交：`c8673931c96c23c510dd29a28440e77f0b03286f`。映像內 `/etc/armbian-release` 與 `/etc/armbian-image-release` 的 `BUILD_REPOSITORY_COMMIT`、`BUILD_REPOSITORY_URL` 為空，不能把外部發布紀錄說成內部自行證明，也不以本工作分支 HEAD 補值。

## 分割區與根系統

| 項目 | 值 |
| --- | --- |
| 分割表 | MBR；唯一主分割區，型別 `0x83` |
| 磁區大小／起點 | `512` bytes／LBA `8192` |
| 長度 | `3006464` 磁區，即 `1539309568` bytes |
| IMG 範圍 | `[4194304, 1543503872)`，未超過 `1.55 GB` 分割擷取上限 |
| MBR 磁碟識別／推導 PARTUUID | `22d064d3`／`22d064d3-01`；只有保持同一 MBR 時適用，不是實板 CID |
| 檔案系統 | ext4 特徵、標籤 `armbi_root`；超級區塊標示 `clean`，未執行完整 fsck |
| 根 UUID | `62cd58de-498d-4325-8528-96a15da7d902` |
| 衍生分割 SHA-256 | `bcab2391e36b194b45a562bd97d9d7b96c40290f592941e6654e065ed4e48fed` |
| 開機前綴 | `4194304` bytes；`571047c09bf8f5acc7ad6bb09806eb64e9ea515048dfa61a131bb6a4bee0ab5f` |

`fstab:1` 以根 UUID 掛載 `/`，另只有 `/tmp` 的 tmpfs，沒有指定 SD 的額外掛載項。
`armbianEnv.txt:8` 使用相同 UUID；initramfs 的 `init` 解析 `root=`，`scripts/functions:430` 支援 UUID／PARTUUID 查找。
initramfs 內的 `etc/fstab` 為空，不覆蓋這個根參數；`RESUME=none`，沒有 growroot／resize 腳本。

## 原配核心與啟動資料

唯一核心／模組目錄版本為 `6.18.49-current-sunxi64`；ARM64 `Image` 內嵌版本也相符。
原符號連結分別指向 `vmlinuz-6.18.49-current-sunxi64`、`dtb-6.18.49-current-sunxi64`、`uInitrd-6.18.49-current-sunxi64`。

| 組件 | bytes | SHA-256 |
| --- | ---: | --- |
| 原 `Image` 指向的核心 | 35371520 | `3cbc28af82fabd8f0c8cbe0a1da5f8c1241a5f733a7e6d9cd6a1ea40089656a9` |
| `initrd.img-6.18.49-current-sunxi64` | 11799798 | `875638aee84a71289b255f1b6c9dc93ea93502a8873627193d5a711fdc508dc1` |
| `uInitrd-6.18.49-current-sunxi64` | 11799862 | `705edf8d9b7bf3ddedd271d7639eab19987772e75d491d7dfd738ffbaaf94528` |
| `sun50i-h618-bananapi-m4-zero-emac.dtb` | 47381 | `2b168ab94a6d0c17c1e595445c332b7d8fbf2520a76c783828b7f593afef751f` |
| `sun50i-h616-bananapi-m4-zero-emac-sdio-wifi-bt.dtbo` | 1272 | `6e37b810c31f6ad5352a740cbf3b7878abeca96fbb5b3ed79272ce96c786ebbd` |
| `sun50i-h616-fixup.scr` | 4203 | `9d15d735e0012f5cad565d5c09ef2c7d0332d7f1eff482a224aee0371ea9b1ca` |
| `boot.scr` | 4641 | `1f45d4a8ede5dba091df47f84ffc4d905d20198c20559217ff12bc5f33514ec2` |

`boot.scr`、fixup 與 `uInitrd` 的標頭／資料 CRC 皆通過。解析腳本資料表後，`boot.scr` 內容與 `boot.cmd` 逐位元一致；`uInitrd` 的資料內容與原 gzip initrd 逐位元一致。
只用標準 gzip 與本機 `cpio --to-stdout` 列舉或取出選定資料，沒有把 initramfs 的裝置節點或符號連結落地，更未執行其 `init`。

`CONFIG_MMC=y`、`CONFIG_MMC_BLOCK=y`、`CONFIG_MMC_SUNXI=y`、`CONFIG_PWRSEQ_EMMC=y`、`CONFIG_EXT4_FS=y`、H616 pinctrl／clock／reset 及 gzip initrd 支援均已設定。
Sunxi MMC、MMC core/block、eMMC power sequence 列於 `modules.builtin`，不因 initramfs 沒有獨立 `sunxi-mmc.ko` 就判定缺驅動。
initramfs 只有同一版本的模組目錄；內建模組清單與 rootfs 相同。另抽查 `cqhci.ko`：initramfs 與 rootfs 位元相同，`vermagic` 版本一致。未宣稱逐一核對所有模組。

## MMC2 與 overlay

Linux 原 DTB 及離線套用指定 overlay 後，MMC2 均保持：

- 路徑 `/soc/mmc@4022000`，`status=okay`，相容 `allwinner,sun50i-h616-emmc`／`allwinner,sun50i-a100-emmc`。
- `bus-width=8`、`non-removable`、`cap-mmc-hw-reset`、`mmc-hs200-1_8v`，最高頻率 `150000000` Hz。
- MMC2 pinctrl 對應 PC1、PC5、PC6、PC8、PC9、PC10、PC11、PC13、PC14、PC15、PC16；VMMC 3.3 V、VQMMC 1.8 V 固定供電描述。

原 MMC0 保持啟用；MMC1 原為停用，指定 overlay 啟用 MMC1 及 UART1 藍牙相關節點，未改動 MMC2。
`/aliases` 沒有固定 `mmc0`／`mmc1`／`mmc2` 編號；`/__symbols__` 中的名稱不是 Linux 裝置編號保證。
所以不能由 MMC2 節點名稱或 Wi-Fi 狀態推定固定的 `/dev/mmcblkN`。

`fdtoverlay` 離線套用成功，結果保存在 `analysis/effective.dtb`，但它不是板上實際最終 DT。
fixup 腳本已解碼保存，未執行；本映像 `armbianEnv.txt` 沒有 `param_*`，指定 overlay 也不是 fixup 特別處理的 `pwm34`。
主代理仍須清除非本套的繼承環境參數，並記錄 U-Boot 的 memory、chosen、MAC 等執行時修正，不能直接拿離線套用結果冒稱完全重現原腳本。
0845 沒有外接 EMAC 硬體，本次不驗證 EMAC、Wi-Fi／藍牙、HS200 或供電電氣行為。

## 原 bootloader 與來源限制

IMG 從 byte `8192` 起的 `869881` bytes，與 `/usr/lib/linux-u-boot-current-bananapim4zeroemac/u-boot-sunxi-with-spl.bin` 逐位元一致：
`da59efc96ad3a67bf69bf25175f990903498ca926ada50b924b69434f7899037`。
SPL 的 eGON 加總檢查碼通過。SPL 長 `40960` bytes；本映像 FIT 實際起於 byte `49152`，即 LBA `96`，不應套用救援橋接的其他 FIT 位置。

| 子組件 | bytes | SHA-256 |
| --- | ---: | --- |
| SPL | 40960 | `1bffe216ad15e9a18fe7649089038ea93e0c703b5c8d4dda0068728ad30f43b6` |
| FIT 中 U-Boot | 741952 | `7d3c47e2333ef0828096ddd44801773845b0eac48ae8f3fd95a33bc6e5264102` |
| FIT 中 TF-A | 53361 | `289f6b43fe909ad6c69842a745d3044a93e33453268e612aedda2e445347afd5` |
| FIT 中 U-Boot 控制 DTB | 31616 | `92e9f932fc4a3c17b4c8177408822473545da8a0b0781771a947c3e2ee6330cc` |

FIT 只有 U-Boot、TF-A、控制 DTB 三個映像節點；沒有另列 SCP／Crust，也未見這三節點的 hash／signature 子節點。
此處 SHA-256 是本次擷取後實算，並非 FIT 簽章認證。FIT 配置指定 TF-A 為 firmware，載入位址 `0x40000000`，U-Boot 為 loadable、載入位址 `0x4a000000`。

配套中繼資料宣告 U-Boot `2026.01-S127a-P6c1a-H0021-Vd063-B83dd-R448a`，上游提交 `127a42c7257a6ffbbd1575ed1cbaa8f5408a44b3`；
TF-A 為 `lts-v2.12.9`，提交 `c2a0e7080d64d69940be4ad0ff6578501f3cbf9e`。這是套件宣告，未重新建置作來源證明。
配置包含 A1 792 MHz DDR 參數、`CONFIG_MMC_SUNXI_SLOT_EXTRA=2`、`CONFIG_SUPPORT_EMMC_BOOT=y`；控制 DTB 模型為普通 Zero，MMC2 也為 `okay`。
控制 DTB 與 Linux 的 EMAC DTB 是不同組件，不能混用。現有 SD SRAM 入口到這套原 SPL／FIT 的橋接仍未驗證。

## 寫入風險的精確界線

| 路徑 | 已查到的行為 | 本輪應對 |
| --- | --- | --- |
| `boot.cmd:9`、`:34` | 預設根為 `/dev/mmcblk0p1`，成功匯入本套 `armbianEnv.txt` 才改用 UUID；最後載入核心／initrd 的命令未形成完整失敗即停止保證 | 明確指定 eMMC、分割區與 `/boot/`；缺檔、載入失敗、overlay 失敗直接拒絕試跑，不能用殘留 RAM 或 DTB 回退 |
| `boot.cmd:80`、`:82` | 將 `mmc 0:1` 的 PARTUUID 放入 `ubootpart`，與實際根 UUID 分開 | 受控引導修正為核對過的 eMMC PARTUUID，或整個刪去該參數；只在匯入前設定 `partuuid` 會被腳本覆蓋 |
| `files/platform_install.sh:10` | 舊選碟 helper 優先讀 `ubootpart`，沒有該參數才退回 `root=`；寫入函式以 8 KiB 偏移執行 `dd` | helper 的存在是風險，但本次已查的現行安裝器與 postinst 未見呼叫它；不宣稱首次開機必然因此寫 SD |
| U-Boot 套件 postinst | 只在 `FORCE_UBOOT_UPDATE=yes` 時由 `root=` 找父磁碟並寫 bootloader；現值為空 | 不啟用強制更新；不執行相關套件變更，另禁止安裝器 |
| 現行 `armbian-install` | 轉交 `module_partitioner`；bootloader 子命令接受明確的整顆 SD／eMMC 目標與 `--yes`，可寫 SD，不是依 CID 保護救援板 | 測試器禁止此命令及分割／安裝 API；不能把選項確認當成本計畫授權 |
| 擴容服務 | 已在 `basic.target.wants` 啟用；`/root/.no_rootfs_resize` 不存在。依 `findmnt /` 找父磁碟，呼叫 `fdisk`、`partprobe`、`resize2fs`，不是硬編 `mmcblk0` | 首次受控試跑先遮罩；若根選錯，動態選碟一樣可能改到救援 SD。之後是否放行擴容須另記錄 |
| BSP postinst／initramfs 更新 hook | `BOOTSCRIPT_FORCE_UPDATE=yes` 會複製模板並重建 `boot.scr`；`99-uboot` 更新 `/boot/uInitrd`，不是直接寫裸 bootloader | `/boot` 不可掛救援 SD；凍結套件版本，防止修正被模板覆蓋或配套變動 |
| firstrun／firstlogin | firstrun 服務啟用、首次登入標記存在；會依條件修改系統與 `/boot/armbianEnv.txt`，不是唯讀執行 | 記錄首次設定、帳號／測試工具與開機檔變更，不把加工具後結果當原始首次啟動 |

APT daily 與 upgrade timers 已啟用；可另加一次性 `systemd.mask=apt-daily.service`、`systemd.mask=apt-daily-upgrade.service` 控制自動變動，但不保證阻止人工套件操作。
上述遮罩不阻擋 initramfs 的根檔案系統檢查、日誌回放或一般 rootfs 寫入，也不是對 root 程式的硬體防寫。

## 最少引導調整與驗收

保留原配檔案，不重建共享核心／initramfs；以主代理核對過的 U-Boot 媒體編號與分割區載入它們。
`prefix=/boot/`、DTB 目錄 `/boot/dtb-6.18.49-current-sunxi64/allwinner`、原 EMAC `fdtfile`、`overlay_prefix=sun50i-h616`、原 Wi-Fi／藍牙 overlay 皆明確指定。
`rootdev` 和 `fstab` 在 UUID 唯一時不用改；若有衝突，必須一致修改測試副本的 UUID 及所有引用，另外列為非原樣調整。

保留原 `cma=256M`、序列／螢幕 console 與必要原參數；第一次受控試跑的新增保護至少為：

```text
systemd.mask=armbian-resize-filesystem.service
```

這個參數由映像內 `systemd-debug-generator` 在本次啟動期間遮罩服務，不必先寫入原 rootfs 的停用標記。
實際開機命令還必須修正或完全移除錯誤 `ubootpart`，且所有檔案載入與 overlay 應失敗即停止；不能只在原 `boot.scr` 前設定參數後假定不會再被覆寫。
不要自動 `saveenv`、更新 SD bootloader 或重新套用其他板型 DTB。記憶體載入位址及一次性選路仍須由主代理既有實板安全基線核對，本文件不猜定可用區間。

要驗收「這套核心」而非僅 rootfs，至少須保存實際載入核心、uInitrd、DTB／overlay 的上述雜湊及開機交接命令，
再由主代理的開機紀錄核對 `6.18.49-current-sunxi64`、實際 eMMC CID／控制器與根 UUID、模組版本，以及沒有退回救援核心。
只在救援 Linux `switch_root`／chroot 進本套 rootfs，不算這套核心執行。

以下均不能稱為原生／完全原樣：改用固定 SRAM 救援引導或共用 U-Boot、修改 `ubootpart`／服務遮罩、跳過或改寫原 boot.scr／fixup 行為、
改 DTB／overlay、為消除 UUID 衝突或部署測試工具修改 rootfs。保留核心位元組只能稱為「原配核心在已記錄受控條件下的系統層測試」。
本次結果中 `kernel_executed=false`、`hardware_validated=false`、`write_authorized=false`，沒有任何實板通過聲明。
