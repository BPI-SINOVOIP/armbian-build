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
