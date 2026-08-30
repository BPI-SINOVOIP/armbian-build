# Banana Pi BPI-R4 Lite 實驗性 current 候選來源政策

更新日期：2026-08-27

## 階段結論

`bananapir4lite` 已完成建置前的固定來源、SD 啟動鏈、MT7987 韌體、核心功能與 L2 驗證契約。板卡仍保留 `.wip`，因為固定 Linux 提交實際為 `6.17.0-rc1`，且尚無本分支實機證據；本階段只證明政策與回歸測試，證據等級維持 L0，不得宣稱可開機或正式發布。

## 固定來源

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| Linux | `https://github.com/frank-w/BPI-Router-Linux.git` | `0529574fee9fcaa75159f9edcedf35e8bc57400d` |
| U-Boot | `https://github.com/u-boot/u-boot` | `34820924edbc4ec7803eb89d9852f4b870fa760a` |
| ATF | `https://github.com/mtk-openwrt/arm-trusted-firmware.git` | `c34e37802efaea356991a0811c8fc50f8a810f5b` |
| Armbian firmware | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| Linux firmware | `https://gitlab.com/kernel-firmware/linux-firmware.git` | `01205307636157a12c29e6a774bf83b218732050` |
| 供應商板級參考 | `https://github.com/BPI-SINOVOIP/BPI-R4Lite-OPENWRT-V24.10.0-Master-Devel` | `42f4c6477d29b4a2dcde56028e740b9c38e135c7` |

固定 Linux 分支包含 MT7987 板級 DTS，但不是長期支援或正式版核心。它只用來建立可重現的實驗性軟體基線；後續須評估移植到穩定核心，不能因 L2 通過就省略安全維護政策。

## SD 啟動鏈政策

- 候選只使用 `mt7987a_bananapi_bpi-r4-lite-sdmmc_defconfig`、MT7987 SDMMC BL2 與 FIP。
- 停用供應商 `CONFIG_USE_DEFAULT_ENV_FILE`、keyed autoboot 與自動顯示選單，避免 `/dev/fit0`、`production`、`recovery`、`install` 與 TFTP 回復路徑取代 Armbian 啟動。
- 啟用 U-Boot BootSTD、bootflow、extlinux、EXT4、MMC 與自動開機，預設 DTB 固定為 `mediatek/mt7987a-bananapi-bpi-r4-lite-sd.dtb`。
- SD 整碟映像使用五分割區 GPT：`bl2`、`ubootenv`、`factory`、`fip` 與根檔案系統。
- U-Boot 環境以 `ubootenv` 分割區名稱定位，每份大小 `0x40000`，並在 `0x400000`、`0x440000` 保留兩份冗餘環境；不得跨入 `factory`。
- Linux DT 移除 OpenWrt `root=/dev/fit0` 與 UBI 參數，根檔案系統只由 extlinux 的 PARTUUID 決定。

本候選不定義 eMMC、SPI NOR、SPI NAND、recovery 或安裝程序。這些媒體即使在供應商程式碼中存在，也不能沿用 SD 映像契約宣稱支援。

## 核心與韌體政策

核心契約新增 GPIO keys 與 PCA9555 GPIO expander，並驗證 MediaTek Ethernet／WED、MT7530 DSA、2.5G PHY、SFP、PCIe、NVMe、MMC、USB、PWM fan、RTC、I2C mux、EEPROM 與標準 I/O 工具。固定 6.17 核心使用 `CONFIG_MEDIATEK_2P5GE_PHY=y`；共用設定同時保留 6.12 BPI 分支採用的舊符號 `CONFIG_MEDIATEK_2P5G_PHY=y`，未被該核心定義的符號會由 Kconfig 忽略。

固定 Linux firmware 提交另提供下列兩個 MT7987 參考檔案：

- `mediatek/mt7987/i2p5ge-phy-DSPBitTb.bin`：SHA-256 `1f7b7fd1c243576e04c16b98c649db1e3326f6a715556c2a56094bcd7d300d71`。
- `mediatek/mt7987/i2p5ge-phy-pmb.bin`：SHA-256 `941e3118493d5cb14323968ebc1193b23411d7c330a566014eeeb51c5ea7ed45`。

固定 6.17 驅動目前實際宣告 `mediatek/mt7988/i2p5ge-phy-pmb.bin`，而非上述 MT7987 路徑；BSP 因此同時安裝這個必要檔案。核心亦包含共享 Filogic Ethernet／WED 驅動，所以一併安裝 MT7981、MT7986 與 MT7988 其餘五個韌體，避免 initramfs 缺檔警告。這些參考與共享檔案不表示 R4 Lite 具有其他 SoC 的硬體。MediaTek 授權原文因法律與來源追溯要求保留，周邊來源說明使用繁體中文。

ATF 的 MT7987 流程會連結供應商提供的預編譯 DRAM／eFuse 物件。完整建置可證明載荷可重現產生，但在逐檔來源與授權旁證補齊前，仍屬再散布合規風險；不得把本地 L2 當成對外授權核准。

## 建置與升級門檻

輸出目錄：

`output/images/2026.08/bananapi-filogic-mt7987-r4lite-trixie-current-cli/`

完整建置命令：

```bash
./tools/run-bananapi-filogic-r4lite-candidate-isolated-cache.sh
```

唯讀映像驗證命令：

```bash
./tools/verify-bananapi-filogic-r4lite-candidate.sh
```

只有完整建置成功，且 IMG／XZ 同一性、GPT、BL2／FIP、U-Boot 禁止字串、DTB 禁止字串、核心設定、韌體雜湊、授權、套件與唯讀掛載內容全部通過後，才可升為 L2。L3 仍需實體板 UART、冷啟動、SD、四個 LAN、2.5GbE PHY、SFP、PCIe、NVMe、USB、I2C、RTC、PCA9555、按鍵、LED、風扇與熱壓力證據。
