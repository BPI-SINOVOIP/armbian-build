# Banana Pi B 批正式板卡來源審查

日期：2026-08-26

## 結論

B 批六張正式設定板卡的建置鏈並不相同，不能以單一「current」命令推論全部可重現。`bananapi` 與 `bananapim2plus` 採成熟 Sunxi 主線流程，可先建立候選；`bananapif3` 可建置但須記錄多階段開機鏈；`bananapim7` 與 `bananapim5pro` 依賴 Rockchip 專有 DDR／BL31 與目前未固定提交的 `rkbin`；`bpi-ai2n` 只宣告 legacy，並依賴預建封裝工具與專有執行期二進位。

所有板卡目前仍以既有證據等級為準。來源審查與主機建置不能取代實機開機、介面或加速功能驗證。

## 逐板判定

| 板卡 | 建議分支 | 來源判定 | 下一步 |
| --- | --- | --- | --- |
| `bananapi` | current | Sunxi U-Boot 固定 `v2024.01`；核心為 `6.18`；板級 DRAM 384 MHz 與停用 U-Boot DE2 有明確設定 | 作為 B 批第一張 A20 候選，完成 L1／L2 |
| `bananapim2plus` | current | 與一般 H3 Sunxi 流程一致，具有 analog-codec 預設 overlay 與 USB gadget serial 設定 | A20 通過後作為 H3 代表板 |
| `bananapif3` | current | OpenSBI 與 U-Boot 固定 `k1-bl-v2.2.9-release` tag；current 核心追蹤 `linux-6.18.y` branch；映像包含 bootinfo、FSBL、OpenSBI 與 U-Boot ITB | 先固定本次實際核心提交及 `esos.elf` 雜湊，再建立候選 |
| `bananapim7` | current | 繼承 ArmSoM Sige7；U-Boot 追蹤 Radxa branch；`rkbin` 預設追蹤 `master`；板設定使用較舊 DDR v1.11／BL31 v1.38，而家族預設已更新 | 先確認舊 blob 是否為板級必要條件並固定 `rkbin` 提交 |
| `bananapim5pro` | edge | 板卡只宣告 edge、vendor；繼承 Sige5 的 RK3576 `spl-blobs`；需 DDR、BL31、SPL、boost、usbplug 與 x86 封裝工具 | 先固定 `rkbin` 與 Radxa U-Boot 提交，edge 建立主線基準後再做 vendor |
| `bpi-ai2n` | legacy | 只支援 `6.1` legacy；核心、TF-A、U-Boot 均追蹤 vendor branch；封裝使用 x86-64 `bptool`／`fiptool`，映像加入 OpenCV、Codec 與 Flash Writer 二進位 | 固定三個來源提交與全部預建資產雜湊，再建立 legacy 候選 |

## 已確認來源缺口

### F3

- `config/boards/bananapif3.conf` 同時設定 `BOARD_VENDOR="sinovoip"` 與 `BOARD_VENDOR="spacemit"`，實際以後者為準；發布品牌與套件來源必須先確認。
- 板設定沒有 `KERNEL_TEST_TARGET`，不能由宣告自動判定代表測試分支。
- `config/sources/families/spacemit.conf` 的 current 核心使用可變 `linux-6.18.y` branch；每次建置必須保存實際提交。
- `packages/blobs/riscv64/spacemit/esos.elf` 為受版本控制二進位，須保存授權文件與 SHA-256；存在檔案不代表可驗證其內部行為。

### M7 與 M5 Pro

- `extensions/rkbin-tools.sh` 預設抓取 `branch:master`，無法只由 Armbian 提交重建相同 DDR／BL31。
- `config/boards/armsom-sige7.csc` 明確覆寫 RK3588 DDR v1.11 與 BL31 v1.38；`config/sources/families/include/rockchip64_common.inc` 的家族預設已更新為 DDR v1.20／BL31 v1.48。未經實機驗證不能直接升級或移除覆寫。
- `config/sources/families/rk35xx.conf` 與 `rockchip-rk3588.conf` 的 Radxa U-Boot 使用可變 branch；候選必須記錄實際提交與所有 blob 雜湊。
- 專有 DDR、BL31 或封裝工具同一性只證明輸入相同，不等於冷啟動、記憶體穩定、SPI、eMMC 或量產通過。

### AI2N

- `config/boards/bpi-ai2n.conf` 只宣告 legacy，不存在可合理執行的 current 候選。
- `config/sources/families/renesas-rzv2n-bpi.conf` 的 Linux、TF-A 與 U-Boot 均使用 vendor branch，需固定實際提交。
- `packages/blobs/bpi-renesas/tools/bptool` 與 `fiptool` 是 x86-64 預建執行檔；OpenCV、Codec 與 Flash Writer 也是預建資產。必須保存檔案雜湊、執行主機架構與散布授權。

## 執行順序

1. `bananapi` current：建立 A20 Trixie CLI 及 L2 守門。
2. `bananapim2plus` current：建立 H3 代表基準，檢查 Wi-Fi、audio overlay 與 USB gadget 設定。
3. `bananapim7` current：先固定並記錄 Rockchip blob 與來源提交，再建置。
4. `bananapif3` current：保存完整多階段開機產物與來源雜湊。
5. `bananapim5pro` edge，之後才評估 vendor。
6. `bpi-ai2n` legacy：完成預建資產守門後建置，不建立虛假的 current 結果。

## 實機最低門檻

- A20／H3：UART 冷啟、SD、SATA／eMMC、GBE、USB、HDMI、Lima、Cedrus、Crypto 與實體 I/O。
- RK3588／RK3576：多容量 DRAM 冷啟與壓力、eMMC、NVMe、PCIe、雙網路、USB、HDMI／DP、GPU、VPU、NPU 與 SPI 開機。
- F3：SD／eMMC／SPI-NOR 開機鏈、雙 GBE、USB 3、顯示、無線及 ESOS／OpenSBI 交接。
- AI2N：SD／eMMC／SPI 開機、DRP-AI、相機、Codec、顯示及預建韌體版本同一性。

缺少上述實機證據時最高只能標示 L2。
