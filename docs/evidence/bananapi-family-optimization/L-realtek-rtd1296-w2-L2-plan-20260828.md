# BPI-W2 RTD1296 L2 內部候選計畫

更新日期：2026-08-28

## 目標

從已推送的乾淨來源提交建立 `bananapiw2` 的 Debian Trixie legacy minimal CLI 完整映像，把既有 U-Boot、Linux、DTB 與 518 個核心模組的 L1 元件證據提升為可追溯的 L2 內部軟體候選。本計畫不授權公開發布，也不產生任何 SD、eMMC、SATA、PCIe、網路、顯示、媒體、USB 或 40-pin 實機通過聲明。

## 固定邊界

- BSP：`BPI-W2-bsp` 提交 `6e6aefc35dc50b1b8231cdb03a995d088f29eb21`。
- Armbian firmware：提交 `f50a2a21bcdb77a562b3976930c5c6b521a1df08`。
- 建置時間：`SOURCE_DATE_EPOCH=1571768256`。
- 固定輸出：`output/images/2026.08/bananapi-realtek-rtd1296-w2-trixie-legacy-cli`。
- 共用快取：`/media/pi/SMCI/armbian/bpi-v26.2.1/cache` 只作唯讀 lower。
- 專用 upper：`.tmp/bananapi-realtek-w2-candidate-cache-overlay`。
- 公開發布、硬體通過、工具鏈與不透明載荷再散布授權一律維持 `false`。

## 已知來源與風險

1. U-Boot 2015.07 與 Linux 4.9.119 均為停止維護的 vendor 基線。
2. U-Boot 實際連結四個無來源靜態庫；`bluecore.audio` 也缺少可重建來源及已確認的再散布授權。
3. 原廠 `spirom-bpi-w2.bin` 與舊 `uInitrd` 必須排除，完整映像只使用本次來源建置的 U-Boot 與 Armbian 產生的 initramfs。
4. W2 沒有板載 Wi-Fi；不可因共用 firmware 或外接擴充介面存在而宣稱板載無線功能。
5. 共用 Realtek legacy 的 shell 引號與固定時間修正已由 M4、W2 行為測試覆蓋，但 W2 正式映像仍須以自己的契約獨立重驗。

## 執行階段

1. L2 重建契約：補齊 rootfs、映像、U-Boot 載荷、FAT vendor boot、分割區、最終核心設定與固定來源欄位；不預填不存在的正式映像證據。
2. 工具建立：新增 W2 專用建置器、OverlayFS 執行器、候選驗證器與歷史重驗模式，固定唯一輸出及唯一專用 upper。
3. 校準建置：從已推送提交建立第一份完整映像，取得實際 MBR、分割區大小、最終 Kconfig、映像 DTB、U-Boot 載荷大小與位置；任何不符都由守門拒絕並記錄。
4. 正式重建：校準後先提交並推送精確契約，移除校準輸出與專用 upper，再從新提交乾淨重建 U-Boot、Linux、rootfs、IMG 與 XZ。
5. 唯讀物質驗證：核對 IMG／XZ 同一性、MBR、FAT／ext4、根標籤、vendor boot 資產、DTB、最終核心設定、必要套件、模組及 40 KiB U-Boot 載荷。
6. 證據閉合：回填來源 commit／tree、建置時 validation、來源契約投影、IMG／XZ、候選矩陣、完成狀態與驗證清單雜湊；中央 48 板登錄通過後才提升 L2。
7. 安全回收：證據提交推送且歷史重驗通過後，確認沒有掛載、程序、開啟檔案或容器引用，只移除 W2 專用 OverlayFS；正式 IMG 與 XZ 保留。

## 拒絕條件

- 建置來源不是已推送乾淨提交，或建置與驗證使用不同提交、tree、validation 或來源投影。
- 使用非固定輸出、非 W2 專用 OverlayFS，或企圖修改共用 cache lower。
- IMG 與 XZ 不同、壓縮串流損毀、分割布局不符，或 U-Boot 載荷沒有位於契約偏移。
- vendor boot 目錄、W2 DTB、`uEnv.txt`、initramfs、必要核心選項、套件或模組不完整。
- 封裝原廠 `spirom-bpi-w2.bin`、舊 `uInitrd`，或把無來源靜態庫與 `bluecore.audio` 誤標成已審計、可重建或可公開散布。
- 將 `.wip`、完整映像、DT 節點或套件存在誤宣稱為實機功能通過。

## 實機後續

L2 閉合後仍需使用 BPI-W2、UART、SD、eMMC、SATA、PCIe、乙太網路、HDMI TX、DisplayPort TX、USB host／gadget 與 40-pin 測試治具完成多次冷啟動、資料完整性、吞吐、角色切換、媒體、重啟、關機及長時間壓力測試。四個連結靜態庫、`bluecore.audio` 與工具鏈的再散布授權未閉合前，不得對外發布組合映像。

## 執行紀錄

### 2026-08-28 校準建置

- 來源提交：`5e0776efe5413a5bf2d9b4b1126a4192d4d4d7a7`。
- 建置結果：Debian Trixie legacy minimal CLI 完整映像建立成功，建置器返回碼為 0。
- 校準 IMG：2,088,763,392 bytes，SHA-256 為 `e14404d28ae80da761bf355b022f538de338fc403227fe6585832e63ab23fd95`。
- 校準 XZ：394,040,000 bytes，SHA-256 為 `d91ab06671deb7bdda10b4a5cf385aff43748198a55714374b3ef3b4bd80f615`；串流與解壓後 IMG 同一性通過。
- 最終核心設定：`0bcd9fdd4e4dcbb1dbe5bd2702ad08171e425c8abf1f9e30e05f6fe4301ec6a3`。
- U-Boot：位於映像位移 40,960 bytes，大小 432,240 bytes，SHA-256 為 `d4d425862ded2334d354b421ff2df8cdb965041b3b3b2c903fbeddd29ab23890`。
- MBR：FAT 分割區自 LBA 8192 起、長度 524,288 sectors；ext4 根分割區自 LBA 532480 起。
- 唯讀驗證：來源身分、二進位資產、IMG、XZ、FAT、ext4、DTB、`uEnv.txt`、U-Boot 載荷與核心設定全部通過 L1 校準守門。
- 證據邊界：此產物只供校準；在精確契約提交並推送後，必須刪除校準產物與 W2 專用 upper，再從新提交正式重建，才可閉合 L2 軟體證據。
- 編譯限制：校準日誌含 229 行既有 vendor 警告，其中包含回傳型別、可能未初始化及 section mismatch 類別；不阻擋內部 L2 軟體候選，但必須保留為實機穩定性風險。
- 重現限制：U-Boot 時間已固定，initramfs 與 APT 套件來源仍會隨實際建置時間與套件倉狀態改變；本階段不宣稱整體映像可逐位元重現。
- 守門補強：W2 來源守門器已加入正式 L2 證據形狀、原始提交與 tree、IMG／XZ、MBR、清單、U-Boot 載荷、核心設定及 `--verify-historical-image` 驗證；過渡契約不得執行歷史映像重驗。

### 2026-08-28 正式 L2 重建

- 來源提交：`7882ba85da55ad5a8096321811a8c2ff531b4c01`；來源 tree：`10ccab5ed21a148cb33d3693490d80fdbfc48b38`。
- 建置契約與驗證契約 SHA-256：`77c712d668959ac7aa96f537fae7a31dedfe3e63a1c6fbb667b5923775c0a4b0`。
- 正式 IMG：2,088,763,392 bytes，SHA-256 為 `37d28132a24e0944112097caf66ce714ee589e6b8317351e861a6ff0c85a34fe`。
- 正式 XZ：393,632,040 bytes，SHA-256 為 `ae74b820d3b3e540d79bf8a60d2d92210f1e41090e7c3ef14b28d0504072b116`；XZ 串流與解壓後 IMG 同一性通過。
- W2 DTB SHA-256：`e2f0d51977310ecd06a8b72088a3ee3fbcec439b850ceacd9887c9b557d1c420`；最終核心設定 SHA-256：`0bcd9fdd4e4dcbb1dbe5bd2702ad08171e425c8abf1f9e30e05f6fe4301ec6a3`。
- U-Boot 位於映像位移 40,960 bytes，大小 432,240 bytes，SHA-256 為 `d4d425862ded2334d354b421ff2df8cdb965041b3b3b2c903fbeddd29ab23890`。
- 候選矩陣、完成狀態、驗證清單、U-Boot 載荷清單與最終核心設定清單均已綁定至正式來源提交；唯讀驗證完成時間為 `2026-08-28T01:26:39Z`。
- 校準 IMG／XZ 與校準專用上層共回收 5,311,117,507 bytes；正式 IMG／XZ 保留，正式專用上層須等本節證據提交、推送及歷史重驗通過後才可移除。
- 結論只提升為 L2 內部軟體候選。沒有執行實體板開機或周邊測試，也沒有解除不透明載荷、工具鏈及文件的再散布限制。
