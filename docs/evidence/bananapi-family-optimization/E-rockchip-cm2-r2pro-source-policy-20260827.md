# Banana Pi CM2 搭配 BPI-R2 Pro 載板來源與驗證政策

日期：2026-08-27

## 結論

`bananapicm2` 本次建立的是「Banana Pi BPI-CM2 模組搭配 Banana Pi BPI-R2 Pro 載板」的 current 軟體候選，不是通用 BPI-CM2 映像。倉庫原有板檔直接使用 `bpi-r2-pro-rk3568_defconfig` 與 `rk3568-bpi-r2-pro.dtb`，能證明的板級接線只有 R2 Pro 載板；其他 CM2 載板可能有不同的供電、儲存、乙太網路、PCIe、USB、顯示及排針配置，禁止由本候選推論相容。

本次已為此組合建立專用 Linux DTB、U-Boot DTS、U-Boot defconfig、固定來源、RKBin 雜湊、授權安裝、validation 契約及薄入口。只完成靜態、元件編譯與補丁套用證據，沒有執行完整映像建置，也沒有實體板測試，因此目前是 L2 待建候選，不得標示為已完成 L2、可開機、可量產或通用 CM2 支援。

## 固定身分

| 項目 | 固定值 |
| --- | --- |
| Armbian 板號 | `bananapicm2` |
| 模組 | `Banana Pi BPI-CM2` |
| 載板 | `Banana Pi BPI-R2 Pro carrier board` |
| Linux DTB | `rockchip/rk3568-bpi-cm2-r2pro-carrier.dtb` |
| U-Boot defconfig | `bpi-cm2-r2pro-carrier-rk3568_defconfig` |
| model | `Banana Pi CM2 module on BPI-R2 Pro carrier board` |
| 首要 compatible | `sinovoip,rk3568-bpi-cm2-r2pro-carrier` |

專用 DTS 只包含既有 `rk3568-bpi-r2-pro.dts` 並覆寫根節點身分，沒有宣稱已掌握模組與載板之間尚未公開或尚未比對的硬體差異。保留 R2 Pro 相容字串是為了表達本候選的載板硬體基礎，不表示 R2 Pro 單板映像已改名為 CM2 映像。

## 固定來源

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| Linux stable | `https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git` | `1f99e9ab748fc5c32120de9c4eca31abfe54a4d5` |
| U-Boot | `https://github.com/u-boot/u-boot` | `866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e` |
| RKBin | `https://github.com/armbian/rkbin` | `46c4793ea2dcea7c8331fce9f07b5c80561a0395` |
| Armbian firmware | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |

RKBin 受控檔案如下：

| 檔案 | SHA-256 |
| --- | --- |
| `LICENSE.TXT` | `0b37e1522c36cf4579c45dfb138798c3cb5665fcf6302b95377179fbed38e35c` |
| `rk35/rk3568_ddr_1560MHz_v1.21.bin` | `bb19ec7197116d4e12580f947d2b9041876c78f3bdd02e1ab8cd6300c3a8c3de` |
| `rk35/rk3568_bl31_v1.44.elf` | `65110f822fdbdd0163ce2dabc60591e7a8a0ffbc9471780e29eef0062f9ed7b6` |
| `rk35/rk356x_spl_loader_v1.21.113.bin` | `aaa3f13c84275bb864e78b5dec29fcce43dec2898ecac6696a06f14a3dec679e` |

DDR 檔名中的 1560 MHz 只是固定二進位的版本名稱，不是 CM2 記憶體穩定性、實際工作頻率或所有記憶體料號相容性的結論。

## RKBin 授權邊界

固定提交的 `LICENSE.TXT` 允許以二進位形式複製與散布，但限制只能搭配採用 Rockchip 積體電路的平台，禁止獨立散布及修改，並要求散布時附上授權副本。板級 BSP 建置鉤子會把相同雜湊的授權檔安裝到：

```text
/usr/share/doc/armbian-bsp-bananapicm2/rkbin.LICENSE.TXT
```

validation 明確設定 `rkbin_standalone_redistribution_authorized=false` 與 `rkbin_distribution_review_required=true`。這些守門只能防止遺漏授權及來源漂移，不取代正式法務或出口管制審查。

## 啟動與映像契約

- 映像使用 GPT，第一分割區名稱為 `rootfs`，起點為 sector 32768。
- `idbloader.img` 預定寫入 byte 32768；`u-boot.itb` 預定寫入 byte 8388608。
- U-Boot 必須由專用 defconfig 產生，預設 DTB 與 extlinux FDT 都必須指向 CM2＋R2 Pro 載板專用檔名。
- Linux 與 U-Boot 的 model、compatible 必須同時表達模組與載板，不接受只顯示 R2 Pro 或通用 CM2。
- 完整映像守門還必須核對 SD 4-bit、eMMC 8-bit、雙乙太網路、MT7531、SATA、雙 PCIe、USB host、HDMI、GPU、媒體節點、核心設定及標準 I/O 工具。

## 已完成的元件證據

以固定 Linux 提交直接編譯專用 DTB，已通過 model 與三個 compatible 檢查；該次元件產物 SHA-256 為 `173361fb91da6893e393742c96ef6737dd73e5e636224292e7c204f103a05bfa`。此雜湊沒有包含完整 Armbian 核心補丁與成品設定，只是 DTS 可編譯證據，不能填入未來完整映像的 `dtb_sha256`。

U-Boot 專用補丁以固定提交為基準產生，新增專用 DTS、defconfig 及 Makefile 目標。補丁已通過 `git apply --check`，並使用固定 RKBin 元件完成專用 defconfig 編譯：

| 元件產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| `idbloader.img` | 178176 | `463735988e09f1a7dc4a919c8b04043fda4b6980cf45a86b4a28b4b0536d0027` |
| `u-boot.itb` | 1154048 | `1894a86afdadaf679b29b4bca188937a0f6d1dbc19e3f1287b41eb94d270fc5c` |
| `u-boot.bin` | 895808 | `31ec5fcebcb92eb37e62d9953ec0b3e6b23d659bd789635b7b6e7cba9af00e0d` |
| U-Boot 專用 DTB | 79064 | `c5bf41c707ada3925b389c560f22d2bb0bbcf9aef598607ccadb97286578bfef` |

編譯器提示缺少選用的 `tee-os`，本候選沒有設定 OP-TEE，也不宣稱具備可信任執行環境。上述雜湊只是此次固定來源元件證據；完整 Armbian 建置可能因時間戳與打包設定產生不同雜湊，必須由成品守門重新記錄。完整映像建置前不得把元件編譯成功解讀為實體板啟動成功。

## L2 完成門檻

1. 使用專用 OverlayFS 入口執行 Trixie current minimal CLI 完整乾淨建置。
2. 確認 Armbian 補丁狀態沒有 `needs_rebase`、未套用或模糊套用問題。
3. 固定成品 DTB SHA-256，核對 model、compatible、節點與匯流排契約。
4. 核對 IMG／XZ 同一性、GPT、兩段 U-Boot 載荷、固定來源、RKBin、映像內授權檔、核心來源與必要套件。
5. 保存建置提交、來源樹、政策雜湊、產物雜湊、完整日誌與唯讀驗證結果。

## L3 實體門檻

- 使用確認過型號與記憶體配置的 BPI-CM2，實際安裝在 BPI-R2 Pro 載板。
- 執行至少 30 次完整斷電冷啟並保存 UART 全程日誌與失敗率。
- 分別驗證 SD、eMMC、SATA、兩個 PCIe、所有 USB host、HDMI、GPU 與媒體路徑。
- 驗證雙 GMAC、MT7531 的 WAN／LAN0 至 LAN3、VLAN、橋接及長時間網路負載。
- 依 CM2 與 R2 Pro 載板原理圖逐腳核對 GPIO、I2C、SPI、UART、PWM 與供電控制。

任何其他 CM2 載板都必須建立自己的 DTS、供電與 I/O 契約及實體證據，不能沿用本候選的 L2 或 L3 結論。
