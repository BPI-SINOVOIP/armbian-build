# Banana Pi CM2 的 BPI-R2 Pro 軟體參考板來源與驗證政策

日期：2026-08-27

## 結論

`bananapicm2` 目前只是 BPI-CM2 的初始移植工作入口。官方資料把 BPI-CM2 定義為使用 CM4 類型板對板連接器的 RK3568 運算模組，把 BPI-R2 Pro 定義為具有五個乙太網路埠、SATA、PCIe、USB 與顯示介面的完整路由器開發板；目前沒有證據證明 BPI-R2 Pro 是可安裝 BPI-CM2 的載板。

原始板檔直接沿用 `bpi-r2-pro-rk3568_defconfig` 與 `rk3568-bpi-r2-pro.dtb`。這只能作為相同 SoC 的軟體參考板，不能證明 CM2 的供電、記憶體、eMMC、乙太網路 PHY、PCIe、USB、顯示、無線模組或連接器接線。先前把 R2 Pro 裝置樹改名成「CM2 搭配 R2 Pro 載板」的做法已撤回，避免建立不存在的硬體身分。

目前只有固定來源、RKBin 雜湊、授權邊界與參考板契約，因此目前稽核層級為內部 L0。沒有可接受的 CM2 元件產物、完整 IMG／XZ、唯讀內容驗證或實體板證據。即使未來參考板映像完成建置與唯讀驗證，也最多只能標示為內部 L1，不得推論 BPI-CM2 可開機或功能可用。

官方參考來源：

- BPI-CM2：`https://docs.banana-pi.org/en/BPI-CM2/BananaPi_BPI-CM2`
- BPI-R2 Pro：`https://docs.banana-pi.org/en/BPI-R2_Pro/BananaPi_BPI-R2_Pro`

## 固定來源

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| Linux stable 6.18.46 | `https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git` | `1f99e9ab748fc5c32120de9c4eca31abfe54a4d5` |
| U-Boot 2024.01 | `https://github.com/u-boot/u-boot` | `866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e` |
| RKBin | `https://github.com/armbian/rkbin` | `46c4793ea2dcea7c8331fce9f07b5c80561a0395` |
| Armbian firmware | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |

2026-08-27 已確認四個提交網址可解析。板檔會在 current 分支覆寫 Linux、U-Boot 與 firmware 的可移動來源，RKBin 擴充也會使用固定提交。

RKBin 受控檔案如下：

| 檔案 | SHA-256 |
| --- | --- |
| `LICENSE.TXT` | `0b37e1522c36cf4579c45dfb138798c3cb5665fcf6302b95377179fbed38e35c` |
| `rk35/rk3568_ddr_1560MHz_v1.21.bin` | `bb19ec7197116d4e12580f947d2b9041876c78f3bdd02e1ab8cd6300c3a8c3de` |
| `rk35/rk3568_bl31_v1.44.elf` | `65110f822fdbdd0163ce2dabc60591e7a8a0ffbc9471780e29eef0062f9ed7b6` |
| `rk35/rk356x_spl_loader_v1.21.113.bin` | `aaa3f13c84275bb864e78b5dec29fcce43dec2898ecac6696a06f14a3dec679e` |

DDR 檔名中的 1560 MHz 只是固定二進位的版本名稱，不是 CM2 記憶體穩定性、實際工作頻率或料號相容性的證據。

## 參考板契約

目前建置入口保留下列 R2 Pro 參考板值：

| 項目 | 參考板值 |
| --- | --- |
| U-Boot defconfig | `bpi-r2-pro-rk3568_defconfig` |
| Linux／U-Boot DTB | `rockchip/rk3568-bpi-r2-pro.dtb` |
| model | `Bananapi-R2 Pro (RK3568) DDR4 Board` |
| compatible | `sinovoip,rk3568-bpi-r2pro`、`rockchip,rk3568` |

validation 中的雙乙太網路、MT7531、SATA、雙 PCIe、USB host、HDMI、GPU 與媒體節點都是 R2 Pro 參考板的內容檢查，不能轉化為 CM2 功能聲明。`carrier_verified=false`、`donor_only_contract=true` 與 `hardware_evidence.present=false` 必須維持到硬體資料和實測證據完成。

## RKBin 授權與發布邊界

固定提交的 `LICENSE.TXT` 授權二進位形式的使用、複製及有限散布，但禁止獨立散布、修改、反編譯或反組譯；二進位只能隨採用 Rockchip 積體電路的平台散布，且必須附上同一授權。板級 BSP 鉤子會把固定雜湊的授權檔安裝到：

```text
/usr/share/doc/armbian-bsp-bananapicm2/rkbin.LICENSE.TXT
```

這只是技術守門，不是法律意見。現階段的參考板身分無法證明成品就是 BPI-CM2 的有效平台映像，因此 `public_release_allowed=false` 與 `public_redistribution_authorized=false`，禁止建立公開發布候選；建置入口在 `PUBLIC_RELEASE=yes` 時必須拒絕執行。

## 證據分級

### 目前 L0

- 四個可移動來源已固定到 40 位元提交。
- RKBin 授權檔與三個預建二進位已逐檔固定 SHA-256。
- 板檔、validation、發布阻擋與回歸測試已建立。
- 先前改名產生的 DTB 與 U-Boot 雜湊不接受為目前契約的元件證據，因為那些產物建立了未證實的 CM2＋R2 Pro 硬體身分。

### 內部 L1 上限

只有在明確標示為參考板的完整映像完成建置，且 IMG／XZ、GPT、U-Boot 載荷、固定來源、RKBin、授權檔、核心來源與唯讀內容全部通過後，才可記為內部 L1。這仍只是 R2 Pro 參考軟體回歸，不是 CM2 支援證明。

### 升級到 CM2 L2 的必要條件

1. 確認實際 CM2 載板型號、版本與原理圖，逐項比對板對板連接器。
2. 依 CM2 模組及載板建立專用 DTS、供電、時鐘、PHY、儲存與 I/O 契約，不得只改參考板的 model 或 compatible。
3. 使用固定來源完成 CM2 專用 U-Boot、DTB、核心與代表性 Armbian 映像。
4. 通過 IMG／XZ 同一性、唯讀內容、來源中繼資料、授權與啟動載荷驗證。
5. 若缺少實體板證據，L2 仍不得聲明可開機或功能可用。

## 實體與發布阻擋

- 未確認 BPI-CM2 實際載板、原理圖、連接器映射與硬體版本。
- 未驗證 DRAM、eMMC、SD、UART、乙太網路、PCIe、USB、顯示、音訊、GPIO、I2C、SPI 或 PWM。
- 未確認 CM2 記憶體與 eMMC 料號矩陣，也沒有冷啟動及壓力測試。
- RKBin 仍需正式發布、再散布及出口管制審查。
- 在上述阻擋解除前，不得對外發布、標示可開機、宣稱通用 CM2 支援或用於量產。

## 暫存與清理安全

CM2 薄入口不使用 `rm -rf`。隔離快取執行器只掛載及卸載工作樹內的 `cache` 目標，不刪除唯讀 cache lower，也不自動刪除 OverlayFS upper。共用驗證器的暫存檔由工作樹內 `.tmp` 的 `mktemp` 建立；清理只針對該單一暫存檔。任何後續人工清理都必須先核對解析後路徑、掛載狀態與工作樹前綴，禁止操作 `/media/pi/SMCI/armbian/bpi-v26.2.1/cache`。
