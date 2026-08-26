# Banana Pi BPI-Forge1 vendor 候選來源政策

更新日期：2026-08-27

## 階段結論

本階段完成 `bananapiforge1` 的固定來源、RK3506J 啟動鏈、供應商核心、板級身分、UART、USB HID、常用 I/O 工具、RKBin 授權及唯讀映像驗證契約。依任務限制尚未執行完整映像建置，也沒有實體板證據，因此目前仍是 L0 的 L2 候選實作，不得宣稱已達 L2、可開機或可正式發布。

板檔不再繼承 `armsom-forge1.csc`，避免 ArmSoM 預設值、可移動來源與 Banana Pi 板級政策混在一起。BPI-Forge1 與既有 ArmSoM Forge1 DTS 的硬體等同性仍須由原理圖、板卡版本及實機測試確認；本候選保留原相容字串，沒有把來源中不存在的周邊虛構成已支援功能。

## 固定來源

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| U-Boot | `https://source.denx.de/u-boot/contributors/kwiboo/u-boot.git` | `a72ec1294fc6ba6b0bfd5ebc912a7bed2dc2513d` |
| Linux | `https://github.com/armbian/linux-rockchip.git` | `c6157104418d012823413c02f9222f3fe123dd25` |
| RKBin | `https://github.com/armbian/rkbin` | `1d3c61008fa823936ae7a59615393f8294b64456` |

U-Boot 固定使用 `forge1-rk3506j_defconfig`。Linux 固定在 Rockchip `6.1` vendor 基線，而非上游主線；其供應商驅動可支援目前 DTS 描述的 RK3506J 周邊，但後續安全修補與主線移植仍需獨立維護政策。

## RKBin 與授權

下列 SHA-256 是從固定 RKBin 提交直接取出檔案後重新計算，不使用縮寫：

| 檔案 | SHA-256 |
| --- | --- |
| `rk35/rk3506b_ddr_750MHz_v1.06.bin` | `14a607be903eff6c0984cdbeda77e7ce2963afad74aa900cad17149ec3fc65a7` |
| `rk35/rk3506_tee_v2.10.bin` | `93603ca22cdf22e47ac130e4ac386cdf9474443ab076039807dfc2d5d30b7ecd` |
| `LICENSE.TXT` | `0b37e1522c36cf4579c45dfb138798c3cb5665fcf6302b95377179fbed38e35c` |

DDR 與 TEE 都是不能由本倉庫來源重建的預編譯二進位；雜湊只能證明位元內容及來源提交一致，不能證明內部行為可審計。`LICENSE.TXT` 要求二進位只能搭配採用 Rockchip 積體電路的平台散布，且散布時必須附上授權副本。板級 BSP 套件會安裝固定授權檔，Rockchip 建置器與驗證器會同時核對提交、三個檔案雜湊及映像內授權檔。對外發布前仍須由授權負責人確認實際散布方式符合條款。

## 啟動與分割政策

- U-Boot 以 `u-boot-rockchip.bin` 單一載荷封裝 DDR 初始化、SPL、U-Boot 與 TEE，寫入整碟映像 offset 32768 bytes。驗證器由受 basename 限制的板級欄位指定此載荷，並逐字確認 Banana Pi 板級身分與 Linux DTB 路徑。
- GPT 第一分割區固定從 sector 32768 開始，也就是 16 MiB；驗證器會拒絕載荷超出保留區或映像內容與套件載荷不同。
- U-Boot 控制 DT 使用 `rk3506j-bananapi-forge1`，供應商 Linux 則提供 `rk3506b-bananapi-forge1.dtb`。兩者都是繼承 ArmSoM 硬體描述的 Banana Pi 專用 wrapper，不會改寫共用參考 DTS；啟動腳本固定 Linux DTB 檔名，避免混用 U-Boot 的 `rk3506j` 名稱。
- 專用啟動腳本使用 `ttyFIQ0,1500000n8`，不再沿用共用 RK3506 腳本硬編碼的 `ttyS2`。
- 目前只定義 SD 整碟候選。DTS 雖描述 SPI-NAND，尚未建立 NAND 分割、燒錄、壞區及升級驗證流程，不得把 SD 的 L2 結果外推為 NAND 支援。

## 板級與功能政策

核心補丁只調整板級 `model` 與 `compatible`，保留既有 ArmSoM 及 Rockchip 相容字串。驗證契約依固定來源 DTS 檢查 SD、雙 RMII Ethernet、USB OTG、USB host、SPI-NAND、I2C、RTC、RK730 音訊、CAN、RGA、RNG、溫度、DSI 顯示及觸控節點。這是映像內容契約，不是實物功能證明。

實際編譯後 `rk3506b-bananapi-forge1.dtb` 的 SHA-256 固定為 `bc6a4d9329a095dcbdc21f0f38912c0aa90f778f4c5286f598419533d10cb657`。除了精確雜湊，守門仍會解析 model、compatible、節點、alias、bus width 及必要屬性；任一來源、補丁或工具鏈變更造成 DTB 漂移時，必須重新審查而不能沿用既有 L2 結論。

核心設定明確啟用 USB、DWC2、USB HID、通用 HID、`hidraw`、USB gadget、ConfigFS mass storage 與 GPIO character device，並保留 vendor 基線既有的 I2C、SPI、MMC、CAN、Ethernet、音訊、顯示及 RGA 支援。根檔案系統加入 `gpiod`、`i2c-tools`、`python3-libgpiod`、`python3-spidev`、`spi-tools`、`usbutils`、`usb-modeswitch`、`evtest`、`can-utils`、`ethtool`、`iproute2` 與 `iperf3`。

板上沒有 Wi-Fi／Bluetooth，因此本候選不加入無線套件、板級載入規則或虛構的韌體來源。一般 Armbian 根檔案系統可能仍由共用套件攜帶其他平台韌體，但這不構成 BPI-Forge1 板載無線支援聲明。

## 元件級建置證據

固定 Linux 提交已以 `rockchip_linux_defconfig` 單獨編譯 Banana Pi 專用 DTB。從輸出 DTB 讀回的 model、三個 compatible，以及驗證契約列出的 38 個節點、屬性、alias 與 SD bus-width 全部符合預期。

固定 U-Boot 提交已套用專用補丁，搭配上述固定 DDR 與 TEE blob 產生 `u-boot-rockchip.bin`。輸出可讀回 `Banana Pi BPI-Forge1` 與 Banana Pi 專用 Linux DTB 路徑，建置設定亦包含 `CONFIG_CMD_BTRFS=y`、`CONFIG_CMD_USB_MASS_STORAGE=y` 與 `CONFIG_BAUDRATE=1500000`。

上述結果只證明固定來源可在本機交叉編譯，且關鍵輸出內容符合靜態契約；它不包含 Armbian 根檔案系統、分割表、套件、整碟載荷同一性或實機啟動，因此不能取代完整映像 L2 與硬體 L3 驗證。

## 建置與驗證入口

完整映像建置入口如下；本階段沒有執行：

```bash
./tools/run-bananapi-rockchip-forge1-candidate-isolated-cache.sh
```

建置完成後的唯讀驗證入口：

```bash
./tools/verify-bananapi-rockchip-forge1-candidate.sh
```

只有完整建置成功，且 IMG／XZ 同一性、GPT、16 MiB 保留區、U-Boot 載荷、固定來源、RKBin、授權、啟動腳本、DTB、核心設定、必要套件及唯讀掛載內容全部通過後，才能升為 L2。L3 還需要實體板 UART、多次冷啟動、SD、雙 Ethernet、USB 鍵盤滑鼠與儲存、GPIO、I2C、SPI、CAN、DSI、觸控、音訊、重新啟動、關機及長時間壓力證據。
