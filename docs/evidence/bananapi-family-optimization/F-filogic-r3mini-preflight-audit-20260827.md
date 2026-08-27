# Banana Pi R3 Mini 完整映像預檢稽核

## 結論

Banana Pi R3 Mini 目前維持 L1 元件候選。固定時間戳、OverlayFS、來源競態、狀態廢止、實檔 L2 政策、分割區類型、root 標籤／檔案系統與 eMMC 冷啟動邊界已閉合到工具與回歸測試；但本次禁止完整映像建置，因此精確根分割區大小、映像 DTB 與最終核心／U-Boot 設定雜湊仍須由首次隔離預檢產生，不能直接提升為內部 L2。

本稽核只讀取設定、工具、測試與既有元件證據；沒有執行映像建置、寫入 eMMC、修改板卡或宣稱實機功能通過。

## 執行前條件

- 受控入口：`tools/run-bananapi-filogic-r3mini-candidate-isolated-cache.sh`。
- 輸出目錄：`output/images/2026.08/bananapi-filogic-mt7986-r3mini-emmc-trixie-current-cli/`。
- 必須等待其他 `compile.sh build` 完成及 OverlayFS 正常卸載。
- 現有共用建置器預設要求至少 80 GiB 可用空間；預檢前應保留 100 GiB 以上，避免映像與隔離快取上層耗盡磁碟。
- 只能使用 R3 Mini 專用 OverlayFS 上層，共用 `/media/pi/SMCI/armbian/bpi-v26.2.1/cache` 維持唯讀下層。

## 原始缺口與目前狀態

### 狀態機可接受假 L2：已修正

來源契約檢查與物質證據檢查已分離。前者不依賴舊 output；後者會重新解析真實 IMG，不接受重複字元製造的提交、雜湊、人工 manifest 或過小假映像，並要求：

- 來源提交與驗證器提交相同。
- 建置與驗證所用 validation SHA-256 相同。
- `CANDIDATES.tsv`、U-Boot 載荷與最終設定清單雜湊。
- IMG、XZ 的路徑、大小及 SHA-256。
- MBR、GPT 主／備份結構與 CRC、五個分割區大小與類型。
- BL2／FIP 的實際映像偏移，以及 rootfs、DTB、最終 config、必要套件與韌體來源。
- 唯讀內容驗證成功，且 `hardware_tested=false`。

第一次 L1 預檢只能校準契約，不得直接以改標籤方式升級。

### 韌體來源解析未閉合：已修正

Armbian firmware、MT76 與 Linux firmware 已固定 source、ref 與 commit。建置日誌與映像內 `firmware-source-contract.tsv` 必須閉合相同來源；`SOURCE_DATE_EPOCH=1787793187` 同時進入參數雜湊與 metadata，且不可由環境變數覆寫：

- validation 必須有精確 `firmware_ref`。
- validation 必須設定 `verify_firmware_source_resolution=true`。
- 板檔必須固定 `ARMBIAN_FIRMWARE_GIT_SOURCE_BOARD` 與 ref。
- 建置日誌必須證明實際解析到相同來源與提交。

### 完整映像契約不足：保留首次預檢校準門檻

- 既有 `bl2.img`、GPT 與 FIP 雜湊來自獨立元件建置，不能直接冒充完整映像證據。
- 缺少 `final_kernel_config_sha256` 與 `final_uboot_config_sha256`。
- 根分割區仍以 `5:*:32768:*` 表示，缺少實際結束位置或大小。
- 缺少完整映像 DTB 與 U-Boot payload 的實際雜湊。
- 根檔案系統類型、標籤及核心命令列解析尚未形成精確契約。

## 預檢後必須回填

1. `source_date_epoch`、`firmware_ref` 與 `verify_firmware_source_resolution`。
2. 最終核心設定、U-Boot 設定、映像 DTB 與 U-Boot payload SHA-256。
3. 五個 GPT 分割區的精確起點、大小、標籤及檔案系統。
4. `root_partition_start_sector=32768` 與根檔案系統解析契約。
5. 重新計算 `source_contract_projection_sha256`；動態映像證據不得寫回 validation。

首次 L1 預檢會輸出機器可讀的 `R3MINI_CALIBRATION.json`。回填後先提交、清除首次 output，再由同一提交正式重建及唯讀驗證，才可形成內部 L2。

## eMMC user area 邊界

| 區域 | sector | 位元組範圍 |
| --- | ---: | ---: |
| `bl2` | 34–8191 | 17,408–4,194,303 |
| `ubootenv` | 8192–9215 | 4,194,304–4,718,591 |
| `factory` | 9216–13311 | 4,718,592–6,815,743 |
| `fip` | 13312–21503 | 6,815,744–11,010,047 |
| 根檔案系統 | 32768–映像尾端 | 16,777,216 起 |

映像內 BL2 位於 byte `17408`，FIP 位於 byte `6815744`。這只能證明 user-area 映像內部配置，不代表空白 eMMC 可冷啟動。

## boot0 與 factory 限制

冷啟動另需處理 `/dev/mmcblk0boot0`：

- BL2 寫入偏移為 `0`。
- 寫入前必須受控處理 `force_ro`。
- 必須執行 `mmc bootpart enable 1 1 /dev/mmcblk0`。
- 上述流程尚未實機驗證。

完整 IMG 寫入已配置的 `/dev/mmcblk0` 會覆蓋 `factory` 分割區。建立安裝工具前，必須先完成 factory 校準資料的備份、雜湊核對、寫入後驗證及失敗還原流程。因此持續保持 `automatic_emmc_install_authorized=false`，user-area IMG 不得稱為空白 eMMC 的完整冷啟動安裝映像。

## 本次守門結果

本文件的數量紀錄以最終提交驗證結果為準。通過工具、政策與負向回歸只證明目前 L1 流程可執行，不補足上述 L2 與實機證據缺口。
