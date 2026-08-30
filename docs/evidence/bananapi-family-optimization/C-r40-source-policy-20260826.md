# Banana Pi R40 current 來源固定政策

日期：2026-08-26

## 結論

`bananapim2berry` 與 `bananapim2ultra` 共用 Allwinner R40 current 核心、U-Boot、AP6212 無線模組與八項排針 overlay，可以採同一套來源政策與驗證器。兩張板的儲存及周邊節點不同，必須各自完整建置與唯讀驗證，不能把同一 SoC 視為同一映像。

本階段建立 L2 軟體候選；沒有實機證據前，不推論 SD／eMMC 冷啟動、DDR、SATA、網路、無線、顯示、音訊、USB 或排針已通過。

## 固定來源

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| Linux 6.18.46 | `https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git` | `1f99e9ab748fc5c32120de9c4eca31abfe54a4d5` |
| U-Boot 2024.01 | `https://github.com/u-boot/u-boot` | `866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e` |
| Armbian firmware | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |

三個提交只在兩張板的 `current` hook 生效，不修改 `edge`、`legacy` 或其他 Sunxi 板卡。韌體 artifact 同步支援 `ARMBIAN_FIRMWARE_GIT_REF`，使版本計算與實際封裝都解析同一個 commit，避免遠端 `master` 更新後得到不同內容。

## DRAM 政策

兩張板的 U-Boot 上游 defconfig 都使用 576 MHz。本次不新增 `CONFIG_DRAM_CLK` 覆寫，避免在沒有跨批次冷啟動證據時任意升降頻。

576 MHz 只代表可重現的建置設定，不代表任一記憶體顆粒或板卡批次已完成壓力測試。

## 板級差異

| 項目 | M2 Berry | M2 Ultra |
| --- | --- | --- |
| DTB | `sun8i-v40-bananapi-m2-berry.dtb` | `sun8i-r40-bananapi-m2-ultra.dtb` |
| SD | 4-bit | 4-bit |
| AP6212 SDIO | 4-bit、不可移除 | 4-bit、不可移除 |
| eMMC | DTB 未啟用 | 8-bit、不可移除 |
| IR、類比音訊及額外 USB host | DTB 停用 | DTB 啟用 |

目前兩張主線 DTB 都沒有啟用 R40 MUSB OTG 控制器，因此本候選不宣稱 USB gadget 或 OTG。Berry 的停用節點也不列為必須通過的能力。

## 無線韌體

映像固定 BCM43430 Wi-Fi 韌體、校準資料及 BCM43430A1 Bluetooth 韌體。Bluetooth 檔在映像中由 `/lib/firmware/brcm/BCM43430A1.hcd` 連至 AP6212 實體檔案，驗證器對解參照後內容執行 SHA-256。

板級 DTB 驗證 SDIO 電源序列、Broadcom Bluetooth 相容字串與 UART 流量控制；這只證明靜態設定和韌體已封裝，無法取代天線、射頻、配對及吞吐量實測。

## L2 軟體門檻

- 驗證 IMG／XZ 同一性、SHA-256、第一階段開機區及 U-Boot 套件 payload。
- 驗證 Berry 與 Ultra 各自的 DTB model、compatible、儲存匯流排及啟用節點。
- 驗證 SATA、Gigabit Ethernet、Lima、Cedrus、Sun8i crypto、顯示、音訊、無線及標準 I/O 核心設定。
- 驗證 I2C、SPI 與 UART 的八項 R40 overlay 已封裝，且 6.18／7.0 overlay 相容字串沒有誤用 H3。
- 驗證 GPIO、I2C、SPI、V4L2、無線、Bluetooth、SATA、網路、USB 與音訊診斷工具已安裝。

取得實體板後仍需分板執行 UART、冷啟動、重新啟動、儲存壓力、網路、Wi-Fi、Bluetooth、顯示、音訊、USB host、SATA、排針迴路及長時間穩定性測試。
