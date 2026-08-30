# Banana Pi BPI-F2S 固定來源候選政策

更新日期：2026-08-27

## 階段結論

`bananapif2s` 已建立可追溯的 Sunplus SP7021 legacy 候選契約。候選固定供應商 BSP 提交、Linux、U-Boot、第一階段預建啟動資產、板級 DT 身分、MBR/FAT boot 佈局及根檔案系統 UUID 政策，並補上元件建置、OverlayFS 隔離與完整映像唯讀驗證入口。

本板仍維持 `.wip`。本次已建立完整 Trixie minimal CLI 映像並通過 L2 唯讀軟體守門，但兩個第一階段啟動二進位沒有可對應的原始碼或明確再散布授權，供應商核心停留在 Linux 5.4.35，且沒有燒錄、UART 或實機介面證據。因此 `public_release_allowed=false`、`hardware_claims_allowed=false`，不得把 L2 軟體證據描述成可公開發布或實機支援完成。

## 固定來源

| 元件 | 來源 | 固定提交 | 授權證據 |
| --- | --- | --- | --- |
| BSP 容器倉庫 | `https://github.com/BPI-SINOVOIP/BPI-F2S-bsp.git` | `3eee97bd8fb7582c2d9942a533647c3d78222bb5` | 倉庫頂層沒有統一授權檔，必須逐子樹與逐資產判定 |
| Linux 5.4.35 | 同一 BSP 的 `linux-sp/` | 同上 | `linux-sp/COPYING`，SHA-256 `ee5808b032a67f587d3541099d46de34f5bec8cd5976114ba07f1299ee6001ff` |
| U-Boot 2019.04 | 同一 BSP 的 `u-boot-sp/` | 同上 | `u-boot-sp/Licenses/README`，SHA-256 `7e354ab349b7c11f1fe93639c3096bfe2bb4591659caaa712e2ee101299cf1d4` |
| 交叉編譯器 | 同一 BSP 的 `toolchains/` | 同上 | GCC 執行檔 SHA-256 `ae824ab0542db07ea468297474f3310cdee2abf8d316220b9e3081bada1f7da3`；預建工具鏈未找到整體再散布授權檔 |

2026-08-27 查核遠端 `master` 與 `HEAD` 均指向上述提交。板檔使用 `commit:`，不會因遠端分支移動而靜默取得不同來源。

固定提交只解決可重取性，不代表安全維護。Linux 5.4.35 與 U-Boot 2019.04 都是老舊供應商基線；對外產品若繼續使用，必須另行建立漏洞回補、維護期限與升級策略。

首次自動授權守門曾拒絕 `u-boot-sp/Licenses/README`：人工稽核誤把 `u-boot-sp/README` 的 `09f3…` 雜湊填入授權檔欄位。固定提交中的實際授權檔雜湊是 `7e354a…`；修正後由建置器直接對來源檔重算，不再只檢查欄位格式。

元件採用 BSP 內的 Linaro GCC 7.3.1；其 GCC 執行檔大小為 981376 位元組，建置清單 SHA-256 為 `8cf7e1718ef3f155bd65355a123d8156fc1227955f9282b661e6f1ec2bb9ffbb`。建置器會驗證 GCC 大小與雜湊。工具鏈不會進入執行映像，但預建套件的整體再散布授權尚未確認，因此不能把來源提交固定等同於工具鏈授權已完成。

## 本機稽核證據

本次也核對既有文件倉的官方資料快照。下列檔案不複製進候選，只在機器契約記錄絕對路徑與 SHA-256；它們能證明本次判讀所用輸入，不能取代原始發布者授權或實機驗證。

| 證據 | 本機路徑 | SHA-256 | 限制 |
| --- | --- | --- | --- |
| BPI-F2S 官方產品頁快照 | `/media/pi/SMCI/bpi/doc/banana-pi-doc-benchmark-20260621/downloads/bpi-f2s-residual-gap-audit-p417-20260623/bpi_f2s_official_page.html` | `58842d4e66d56724c580e6fb27cbd602d6b082a1d043019cce3ffebc3b8ad297` | 僅作產品頁歷史證據 |
| BPI-F2S V3.0 原理圖 | `/media/pi/SMCI/bpi/doc/banana-pi-doc-benchmark-20260621/downloads/bpi-f2s-schematic-p239-20260623/Banana-Pi-BPI-F2S-SCH_V3.0.pdf` | `e43a6bfd16d51a0fa640a7fe372694c41becf2f0c1c511a58a2d2f8c1625d9d5` | 文件標題含 SP7021 示範板字樣，尚未完成板版次與電氣簽核 |
| PLUS1 SP7021 資料表 | `/media/pi/SMCI/bpi/doc/banana-pi-doc-benchmark-20260621/downloads/f2-sp7021-datasheet-p198-20260622/PLUS1-SP7021-Datasheet.pdf` | `d67c027f995e6b9d04a1e2a36c2ee9237561033cd6d9ee5b5e3013bba4acfb6e` | 只描述 SoC，不證明 BPI-F2S 板級拓撲 |
| BSP README 快照 | `/media/pi/SMCI/bpi/doc/banana-pi-doc-benchmark-20260621/downloads/bpi-f2-residual-gap-audit-p385-20260623/github_BPI-F2S-bsp_README.md` | `409b2d4348f264f9034792954730e3ea43a56f3edeabdd2797fd6f5f2b43cb26` | 只作供應商建置流程脈絡，不是授權檔 |

四份文件都未確認再散布授權，`included_in_candidate=false`。BSP 原始碼授權仍以 Linux 與 U-Boot 子樹各自的授權檔判定；xboot 與 BSP 其他內容不能由這些文件推導出發布權利。

## SoC 與啟動鏈

SP7021 是四核心 32 位元 Cortex-A7，Armbian 架構為 `armhf`。此平台不使用 TF-A；啟動順序是 Sunplus Boot ROM、xboot、U-Boot、Linux。xboot 是不可由本倉庫來源重建的預建碼，不能把 `ATF_COMPILE=no` 解讀成整條啟動鏈皆為開源建置。

受控資產如下：

| 資產 | 用途 | 大小 | SHA-256 | 判定 |
| --- | --- | ---: | --- | --- |
| `sp-pack/sp7021/common/bin/ISPBOOOT.BIN` | SD FAT boot 分割區第一階段 xboot | 65536 位元組 | `e01081a92b55156868b9df7918e0d5f503d1dda3af94335ed24637786124964a` | 無來源建置與再散布授權證據 |
| `sp-pack/sp7021/common/bin/BPI-F2S-xboot-emmc-boot0-0k.img.gz` | eMMC `boot0` xboot 套件資產 | 22286 位元組 | `67e507efb5bef1d67f97f22da3e87dcc32a285a8a8d003ff3149416fc7dbc81d` | 無來源建置與再散布授權證據 |

第二個資產解壓後為 2097152 位元組，SHA-256 為 `bd5d8139bac8cec6bf9776a045484fc8f51b6c50f7e1f4f683c0efb7bf07acd0`。固定雜湊只能證明位元同一性；在取得權利人授權或可重建替代方案前，完整映像只能作內部技術驗證。

## 儲存與開機佈局

- 分割表明確固定為 MBR／`msdos`，避免把 sector 34 的 U-Boot 位置與 GPT 項目區混淆。
- `u-boot.img` 依原 BSP 寫入 `512 * 34 = 17408` bytes。
- SD xboot 以 `/boot/ISPBOOOT.BIN` 存在於 FAT boot 分割區。
- Linux、initrd 與 DTB 同時依舊版 BPI 契約放在 `/boot/bananapi/bpi-f2s/linux/`。
- 產生的 `/boot/uEnv.txt` 改用該映像實際根檔案系統 `UUID=`，不再依賴 SD 為 `/dev/mmcblk1p2`、eMMC 為 `/dev/mmcblk0p2` 的枚舉順序。

完整映像驗證器新增可選的 `boot_partition_number` 與 `root_partition_number`，會先唯讀掛載 root，再把 FAT boot 分割區唯讀掛載至 `/boot`。Sunplus `uEnv.txt` 必須含與實際 root 分割區一致的 UUID，且不得含 `root=/dev/mmcblk*`。

本候選沒有驗證 eMMC `boot0` 寫入流程。SD 整碟映像、eMMC user area 及 eMMC `boot0` 是不同媒體與位址空間，不得互相推論。

## 板級 DT 與介面邊界

供應商 DTS 原本只使用 `SP7021/CA7/BPI-F2S` model，沒有 Banana Pi 專屬 compatible。候選在 Linux 與 U-Boot 各自加入：

- model：`Banana Pi BPI-F2S`
- compatible：`sinovoip,bpi-f2s`、`sunplus,sp7021-achip`

固定 DTS 描述 512 MiB RAM、8-bit eMMC、4-bit SD、雙埠 Sunplus L2 switch、HDMI、MIPI CSI、RTC、GPIO LED，以及多組預設停用的 I2C、SPI、UART、PWM 與 SDIO。節點存在只表示供應商 DTS 的靜態描述，不證明板上配線、擴充模組、訊號品質或驅動在實機可用。

候選啟用供應商核心已有的 thermal、watchdog 與 USB ConfigFS mass storage 設定。這只證明程式可編譯；OTG 接口角色、VBUS、host 枚舉、`g_mass_storage`、溫度讀值與 watchdog 重置都必須另做實機測試。

診斷套件涵蓋 GPIO、I2C、SPI、MMC、USB、視訊、網路與壓力工具。供應商核心沒有 DRM，顯示使用專有 framebuffer／video 路徑；不得宣稱具備現代 DRM、Mesa OpenGL ES 或硬體視訊解碼支援。

## 元件驗證

完整映像建置前先執行下列元件建置與驗證，將供應商來源、工具鏈及啟動資產問題與 rootfs 封裝問題分離：

```bash
./tools/build-bananapi-sunplus-f2s-components.sh
./tools/verify-bananapi-sunplus-f2s-components.sh
```

元件建置器會取得固定 BSP 提交、套用 U-Boot DTC 相容修補、板級 DTS 身分修補與固定時間修補，驗證兩個預建啟動資產、eMMC xboot 解壓內容及 Linux／U-Boot 授權檔雜湊，使用 BSP 內固定 Linaro GCC 7.3.1，並建置 U-Boot、`uImage`、`zImage`、DTB 與 modules。產物、建置日誌與雜湊位於 `.tmp/bananapi-sunplus-f2s-component/output/`，不加入 Git。

2026-08-27 已以 12 個平行工作完成元件編譯，`component_build_completed=true`，且狀態檔明確保留 `full_rootfs_image_built=false`。編譯所得 DTB 證實 eMMC、SD、網路、HDMI、MIPI CSI 與 SPI 控制器位於 `/soc@B`；機器契約已依產物修正，沒有沿用編譯前推測的 `/soc@A`。

| 產物 | 大小（位元組） | SHA-256 |
| --- | ---: | --- |
| `u-boot.img` | 431984 | `79f780497d9ab1e6c59d109c9677b930ea8241f61283384e405dd610d9ab657f` |
| `u-boot.bin` | 431920 | `b8e1ee44ea80022e8942ba29e72575a5c4190b30162f6efe55bf5f3c57cb99cd` |
| `u-boot.dtb` | 20136 | `91287972ef42e99befccbed6470bd424bd4dcaba848e7fda7e4283fcf6362dbb` |
| `uImage` | 4314160 | `37034ac2213700f6b4c877c582899e15f207c80219d03ee3c1d4e8f2464a86fc` |
| `zImage` | 4314096 | `4c85d6a202f207362918fb386ae7f9a280d2d5a0598b52348684b3190ddd9454` |
| `sp7021-bpi-f2s.dtb` | 20348 | `b2e84da3896f3adc32d8200e51c65bb4c7ec883b54b35fbb652b6dd6b6acd137` |
| `linux.config` | 91620 | `b7361ac5bd384e762afd01a71a196df7358de709d42573439639b61f05107e9d` |
| `linux-modules.tar.xz` | 407824 | `27849187e317fc26a33a0f0eed7daacec1d2fc9d444c65b917fdc10f598d9c09` |

元件驗證器會重新計算大小與雜湊、比對 Git 內機器契約、確認 Linux 與 U-Boot DT 身分、檢查核心設定及模組封裝。U-Boot 編譯有舊原始碼既存的巨集重複定義、主機工具越界讀取及未初始化變數警告；Linux 編譯有 OpenSSL 3 棄用介面、未使用程式碼與靜態匯出警告。兩份日誌都沒有編譯錯誤，但警告尚未逐項排除，因此不能把本次產物視為品質或安全發布證明。

初次稽核發現舊版 U-Boot 的 `quickboot` 映像類型直接採用輸入檔時間，繞過既有的 `SOURCE_DATE_EPOCH` 支援。候選已修補 `qkboot_image.c` 改用 `imagetool_get_source_date()`；元件建置器會改變 `u-boot.bin` 檔案時間、重新產生 `u-boot.img` 並要求兩次 SHA-256 相同。這項守門只證明同一來源樹與工具鏈下的 U-Boot 封裝不受該檔案時間影響，不能擴張成所有元件、所有主機或完整映像皆達位元級可重現。

## 完整候選建置與 L2 證據

完整 Trixie minimal CLI 候選以 F2S 專屬 OverlayFS 入口建置：

```bash
./tools/run-bananapi-sunplus-f2s-candidate-isolated-cache.sh
```

建置完成後執行唯讀驗證：

```bash
./tools/verify-bananapi-sunplus-f2s-candidate.sh
```

OverlayFS 入口只把既有 Armbian cache 當唯讀 lower，所有變更寫入 F2S 專屬 upper；不得直接修改或清除共用 lower cache。

2026-08-27 以提交 `132646e1eb53644bdc4112cd7af4d9cc54502aca` 完成建置與驗證。驗證器確認 MBR、FAT boot 分割區、ext4 root 分割區、實際 root UUID、核心、initrd、F2S DTB、U-Boot target 組態與三個受控啟動載荷；掛載全程使用唯讀模式。第一次完整建置因舊版 Sunplus U-Boot 套件缺少 target 組態證據而被拒絕，修正封裝後重新完整建置，不沿用第一次產物作為通過證據。

| 產物 | 大小（位元組） | SHA-256 |
| --- | ---: | --- |
| 原始 IMG | 1832910848 | `08aa83f5e0f002d607214e42b1c67a0a4dc64a341f9567f047b1d1102af60dd3` |
| XZ 封裝 | 357232960 | `76c4f116512f5ffc6b39a7a90a21def1c0849d6b0b9d4fbcecc02ca391d3a736` |

建置時與驗證時的機器契約 SHA-256 均為 `ebcc16ed50a12100e465884ca3bffbe3bc14f9eb9f7c40c02d221b749aea2e25`，U-Boot 載荷清單 SHA-256 為 `530a58d29e445d85b9240dda0b43aa092312441090ba660b1e07b1b259ac9043`。目前證據等級為內部 L2，不是實機或發布許可。

## 實機與發布阻礙事項

1. 取得 `ISPBOOOT.BIN` 與 eMMC xboot 的可追溯來源、建置方法或明確再散布授權。
2. 以實際 BPI-F2S 保存 UART 冷啟動、暖重啟、關機與斷電重啟紀錄。
3. 分開驗證 SD FAT xboot、SD root、eMMC user area 與 eMMC `boot0` 安裝；確認 UUID root 不受枚舉順序影響。
4. 驗證兩個 Ethernet 埠、USB host／OTG、HDMI、MIPI CSI、RTC、GPIO、I2C、SPI、UART、PWM 與 TPM。
5. 驗證 thermal、watchdog、USB mass storage 及壓力情境，監看核心錯誤、儲存錯誤與網路錯包。
6. 為 Linux 5.4.35 與 U-Boot 2019.04 建立安全維護政策，或完成較新核心與啟動器移植。

在這些阻礙事項完成前，候選只能維持內部 L2 軟體證據；完整映像已通過唯讀檢查，但仍受啟動 blob 授權、老舊供應商基線與缺少實機證據阻擋，不能直接公開發布。
