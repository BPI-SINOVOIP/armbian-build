# Banana Pi AIM7 RK3588 vendor 來源與候選守門政策

日期：2026-08-27

## 結論

原本的 `bananapiaim7.wip` 直接載入 `armsom-aim7-io.csc`，Linux 與 U-Boot 也沿用 ArmSoM AIM7 IO 身分。這種設定可供初期帶板，但無法獨立回答 Banana Pi 板級來源、使用哪個固定提交、載荷是否可依法散布，以及哪些介面只是靜態描述。

本候選改為自足 Banana Pi 板檔，新增 Banana Pi 專用 Linux／U-Boot DTS wrapper 與專用 U-Boot defconfig。wrapper 只覆寫 `model` 與 `compatible`，底層硬體描述仍明確繼承 `rk3588-armsom-aim7-io.dts`；沒有原理圖與實機證據的差異一律不猜測。

目前完成的是可重現 L2 軟體候選準備，不等於正式 L2 映像已通過。這次依要求沒有建置完整根檔案系統映像，也沒有實體板 L3 證據；在完整映像與硬體守門完成前，不得宣稱硬體介面已通過，也不得核准候選對外發布。

## 稽核依據

Banana Pi 官方 AIM7 頁面將 Linux BSP、Linux 核心與 U-Boot 來源指向 ArmSoM 專案，並描述 AIM7 模組與 AIM7 IO 開發套件。這可解釋既有 Armbian 初始移植為何採用 ArmSoM AIM7 IO DTS，但不能證明兩者所有載板差異已被核對。

- 產品與開發資料：`https://docs.banana-pi.org/zh/BPI-AIM7/BananaPi_BPI-AIM7`
- Linux 固定來源：`https://github.com/armbian/linux-rockchip.git`
- U-Boot 固定來源：`https://github.com/radxa/u-boot.git`
- RKBin 固定來源：`https://github.com/armbian/rkbin`

## 固定來源

| 元件 | 固定提交 | 本次用途 |
| --- | --- | --- |
| Linux 6.1.115 | `c6157104418d012823413c02f9222f3fe123dd25` | 建置 `rk3588-bananapi-aim7.dtb` |
| U-Boot 2017.09 | `39cd993e5d6296635438e84f4576b3a9bf76f86e` | 建置 AIM7 SPL、U-Boot DTB 與 FIT |
| RKBin | `1d3c61008fa823936ae7a59615393f8294b64456` | 提供 DDR v1.20、BL31 v1.48 與 RockUSB loader |

板檔固定 `BOOTBRANCH_BOARD`、`KERNELBRANCH_BOARD` 與 `RKBIN_GIT_REF`，並在 vendor 分支設定階段覆寫家族的可移動分支。RKBin 的 `LICENSE.TXT`、DDR、BL31 與 RockUSB loader 另以 SHA-256 固定。

## RKBin 散布政策

固定提交的 `LICENSE.TXT` 提供二進位軟體的複製與散布授權，但同時限制只能以二進位形式隨採用 Rockchip 積體電路的平台散布，不得獨立散布或修改，且散布內容必須附上相同授權文件。

AIM7 採用 RK3588，符合平台範圍；映像建置仍必須保留未修改的 RKBin 載荷，並將雜湊相同的 `LICENSE.TXT` 安裝到 `/usr/share/doc/armbian-bsp-bananapiaim7/rkbin.LICENSE.TXT`。任何載荷雜湊、授權檔或平台條件不符時，都必須拒絕發布。

機器契約目前設定 `candidate_public_release_approved=false`。這不是否定授權，而是表示完整映像建置、唯讀驗證、原理圖稽核與實機證據尚未封閉，不能把「具備條件式散布授權」誤寫成「候選已獲發布核准」。

## 板級身分與啟動鏈

- Linux DTB：`rockchip/rk3588-bananapi-aim7.dtb`
- U-Boot DTS：`rk3588-bananapi-aim7.dts`
- U-Boot defconfig：`bananapi-aim7-rk3588_defconfig`
- `idbloader.img` 映像偏移：32768 bytes
- `u-boot.itb` 映像偏移：8388608 bytes
- GPT 第一分割區起點：32768 sectors
- DDR：`rk3588_ddr_lp4_2112MHz_lp5_2400MHz_v1.20_20250926.bin`
- BL31：`rk3588_bl31_v1.48.elf`

Linux 與 U-Boot 二進位都必須含 `Banana Pi AIM7` 與 `bananapi,bpi-aim7`，並拒絕仍保留 `ArmSoM AIM7 IO` model 的成品。保留 `armsom,aim7-io` 次級相容字串，是為了忠實記錄硬體描述來源，不代表 ArmSoM 品牌身分仍是主要板級身分。

## 元件建置證據

本次只在乾淨固定提交上執行 Linux DTB 與 U-Boot 元件建置，沒有執行完整映像建置。

| 元件 | 大小 | SHA-256 |
| --- | ---: | --- |
| Linux `rk3588-bananapi-aim7.dtb` | 265522 | `fdf3d029773c5374411a08edc6fcfe65532c5fa94d7845b05e28988f338e796f` |
| U-Boot `idbloader.img` | 323584 | `6980555b2aacaec0ec8dee312ffe6c0e7c74c2f4f8a8ece49c3ca69b263328ae` |
| U-Boot `spl/u-boot-spl.bin` | 242776 | `d20221a30b870fbbeeb30ada7a021b068181f817ff6b2d883dfb7a52f33a7dcb` |
| U-Boot `u-boot.dtb` | 10735 | `9fa10b2d75ecfbad937c3add9c1c7214eaf83a149d46b13f7cc696c309719a69` |
| U-Boot `u-boot.itb` | 1462784 | `e09369b519700d6916889d9ad9ccfb8bef1616405c22e3d7e00098ab845f3f6f` |

這些雜湊證明固定來源與本次板級 wrapper 可建置，不是正式候選映像的雜湊，也不能替代 SD／eMMC 開機證據。

## 靜態 I/O 與加速器邊界

機器契約要求映像納入 GPIO、I2C、SPI、PCIe/NVMe、USB、DRM、OpenGL ES、Vulkan、V4L2、FFmpeg、GStreamer 與 OpenCL 診斷工具。核心契約涵蓋 GPIO character device、I2C、SPI、PCIe、Mali Bifrost、Rockchip MPP、RGA、RKNPU、DRM、USB gadget 與儲存驅動。

固定 Linux DTS 可確認 SD、eMMC、GbE、PCIe、HDMI、DP、USB host、GPU、VPU、RGA 與 NPU 節點的靜態狀態，但仍存在下列差異：

- 產品資料描述 PCIe 3.0 四通道，繼承 DTS 的啟用節點卻是 `num-lanes = 1`；在原理圖與訊號完整性驗證前維持現況。
- SPI 與 DSI 節點仍停用；工具存在不表示這些介面已可使用。
- GPIO 與 I2C controller 存在，不代表 260-pin 載板腳位、電壓域、上拉或 pinmux 衝突已核對。
- GPU、VPU、RGA 與 NPU 節點啟用，不代表使用者空間驅動、韌體、效能或穩定性已通過。
- 目前 USB DRD 節點設定為 host；核心具備 gadget 功能不代表該連接器已驗證裝置模式。

## 後續入口

完整候選建置：

```bash
./tools/build-bananapi-rockchip-aim7-candidate.sh
```

使用唯讀下層快取與專用 OverlayFS：

```bash
./tools/run-bananapi-rockchip-aim7-candidate-isolated-cache.sh
```

完整映像產生後執行唯讀驗證：

```bash
./tools/verify-bananapi-rockchip-aim7-candidate.sh
```

## 正式 L2 與 L3 尚缺證據

- 隔離快取完整映像建置、壓縮串流、分割表、payload 與唯讀根檔案系統驗證。
- AIM7 核心板、AIM7 IO 載板與 ArmSoM AIM7 IO DTS 的逐項原理圖差異表。
- SD、eMMC、不同容量 LPDDR4x、冷啟動、重啟與長時間壓力測試。
- PCIe lane 實際拓撲與 NVMe、GbE、USB、HDMI、DP、DSI、CSI、GPIO、I2C、SPI、UART、PWM 實測。
- GPU、VPU、RGA 與 NPU 的使用者空間驅動版本、硬體路徑、效能及錯誤日誌。
