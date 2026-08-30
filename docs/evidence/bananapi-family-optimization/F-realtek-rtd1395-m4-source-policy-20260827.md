# Banana Pi BPI-M4 legacy 候選來源與最佳化政策

更新日期：2026-08-28

## 階段結論

`bananapim4` 是 Realtek RTD1395 板卡，不是 Allwinner H618 的 M4 Berry 或 M4 Zero。本候選已把 vendor BSP、韌體與建置時間固定到精確提交，補上 1 GiB／2 GiB DTB 身分、穩定根檔案系統標籤、MBR 儲存契約及板級介面驗證邊界。

板卡仍保留 `.wip`。目前中央登錄已由元件證據提升為 `L2` 內部軟體候選；正式 IMG 與 XZ 來自已推送乾淨提交，並已完成唯讀內容守門及歷史重驗。L2 只證明軟體映像內容與來源契約閉合，不得宣稱可開機、介面可用、硬體通過或允許公開發布。既有 2026 年 5 月映像只用來校準分割布局，不作本候選證據。

本候選修改 M4 板級設定、M4 專用修補、機器契約、文件、工具及測試。共用 Realtek legacy include 只修正根標籤 `sed` 參數交給記錄執行器時的 shell 引號，不改變 M4 與 W2 預期的根標籤內容。共用唯讀映像守門器新增 `realtek_bpi_uenv` 模式，用來核對 FAT vendor boot 目錄、雙 DTB、根標籤與不封裝 defconfig 的舊 U-Boot 契約。共用 `/media/pi/SMCI/armbian/bpi-v26.2.1/cache` 只作 OverlayFS 的唯讀 lower，不直接修改。

## Realtek 家族邊界

- `bananapim4` 使用 RTD1395 與 `realtek-rtd139x-bpi`，是本候選完整稽核範圍。
- `bananapiw2` 使用 RTD1296 與 `realtek-rtd129x-bpi`，和 M4 共用 `config/sources/families/include/realtek_bpi_legacy_common.inc`，本次只確認共用關係，不引用 W2 元件作 M4 證據。
- `xpressreal-t3` 使用 RTD1619B 與獨立的 `realtek-rtd1619b` 整合，來源為不同的 U-Boot 2024.01 與 Linux 6.6 路徑，不屬 M4 legacy 候選。

共用 legacy include 只有 shell 引號修正，沒有將 M4 的儲存、啟動或周邊假設擴散到 W2；W2 日後仍必須使用自身契約重驗。

## 固定來源

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| Linux 4.9.119 | `https://github.com/BPI-SINOVOIP/BPI-M4-bsp.git` | `25f5b88ec4ba34029f964693dc34028b26e6c67c` |
| U-Boot 2015.07 | `https://github.com/BPI-SINOVOIP/BPI-M4-bsp.git` | `25f5b88ec4ba34029f964693dc34028b26e6c67c` |
| Armbian firmware | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |

Linux 與 U-Boot 位於同一個 vendor 單體倉庫。板檔使用精確 `commit:`，避免 `master` 移動後取得不同內容。BSP 提交時間為 Unix 時間 `1711071187`；隔離建置同時固定 `SOURCE_DATE_EPOCH`、UTC、建置使用者及主機名稱。M4 專用 U-Boot 修補將內嵌版本時間改由此 epoch 產生，避免每次建置只因牆鐘時間而改變雜湊。

Linux 授權入口 `linux-rtk/COPYING` 的 SHA-256 為 `af8067302947c01fd9eee72befa54c7e3ef8a48fecde7fd71277f2290b2bf0f7`。U-Boot 授權入口 `u-boot-rtk/Licenses/README` 的 SHA-256 為 `8ce570d60d1fbc0ab8ea22e31e06bd7c9773953ba58fd7075d4e5690de9a08d0`。這些入口只能證明上游存在授權聲明，不能取代二進位資產的逐項來源與再散布審查。

BSP 內含 `gcc-linaro-7.3.1-2018.05` 工具鏈。建置入口會核對 GCC 大小與 SHA-256，但不把工具鏈封裝進執行期候選；其完整再散布審查尚未完成。

## 預建資產與發布阻擋

第一次元件建置的守門曾拒絕產物，原因是原先把 Makefile 的條件引用誤判成實際連結。重新檢查 `.u-boot.cmd` 與 `u-boot.map` 後，確認下列四個 `.32` 預建靜態庫存在於來源、Makefile 也有條件式規則，但本次 AArch64 連結命令與映射都沒有納入它們。它們不在本次 `u-boot.bin`，但因缺少來源與已確認的再散布授權，若日後散布完整 BSP 來源包仍須先釐清：

| 資產 | 大小 | SHA-256 |
| --- | ---: | --- |
| `u-boot-rtk/static_lib/libefuse.a.32` | 21668 | `d4f2409e059808218fae3d442ec0265d1df42083df24389bfc4dc602183244d3` |
| `u-boot-rtk/static_lib/libsha1_util.a.32` | 8884 | `df48723ffb4442bb63d8c8e44f1af0dd56290a20e7d49aee1df8338f7c51fb4e` |
| `u-boot-rtk/static_lib/libsecurity.a.32` | 22948 | `f20288c80a8c6af5109587b90397259a59888b0313c269e66f5024566887cf27` |
| `u-boot-rtk/static_lib/libkeyset.a.32` | 37244 | `6b68dd3d0f203739271dc2316e50e29c7a42bdfa40b293dc795b907ce4a0d743` |

實際 U-Boot 連結命令納入 `libbootload.o`。此物件由倉庫可見的 `bootload.c` 產生，但又嵌入六個已追到組合語言與 Makefile 規則的 RTD1395 啟動影像：`a_entry.img`、`exc_dispatch.img`、`exc_redirect.img`、`isr_video.img`、`ros_bootvector.img` 與 `v_entry.img`。建置守門會從 `libbootload.o` 抽出各 section，逐一核對原始影像雜湊；然而 BSP 指定的 `/usr/local/rsdk-1.5.5` MIPS 工具鏈沒有被固定或提供，所以本候選沒有重建這六個影像。來源可見不等於完整重建鏈已閉合，也不等於再散布授權已確認。

執行期 `bluecore.audio` 是 4319769 bytes 的 Realtek 音訊 DSP 載荷，SHA-256 為 `774832da0a18c1ba837ff41926502fb8444e83397562c7170c71435416aff4df`；沒有可重建來源與已確認的再散布授權。它會被保存於內部元件證據，因為現有 vendor 啟動配置需要此檔，但禁止把該內部保存解讀為可對外發布。雜湊固定只代表內容可辨識，不代表內容可審計或可合法再散布。

元件候選明確排除原廠預建 `u-boot-bpi-m4.bin`、原廠舊 `uInitrd`、`bluecore.audio.enc.A01` 與未由一般候選連結的 `libobfuseLib.a.32`。完整 Armbian 映像必須自行產生 initramfs，不得把原廠舊檔誤作目前 rootfs 證據。

## 啟動與儲存契約

- U-Boot 使用 `rtd1395_bananapi_defconfig`，整碟載荷位置為 40960 bytes。
- 分割表固定為 MBR／`msdos`，避免 GPT 項目區與 40 KiB 載荷重疊。
- FAT boot 分割區為第 1 區，根檔案系統為第 2 區；`uEnv.txt` 改用 `root=LABEL=BPI-ROOT`，避免綁定 `/dev/mmcblk0p2` 的核心枚舉順序。
- boot 目錄固定為 `/boot/bananapi/bpi-m4/linux`，需包含 `uEnv.txt`、`bluecore.audio`、`uImage`、`uInitrd` 與 1 GiB／2 GiB DTB。
- DT 靜態契約涵蓋 SD、eMMC 與 PCIe；PCIe 節點已從錯誤的 `/pcie@9804E000` 更正為 DTB 實際的 `/pcie@98060000`。節點存在不代表媒體啟動、熱插拔、吞吐或資料完整性已通過實機測試。

## 板級介面與最佳化邊界

M4 專用修補把兩個 DTB 的根節點固定為 `Banana Pi BPI-M4`，相容字串依序為 `bananapi,bpi-m4` 與 `realtek,rtd1395`。兩個 DTB 分別保留 1024 MiB 與 2048 MiB 的記憶體範圍，不能只保存其中一個而宣稱完整支援。

- 網路：DT 有 GMAC 節點，核心設定為 `CONFIG_R8168=y`；尚未驗證實體埠、協商速率、吞吐或長時間穩定性。
- USB：產品資料列出四個 USB 2.0 與一個 Type-C；核心包含 DWC3 dual-role、ConfigFS ECM 與 mass storage，但沒有實體角色切換及供電證據。
- 顯示與多媒體：DT 宣告 HDMI TX 與 Mali-470 GPU 節點，核心有 Realtek HDMI TX；目前沒有證明可用的 Mali 核心驅動、使用者空間 OpenGL ES 堆疊或視訊硬解路徑，不能以 DT 節點存在取代功能測試。
- 無線：產品頁列出 RTL8821 類模組，核心保存 `CONFIG_RTL8821CU=m`；仍須核對實際 BOM、firmware、天線、Wi-Fi 與 Bluetooth 功能。
- 40-pin：靜態契約涵蓋 GPIO、三組 I2C、一組 SPI、PWM 與 UART。`BPI-WiringPi2`、`RPi.GPIO` 與官方頁面只作腳位比對來源，尚未完成電壓、多工、方向、中斷及外接迴路實測。
- 診斷：板級套件清單補入 GPIO、I2C、SPI、MMC、PCIe、USB、網路、顯示、音訊、熱感測、watchdog 及壓力測試工具；套件存在不等於硬體功能成立。

## 受維護核心移植線索

本機既有研究證據顯示較新的 Linux 原始碼已有 `arch/arm64/boot/dts/realtek/rtd1395-bpi-m4.dts`、`bananapi,bpi-m4` 相容字串及 RTD1395 的基礎繫結。來源報告為 `/media/pi/SMCI/bpi/doc/banana-pi-doc-benchmark-20260621/reports/bpi_m4_mainline_dts_baseline_20260623.md`，SHA-256 是 `73fc2b4220d6338ef8e7bfea25c5b4d9a19867f1d669f5184eb959a030206ee4`。這只能證明主線原始碼具有板級描述起點，不能證明目前 vendor 啟動鏈、GPU、VPU、RTL8821CU、音訊 DSP、Type-C、eMMC 或 40-pin 已可用。現階段仍以 Linux 4.9.119 保存舊 BSP 元件；後續主線化應獨立處理啟動鏈與周邊缺口，不能把 DTS 存在誤標為完整支援。

## 建置與驗證流程

靜態來源政策檢查：

```bash
python3 tools/check-bananapi-realtek-m4-source-policy.py \
  config/validation/bananapi-realtek-rtd1395-m4-legacy.json
python3 -m unittest tests.test_bananapi_realtek_m4_candidate
```

隔離元件建置與唯讀驗證：

```bash
./tools/build-bananapi-realtek-m4-components.sh
./tools/verify-bananapi-realtek-m4-components.sh
```

L2 內部候選建置與唯讀驗證：

```bash
./tools/run-bananapi-realtek-m4-candidate-isolated-cache.sh
./tools/verify-bananapi-realtek-m4-candidate.sh
python3 tools/check-bananapi-realtek-m4-source-policy.py \
  config/validation/bananapi-realtek-rtd1395-m4-legacy.json \
  --verify-historical-image
```

L2 入口只接受 `trixie`、`legacy`、minimal CLI 與固定 `SOURCE_DATE_EPOCH=1711071187`，輸出目錄固定為 `output/images/2026.08/bananapi-realtek-rtd1395-m4-trixie-legacy-cli`。建置期間必須使用專用 OverlayFS upper，並禁止公開發布及硬體通過聲明。

建置入口只在可用空間至少 50 GiB 時執行，最多使用 8 個工作。它以 `git clone --shared --no-checkout` 從固定 BSP 物件庫建立專用工作樹，只讀取共用 cache；不清理、不覆寫 cache，也不建立 rootfs 或整碟映像。完整隔離來源與建置樹位於 `.tmp/bananapi-realtek-m4-component`，不屬可攜證據。

本次隔離元件建置已於 `2026-08-27T05:37:35Z` 完成，證據提升為 `L1 元件候選`。可攜元件位於 `output/components/2026.08/bananapi-realtek-rtd1395-m4-legacy`，保存固定時間的 U-Boot、核心 Image、1 GiB／2 GiB DTB、核心設定、517 個模組的壓縮封裝、`uEnv.txt`、內部使用的 `bluecore.audio`、真實 U-Boot 連結命令、來源資產清單、預建輸入正負證據與建置記錄。

U-Boot 在相同固定來源、時間、使用者與主機資訊下連續建置兩次，SHA-256 都是 `5e91ddf0140820c1f091ac40d8af0daa180bf1e45b851231269e4df7be3e7003`。U-Boot 建置記錄沒有警告；Linux 4.9 建置記錄有 230 個警告，包含舊 vendor 程式碼的型別、未使用變數、控制流程及 section mismatch 類別。Linux、DTB 與 modules 本次各只建置一次，其雜湊是產物身分證據，不是雙重建置一致性證明。因此 L1 只代表元件已保存且靜態守門通過，不代表核心可重現性或這些技術債已消除。

正式 L2 已從提交 `19b21c370b5ac0f9253b58da5b2c989b9235c9c9` 建立 rootfs、initramfs、整碟 IMG 與 XZ，並完成雙分割區唯讀掛載、開機資產、最終設定、套件、模組與 U-Boot 載荷檢查。版本控制內證據另可重新核對來源提交、當時的 validation、實檔雜湊、XZ 解壓串流及 IMG 內載荷。完整數值記錄於 `K-realtek-rtd1395-m4-L2-build-20260828.md`。

`bluecore.audio` 與未閉合的輔助處理器重建鏈仍使這份完整映像只限內部稽核，不能直接公開發布。

## 升級與發布門檻

要從內部 L2 升級為可實機採用或對外發布的候選，至少仍須：

1. 釐清四個條件式 U-Boot 預建庫、六個已嵌入啟動影像、`bluecore.audio`、內含工具鏈及外部文件的來源與再散布授權，並固定可重建六個啟動影像的 MIPS 工具鏈。
2. 以實體 M4 及 UART 完成多次冷啟動、1 GiB／2 GiB、SD、eMMC、PCIe、網路、USB host／gadget、HDMI、音訊、Wi-Fi、Bluetooth、GPIO、I2C、SPI、PWM、熱感測、watchdog、重新啟動、關機及長時間壓力測試。
3. 補齊 Mali-470 核心與使用者空間堆疊、視訊解碼 API 及授權證據，再用實際渲染器與解碼統計驗證；在此以前不得宣稱 GPU 或 VPU 硬體加速。
4. 評估 Linux 4.9.119 與 U-Boot 2015.07 的安全維護風險，建立可持續更新或移植到受維護版本的方案。

上述阻擋關閉前，`public_release_allowed`、`hardware_validated` 與 `hardware_claims_allowed` 必須維持 `false`。
