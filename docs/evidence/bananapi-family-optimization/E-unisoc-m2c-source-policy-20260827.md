# Banana Pi M2C Unisoc L0 本機來源快照稽核政策

日期：2026-08-27

## 結論

`bananapim2c` 目前只能建立 L0 本機來源快照稽核。它能辨識 `UNC_LINUX_RLS_25C_W26.07.2` 的 95 個 `repo` 專案提交、41 組本機追蹤差異、M2C 板級檔案與一份歷史 PAC 記錄，但不能把這些內容解讀成可重放來源、元件建置、完整映像或硬體證據。

板檔必須保留 `.wip`，契約固定 `public_release_allowed=false`、`hardware_claims_allowed=false`、`complete_rootfs_image_allowed=false` 與 `component_build_allowed=false`。本次沒有編譯元件、沒有建立 rootfs、沒有重新簽署，也沒有打包或發布 PAC。

## 稽核範圍

- Armbian 基準提交：`3f2cd8493b00be096c004278f8a67269e1b93867`
- Unisoc 來源：`/media/pi/SMCI/bpi/unisoc/sync-20260524/source_sync_rls_25c`
- 基線名稱：`UNC_LINUX_RLS_25C_W26.07.2`
- manifest 提交：`7ac2b5ae548b9dd9c4d2f0b32476abd5c6fa7058`
- 解析後 manifest：`source_sync_rls_25c_manifest_20260524_110912.xml`
- 解析後 manifest SHA-256：`bc74dbe539efeb34d9ae6fbf11efc5110e6148a25e6cfc745098f117524d2a52`
- 專案數：95
- 原廠目標：`uis7885_2h10+wayland+wayland+sec+uboot22 userdebug`

機器可讀契約位於 `config/validation/bananapi-unisoc-uis7885-m2c-vendor.json`。驗證器以 XML parser 解析 manifest，逐專案比對實際 `HEAD`，不以分支名稱或浮動標籤替代提交碼。

## 基準提交識別

| 元件 | 路徑 | manifest 提交 |
| --- | --- | --- |
| Linux 5.4 | `source/kernel/kernel5.4` | `7be74a4ba47b9dcbebfb7c8e58ec84b7f4cbce90` |
| U-Boot 2023.01 衍生碼 | `source/bsp/u-boot22` | `b694efd926eed1284f0b2227ed9639509b26a0c5` |
| chipram／SPL／FDL1 | `source/bsp/chipram` | `ecc21f66e321b413809ebb6962b09ab821a6e176` |
| Unisoc Yocto layer | `layers/meta-unisoc` | `b4431ddfec6fd1d1c78296e088b6adaaa4468531` |
| PAC 設定 | `prebuilts/pac_config` | `87e35ff4cc71fc47d2c5fe00c27a8374efe790c5` |
| PAC 工具 | `prebuilts/pac_script` | `8a77fab02088d25a83cf740c9bbd901c073cefe9` |
| 建置與簽署腳本 | `prebuilts/scripts` | `30b1a46095442efbaa88f20092796a7411fb6c64` |
| Unisoc 預建內容 | `prebuilts/unisoc_bin` | `6a5ed1221ca86e26e0046a1621c39710a71eeffb` |
| PAC 預建內容 | `prebuilts/pac-binary` | `160417c5b2524bd329732e8918946abf312a54b3` |
| Yocto 離線來源 | `source/tarballs` | `d02cdfddf832a8f6307d0949f64bea25e06f98f3` |

這些提交只識別目前 checkout 的基準，不能還原下列本機追蹤差異與未追蹤內容。

## 41 組不可重放差異

來源樹不是乾淨 checkout，共有 41 個專案含追蹤差異，範圍包括 SD boot 的 chipram、U-Boot、Yocto layer、PAC 設定、M2C pinmap，以及 Linux、modem、sensorhub、GPU、NPU、相機與電源等既有差異。

契約保存各專案 `git diff --binary --no-ext-diff` 輸出的 SHA-256，只能偵測目前本機內容是否漂移，不能從雜湊還原修補。`work/20260604/Release` 與 `work/20260605/Release` 的五份外部證據也不是完整修補集合：其中 `layers/meta-unisoc` 與 U-Boot 證據不能完整反向套用目前工作樹。因此這 41 組差異不能作為正式來源，也不能複製到公開候選。

## 未追蹤輸入阻擋

2026-08-27 的唯讀盤點結果如下：

- 55 個專案含未追蹤檔。
- 未追蹤檔總數為 6,751。
- `source/tarballs` 有 3,768 個未追蹤檔；其中 `git2` 約 6.6 GiB，而 Yocto 把 `source/tarballs` 設為 `DL_DIR`。
- `prebuilts/unisoc_bin` 有 137 個未追蹤檔，包含二進位、共享函式庫、韌體與建置工作目錄。
- 其他未追蹤內容分布於 sensorhub、modem、GPU、NPU、相機、Trusty、GNSS 與其他元件。

契約採 `deny-unless-allowlisted`。允許清單目前為空，且 `known_unclassified.blocking=true`，所以來源驗證必須列舉全部未分類路徑後失敗。未來每一個必要未追蹤檔都必須記錄來源根目錄相對路徑、SHA-256、用途與授權狀態；任何清單外檔案、符號連結、雜湊漂移或缺少項目都必須失敗。守門不會刪除、搬移或修正既有 6,751 個檔案。

## 遠端授權限制

manifest 遠端為 `https://git.unisoc.com/gerrit/platform/cus-manifest`，其餘專案由解析後 manifest 指向 Unisoc Gerrit。2026-08-27 未登入測試時，manifest 回覆 `Unauthorized`，核心遠端要求 `bananapi` 帳號密碼。

契約只記錄遠端位址與提交碼，不保存帳號、密碼、權杖或私鑰。尚未建立可供另一台機器使用的授權操作說明、受控鏡像或完整來源封存，因此不能聲明跨機取得已完成。

## 開機與打包鏈

目前髒來源樹含下列設定：

- `KERNEL_BOARD = "uis7885-2h10"`
- `UBOOT_BOARD = "uis7885_2h10"`
- `SUPPORT_EMMC_UFS_SDBOOT = "yes"`
- `SECBOOT_ENABLE=sec`
- `UNISOC_PRODUCT=uboot22`

PAC 設定引用 signed FDL、eMMC／UFS／SD SPL、U-Boot、boot、DTBO、Trusty、SML、teecfg、modem 與其他分割區。`u-boot22.bb` 與 `chipram.bb` 繼承 `sign_unisoc_binary`。這些字串只能證明設定存在，不能證明 SD 已開機、一般非安全開機可用或一般 Armbian raw image 已受支援。

## 歷史 PAC 證據限制

2026-06-10 的本機記錄顯示原廠目標曾完成 Yocto 與 PAC 打包：

- 記錄：`logs-build/bpi_m2c_sdboot_pinmap_build_20260610_124058.log`
- 記錄 SHA-256：`c1fbf1de3a71e4823fbb8efba94111c2a623cf1ef53c25a44f685dbea9c4d59d`
- PAC 大小：`1390235800` bytes
- PAC SHA-256：`8c0e86e39dc7050701833e3821ae01aa36a00d2a36ebc2e30e6efd7fc83bf1e9`
- 記錄結尾：`Build END: 2026-06-10 14:53:15`

這只證明當時的既有本機髒來源樹完成原廠 secure PAC 打包。它不是本次稽核產物，不證明乾淨來源可重放、Armbian rootfs 可用、SD 或 eMMC 已開機，也不授權公開散布。歷史 PAC 與記錄不是來源守門必要輸入，缺少時不能因此改變目前 L0 判斷。

## 授權與二進位邊界

| 範圍 | 現有證據 | 判斷 |
| --- | --- | --- |
| Linux | `COPYING` 可辨識 | 只能稽核該來源；完整開機鏈仍受其他元件限制 |
| U-Boot | `Licenses/README` 與 recipe 的 `GPLv2+` | 不能自動涵蓋韌體標頭、簽署鏈與本機差異 |
| chipram | recipe 宣告 `GPLv2`，但根目錄 `COPYING` 是空檔 | 授權證據不足，不得公開散布 |
| 建置腳本 | `prebuilts/scripts/LICENSE` 為 MIT | 不自動涵蓋 PAC、簽署工具與預建內容 |
| PAC、modem、Trusty、GPU、NPU、VPU、GNSS 與其他 binary | 未找到完整逐項再散布授權 | 阻擋公開候選與元件包 |

沒有查閱、複製或輸出任何私鑰內容。簽署設定、憑證與金鑰管理必須由有權限的維護者另行封閉。

## 工具守門

- `tools/verify-bananapi-unisoc-m2c-sources.sh`：驗證 95 個提交、41 組追蹤差異、未追蹤允許清單、manifest 與板級檔案；目前預期失敗並列舉 6,751 個未分類輸入。
- `tools/build-bananapi-unisoc-m2c-candidate.sh`：只有來源守門通過時才建立五個固定檔案的 L0 稽核包；目前不會建立輸出目錄。
- `tools/verify-bananapi-unisoc-m2c-candidate.sh`：只接受五個精確白名單檔案，拒絕任何額外檔案、目錄或符號連結。
- `tools/bananapi-m2c-l0-guard.sh`：讓舊 Yocto、PAC、混合 rootfs、重新簽署與發布入口共用同一份契約。
- `tools/bananapi-safe-removal.sh`：遞迴清除前檢查解析後前綴、符號連結、掛載點、相對深度與絕對深度。

公開發布入口一律由 `public_release_allowed=false` 阻擋。內部建置入口必須先通過來源快照守門；現階段會因未分類未追蹤輸入與契約阻擋狀態而停止。守門沒有環境變數繞過開關。

## 後續必要條件

1. 將 41 組必要差異整理成具內容、來源、授權與順序的修補集，移除純檔案模式雜訊。
2. 將必要未追蹤輸入逐檔分類並建立允許清單，拒絕所有清單外內容。
3. 建立不含憑證的遠端授權操作說明或受控來源鏡像，並由全新工作目錄驗證取得結果。
4. 取得所有必要預建元件、PAC 與簽署工具的使用及再散布授權。
5. 建立不暴露私鑰、輸入受控且保存完整記錄的簽署流程。
6. 完成完整映像的分割表、signed boot chain、核心、DTB／DTBO、rootfs、模組、韌體與壓縮串流唯讀驗證。
7. 軟體證據完成後另行審查證據等級；UART、冷啟動、SD、eMMC、網路、無線、顯示、音訊、USB、GPIO 與壓力測試仍須個別實機證據。
