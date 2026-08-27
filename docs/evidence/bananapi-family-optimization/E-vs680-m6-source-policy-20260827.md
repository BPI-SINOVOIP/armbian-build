# Banana Pi M6 固定來源、授權與候選政策

## 結論

本候選建立可追溯的 VS680 legacy 軟體路徑，並已完成固定來源的 U-Boot 與 Linux 元件編譯；沒有建立完整 rootfs 映像，也不產生實機支援聲明。現階段證據等級為 L1；必須再完成完整映像唯讀驗證及實機啟動，才可依序評估 L2 與 L3。

板檔維持 `config/boards/bananapim6.wip`，`public_release_allowed=false`、`hardware_claims_allowed=false`。這些限制不是保守標籤，而是由不透明啟動載荷、逐檔授權缺口、舊版核心與未完成實機驗證共同決定。

## 板卡與原理圖身分

- 官方板卡頁：`https://docs.banana-pi.org/en/BPI-M6/BananaPi_BPI-M6`
- 官方公開來源指向 `pi-u-boot` 的 `v2019.10-vs680-hdmi-rx` 與 `pi-linux` 的 `pi-5.4-vs680-hdmi-rx`。
- 官方頁引用的歷史 Armbian 提交：`9163a04ca984461bec2516e9be0acd8a990863b9`。
- 本機官方頁快照：`downloads/bpi-m6-exact-mainline-status-p357-20260623/BPI-DOC-0015_Banana-Pi-BPI-M6.html`；SHA-256：`d8bbf75a07d7acff299f48b42bcc772436de58b8e3e21746cd2a6c9d3a15cba0`。相對路徑的基準目錄為 `/media/pi/SMCI/bpi/doc/banana-pi-doc-benchmark-20260621`。
- 本機原理圖：`/media/pi/SMCI/bpi/doc/banana-pi-doc-benchmark-20260621/downloads/m6-dxf-schematic-p224-20260622/BPI-M6-V11-SCH-Reduce.pdf`。
- 原理圖版本：BPI-M6 V1.1；SHA-256：`a496414df256b834648f9dda141ca4ec24bfd151e5915572904be0dbbbc46f53`。
- 原理圖與官方頁均識別 Synaptics VS680，因此本候選沒有借用其他 SoC 或其他 Banana Pi 板卡身分。

本機另有三組稽核證據，均以 `/media/pi/SMCI/bpi/doc/banana-pi-doc-benchmark-20260621` 為基準：

| 證據 | SHA-256 | 使用邊界 |
| --- | --- | --- |
| `reports/bpi_m6_exact_mainline_status_baseline_20260623.md` | `d334e351d2754427fc67964080b5ee6f1b03df20511a3dc2d219db2924e8b881` | 證明受查 Linux 6.x 樹沒有精確 M6 DTS |
| `reports/bpi_m6_residual_gap_audit_20260623.md` | `e53c34ef2b6491683969883449d7f7950c001cb0fafeee3e50a0dc2c6adc7468` | 記錄 BSP、實機與 Yocto 或 Buildroot 證據缺口 |
| `downloads/bpi-m6-residual-gap-audit-p423-20260623/local_bananapim6_build.log` | `5dc1791a5ad4155477314b94459e4b805cc1ae5b8ff2a23c8d7a8c5904d40c9d` | 只證明舊候選曾建置，不替代本提交元件編譯 |

## 固定來源

| 元件 | 來源 | 原始分支 | 固定提交 |
| --- | --- | --- | --- |
| Linux 5.4 | `https://github.com/BPI-SINOVOIP/pi-linux.git` | `pi-5.4-vs680-hdmi-rx` | `3229415e99a06edc972948c0a856cbcf7de7ce55` |
| U-Boot 2019.10 | `https://github.com/BPI-SINOVOIP/pi-u-boot.git` | `v2019.10-vs680-hdmi-rx` | `ccca1c75bb6d06470b8a3f6104068b43763ee468` |
| Armbian firmware | `https://github.com/armbian/firmware` | 不採用可移動分支 | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |

`SOURCE_DATE_EPOCH` 固定為 `1717001894`，即固定 U-Boot 提交的時間戳。板檔在 family 設定載入後重新指定所有來源，避免 `vs680.conf` 內的舊可移動分支悄悄改變輸入。

## 板級身分與必要繼承

Linux 原有 M6 檔名，但根節點仍顯示 `Synaptics VS680 EVK`。本候選改為 `model = "Banana Pi M6"`，並把 `sinovoip,bananapi-m6` 放在 compatible 第一順位。

Linux 仍保留 `syna,vs680-evk` 與 `syna,vs680`。U-Boot 仍保留 `Synaptics,vs680` 與 `Synaptics,asserial`。這些字串屬舊 vendor BSP 的相容性契約；目前沒有足夠實機與原始設計證據證明可移除，因此本次不以純名稱清理冒險改變啟動行為。機器契約會同時檢查精確 M6 身分與受控繼承清單。

U-Boot 採用 `vs680_oemboot_c05_defconfig`。來源樹具有專屬 `vs680-bananapi-m6.dts`，但 `C05` 與公開 BPI-M6 V1.1 原理圖的完整拓撲等同性沒有獨立文件；故 C05 只視為既有 vendor 啟動設定，不視為完整硬體證明。

官方頁引用的歷史 Armbian 提交原本使用 `vs680_spi_boot_defconfig`，且 Linux、U-Boot 都是可移動分支。現有本地 WIP 最早在 `4fdb12abbaf01fca8d126f47fc1c0e23d88d6c53` 引入，改用 C05 OEM 設定及精確 M6 DTB，卻沒有補上 donor 拓撲說明。本候選移除可移動分支與 Linux 根節點的錯誤 EVK 顯示身分；C05 defconfig、Linux 的 EVK fallback compatible 與 U-Boot 舊 compatible 則列為必要但未完全證實的繼承，不把它們當成板級證明。

公開來源沒有提供可對應這條 M6 啟動鏈的獨立 ATF 原始碼或建置流程。板檔明確設定 `ATF_COMPILE=no`，啟動所需安全世界能力由下述不透明載荷供應；因此本任務只能編譯 U-Boot 與 Linux，不能把未編譯 ATF 說成已驗證元件。

## 啟動鏈

1. VS680 ROM 讀取映像位移 `512` 位元組起始的 `bpi-m6-tzk-4MB.bin`。
2. TZK 內部內容不透明，外觀含 CRC 不完整的非標準 GPT 資料；不得獨立修復或重建。
3. U-Boot `u-boot.bin` 寫入映像位移 `2097152` 位元組。
4. U-Boot 本身還嵌入 `arch/arm/mach-synaptics/sm.bin`，因 `CONFIG_SYNA_INCLUDE_SM_FW=y` 而進入最終二進位。
5. 第一分割區從 sector `204800` 開始，為 FAT `BPI-BOOT`；第二分割區為 `BPI-ROOT`。
6. `boot-vs680.cmd` 從個別 boot 分割區載入 `Image`、`uInitrd` 與 `synaptics/vs680-a0-bananapi-m6.dtb`。

TZK 從位移 `512` 延伸至 `4194304`，所以後寫入的 U-Boot 會受控覆蓋 TZK 內自位移 `2097152` 起始的一段。這不是兩個互不重疊的 payload。唯讀驗證必須依寫入順序分別比較 TZK 前段、完整 U-Boot 與 U-Boot 結尾後的 TZK 尾段；直接拿完整 TZK 對映像連續區域做比較會得到錯誤結論。

啟動參數仍含 vendor 路徑的 `tz_enable`、`vppta`、固定 `chipid=43111a82aee08964` 與固定 CMA 區間。沒有可證明這些參數可安全移除的文件或實機對照，所以本候選先保留並把它們列為 blocker；不得把固定 `chipid` 解讀為每片硬體均已正確識別。

## 二進位與授權

| 項目 | 大小 | SHA-256 | 判定 |
| --- | ---: | --- | --- |
| `bpi-m6-tzk-4MB.bin` | 4193792 | `175e9b9313dffb70a97852ae21d855d3472916cc2af28f678ebcddc44828e411` | 無原始碼、無重建流程、無逐檔再散布授權 |
| U-Boot `sm.bin` | 27004 | `3896305340cfdc6716861ddac19832b4087f201d6f3a95a0e1fe9f884f6ef2a4` | 嵌入 U-Boot，但未找到自身授權或原始碼 |

U-Boot 樹含 GPL-2.0 授權文件，Linux 樹依 GPL-2.0 發布；這不會自動替個別預編譯載荷補上授權。Armbian firmware 也是逐檔混合授權集合，本候選雖固定提交，仍未完成 M6 實際安裝檔案的逐項授權盤點。

因此目前只能在已有權利的內部驗證環境使用候選。不得因原始碼倉庫可公開下載，就推論所有組合映像均可公開再散布。

## 核心與功能邊界

固定核心為 vendor Linux `5.4.195`。精確 BPI-M6 DTS 不存在於已稽核的 Linux 6.x 主線樹，因此本候選不是主線支援。

核心設定含 VS680 GPU、VPU、VPP、NPU、HDMI RX、PCIe、USB、MMC、GPIO、I2C、SPI 與網路驅動；DTS 也有相應節點。這些只證明原始碼與靜態設定存在。Imagination GPU、SyNAP NPU 及多媒體路徑仍需要相符的專有使用者空間、韌體與授權，不能由核心選項直接推論硬體加速可用。

診斷套件只用於後續 GPIO、I2C、SPI、PCIe、USB、影像、音訊與網路驗證，不代表 40-pin 或加速器已通過。

## 可重跑守門

### 內部 L2 守門準備

本次補齊的是未來建立內部 L2 完整映像所需的機器守門，沒有執行完整映像建置，也沒有把 M6 升為 L2。中央盤點與 validation 仍維持 `L1 元件候選`；只有在相同來源提交完成 IMG、XZ 串流與唯讀內容驗證後，才能另行登錄 L2 證據。

新增的政策狀態機會拒絕只改候選標籤的假 L2，並核對下列條件：

- Linux、U-Boot 與 Armbian firmware 都必須解析到已記錄的精確提交。
- 建置來源提交必須等於驗證器提交；建置與驗證使用的 validation SHA-256 必須相同。
- `COMPLETION_STATUS.json` 必須綁定來源提交、來源樹、validation 與 `CANDIDATES.tsv` 雜湊。
- L2 必須驗證 XZ 串流與 IMG 同一性，失敗時覆寫舊成功狀態。
- 最終核心設定 SHA-256 固定為 `b67480db7854ea797a1813102b2ef1c7a1312c9291797912612368821b058786`。
- 最終 U-Boot 設定 SHA-256 固定為 `f31af0f1449901eb3834fd17e9c8c69034bd50b126a29108168683ba6b38c1f6`。

映像契約固定為 DOS/MBR 雙分割區：第一分割區 `1:*:204800:524288`、類型 `ea`、標籤 `BPI-BOOT`；第二分割區 `2:*:729088:*`、類型 `83`、標籤 `BPI-ROOT`。驗證器以唯讀 loop 與唯讀掛載檢查兩個分割區，要求 `armbianEnv.txt` 的 `fdtfile` 指向 M6 DTB，且 `rootdev=UUID=...` 唯一對應第二分割區。`boot.scr` 由 `dumpimage` 抽取後，必須與 `config/bootscripts/boot-vs680.cmd` 內容相同。

受控重疊不再以兩個完整 payload 分別比對。驗證器依 `payload_write_order` 檢查 TZK 前段、完整 `u-boot.bin` 與 U-Boot 結尾後的 TZK 尾段，並同時核對套件 MD5、精確大小、SHA-256 及產生 `UBOOT_PAYLOAD_EVIDENCE.tsv`。既有受控元件套件重新量得 TZK 為 `4193792` 位元組、`u-boot.bin` 為 `616575` 位元組；兩者的 SHA-256 均與 validation 相符。核心與 U-Boot 最終設定則寫入 `FINAL_CONFIG_EVIDENCE.tsv`。

完整映像建置入口只接受 M6 專用 OverlayFS runner，固定 `SOURCE_DATE_EPOCH=1717001894`，且可用空間下限不得低於 40 GiB。本次沒有執行該入口，因此沒有新增或修改 `output` 產物。

### 元件建置證據

2026-08-27 已在獨立 OverlayFS 上層完成 U-Boot 2019.10、Linux 5.4.195 image、DTB、headers 與 libc-dev 元件封裝，完整 IMG 數量為 0。元件來源提交為 `b6339cf4a2135e3ad75992f7574889d5ff34a249`；清單 SHA-256 為 `1eb1cbbe973badcb18c35e46c3e8be147c0fed77a1af940483b41620e153ea7e`，元件驗證狀態 SHA-256 為 `aa1c2474fc1c3d12384ba0b1d6fb13735e360bfc0125d09b817582edcd3268e5`。

| 元件 | SHA-256 |
| --- | --- |
| U-Boot 套件 | `62924209e23e422845f6ef517e194fb25731ddb132dd02bec4d58d1282faec28` |
| Linux image 套件 | `6e06043378d82078e6b310c5e9d26844507913a4800ee3cc8f952bae614912f3` |
| Linux DTB 套件 | `7c0bf5d91cbbb5e59de906469ddbc830bac1f2ff14dd2656991fb5c62847b966` |
| Linux headers 套件 | `7fd86f58fc015c97b016ed5f55d1015c97186529144c25c143a01f19a2d2fb22` |
| Linux libc-dev 套件 | `6a6ccb4307e91e2c037763b93cd6fd868211f57a906c62f9e7b3edade99b0194` |
| 編譯後 DTB | `52c58e8a1413fd644b812480215350410659371083afa9930684df5752625413` |
| 編譯後 `u-boot.bin` | `4d8158b3ed44de9384fabb009a0639cbe2c83e964a32724b5c87ce9911f72bda` |

元件建置日誌未發現編譯錯誤或警告；元件驗證確認 M6 DTB 身分、compatible、U-Boot 身分、固定 TZK 及必要設定。這只證明元件可在受控來源上編譯與封裝，不證明 TZK／`sm.bin` 授權、完整啟動鏈、rootfs 或實體硬體功能。

- `tools/verify-bananapi-vs680-m6-sources.sh`：驗證固定提交、原始 DTS blob、修補可套用性、不透明載荷雜湊及可選遠端分支對照。
- `tools/build-bananapi-vs680-m6-components.sh`：只建置 U-Boot 與 Linux 元件，不建立 rootfs。
- `tools/verify-bananapi-vs680-m6-components.sh`：重新核對元件證據檔、套件大小與 SHA-256，且維持硬體與發布聲明為否。
- `tools/build-bananapi-vs680-m6-candidate.sh`：未來建立單一 Trixie CLI 完整候選；本任務不執行。
- `tools/run-bananapi-vs680-m6-components-isolated-cache.sh` 與 `tools/run-bananapi-vs680-m6-candidate-isolated-cache.sh`：以獨立 OverlayFS 保護共用下層快取。
- `tools/verify-bananapi-vs680-m6-candidate.sh`：以唯讀 loop 與唯讀掛載檢查雙分割區、來源中繼資料、DTB 身分及兩段 boot payload。

## 未解除 blocker

1. TZK 與 U-Boot `sm.bin` 缺少原始碼、重建鏈與逐檔再散布授權。
2. 固定 `chipid`、TZ/VPPTA 與記憶體保留區缺少公開規格及板級對照。
3. 沒有可對應此啟動鏈的公開 ATF 原始碼與獨立建置方式。
4. 沒有精確 BPI-M6 主線 Linux 支援。
5. GPU、NPU、VPU、HDMI RX 與 DRM 使用者空間沒有形成可再散布、版本封閉的套件集合。
6. C05 啟動設定與 BPI-M6 V1.1 原理圖尚未完成逐網路拓撲核對。
7. 尚未以本候選完成 SD/eMMC、UART、網路、USB、顯示、音訊、40-pin、重啟、關機與壓力實機驗證。
8. 尚未取得精確 BPI-M6 的 Yocto 或 Buildroot manifest、defconfig、建置紀錄或官方不支援聲明。

在上述條件解除並留下可重跑證據之前，不得移除 `.wip`，不得把 L1/L2 軟體結果描述成硬體支援完成，也不得公開發布完整映像。
