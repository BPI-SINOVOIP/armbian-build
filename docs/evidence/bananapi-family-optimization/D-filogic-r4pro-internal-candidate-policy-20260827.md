# Banana Pi BPI-R4 Pro 8X 內部 SD 實驗性候選政策

更新日期：2026-08-27

## 階段結論

本變更建立 `bananapir4pro` 的 SD-only 實驗性 L2 軟體候選。提交 `12d304707` 已完成一次完整預檢映像且唯讀內容守門通過，但當時仍使用整個 Filogic 共用 U-Boot 修補佇列；摘要含 16 個 `not_mbox`、兩個 `needs_rebase` 與一個 `invalid_diff`，其中 R4 Pro 專屬修補本身無法被正規 diff parser 解析。因此該映像只保留為預檢，不得升級為正式 L2。

正式候選改用 `u-boot-filogic-r4pro` 專用修補目錄，只納入 R4 Pro 共用 DTS、SD DTS、精簡 SD defconfig 與必要的 extlinux 設定；預設掃描目標限縮為 `mmc0`。專用佇列必須以 mail format 完整解析、全部無偏移套用且零修補問題後重新建置。沒有實體板證據前，不得宣稱可開機、硬體相容性或正式發布資格。

Linux 固定提交的版本是 `6.19.0-rc1`，只適合內部整合與風險盤點。ATF 會連結未提供逐檔授權旁證的預編譯 DRAM／eFuse 物件；在授權釐清前，即使後續完整建置與唯讀映像驗證通過，仍不得核准公開散布。

## 固定來源

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| Linux | `https://github.com/frank-w/BPI-Router-Linux.git` | `20fb2a966dcea69df6987463ae1fe1c67cff36b6` |
| U-Boot | `https://github.com/u-boot/u-boot` | `34820924edbc4ec7803eb89d9852f4b870fa760a` |
| ATF | `https://github.com/mtk-openwrt/arm-trusted-firmware.git` | `c34e37802efaea356991a0811c8fc50f8a810f5b` |
| Armbian firmware | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| mt76 firmware | `https://github.com/openwrt/mt76.git` | `c5a3bd91aa735b669618610d5f0ebfa5786845a6` |
| Linux firmware | `https://gitlab.com/kernel-firmware/linux-firmware.git` | `01205307636157a12c29e6a774bf83b218732050` |

板級 U-Boot 修補取自官方 BPI-R4 Pro 8X OpenWrt 儲存庫提交 `56e0e77adad258ba05782fee8f94f00d17b0b991`。官方檔案 SHA-256 是 `e6663063c6cbc563c25290bc274007bc6e594737a29d9499aa3a4518082c0064`。專用候選的第一個修補只保留 SD 所需三個檔案，移除供應商預設環境及其 NAND／eMMC 安裝腳本，並配合 U-Boot v2025.04 將 GPIO 參照改為 `pio`、Ethernet 節點改為 `eth0`、pinctrl 節點改為 `pio`，SHA-256 為 `696039c706293e393888ab164a8a8412c9ac6fbfbd311d9262b21fa86a6bc5a7`。第二個修補加入內建 SD extlinux 環境並把掃描目標限制為 `mmc0`，SHA-256 為 `10ecafc1603463f2114cb0349b1791bfd2126ba3e208e52fab040d95d9c56a4a`。

## SD 啟動契約

- 唯一候選 defconfig 是 `mt7988a_bananapi_bpi-r4-pro-8x-sdmmc_defconfig`。
- 映像只封裝 SDMMC BL2、FIP 與五分割區 GPT；BL2 位於 byte `17408`，FIP 位於 byte `6815744`。
- GPT 分割區依序是 `bl2`、`ubootenv`、`factory`、`fip` 與 Armbian 根檔案系統。
- U-Boot 停用供應商預設環境、keyed autoboot、自動選單、eMMC boot、HS200、SPI-NAND 與 UBI 命令，改用 BootSTD、extlinux、EXT4 與 SD 自動開機。
- U-Boot 專用修補不包含 eMMC 或 SPI-NAND DTS、defconfig 與環境，`BOOT_TARGETS` 只允許 `mmc0`。
- U-Boot 以 `ubootenv` 分割區名稱定位兩份冗餘環境，避免跨入 `factory` 分割區。
- 驗證器拒絕 `/dev/fit0`、production/recovery、eMMC 寫入與 UBI 安裝字串，防止供應商 OpenWrt 路徑重新進入候選。

本候選明確不支援 eMMC、SPI NAND、SPI NOR、NVMe 或 USB 開機。NVMe 與 USB 仍可作為 Linux 執行期裝置，但不能據此宣稱它們是本候選的啟動媒體。

## DTB 與介面契約

固定 Linux 原始樹以兩個元件產生 `mediatek/mt7988a-bananapi-bpi-r4-pro-8x-sd.dtb`：

1. `mediatek/mt7988a-bananapi-bpi-r4-pro-8x.dtb`
2. `mediatek/mt7988a-bananapi-bpi-r4-pro-sd.dtbo`

SD overlay 限定 4-bit、48 MHz、`cap-sd-highspeed`、`no-mmc` 與卡片偵測 GPIO。驗證契約同時檢查四組 PCIe、兩組 USB、MT7988 交換器、SFP、I2C mux、PCA9555、PCF8563、AT24 EEPROM、RT5190A、PWM 風扇、按鍵、LED、watchdog 與標準 GPIO/I2C/SPI 工具。CN15 與 CN18 overlay 是選配插槽模式，不列為預設 overlay，也不在本候選宣稱已驗證。

## 網路韌體契約

- MT7996 的十一個檔案取自固定 mt76 提交，並把該來源的 `firmware/LICENSE` 安裝到 BSP 文件目錄。
- MT7988 的 `i2p5ge-phy-pmb.bin`、`mt7988_wo_0.bin` 與 `mt7988_wo_1.bin` 取自固定 Linux firmware 提交，連同 `LICENCE.mediatek` 與繁體中文來源說明安裝。
- JSON 契約固定每個安裝路徑與 SHA-256；後續完整映像驗證必須逐檔比對，不能只檢查檔名存在。

## 公開散布阻擋

固定 ATF 提交直接連結下列預編譯物件：

| 物件 | SHA-256 |
| --- | --- |
| `plat/mediatek/mt7988/drivers/dram/release/dram.o` | `14bc199bb4d6a39ef330e4547a1e7346aa3759218fbcd289b8c69e5254f421e2` |
| `plat/mediatek/mt7988/drivers/efuse/release/efuse_cmd.o` | `7620ee6b244a06f1347d57122fd20427cdea703130efb53c1964a006e77769a8` |
| `plat/mediatek/mt7988/drivers/efuse/release/plat_efuse.o` | `195eaf1ebaf53ae5a34e3818090827c7cf797249cf6c543302bd44e43203269a` |

儲存庫層級授權不能自動證明這三個二進位物件具有相同的原始碼、重製與再散布權。`check-bananapi-filogic-r4pro-policy.py` 因此要求 `public_distribution_approved=false`、授權狀態維持「未釐清」，並拒絕缺少上述物件清單的契約。授權與來源旁證未完成前，不得移除這個守門。

## 後續建置與驗證

內部完整建置入口：

```bash
./tools/run-bananapi-filogic-r4pro-candidate-isolated-cache.sh
```

既有映像唯讀驗證入口：

```bash
./tools/verify-bananapi-filogic-r4pro-candidate.sh
```

只有完整建置成功，且 IMG／XZ 同一性、GPT、BL2／FIP、U-Boot 禁止字串、合併 DTB、核心設定、韌體雜湊、授權文件、套件與唯讀掛載內容全部通過後，才具備內部 L2 證據。L3 仍需實體板 UART、冷啟動、重啟、SD、網路埠、SFP、MT7996、PCIe、NVMe、USB、I2C、RTC、GPIO、LED、風扇與熱壓力測試。
