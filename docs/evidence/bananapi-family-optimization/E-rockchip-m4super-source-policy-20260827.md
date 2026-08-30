# Banana Pi M4 Super L0 donor-only 來源政策

日期：2026-08-27

## 結論

`bananapim4super.wip` 目前只是一份 L0 donor-only 研究入口。板檔仍使用 ArmSoM Sige3 的 `rk3568-armsom-sige3.dtb` 與 `armsom-sige3-rk3568_defconfig`，用途是固定來源並列出差異，不能視為 Banana Pi M4 Super 的板級支援。

本契約只允許 L0。不得宣稱 L1、L2、M4 Super 專屬 DTS、專屬 U-Boot defconfig、元件建置、完整映像、開機、周邊、硬體相容或公開發布已完成。

## 固定研究來源

| 元件 | 來源 | 固定提交 | L0 用途 |
| --- | --- | --- | --- |
| Linux vendor 6.1 | `https://github.com/armbian/linux-rockchip.git` | `c6157104418d012823413c02f9222f3fe123dd25` | 檢查 Sige3 donor 裝置樹 |
| Radxa/Rockchip U-Boot | `https://github.com/radxa/u-boot.git` | `39cd993e5d6296635438e84f4576b3a9bf76f86e` | 檢查 Sige3 donor 啟動設定 |
| RKBin | `https://github.com/armbian/rkbin` | `1d3c61008fa823936ae7a59615393f8294b64456` | 記錄 donor 所參照的 DDR、BL31 與 RockUSB 路徑 |
| Armbian firmware | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` | 後續盤點韌體來源與授權 |

固定提交只提供可追溯的研究起點，不證明這些來源可產生 M4 Super 元件或映像，也不證明 Sige3 與 M4 Super 硬體等效。

## 已知矛盾

1. 官方 M4 Super 頁面記錄無線模組為 `SYN43752`，Sige3 donor DTS 記錄為 `ap6275s`，正規化名稱是 `AP6275S`。兩者不同，尚未取得 M4 Super 原理圖、模組接線、韌體檔案與實物探測結果來關閉差異。
2. 同一官方頁面的硬體規格表寫 `PCIe 3.0 x1`，產品比較表則寫 `PCIe 3.0 x2`；Sige3 donor DTS 又包含 `pcie2x1` 與 `pcie3x2` 描述。lane 數、連接器路由、電源與重置信號尚未確認。
3. PMIC、USB Type-C PD、SD、eMMC、雙乙太網路、HDMI、MIPI DSI、USB、UART 與 40-pin mux 都尚未完成原理圖逐項比對。

官方來源：`https://docs.banana-pi.org/en/BPI-M4S/BananaPi_BPI-M4S`。

## 已撤回項目

- 撤回只包含 Sige3 DTS 並改寫 `model`、`compatible` 的 M4 Super Linux 與 U-Boot DTS。
- 撤回只更換預設 DTB 與提示字串的 M4 Super U-Boot defconfig。
- 撤回未經原理圖、電壓、pinmux、衝突與實物驗證的 I2C3、I2C5、SPI2 overlay 及其 Makefile 補丁。
- 撤回 M4 Super 完整映像建置、隔離快取建置與 L2 驗證入口，避免一般驗證器把 donor 映像說成 M4 Super 成品。

## 授權與發布邊界

RKBin 只記錄固定提交與 donor 參考路徑。本階段沒有可發布的 RKBin 元件包或映像；若後續實際散布，仍須遵守平台隨附、禁止獨立散布及附帶授權副本等條件。Armbian firmware 的實際收錄檔案尚未知，必須等真實映像存在後逐檔盤點授權。

`donor_hardware_equivalence_verified=false`、`component_build_completed=false`、`full_image_built=false`、`hardware_validated=false` 與 `public_release_allowed=false` 都是強制阻擋條件。

## 升級條件

在升級到 L1 前，至少必須取得可追溯的 M4 Super 原理圖與 PCB 版本，完成 Sige3 donor 的供電、高速介面、無線模組、儲存、UART 與 40-pin 逐項差異表，據此建立真正的板級 DTS 與 U-Boot 設定。之後還要保留乾淨建置記錄及真實產物，才能另行審查元件或完整映像證據。

本文件是差異管理與禁止聲明政策，不是建置或硬體證據。
