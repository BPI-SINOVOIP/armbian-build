# Banana Pi C 批 Allwinner 來源審查

日期：2026-08-26

## 結論

C 批十一張板卡橫跨 A20、A31s、R16/A33、H2+、A64、R40 與 A83T，不能把 H3 overlay 或單一 U-Boot 設定套用到全部板卡。第一階段以 `bananapipro` 作為 A20 社群代表板；其餘板卡依 SoC 分組展開。所有板卡目前仍需建立本分支候選與實機證據。

## 執行順序

1. `bananapipro` current：與正式 `bananapi` 共用 A20 核心與 U-Boot 基準，建立首張 C 批 L1／L2。
2. `bananapim1plus` current：完成 A20 同家族差異回歸。
3. `bananapim2berry`、`bananapim2ultra` current：驗證 R40 overlay、SATA、eMMC、Wi-Fi／Bluetooth。
4. `bananapim2zero`、`bananapip2zero` current：驗證 H2+ 無線、USB gadget 與 eMMC 差異。
5. `bananapim64` current：驗證 A64、Crust、eMMC、無線及 HDMI。
6. `bananapim2` current：驗證 A31s 主線功能與 `sun6i-a31s` overlay。
7. `bananapim2magic` current：只使用 A33 相容前綴，先建立沒有 H3 overlay 誤配的基準。
8. `bananapim3` current：先審核板級 U-Boot patch 目錄是否刻意排除共同修補。
9. `bananapi6204` legacy：保留 `.wip`，驗證工控介面與保守 eMMC；不由 M2 Ultra 結果推論通過。

## 已完成的來源修正

- `bananapim2magic` 已設定 `OVERLAY_PREFIX="sun8i-a33"`。主線板卡 DTS 相容 SoC 為 A33；此修正只防止誤用 H3 overlay，不代表已新增 A33 overlay。
- Sunxi `6.18` 與 `7.0` 的 R40 I2C2／I2C3 overlay 已由錯誤的 `allwinner,sun8i-h3` 修正為 `allwinner,sun8i-r40`，四個來源均通過 `dtc` 編譯與回歸測試。

上述修正位於提交 `2ca48b1b9bdf27c48ed138091a731a706f3b1b09`，尚須在對應板卡映像及實機完成驗證。

## 尚待處理的來源風險

### R40

- R40 overlay 已列入 `6.18` 與 `7.0` 的 Makefile，但目前共用 `README.sun8i-h3-overlays`，缺少 R40 專屬接腳、衝突與參數文件。
- M2 Berry、M2 Ultra 與 BPI-6204 雖共用 R40，實際儲存、電源、無線與 I/O 不同，DTB 與實機結果必須分開。

### M3

- `config/boards/bananapim3.csc` 把 `BOOTPATCHDIR` 設為 `u-boot-sunxi/board_bananapim3`。Armbian 只會套用列出的 patch 目錄，因此一般 `u-boot-sunxi` 共同修補不會自動納入。
- 目前板級目錄只有 A83T 校準修補。建置前須比較共同修補是否有 M3 必要修正，不能直接刪除覆寫或盲目合併。

### BPI-6204

- 板卡使用 M2 Ultra U-Boot defconfig，但 Linux 使用專用 DTB；這是可建置起點，不是兩板硬體等同證據。
- 目前只宣告 legacy `6.12`，專用 DTS 將 eMMC 限制為 25 MHz、8-bit，核心另停用 BPI-6204 的 DDR52 能力，顯示現有政策以穩定為先。
- `.wip` 狀態必須保留，直到 SD 偵測、eMMC、Wi-Fi、CAN、UART、RS-485、網路與長時間壓力完成實機驗證。

## 共同驗證門檻

- 每板保存 U-Boot、核心、DTB、overlay、映像與壓縮檔 SHA-256。
- 唯讀檢查映像內板名、DTB、overlay 前綴、核心設定與標準 I/O 工具。
- 實機逐項驗證 SD／eMMC／SATA、Ethernet、USB host／gadget、HDMI、Wi-Fi、Bluetooth、Cedrus、Lima、Crypto、GPIO、I2C、SPI、UART 與 PWM。
- 只有實際具備的介面才列為板卡門檻；裝置節點存在不能代替資料傳輸或壓力測試。

缺少實機時最多標示 L2；`.csc` 或 `.wip` 不因單次建置成功自動升級為正式支援。
