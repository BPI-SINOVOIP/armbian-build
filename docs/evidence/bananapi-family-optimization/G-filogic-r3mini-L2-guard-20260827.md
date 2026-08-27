# Banana Pi BPI-R3 Mini 內部 L2 候選守門補強紀錄

更新日期：2026-08-27

## 結論

本階段只補強完整映像候選工具、政策、測試與文件，沒有執行完整映像建置、沒有寫入任何媒體，也沒有實機測試。R3 Mini 仍是 `L1 元件候選`；工具已準備好讓後續固定提交的首次隔離預檢產生可信校準資料，但不得在校準與正式重建完成前標示為 L2。

## 已閉合項目

- 固定 `SOURCE_DATE_EPOCH=1787793187`，納入 `compile.sh` 參數、建置參數 SHA-256 與 `artifact.metadata.txt`，拒絕外部覆寫。
- R3 Mini 建置封裝只接受專用 OverlayFS runner 的內部解鎖，並強制 `REQUIRE_ISOLATED_CACHE=yes`。
- 共用建置器在建置返回及候選矩陣完成前核對 HEAD、tree 與乾淨工作樹，拒絕建置期間來源漂移。
- L1 與 L2 都強制候選來源提交等於驗證器提交，建置與驗證 validation SHA-256 相同。
- 驗證入口在前置政策、來源或內容檢查失敗時原子寫入 `failed`，不沿用舊的 `complete` 狀態。
- L2 固定啟用 XZ 完整性與解壓串流同一性，並以唯讀 loop 裝置檢查完整映像內容。
- GPT 類型固定為三個 Linux filesystem、一個 EFI System Partition 與一個 Linux root；根分割區固定由 sector 32768 開始，標籤 `armbi_root`、檔案系統 `ext4`。
- BL2、FIP 與 GPT 固定來源、精確大小、SHA-256、映像偏移及套件內容；最終核心與 U-Boot 設定須由映像內套件產生清單。
- 政策檢查器實際讀取 IMG、XZ、矩陣、metadata、建置／驗證狀態與證據清單，不接受只有格式正確的虛構值。

## eMMC 證據語意

R3 Mini 的一般 IMG 是 eMMC user-area 映像。它包含 GPT、user-area BL2 複本、FIP 與根檔案系統，可用於寫入已具備必要 boot region 的受控媒體；這不等於空白 eMMC 冷啟動安裝器。

空白 eMMC 冷啟動另需受控處理 `/dev/mmcblk0boot0` 的 `force_ro`、在偏移 0 寫入 BL2，並執行 `mmc bootpart enable 1 1 /dev/mmcblk0`。這個流程尚未實機驗證，也沒有自動安裝授權。user-area IMG 還會涵蓋 `factory` 分割區，任何安裝工具都必須先具備校準資料備份、核對與失敗還原流程。

因此即使後續取得內部 L2，仍只代表固定來源完整映像通過軟體身分、XZ 與唯讀內容驗證；不代表空白 eMMC 可冷啟動、不代表硬體功能通過，也不代表可以公開發布。

## 下一次受控預檢

只能從乾淨且固定的提交執行：

```bash
./tools/run-bananapi-filogic-r3mini-candidate-isolated-cache.sh
```

首次預檢通過後，先保存精確根分割區名稱與 sector 數、映像 DTB、最終核心／U-Boot 設定及全部狀態與清單雜湊。接著在新的固定提交中回填契約，再從該提交正式重建並以 L2 模式驗證；不得直接修改層級標籤沿用預檢產物。

## 剩餘限制

- 本階段沒有完整 IMG／XZ，因此沒有 L2 映像雜湊。
- 根分割區最終 sector 數、映像 DTB 與最終 config 雜湊尚待首次預檢。
- ATF 的 MT7986 `dram.o` 再散布範圍仍未釐清，公開發布持續阻擋。
- eMMC boot0、冷啟動、斷電重啟、網路、Wi-Fi、PCIe、USB 與 GPIO 等皆沒有實機證據。
