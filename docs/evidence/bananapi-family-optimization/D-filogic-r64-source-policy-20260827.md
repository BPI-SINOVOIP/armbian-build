# Banana Pi BPI-R64 current 候選來源政策

更新日期：2026-08-27

## 階段結論

`bananapir64` 已由開發中 `.wip` 提升為社群候選 `.csc`，並完成建置前的來源、啟動鏈、韌體、核心與驗證契約。此階段只證明政策及本機回歸通過，尚未產生本政策對應的新映像，因此證據等級維持 L0；不得提前宣稱可開機或硬體功能通過。

## 固定來源

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| Linux | `https://github.com/frank-w/BPI-Router-Linux.git` | `4a4506842b77b597f11e7fc53be1dcdbdc97eea9` |
| U-Boot | `https://github.com/u-boot/u-boot` | `34820924edbc4ec7803eb89d9852f4b870fa760a` |
| ATF | `https://github.com/mtk-openwrt/arm-trusted-firmware.git` | `c34e37802efaea356991a0811c8fc50f8a810f5b` |
| Armbian firmware | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| Linux firmware | `https://gitlab.com/kernel-firmware/linux-firmware.git` | `01205307636157a12c29e6a774bf83b218732050` |

板級設定在 `current` 分支覆寫可移動來源，讓後續完整建置、映像中繼資料與驗證器使用相同提交。

## 啟動鏈政策

- 使用 `mt7622_bananapi_bpi-r64-sdmmc_defconfig`、MT7622 SDMMC BL2 與 FIP。
- 啟用 U-Boot BootSTD、bootflow、extlinux、EXT4、MMC 與自動開機。
- 修正預設 DTB 為 `mediatek/mt7622-bananapi-bpi-r64.dtb`，避免缺少 `mediatek/` 目錄造成載入失敗。
- SD 映像採五分割區 GPT：`bl2`、`ubootenv`、`factory`、`fip` 與根檔案系統。
- U-Boot 環境以 GPT 分割區名稱 `ubootenv` 定位，大小為 `0x40000`，啟用兩份冗餘環境；固定偏移 `0x400000` 與 `0x440000` 只作分割區名稱無法解析時的受控回退。
- `factory` 是校準資料保留區，建置、安裝及測試流程不得任意覆寫。

此候選只產生 SD 映像，不定義 eMMC、SPI NOR 或 SPI NAND 的直接安裝程序。

## 核心與韌體政策

核心契約補入 MT7622 pinctrl、基礎／Ethernet／HIF clocks、舊世代 MediaTek PCIe、MT7615/MT7622 WMAC 與 MediaTek 藍牙 UART 驅動。原有 SATA AHCI、MT7530 DSA、MMC、SPI NAND、USB、I2C、SPI、PWM、RTC 及標準網路與 I/O 工具一併納入映像守門。

以下三個執行期韌體由固定 Linux firmware 提交收入板級 BSP，並以精確 SHA-256 驗證：

- `mediatek/mt7622pr2h.bin`：藍牙 UART 韌體。
- `mediatek/mt7622_n9.bin`：內建無線網路 N9 韌體。
- `mediatek/mt7622_rom_patch.bin`：內建無線網路 ROM 修補韌體。

MediaTek 授權原文因法律與來源追溯要求保留；中文來源說明記錄官方提交、用途與每個檔案雜湊。

## 硬體限制

板級 DT 以 GPIO90 在第二組 PCIe 與 SATA 間切換。雖然兩個控制器節點都存在並啟用，兩者不得宣稱可同時使用；實機測試必須分別選擇模式並保存結果。

## 建置與升級門檻

預定輸出目錄：

`output/images/2026.08/bananapi-filogic-mt7622-r64-trixie-current-cli/`

完整建置命令：

```bash
./tools/run-bananapi-filogic-r64-candidate-isolated-cache.sh
```

唯讀映像驗證命令：

```bash
./tools/verify-bananapi-filogic-r64-candidate.sh
```

只有完整建置成功，且 IMG／XZ 同一性、GPT、BL2／FIP、U-Boot 設定、DTB、核心設定、韌體雜湊、套件與唯讀掛載內容全部通過後，才可升為 L2。L3 仍需實體板 UART、冷啟動、SD、網路、無線網路、藍牙、儲存與 I/O 證據。
