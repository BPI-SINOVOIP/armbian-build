# Banana Pi M2 A31s current 來源固定政策

日期：2026-08-26

## 結論

`bananapim2` 可在 current 建立受控的 A31s L2 軟體候選。此階段固定 Linux、U-Boot 與 AP6210 韌體來源，驗證主線 DTB 已明確提供的 SD、SDIO Wi-Fi、Gigabit Ethernet、USB host、IR、UART 與基本 I/O 能力。

主線 DTB 沒有足夠設定可證明顯示、音訊、eMMC、Bluetooth、OTG、USB gadget、GPU、VPU 或完整排針功能。本政策不以產品規格頁或舊版 BSP 推論 current 成品已支援這些功能。

## 固定來源

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| Linux 6.18.46 | `https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git` | `1f99e9ab748fc5c32120de9c4eca31abfe54a4d5` |
| U-Boot 2024.01 | `https://github.com/u-boot/u-boot` | `866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e` |
| Armbian firmware | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |

三個提交只在 `bananapim2` 的 `current` hook 生效，不修改 `legacy` 或其他 Sunxi 板卡。

## DRAM 政策

A31s 的上游 U-Boot defconfig 使用 432 MHz。本次不新增 `CONFIG_DRAM_CLK` 覆寫；432 MHz 只代表可重現的建置設定，不代表任一板卡或記憶體批次已完成冷啟動與壓力測試。

## 無線與周邊邊界

映像固定 AP6210 所需的 BCM43362 韌體及 NVRAM，並驗證 SDIO 控制器、不可移除屬性、電源序列與 Broadcom Wi-Fi 相容字串。DTB 沒有 Bluetooth 節點，因此不安裝 Bluetooth 專用工具，也不宣稱 Bluetooth 可用。

`OVERLAY_PREFIX` 維持 `sun6i-a31s`，但目前共用核心沒有可供本板驗證的 A31s overlay；L2 守門只檢查核心 I2C、SPI、GPIO 與 UART 能力及使用者空間工具，不推論排針腳位已完成映射。

## L2 軟體門檻

- 驗證 IMG／XZ 同一性、第一階段開機區及 U-Boot 套件 payload。
- 驗證 A31s M2 DTB model、compatible、SD／SDIO 匯流排及啟用節點。
- 驗證 Ethernet、USB host、IR、Wi-Fi、Crypto、GPIO、I2C、SPI 與 UART 核心設定。
- 驗證 BCM43362 韌體及 NVRAM 雜湊。
- 驗證網路、USB、GPIO、I2C、SPI 與 V4L2 診斷工具已安裝。

取得實體板後仍需執行 UART、冷啟動、重新啟動、SD、網路、Wi-Fi、USB host、IR、排針迴路及長時間穩定性測試。未補齊主線 DTB 與驅動前，不應把舊 BSP 的周邊能力列入 current 發布聲明。
