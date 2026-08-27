# Banana Pi F2P 來源、啟動鏈與發布政策

## 結論

目前證據仍是 **內部使用、SD-only、可追溯的 L1 元件候選**。本階段已補齊完整映像的 L2 守門，下一步會由同一來源提交實際重建與唯讀驗證；在成功證據產生前不提前升級。F2P 專用 Linux DTS、U-Boot DTS 與 defconfig 可由固定 BSP 提交編譯，但現有 BSP 沒有 F2P 專用 eMMC xboot，預建 `ISPBOOOT.BIN` 也沒有足以支持再散布的明確授權證據。因此：

- 板檔維持 `.wip`。
- `public_release_allowed=false`。
- `hardware_claims_allowed=false`。
- 禁止使用 `BPI-F2S-xboot-emmc-boot0-0k.img.gz` 建立 F2P eMMC 候選。
- 完整 rootfs 建置與 L2 唯讀驗證已列入本階段執行範圍；完成前不宣稱可開機或介面可用。

## 固定來源

| 元件 | 來源 | 固定版本 | 稽核結果 |
| --- | --- | --- | --- |
| BSP 容器 | `https://github.com/BPI-SINOVOIP/BPI-F2S-bsp.git` | `3eee97bd8fb7582c2d9942a533647c3d78222bb5` | 同一提交包含 Linux、U-Boot、封裝資產與工具鏈 |
| Linux | BSP 內 `linux-sp` | `5.4.35-BPI-F2P-Kernel` | F2P defconfig 與 DTS 存在，元件可編譯 |
| U-Boot | BSP 內 `u-boot-sp` | `2019.04` | F2P defconfig 與 DTS 存在，套用既有 `yylloc` 主機工具相容修補後可編譯 |
| 交叉工具鏈 | BSP 內 `toolchains/gcc-linaro-7.3.1-2018.05-x86_64_arm-linux-gnueabihf` | `Linaro GCC 7.3-2018.05` | 可執行，但尚未完成獨立再散布授權稽核 |
| Armbian 韌體 | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` | 板檔、驗證契約、建置日誌與映像套件必須解析為同一提交 |
| 第一階段資產 | BSP 內 `sp-pack/sp7021/common/bin/ISPBOOOT.BIN` | SHA-256 `e01081a92b55156868b9df7918e0d5f503d1dda3af94335ed24637786124964a` | 預建二進位；來源、F2P 板級相容性與再散布授權均未閉合 |
| TF-A | 不適用 | 不適用 | 現有 32 位元 SP7021 BSP 流程未建置或封裝 TF-A |

固定提交日期為 2020-12-27。未找到可把 BSP 內預建資產視為開源或可任意再散布的倉庫層級授權聲明。

## 本地與官方證據

- 既有板檔：`config/boards/bananapif2p.wip`。
- 既有族群整合：`config/sources/families/include/sunplus_sp7021_bpi_legacy_common.inc`。
- 本地文件套件：`/media/pi/SMCI/bpi/doc/banana-pi-doc-benchmark-20260621/board-packages/BPI-DOC-0006_Banana-Pi-BPI-F2P/`。
- 本地官方頁面快照 SHA-256：`c499a0d1ec93c3199a9dd06643d92791fd339fdd61f0de6352a8ae184b397e0e`。
- 官方頁面擷取資料指出 SoC 為 Sunplus SP7021，儲存介面含 microSD 與 8 GB eMMC；此資料只用來確認產品範圍，不取代原理圖或實機驗證。
- BSP `README.md` 明確描述同一倉庫涵蓋 F2S/F2P，`configure` 也有 `bpi-f2p` 目標；但倉庫只提供 F2S 命名的 eMMC xboot。

## 候選啟動鏈

依現有程式與封裝邏輯推定的 SD 候選流程如下，仍需 UART 實機證明：

1. SP7021 BootROM 載入預建 `ISPBOOOT.BIN`。
2. `ISPBOOOT.BIN` 進入位於媒體偏移 17,408 位元組的 `u-boot.img`。
3. U-Boot 讀取 FAT 開機分割區的 `uEnv.txt`。
4. `uEnv.txt` 載入 `bananapi/bpi-f2p/linux/uImage`、`uInitrd` 與 `sp7021-bpi-f2p.dtb`。
5. 根檔案系統預期位於第二分割區。

這條鏈不包含 F2S eMMC xboot。`SUNPLUS_BPI_EMMC_XBOOT_ASSET` 在 F2P 板檔明確設為空值，共用整合只在板檔明確指定時才封裝 eMMC xboot。

## 完整映像守門

- `tools/check-bananapi-sunplus-f2p-source-policy.py` 驗證固定來源、授權邊界及 L1/L2 狀態機，拒絕只有標籤而沒有完整證據的假 L2。
- `tools/build-bananapi-sunplus-f2p-candidate.sh` 固定 `SOURCE_DATE_EPOCH=1609074838`，要求至少 `40 GiB` 可用空間，且只接受專用 OverlayFS 入口。
- `tools/verify-bananapi-sunplus-f2p-candidate.sh` 強制 XZ 串流同一性與 L2 共用驗證，失敗時覆寫舊成功狀態。
- 驗證契約要求恰好兩個 MBR 分割區，分別為 `8192+524288` 與 `532480+2613248` sectors；`u-boot.img` 位於位元組偏移 17408，`ISPBOOOT.BIN` 為套件與 FAT 開機檔，並在 rootfs 與 FAT 分割區排除 F2S eMMC xboot。
- 完整映像必須保留唯一內容的核心設定與唯一 U-Boot 最終設定證據；舊 BSP 可保留兩個名稱不同但雜湊相同的核心設定檔，不允許出現第二種設定內容。驗證同時固定 Git revision、DTB 身分與雜湊、SD/eMMC 匯流排寬度，以及 UUID 根檔案系統路徑。

## 授權判定

| 範圍 | 現有證據 | 判定 |
| --- | --- | --- |
| Linux | `linux-sp/COPYING` | 核心來源有 GPLv2 證據；仍須處理完整對應來源與個別韌體例外 |
| U-Boot | 多數來源檔有 SPDX 或 GPL 標示，但 BSP 頂層與 `u-boot-sp` 頂層缺少完整授權彙整 | 可進行內部編譯；對外散布前須補齊授權清單與對應來源 |
| `ISPBOOOT.BIN` | 僅有二進位檔，未找到明確授權檔 | 不允許據此建立公開散布授權結論 |
| F2S eMMC xboot | 僅有二進位檔，且板級身分不符 F2P | F2P 必須排除 |
| 隨附工具鏈 | 含部分元件授權文件，但未完成整包逐項稽核 | 不隨本候選重新散布 |

## DTS 與介面限制

- Linux DTB 可編譯，根節點 `model` 為 `SP7021/CA7/BPI-F2P`，`compatible` 為 `sunplus,sp7021-achip`。
- eMMC、SD、雙網路、USB host／device、HDMI、MIPI CSI、TPM 與多組 UART 節點存在。
- SPI0 至 SPI3、I2C0、I2C3、音訊、PWM 與第二組 MIPI CSI 在目前 DTS 明確停用。
- `dtc` 對舊 DTS 回報多項單元位址、simple-bus、別名與中斷控制器警告；這些不是可忽略的硬體通過證據。
- Linux 5.4.35 與 U-Boot 2019.04 已老舊，安全性、維護性與 Trixie 使用者空間相容性尚未驗證。

## 升級條件

要解除內部 SD-only 限制，至少必須完成：

1. 取得 `ISPBOOOT.BIN` 的來源或可再散布授權，以及 F2P 板級相容證據。
2. 若要支援 eMMC，取得 F2P 專用 xboot、寫入配置及可還原的實機程序。
3. 以固定來源建立完整候選映像，通過唯讀分割區、DTB、U-Boot 偏移與禁止資產檢查。
4. 在標明板號與板修的 F2P 上取得 UART 開機、SD、eMMC、雙網路、USB、HDMI、TPM、GPIO、I2C 與熱穩定性證據。
5. 完成 Linux、U-Boot、第一階段資產與工具鏈的對外散布授權稽核。
