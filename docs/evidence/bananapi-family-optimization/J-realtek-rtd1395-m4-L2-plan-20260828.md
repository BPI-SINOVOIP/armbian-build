# Banana Pi BPI-M4 RTD1395 L2 內部候選計畫

更新日期：2026-08-28

## 目標

從已推送的乾淨來源提交建立 `bananapim4` 的 Trixie legacy minimal CLI 完整映像，把現有 U-Boot、Linux、1／2 GiB DTB 與 modules 的 L1 元件證據提升為可追溯的 L2 內部軟體候選。本計畫不授權公開發布，也不產生任何實機、GPU、VPU、無線或 40-pin 通過聲明。

## 固定邊界

- BSP：`BPI-M4-bsp` 提交 `25f5b88ec4ba34029f964693dc34028b26e6c67c`。
- Armbian firmware：提交 `f50a2a21bcdb77a562b3976930c5c6b521a1df08`。
- 建置時間：`SOURCE_DATE_EPOCH=1711071187`。
- 固定輸出：`output/images/2026.08/bananapi-realtek-rtd1395-m4-trixie-legacy-cli`。
- 共用快取：`/media/pi/SMCI/armbian/bpi-v26.2.1/cache` 只作唯讀 lower。
- 專用 upper：`.tmp/bananapi-realtek-m4-candidate-cache-overlay`。
- 公開發布、硬體通過與不透明載荷再散布授權一律為 `false`。

## 執行階段

1. 契約閉合：固定 MBR、40 KiB U-Boot、256 MiB FAT、`BPI-BOOT`、`BPI-ROOT`、Realtek vendor boot 目錄與雙 DTB。
2. 來源推送：執行 JSON、Python、Shell、專用測試與全案回歸，推送乾淨建置提交。
3. 正式建置：透過專用 OverlayFS 從該提交重建 U-Boot、Linux、rootfs、IMG 與 XZ。
4. 唯讀驗證：核對 IMG／XZ 同一性、MBR、分割區、U-Boot 偏移、套件、核心設定、RTL8821CU 模組、雙 DTB、`uEnv.txt`、initramfs 與 `bluecore.audio`。
5. 證據閉合：回填 IMG／XZ、來源 tree、validation、驗證清單與最終設定雜湊，中央登錄通過後才提升 L2。
6. 安全回收：確認提交已推送、沒有掛載、開啟檔案、建置程序或容器引用後，只移除專用 OverlayFS upper；正式 IMG 與 XZ 保留。

## 拒絕條件

- 來源工作樹不乾淨、建置與驗證提交不一致，或 validation 雜湊不同。
- 使用非固定輸出、非專用 OverlayFS，或企圖修改共用 cache。
- IMG 與 XZ 不同、壓縮串流破損、分割布局不符，或 U-Boot 不在 40960 bytes。
- `uEnv.txt` 仍使用 `/dev/mmcblk*`、雙 DTB 不完整，或 vendor boot 資產不一致。
- 將 `.wip`、靜態 DT 節點、套件存在或未實機候選誤宣稱為可發布或硬體通過。

## 實機後續

L2 閉合後仍需另行使用 1 GiB 與 2 GiB BPI-M4、UART、SD 與 eMMC 完成冷啟動、網路、USB host／gadget、HDMI、音訊、Wi-Fi、Bluetooth、PCIe、40-pin、重啟、關機與壓力測試。`bluecore.audio`、六個輔助處理器啟動段與內含工具鏈的再散布授權未閉合前，不得對外發布組合映像。

## 執行紀錄

- 來源提交 `5c74ad23df329dd476ab9e97dc43345093d907da` 已推送，525 項全案回歸通過。
- 第一次正式建置在進入 U-Boot 編譯前拒絕；原因是 `run_host_command_logged` 會將含空白的 `sed` 表達式重新交給 shell 解析，原參數未保留引號。
- 修正改用 Bash `${parameter@Q}` 對 `sed` 表達式與路徑完整引號化；建置器仍會二次驗證產生的 `root=LABEL=BPI-ROOT rw rootfstype=ext4 rootwait`。
- 修正後新增實際經過記錄執行器二次 shell 解析的行為測試；M4、W2 元件唯讀驗證與 526 項全案回歸均通過。
- 來源提交 `1a7de4430611c1049c27ebd6f25744820fe0e0c9` 的完整建置成功，產生 2,126,512,128 bytes IMG 與 404,802,508 bytes XZ；獨立守門隨後正確拒絕候選，因為版控輸入核心設定 `8ffa22ff...` 經 Kconfig 與 Armbian 建置參數正規化後，映像內最終設定為 `926ff6a7...`。
- 機器契約改為分別固定輸入設定與最終設定；必要核心選項已在完整雜湊檢查前通過，校準後 527 項全案回歸通過。仍須從更新後的已推送提交重新完整建置，才可建立可重現 L2 證據。
- 校準提交 `156d74a69c69e9b88212892ee50280f7a51a46a6` 的第二次完整建置成功，最終核心設定守門通過；驗證在 U-Boot 固定日期字串停止。元件路徑明確匯出 `SOURCE_DATE_EPOCH`，完整自訂 U-Boot 路徑則只保留未匯出的 shell 變數，使內層 `bash -c` 與 `make` 使用建置當日。
- 共用 Realtek legacy U-Boot 函式改為只在 `SOURCE_DATE_EPOCH` 有值時建立函式區域匯出，並由行為測試實際走過 M4 直接 `make` 與 W2 頂層 `make u-boot` 路徑；內層 shell 可見固定值，函式返回後則恢復原匯出狀態。M4 defconfig 與交叉編譯器路徑也保留二次 shell 解析所需引號。沒有設定固定時間的一般建置行為不變；修正後 527 項全案回歸通過。
- 固定時間修正提交 `19b21c370b5ac0f9253b58da5b2c989b9235c9c9` 已推送；第三次正式重建產生 2,126,512,128 位元組 IMG 與 402,258,144 位元組 XZ。
- 正式 U-Boot 載荷 SHA-256 為 `5e91ddf0140820c1f091ac40d8af0daa180bf1e45b851231269e4df7be3e7003`，與元件兩次重建結果相同，固定時間字串亦符合契約。
- 共用唯讀守門已通過 IMG／XZ 同一性、MBR、FAT／ext4、雙 DTB、最終核心設定、U-Boot 載荷、必要套件、RTL8821CU 模組及受控資產檢查。
- validation、中央狀態與 48 板報告已閉合為 L2，並加入可從版本控制內證據重新核對正式 IMG、XZ、清單、原始提交與 IMG 內 U-Boot 載荷的歷史重驗模式。
- 計畫第 1 至 5 階段完成；第 6 階段在閉合提交推送及歷史重驗通過後執行，只回收 M4 專用 OverlayFS 上層，正式 IMG 與 XZ 保留。
