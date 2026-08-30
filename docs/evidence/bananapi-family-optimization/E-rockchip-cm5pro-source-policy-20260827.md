# Banana Pi CM5 Pro 固定來源與 L2 軟體候選政策

更新日期：2026-08-27

## 階段結論

`bananapicm5pro.wip` 已具備獨立 Banana Pi 板檔、Linux 與 U-Boot 專用板級身分、固定來源、機器可讀驗證契約、專用建置入口、隔離快取入口及唯讀驗證入口。固定提交上的 Linux DTB 與 U-Boot 元件已完成交叉編譯及靜態檢查，Trixie vendor minimal CLI 也已完成整體建置。

本階段可稱為「內部完整 L2 軟體候選」，代表 IMG／XZ、固定來源、啟動載荷、檔案系統內容與機器契約已通過唯讀守門，但沒有建立任何實機支援聲明。必須完成載板等同性、散布授權與實體板驗證後，才能分別評估 L3、公開發布或商業交付。

以下三項阻擋維持有效：

1. Linux 與 U-Boot 的 Banana Pi DTS 是繼承 ArmSoM CM5 IO 描述的專用包裝層，尚無 Banana Pi 專屬完整 DTS，也尚未完成模組與載板原理圖逐網路等同性審查。
2. RTL8852BS 所需韌體在固定韌體倉庫中存在，但缺少逐檔可核對的再散布授權，不能據此核准公開或商業散布。
3. 尚無 BPI-CM5 Pro 實體板的 UART、啟動、儲存、網路、介面及加速器證據。

因此板檔保留 `.wip`，驗證契約固定 `public_release_allowed=false` 與 `hardware_claims_allowed=false`。

## 繼承稽核

基準提交中的 `bananapicm5pro.wip` 直接載入 `armsom-cm5-io.csc`，並使用 `armsom-cm5-io-rk3576_defconfig` 與 `rk3576-armsom-cm5-io.dtb`。這會讓 Banana Pi 身分、來源版本、啟動鏈及未來板級差異依賴另一塊板的可變預設值。

本候選已改為自足板檔，明確定義 RK3576 啟動情境、GPT、DDR、BL31、加速啟動與 USB 燒錄 blob、Linux、U-Boot、RKBin、韌體來源、套件及核心設定。Linux 與 U-Boot 另建立 `rk3576-bananapi-cm5-pro` 專用 DTS 與 `bananapi-cm5-pro-rk3576_defconfig`，輸出 model 與第一個 compatible 都是 Banana Pi。

專用 DTS 仍以 `rk3576-armsom-cm5-io.dts` 為硬體描述來源，並保留 ArmSoM 相容字串供驅動匹配。官方產品與入門文件使用 CM5 Pro Kit／CM5 IO 名稱，也在部分命令與映像中沿用 ArmSoM 身分；這些資料只支持「目前軟體源自同一供應商設計脈絡」，不能證明每個模組版本、載板線路與 GPIO 複用完全相同。

官方參考頁：

- `https://docs.banana-pi.org/en/BPI-CM5_Pro/BananaPi_BPI-CM5_Pro`
- `https://docs.banana-pi.org/en/BPI-CM5_pro/GettingStarted_BPI-CM5_Pro`

## 固定來源

| 元件 | 固定來源 | 固定提交 | 授權邊界 |
| --- | --- | --- | --- |
| Linux 6.1.115 | `https://github.com/armbian/linux-rockchip.git` | `c6157104418d012823413c02f9222f3fe123dd25` | 倉庫整體為 `GPL-2.0 WITH Linux-syscall-note`；各檔案依 SPDX |
| U-Boot 2017.09 | `https://github.com/radxa/u-boot.git` | `39cd993e5d6296635438e84f4576b3a9bf76f86e` | `GPL-2.0` 或 `GPL-2.0+`；各檔案依 SPDX |
| RKBin | `https://github.com/armbian/rkbin` | `1d3c61008fa823936ae7a59615393f8294b64456` | 受 `LICENSE.TXT` 的平台、未修改二進位及隨附授權條件限制 |
| Armbian 韌體 | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` | 倉庫含專有且散布受限內容；RTL8852BS 缺少逐檔授權證據 |
| RTL8852BS SDIO 驅動 | `https://github.com/armbian/wifi-rtl8852bs.git` | `35d3e2660fd912c36777cc50dd43b3fbc805d56a` | 倉庫授權為 `GPL-2.0`；部分 crypto 檔案另標示 BSD 授權 |

Linux 供應商樹、舊版 U-Boot 與預編譯 RKBin 都需獨立維護安全修補及來源更新政策。固定提交只保證可追溯，不代表安全支援週期、上游接受度或硬體正確性。

## RKBin 散布邊界

`LICENSE.TXT` 允許複製及散布，但二進位只能搭配採用 Rockchip 積體電路的平台、不可修改，且散布時必須附授權副本。驗證契約逐一固定 `RK3576MINIALL.ini`、DDR、BL31、boost、usbplug、`boot_merger` 與授權檔的 SHA-256；板級 BSP 套件會把同一份授權檔安裝至 `/usr/share/doc/armbian-bsp-bananapicm5pro/rkbin.LICENSE.TXT`。

這些 blob 無法由本倉庫原始碼重建。提交與雜湊只能證明來源及位元內容一致，不能證明其內部行為可審計，也不能解除平台與散布限制。

## 韌體散布邊界

固定韌體來源含 `rtl_bt/rtl8852bs_config.bin`、`rtl_bt/rtl8852bs_fw.bin` 及 `rtw89/rtw8852b_fw*.bin`。驗證契約記錄每個檔案及來源 `README.md` 的 SHA-256，以便未來完整映像做位元同一性檢查。

來源 `README.md` 只提供倉庫層級的概括說明，沒有能逐檔對應 RTL8852BS 的授權文件。因此目前只能用於內部技術候選；對外提供映像前，必須取得權利人或供應商可追溯的再散布授權，並由授權負責人審核實際散布方式。

## 外部 Wi-Fi 驅動邊界

固定 Linux 提交本身沒有 RTL8852BS SDIO 驅動。Armbian 核心建置在 `EXTRAWIFI=yes` 時，會從固定 `wifi-rtl8852bs` 提交注入 out-of-tree 驅動，再套用 Linux 6.1 相容修正。CM5 Pro 板檔明確固定驅動來源與提交；共用注入器保留原預設行為，但允許板檔用 `RTL8852BS_GIT_SOURCE` 與 `RTL8852BS_GIT_REF` 覆寫，避免板級候選暗中依賴另一個未記錄的來源。這兩個值也納入核心 artifact 與 driver-patch 快取鍵，變更來源或提交時不能重用舊產物。

驅動倉庫的 `LICENSE` SHA-256 為 `8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643`。板級 BSP 套件會安裝相同授權副本至 `/usr/share/doc/armbian-bsp-bananapicm5pro/rtl8852bs.LICENSE`，唯讀驗證器會檢查雜湊。

驅動 `README.md` 明確說明來源未經驗證或稽核。GPL 授權可提供散布條件，但不代表程式碼品質、安全性、法規認證或硬體相容性已確認；實際模組載入、SDIO 枚舉、rfkill、韌體載入及吞吐仍屬 L3 實機項目。

## 板級靜態契約

Linux 包裝 DTS 在供應商 donor 上增加 Banana Pi model／compatible，並依官方 40-pin 使用方式明確啟用 I2C3 與 SPI3 的 pinctrl，建立 `/dev/spidev3.0` 對應節點。這只是一份待硬體確認的板級介面契約，不能證明腳位電壓、衝突、訊號完整性或周邊可用。

機器契約會解析編譯後 DTB，檢查下列內容：

- Banana Pi model、compatible 與 DTB 精確 SHA-256。
- SD、SDIO、eMMC 的節點、bus width、必要屬性與 alias。
- GPIO 使用所需的 I2C3、SPI3、pinctrl 及 spidev 節點。
- PCIe、NVMe 路徑、Gigabit Ethernet、USB host／OTG、Type-C 角色切換。
- GPU、VPU 解碼、VPU 編碼、RGA、NPU、VOP、HDMI、DisplayPort 節點。
- RTL8852BS Wi-Fi／Bluetooth、RTC、音訊 codec 及風扇節點。

節點存在且 `status = "okay"` 只代表固定 DTS 的描述與驗證契約一致。GPU、VPU、NPU、顯示與無線功能仍取決於核心驅動、使用者空間、韌體、授權、板級線路與實機結果。

## 診斷套件與核心設定

候選套件涵蓋：

- GPIO：`gpiod`、`python3-libgpiod`
- I2C／SPI：`i2c-tools`、`python3-spidev`、`spi-tools`
- PCIe／eMMC／SD／USB：`pciutils`、`nvme-cli`、`smartmontools`、`hdparm`、`usbutils`、`usb-modeswitch`
- 網路與無線：`ethtool`、`iproute2`、`iperf3`、`rfkill`、`iw`、`bluez`、`bluez-tools`
- GPU／VPU／NPU／顯示：`mesa-utils`、`glmark2-es2`、`vainfo`、`vulkan-tools`、`clinfo`、`libdrm-tests`、`v4l-utils`、`ffmpeg`、`gstreamer1.0-tools`
- 輸入與音訊：`evtest`、`alsa-utils`

核心契約要求 Rockchip GPIO、I2C、SPI、MMC、PCIe、Ethernet、USB、Type-C、DRM、Mali、MPP、RGA、RKNPU 與 RTL8852BS 相關設定。NPU 沒有加入未固定來源或未完成授權稽核的 RKNN 使用者空間套件；目前只驗證核心驅動可編譯與靜態節點，後續必須另外固定 RKNN runtime、模型與測試工具才可做功能判定。套件可安裝或元件可編譯都不是實機功能證明；後續仍要讀回驅動綁定、裝置節點、實際資料傳輸、錯誤計數及壓力結果。

## 元件建置證據

固定 Linux 提交套用專用 DTS、固定外部 Wi-Fi 驅動與板級核心設定後，使用 `rockchip_linux_defconfig` 完成 `Image + modules + dtbs` 元件建置。39 項核心設定由 Kconfig 重新解析後全部符合契約，完整建置成功產生下列關鍵產物：

可攜元件證據由 `tools/export-bananapi-rockchip-cm5pro-components.sh` 匯出，再由 `tools/verify-bananapi-rockchip-cm5pro-components.sh` 逐檔核對來源提交、大小、SHA-256、DTB 身分、模組架構、授權與核心版本。可攜目錄不包含來源樹或建置樹，也不會把元件建置提升成完整映像或實機證據。

| 產物 | 大小 | 本次 SHA-256 |
| --- | ---: | --- |
| `arch/arm64/boot/Image` | 39735808 bytes | `29f0ca496b3223906daa6ba95a2057f8157ed3878269961e705ea7c43a2bef3c` |
| `rk3576-bananapi-cm5-pro.dtb` | 274879 bytes | `399683fe7447c160f5e4255309a59f133c5427dc86eab60a93d61a1aab65aee8` |
| `8852bs.ko` | 6680656 bytes | `6e71b698ca4d02ded482777dc485dbf1cc85c54600775223e6332980e9bf8cb6` |
| `.config` | 依文字內容 | `e5b9bd6fbb879bc6f345030490ee6574f57fc28325ac645c50db2ea22aa372f1` |

`8852bs.ko` 讀回為 AArch64 ELF、`license: GPL`、`vermagic: 6.1.115 SMP mod_unload aarch64`。從 DTB 讀回的 model、compatible、節點、屬性、alias 及 bus width 符合機器契約。完整 DT 建置仍出現 donor overlay 的 `reg_format`／預設 address-cells 警告，以及 `dw-dp.c` 的格式字串警告；它們沒有使建置失敗，但後續不得忽略。

核心 Image、模組與 `.config` 可能受工具鏈、建置時間或完整設定影響，上表雜湊只記錄本次元件建置。只有專用 DTB 的精確雜湊列入目前跨流程機器契約。

固定 U-Boot 提交套用專用 DTS 與 defconfig 後，搭配固定 BL31 成功產生下列元件：

| 產物 | 大小 | 本次 SHA-256 |
| --- | ---: | --- |
| `spl/u-boot-spl.bin` | 240421 bytes | `65866bf74ee7d02c8653c42ffe4f21d429b39bbaf6e3563f800671e9f02c48b7` |
| `u-boot.dtb` | 9426 bytes | `396a43ce3a5828339f9093dcdee950f38c91e02c60c69b3d95c4e3ba37c15b18` |
| `u-boot.itb` | 1444864 bytes | `8ab1704b15001f9899d314932687c17dc59681940b772ac4a081a52442197646` |

U-Boot 產物可能含建置時間等非固定欄位，上表雜湊只記錄本次元件建置，不列為跨環境可重現契約。專用 defconfig 與 `u-boot.dtb` 已讀回 Banana Pi model／compatible，並確認 MMC、SPI、USB、USB mass storage 與指定 DTB 路徑設定存在。

上述結果只證明固定來源可交叉編譯且靜態輸出符合契約，不證明 SPL 能初始化實際 DRAM、不證明儲存開機，也不證明任何周邊或加速器功能。

## 建置與驗證入口

完整候選建置入口：

```bash
./tools/build-bananapi-rockchip-cm5pro-candidate.sh
```

隔離快取與 OverlayFS 建置入口：

```bash
./tools/run-bananapi-rockchip-cm5pro-candidate-isolated-cache.sh
```

完整映像完成後的唯讀驗證入口：

```bash
./tools/verify-bananapi-rockchip-cm5pro-candidate.sh
```

Rockchip 建置器會輸出 `RKBIN_EVIDENCE.tsv`／`RKBIN_STATUS.json`；當契約含外部 Wi-Fi 驅動時，也會輸出 `WIFI_DRIVER_EVIDENCE.tsv`／`WIFI_DRIVER_STATUS.json`。驗證器會以候選提交中的 JSON 重算兩份清單，核對來源提交、設定雜湊、檔案雜湊及狀態，再把結果寫入最終驗證狀態。

完整根檔案系統映像已通過 IMG／XZ 同一性、GPT、啟動載荷、固定來源、RKBin、韌體、授權副本、DTB、核心設定、套件與唯讀掛載內容守門。可重跑結果記錄於 `E-rockchip-cm5pro-L2-build-20260827.md`；公開發布仍受前述授權與板級來源阻擋。

## 實體板必要驗證

解除硬體聲明阻擋至少需要：

1. 記錄模組、載板、記憶體、eMMC、無線模組及 PCB 版本，完成 Banana Pi 原理圖與 donor 原理圖差異審查。
2. 保存 UART 全程紀錄，執行多次冷啟動、暖重啟、關機與斷電重啟。
3. 分別驗證 SD 與 eMMC 安裝、開機、讀寫、錯誤計數、斷電恢復及壓力。
4. 驗證 40-pin GPIO 輸入輸出、I2C3 實體裝置、SPI3 loopback，並確認電壓及 pinmux 無衝突。
5. 驗證 PCIe／NVMe、Ethernet、USB host、USB OTG、Type-C 角色切換及 USB mass storage。
6. 驗證 Wi-Fi、Bluetooth、rfkill、吞吐、長時間連線與韌體載入紀錄。
7. 驗證 HDMI、DisplayPort、實際顯示模式、熱插拔、音訊與 DRM 錯誤計數。
8. 以可辨識硬體後端的工具分別驗證 GPU、VPU 解碼／編碼、RGA 與 NPU；只看程式退出碼或節點存在不算通過。
9. 執行 CPU、記憶體、儲存、網路、GPU／VPU／NPU 組合壓力及溫度、降頻、重置、核心錯誤監控。

完成上述工作前，所有功能狀態都必須標記為「靜態候選」或「待實機驗證」。
