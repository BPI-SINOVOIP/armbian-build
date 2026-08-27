# Banana Pi BPI-R3 Mini 內部 L2 候選守門補強紀錄

更新日期：2026-08-27

## 結論

本階段只補強完整映像候選工具、政策、測試與文件，沒有執行完整映像建置、沒有寫入任何媒體，也沒有實機測試。R3 Mini 仍是 `L1 元件候選`；工具已準備好讓後續固定提交的首次隔離預檢產生可信校準資料，但不得在校準與正式重建完成前標示為 L2。

## 已閉合項目

- 固定 `SOURCE_DATE_EPOCH=1787793187`，納入 `compile.sh` 參數、建置參數 SHA-256 與 `artifact.metadata.txt`，拒絕外部覆寫。
- 來源契約與物質證據已分離；validation 不再內嵌會自我參照的 `image_build_evidence`。L2 來源契約可在沒有舊 output 時預檢與重建，正式 L2 則必須由該次 IMG 重新產生物質證據。
- `source_contract_projection_sha256` 只排除元件／映像等動態證據；分割區、DTB、config、韌體來源或其他未來契約一旦改動，舊建置與驗證證據立即失效。
- R3 Mini 建置封裝只接受專用 OverlayFS runner 的內部解鎖，並固定 lowerdir、upperdir、workdir 與 mountpoint；`CACHE_LOWER` 等身分變數不得覆寫，建置器會核對實際掛載參數。
- 共用建置器在建置返回及候選矩陣完成前核對 HEAD、tree 與乾淨工作樹，拒絕建置期間來源漂移。
- 共用驗證器在成功狀態原子發布前，再次核對 HEAD、tree、乾淨工作樹與 validation SHA-256；R3 Mini 在第二次物質檢查後還會再核對一次。
- L1 與 L2 都強制候選來源提交等於驗證器提交，建置與驗證 validation SHA-256、來源契約投影及韌體來源集合相同。
- 驗證入口在前置政策、來源或內容檢查失敗時原子寫入 `failed`，不沿用舊的 `complete` 狀態。
- L2 固定啟用單一 XZ 串流完整性與解壓同一性；共用驗證後，R3 Mini 專用物質檢查器會再次建立唯讀 loop 並掛載 rootfs，不接受人工清單代替 IMG。
- GPT 類型固定為三個 Linux filesystem、一個 EFI System Partition 與一個 Linux root；根分割區固定由 sector 32768 開始，標籤 `armbi_root`、檔案系統 `ext4`。
- BL2、FIP 與 GPT 固定來源、精確大小、SHA-256、映像偏移及套件內容；最終核心與 U-Boot 設定必須直接取自唯讀 rootfs。
- MT76 與 Linux firmware 的 source、ref、commit、執行期日誌及映像內來源契約檔均受守門；對應韌體與授權檔仍須逐檔核對 SHA-256。
- `R3MINI_CALIBRATION.json` 會記錄五個實際分割區、rootfs、DTB、最終 config、載荷、必要套件與受控韌體。L1 清單只供回填，只有正式重建產生的 `mode=formal` 清單可閉合 L2。
- 負向回歸明確拒絕約 1.5 KiB 假 IMG、人工 manifest、OverlayFS 路徑覆寫、來源投影漂移與舊成功狀態。

## eMMC 證據語意

R3 Mini 的一般 IMG 是 eMMC user-area 映像。它包含 GPT、user-area BL2 複本、FIP 與根檔案系統，可用於寫入已具備必要 boot region 的受控媒體；這不等於空白 eMMC 冷啟動安裝器。

空白 eMMC 冷啟動另需受控處理 `/dev/mmcblk0boot0` 的 `force_ro`、在偏移 0 寫入 BL2，並執行 `mmc bootpart enable 1 1 /dev/mmcblk0`。這個流程尚未實機驗證，也沒有自動安裝授權。user-area IMG 還會涵蓋 `factory` 分割區，任何安裝工具都必須先具備校準資料備份、核對與失敗還原流程。

因此即使後續取得內部 L2，仍只代表固定來源完整映像通過軟體身分、XZ 與唯讀內容驗證；不代表空白 eMMC 可冷啟動、不代表硬體功能通過，也不代表可以公開發布。

## 下一次受控預檢

只能從乾淨且固定的提交執行：

```bash
./tools/run-bananapi-filogic-r3mini-candidate-isolated-cache.sh
```

首次預檢通過後，從 `R3MINI_CALIBRATION.json` 回填精確根分割區名稱與 sector 數、映像 DTB、最終核心／U-Boot 設定。接著更新來源契約投影、提交新的固定提交、清除首次預檢 output，再從該提交正式重建並以 L2 模式驗證；不得直接修改層級標籤或沿用預檢產物。

## 剩餘限制

- 本階段沒有完整 IMG／XZ，因此沒有 L2 映像雜湊。
- 根分割區最終 sector 數、映像 DTB 與最終 config 雜湊尚待首次預檢。
- ATF 的 MT7986 `dram.o` 再散布範圍仍未釐清，公開發布持續阻擋。
- eMMC boot0、冷啟動、斷電重啟、網路、Wi-Fi、PCIe、USB 與 GPIO 等皆沒有實機證據。
