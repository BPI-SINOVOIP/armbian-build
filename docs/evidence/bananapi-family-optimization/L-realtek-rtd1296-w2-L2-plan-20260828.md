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
