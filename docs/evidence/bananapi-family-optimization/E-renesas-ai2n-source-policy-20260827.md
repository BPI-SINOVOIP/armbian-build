# Banana Pi AI2N 固定來源與發布政策

## 結論

本候選已把 Linux、U-Boot 與 TF-A 固定到可追溯提交，並以固定 TF-A 原始碼重建 `bptool` 與 `fiptool`。乾淨來源的 AI2N DTB、U-Boot、BL2、BL31、SD BL2 與 FIP 已完成元件建置；完整 Armbian 映像尚未在本階段建置，因此目前是「可進入 L2 完整映像守門的軟體候選」，不是已通過 L2 的發布映像。

映像流程仍會安裝九個缺少可核對再散布授權或 ABI 契約的預建資產。`config/validation/bananapi-renesas-rzv2n-ai2n-legacy.json` 因此固定設定 `public_release_allowed=false`；建置與驗證入口在 `PUBLIC_RELEASE=yes` 時必須拒絕執行。未取得書面授權前，只能用於內部工程驗證。

## 固定來源

| 元件 | 固定來源 | 固定提交 | 授權證據 |
|---|---|---|---|
| Linux 6.1.107 | `https://github.com/BPI-SINOVOIP/pi-linux.git` | `48c742429129c095045823c204209bb2a92fb5b4` | `COPYING`，SHA-256 `fb5a425bd3b3cd6071a3a9aff9909a859e7c1158d54d32e07658398cd67eb6a0` |
| U-Boot 2021.10 | `https://github.com/BPI-SINOVOIP/pi-u-boot.git` | `8aec7f20bcf5555d7d219c2bad295b4a627b6521` | `Licenses/README`，SHA-256 `050bd541ef2ab90bb0b55359756832bb9281b4554be0e9be6b33eea3be8669f0` |
| TF-A 2.10 衍生來源 | `https://github.com/BPI-SINOVOIP/arm-trusted-firmware` | `a011da37865c7649db48efc29b18b36cf87e4bb3` | `license.rst`，SHA-256 `e3f7de3cd0aea44aad09419b6dc5e6356d3d43c7bd850801a44a6211b94162b0` |

原板檔追蹤 `rzv2n-6.1`、`rzv2n-v2021.10` 與 `rzv2n-v2.10` 分支；2026-08-27 解析結果分別為上述三個提交。本候選直接使用 `commit:`，避免分支後續移動改變產物。

框架內沒有 `archive/rzv2n-6.1` 或 `legacy/u-boot-rzv2n-v2021.10` 的實際修補檔；AI2N 板級支援完全來自固定供應商來源。這一點只表示來源樹可重建，不表示硬體功能已通過。

## 封裝工具

原流程使用 `packages/blobs/bpi-renesas/tools/` 內的 x86-64 預建 `bptool` 與 `fiptool`，該目錄只有 Ubuntu 22.04 建置提示，沒有授權或來源提交證據。本候選改用固定 TF-A 來源中的：

- `tools/renesas/rz_boot_param/bptool.c`，SHA-256 `2e55913a71d607234404d16e1e65e3327ca7034e9363e81c2f2255a176477574`
- `tools/fiptool/fiptool.c`，SHA-256 `9c47cf4314e981c5927241bbec129535b0a1181695fc9b185896f32f70ed73cb`

兩個工具均由 TF-A 建置檔標示 `BSD-3-Clause`。原預建檔仍留在倉庫供歷史追溯，但 family 不再引用；validation 仍鎖定其雜湊並以測試防止重新接回。

## 來源內二進位

固定 TF-A 樹含六個跨平台二進位檔：四個 Arm 範例金鑰雜湊、一個 RK3368 DDR 載荷及一個 RK3399 HDCP 載荷。`PLAT=v2n BOARD=evk_1` 不引用這六個檔案，validation 逐檔鎖定雜湊並標示 `used_by_ai2n_build=false`。

固定 Linux 樹含 `net/wireless/certs/sforshee.hex` 與 `wens.hex`；目前核心設定啟用 `CONFIG_CFG80211_USE_KERNEL_REGDB_KEYS=y`，兩者屬於建置輸入，並受 Linux 來源授權與固定提交約束。固定 U-Boot 樹沒有列入相同副檔名清單的預建依賴。

## 預建執行期資產

| 安裝用途 | 倉庫檔案 | SHA-256 |
|---|---|---|
| DRP Codec | `packages/blobs/bpi-renesas/bsp/rzv2n/Codec_Bin.bin` | `f0337b054c25ab8fae0b8e70b0d050e099e136c2fa13c6531227efb9651fbb9c` |
| 原廠燒錄器 | `packages/blobs/bpi-renesas/bsp/rzv2n/Flash_Writer_SCIF_RZV2N_DEV_LPDDR4X.mot` | `e4ec7b41a4fc748a6761d111853b5b8d0633adc1180ae7ea483b4d93cfd30714` |
| OpenCVA | `packages/blobs/bpi-renesas/bsp/rzv2n/OpenCV_Bin.bin` | `f1dfccd3a6f7a41fc0574c0b18a6e5af3bfc09d0759fe58100551487cbf71a06` |
| RTL8821CU 設定 | `packages/bsp/bpi-renesas/usr/lib/firmware/rtl8821cu_config` | `f1118d807006197c0c27e838d927aea0ea670289a5d927f8572cd03b8b5e2972` |
| RTL8821CU 韌體 | `packages/bsp/bpi-renesas/usr/lib/firmware/rtl8821cu_fw` | `816255740f9e22ce730497e992b00b1410e39971c1a7a6ffc5c60e1ae525c518` |
| 相機 ISP | `packages/bsp/bpi-renesas/usr/local/bin/mali_iv021_isp-single.elf` | `a142a0c30d386665125a8585a63aea72a7f6eae209cf779b13c1e7573c78ebb8` |
| 相機 ISP | `packages/bsp/bpi-renesas/usr/local/bin/mali_iv021_isp.elf` | `8358dab9a785c6f97b4a4a9702ebfb31fe3fba6dadf61e2768d2eb4c28a69d10` |
| 範例程式 | `packages/bsp/bpi-renesas/usr/local/bin/sample-app` | `37bed8ccb2f7a3bead30d9b4d977eb3714546ce35d9b51fdd1361bd21509d179` |
| 相機初始化 | `packages/bsp/bpi-renesas/usr/local/bin/v4l2-init.sh` | `c81231caaef6584c49010c7539c9e7c1fca5ccbac6e0aed32ba64ad500b58a52` |

這些資產在 Armbian 倉庫提交 `d1a05029138edd97a460bb0a4d0f6fbc70aa899e` 中加入，但沒有隨附授權檔。兩個 ISP 檔為已移除符號的 AArch64 ELF；`sample-app` 為靜態 AArch64 ELF。`OpenCV_Bin.bin` 可見技術字串 `RENESAS OCA V2H`，與 AI2N 的 RZ/V2N 目標沒有可核對的 ABI 說明，因此不能據此宣稱 OpenCVA 或 DRP-AI 功能通過。

## 元件驗證

2026-08-27 使用 Ubuntu GCC 11.4.0、`aarch64-linux-gnu-gcc` 11.4.0 與 OpenSSL 3.0.2，從三個固定提交的乾淨封存內容執行：

1. Linux `bananapi_ai2n_defconfig` 及 `renesas/bananapi-ai2n.dtb` 建置成功；DTB 大小 70,571 位元組，SHA-256 `51b9c6f78e88ceb61d44a56f2507a71da94bb8245d1f6f163f0ff97f306814de`。
2. U-Boot `bananapi_ai2n_defconfig` 建置成功；最終設定包含 AI2N 身分、SDHI、雙乙太網、SPI、I2C、USB gadget 與 USB mass storage 命令。
3. TF-A 以 `PLAT=v2n BOARD=evk_1 ENABLE_STACK_PROTECTOR=default` 建出 BL2 與 BL31。
4. 由相同 TF-A 來源建出 `bptool`、`fiptool`，再產生 SD BL2 與 FIP；`fiptool info` 可解析 BL31 與 BL33。

U-Boot 2021.10 在 OpenSSL 3.0 會出現已棄用 API 警告，但本次沒有建置錯誤。U-Boot 與 TF-A 產物會受建置時間、路徑及工具鏈影響，因此 validation 不把本次元件 SHA-256 當跨環境固定值；只固定來源、設定、載荷位置、最低大小與最終映像中的套件雜湊。DTB 已驗證為相同來源下可重現，故固定其 SHA-256。

## 映像契約

- SD BL2 寫入位元組偏移 `512`。
- FIP 寫入位元組偏移 `393216`。
- 第一分割區起點固定為 sector `8192`，邏輯 sector 為 512 位元組。
- `u-boot.bin` 只存在 U-Boot 套件；FIP 內以 BL33 形式攜帶。
- 核心 DTB 固定為 `renesas/bananapi-ai2n.dtb`。
- 18 個 BPI 前綴 overlay 必須進入映像，但預設不啟用。
- 核心、DTB、U-Boot、TF-A 來源中繼資料與九個安裝資產必須在唯讀掛載中核對。

DTB 的節點、`status`、匯流排寬度與 alias 只用來防止軟體契約退化。GPU、DRP-AI、相機、顯示、PCIe、雙乙太網、SD、eMMC、USB gadget、Wi-Fi 與 40-pin I/O 均仍需實體板測試，不能以節點存在或核心選項開啟取代。

## 後續完整建置

主機沒有其他 Armbian 建置程序且唯讀下層快取穩定後，執行：

```bash
cd /media/pi/SMCI/armbian/bpi-v26.2.1-bananapi-ai2n-prep
./tools/run-bananapi-renesas-ai2n-candidate-isolated-cache.sh
./tools/verify-bananapi-renesas-ai2n-candidate.sh
```

候選輸出目錄為：

```text
output/images/2026.08/bananapi-renesas-rzv2n-ai2n-trixie-legacy-cli
```

完整驗證通過後，證據層級只能標示為內部 L2。若執行：

```bash
PUBLIC_RELEASE=yes ./tools/run-bananapi-renesas-ai2n-candidate-isolated-cache.sh
```

守門器必須禁止建立公開發布候選。只有在九個資產取得可核對的再散布授權、更新驗證設定，並完成實體板測試後，才能另行審查公開發布。
