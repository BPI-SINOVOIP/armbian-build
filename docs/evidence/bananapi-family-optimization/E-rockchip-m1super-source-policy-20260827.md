# Banana Pi M1 Super 固定來源與發布政策

## 結論

本候選把 `bananapim1super.wip` 從 ArmSoM Sige1 板檔繼承與 Hinlink H28K U-Boot 身分，改成 Banana Pi M1 Super 專屬板檔、Linux DTS、U-Boot DTS 與 defconfig。第一次完整映像預檢揭露並修正建置期間來源提交競態；其後已從固定提交重新完整建置，通過 L1 與 L2 唯讀內容守門，目前為 `L2 內部軟體候選`。這不是公開發布版，也不代表任何實體板功能已通過。

保留 `.wip` 的原因包含量產料號、韌體與預建載荷授權，以及實體儲存裝置、網路、顯示、影音與 40-pin 尚未完成跨板次驗證。現有映像必須維持內部測試用途，直到發布守門條件逐項解除。

## 身分證據

| 證據 | 固定內容 | 判讀 |
| --- | --- | --- |
| Banana Pi 官方產品頁 | `https://docs.banana-pi.org/en/BPI-M1S/BananaPi_BPI-M1S` | 指定 RK3528、SD、eMMC、雙乙太網路、HDMI、USB 與 40-pin，並把核心來源指向 `armbian/linux-rockchip`、U-Boot 指向 `rockchip-linux/u-boot`。 |
| 官方原理圖資料夾 | `https://drive.google.com/drive/folders/1Wxk7qSWOyx7U-4i5ZitM6eRJGs0bsHhk` | 由官方產品頁連出。 |
| V1.2 原理圖 | `BPI-M1S_ArmSoM-Sige1_V1.2_SCH_20240727.pdf` | SHA-256 為 `71d9122b2d6d30916928cc123ce2cece314c922893623a4e6e7d8d2810b279dd`；圖面專案名稱為 `ArmSoM-SIGE1`，可證明兩者具有共同硬體設計來源。 |
| 固定 Linux DTS | `rk3528-armsom-sige1.dts` | 提供現有板級電源、SD、eMMC、乙太網路、HDMI、USB、GPU 與 VPU 描述，但原始 model 與 compatible 不是 Banana Pi 身分。 |
| 既有 Armbian 板檔 | `bananapim1super.wip` | 原先直接載入 `armsom-sige1.csc`，且 U-Boot 使用 `hinlink_rk3528_defconfig`，無法獨立追溯成品身分。 |

共同原理圖來源只足以作為移植基礎，不能推導每一個量產批次、焊接選項與替代料都完全一致。因此 Linux DTS 保留 `armsom,sige1` 作為次要相容字串，但新增第一順位 `bananapi,bpi-m1-super`；U-Boot 則使用相同的專屬 model 與 compatible，不再攜帶 `Hinlink H28K` 身分。

## 固定來源

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| Linux | `https://github.com/armbian/linux-rockchip.git` | `c6157104418d012823413c02f9222f3fe123dd25` |
| U-Boot | `https://github.com/radxa/u-boot.git` | `39cd993e5d6296635438e84f4576b3a9bf76f86e` |
| RKBin | `https://github.com/armbian/rkbin` | `1d3c61008fa823936ae7a59615393f8294b64456` |
| Armbian 韌體 | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| TF-A 參考來源 | `https://github.com/ARM-software/arm-trusted-firmware.git` | `c17351450c8a513ca3f30f936e26a71db693a145`，即 `v2.13.0` |

上游 TF-A 固定版本沒有 RK3528 平台實作，因此本候選不偽造「ATF 已由來源建置」的證據。實際 BL31 為 RKBin 的 `rk3528_bl31_v1.17.elf`；validation 明確標記 `atf_source_build_available=false`，並對載荷本體執行 SHA-256 守門。

Armbian 韌體來源與引用現在同時固定為 `https://github.com/armbian/firmware` 及 `commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08`，並設定 `verify_firmware_source_resolution=true`。完整映像建置與驗證會要求日誌同時出現來源網址、精確提交取用及完整提交解析結果；不能只靠 Validation 內的靜態字串宣稱來源已固定。

## 候選狀態機

政策守門器只接受以下兩組完整狀態，不接受交叉組合或其他名稱：

| 層級 | 範圍 | 完整映像證據 | 可公開發布 | 可作硬體聲明 |
| --- | --- | --- | --- | --- |
| `L1 元件候選` | `internal-component-only` | 必須不存在 | 否 | 否 |
| `L2 內部軟體候選` | `internal-l2` | 必須為完成，且含完整映像 DTB 雜湊 | 否 | 否 |

第一次完整預檢映像的來源證據不具原子性，因此只用於建立精確契約。修正後的正式映像從提交 `8c6533a10c3ec97e0565c46ef34ab857fca7d4d4` 完整重建，建置與驗證 validation SHA-256 均為 `2026b2786f523bcb158f6eb70674535d8e134df690b31a17e76b26d878412f1c`，並通過 L1 與 L2 守門；目前設定為 `L2 內部軟體候選`、`rootfs_image_built=true`，正式證據記於 `F-rockchip-m1super-L2-build-20260827.md`。專用驗證入口依候選層級產生 L1 或 L2 狀態。內部 L2 只代表固定來源完整映像通過 XZ 串流與唯讀軟體守門，不代表實機、量產或對外發布通過。

元件建置所得 Linux DTB 雜湊固定保存在 `component_build_evidence.linux_dtb.sha256` 與板級 `component_dtb_sha256`。第一次完整映像預檢把相同雜湊建立為拒絕式契約；正式映像再次驗證後，已設定 `image_build_evidence.linux_dtb.sha256` 與 `image_dtb_sha256`，並把範圍標為 `full-image-l2`。政策守門器會拒絕交叉組合，並直接核對 Git 建置提交、本機 IMG／XZ、候選矩陣及完成狀態，避免格式正確但不存在的假證據通過。

守門分為兩個階段。`source-contract` 只驗證可重建所需的固定來源與現行契約，不讀取舊輸出，因此 L2 狀態下刪除 `output/` 仍可從固定來源重新建置；`material-evidence` 才要求完整 IMG、XZ、狀態與清單，並直接核對映像內容。validation 另保存不含候選狀態、證據本體及影像衍生欄位的規範投影 SHA-256；來源提交、現行契約與 L2 證據必須具有相同投影。這個設計避免 JSON 自我雜湊循環，也確保新增套件、核心選項或載荷要求時，舊 L2 證據不能繼續通過。

正式流程進一步區分建置與促進。建置完成只寫入 `pending_verification`，並立即廢止舊的 M1 Super 物質證據；正式驗證會先執行共用唯讀守門，再從本次 `CANDIDATES.tsv`、完成狀態與實檔建立即時證據。政策重查全部通過後才原子寫入 `M1SUPER_MATERIAL_EVIDENCE.json` 與 `M1SUPER_MATERIAL_STATUS.json`，接著重新讀回兩者；任何階段失敗都會刪除證據並把狀態改為 `failed`，因此新映像不能沿用舊映像的 `complete`。

物質重查會執行嚴格 `xz -t`、解壓串流同一性、GPT 主表與備份表 CRC／結構、分割區大小與類型、唯讀 ext4 標籤、必要套件、核心模組、核心與 U-Boot 來源中繼資料、`armbianEnv.txt`、DTB、U-Boot 偏移載荷及受控韌體檢查。IMG、XZ 與矩陣路徑只能位於固定 M1 Super 輸出目錄，不接受 `..`；DTB 宣稱路徑必須等於板級 `dtb`。建置與驗證入口也強制 `REQUIRE_SOURCE_DATE_EPOCH_METADATA=yes`，固定時間戳必須同時出現在 validation、產物中繼資料、建置狀態、驗證狀態與專用完成狀態。

## 專屬實作邊界

Linux 專屬 DTS 從經原理圖證明的 Sige1 設計繼承，覆寫 Banana Pi model 與 compatible，並只額外啟用官方 40-pin 表與原理圖都能確認的 `I2C0`、`I2C1` 與 `SPI0`。`SPI0` 提供兩個 `spidev` 端點，頻率上限保守設為 24 MHz。

U-Boot 專屬 DTS 只保留 RK3528 SoC 與共同 U-Boot 描述，不帶入 H28K 的 ADC 音量鍵。專屬 defconfig 固定 DTS 身分並保留 SD、eMMC、USB、顯示與 Rockchip FIT 啟動鏈選項。

## Wi-Fi 與藍牙矛盾

目前三份資料無法完全一致：

- 官方產品頁列出 `SYN43752`。
- V1.2 原理圖的模組頁列出 `AP6275S`／`BW3752-50B1` 類 SDIO Wi-Fi 6 與 UART 藍牙設計。
- 原先 Sige1 DTS 的 `wifi_chip_type` 是 `rtl8852bs`。

本候選依 V1.2 原理圖把軟體識別改為 `ap6275s`，但 `wifi_bom_conflict_resolved=false`。這只是供實機辨識的候選設定，不是所有板次的 Wi-Fi 通過聲明。正式發布前必須取得量產 BOM，並在每一種實際模組上驗證韌體檔名、SDIO 枚舉、藍牙 UART、休眠喚醒與射頻穩定性。

在 BOM 未決期間，機器契約使用 `provisional-ap6275s`，固定 `brcmfmac`／SDIO 與 `hci_uart`／UART 軟體路徑，並要求映像含有下列固定提交中的 Wi-Fi 檔案：

| 映像路徑 | SHA-256 |
| --- | --- |
| `/lib/firmware/brcm/brcmfmac43752-sdio.bin` | `46f62076768e50938d0e29b306b24d4663de20b07b474c4759d5801fcbf0bdde` |
| `/lib/firmware/brcm/brcmfmac43752-sdio.clm_blob` | `5143146e1923f87f7aab8df043abcf89a657fa9fdc3b22a38806399730d9a97a` |
| `/lib/firmware/brcm/brcmfmac43752-sdio.txt` | `2d2723101fe9c66c853ddb1e2d715851ba100a4390f8ac72fc84dd35736cc66f` |

完整映像還必須含 `brcmfmac.ko` 與 `hci_uart.ko`。這些要求只證明軟體資產存在且來自固定提交；Validation 仍明確記錄 `bom_identity_confirmed=false`、`bluetooth_firmware_identity_confirmed=false` 與 `runtime_hardware_validated=false`，所以不得據此宣稱 Wi-Fi 或藍牙可用。

## 授權與散布

RKBin 的 `LICENSE.TXT` 允許在採用 Rockchip 積體電路的平台上，以未修改二進位形式複製與散布相關載荷，但不允許獨立散布、逆向或修改，且授權文件必須隨二進位提供。本候選會把該授權檔安裝至板級 BSP 文件目錄，並驗證授權檔與三個 RK3528 載荷的固定雜湊。

Armbian 韌體倉包含多個不同上游與授權範圍；目前沒有完成 M1 Super 映像實際攜帶檔案的逐檔散布稽核。因此 `firmware_redistribution_audit_complete=false`，即使映像通過軟體驗證，也不得直接對外散布。

## L1 歷史元件驗證範圍

L1 元件候選只允許證明以下事項：

- 所有 Git 來源與二進位載荷固定且可追溯。
- 專屬 Linux DTB 與 U-Boot 元件能由固定來源建置。
- DTB 的 model、compatible、SD、eMMC、I2C、SPI、網路、USB、GPU、VPU 與 HDMI 靜態契約一致。
- U-Boot 不含 H28K model，載荷偏移不跨越根分割區。
- 專用 OverlayFS 完整映像入口已完成一次預檢，證明建置鏈與唯讀內容可執行；因來源提交競態，該結果只可建立第二輪契約，不能視為正式 L2 證據。

L1 不得證明完整映像建置成功。現行 L2 已補上完整映像的軟體內容證據，但仍不得證明開機成功、記憶體穩定、儲存壽命、網路吞吐、GPU／VPU 硬體加速、HDMI 相容性、USB OTG、40-pin 電氣安全或量產可用性。

## 解除發布阻擋

1. 取得實際量產版號、BOM 與可公開原理圖版本，消除 Wi-Fi 料號矛盾。
2. 至少驗證 1 GB、2 GB、4 GB 記憶體選項與實際 eMMC 容量組合；每種組合執行冷啟動、重啟與長時間壓力測試。
3. 驗證 SD 與 eMMC 開機、雙乙太網路、Wi-Fi、藍牙、HDMI、聲音、USB host／OTG、RTC、風扇與 40-pin。
4. 對映像內所有韌體與使用者空間 GPU／VPU 元件完成逐檔授權稽核。
5. 把實機記錄、映像雜湊、序號去識別樣本與失敗案例納入證據，再由發布負責人核准升級。
