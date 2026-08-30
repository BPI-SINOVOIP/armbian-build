# Banana Pi M2 Magic A33 current 來源固定政策

日期：2026-08-26

## 結論

`bananapim2magic` 可在 current 建立受控的 A33 L2 軟體候選。此階段固定 Linux、U-Boot 與 AP6212 韌體來源，驗證 SD、SDIO Wi-Fi、8-bit eMMC、Bluetooth UART、USB host／OTG、音訊、Lima GPU、Cedrus VPU、Crypto 與基本 I/O 軟體能力。

主線 DTB 明確停用顯示引擎與 DSI，板卡沒有 HDMI；目前也沒有 A33 排針 overlay。因此候選不宣稱顯示輸出、SPI 裝置、完整排針或任何實機硬體功能已通過。

## 固定來源

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| Linux 6.18.46 | `https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git` | `1f99e9ab748fc5c32120de9c4eca31abfe54a4d5` |
| U-Boot 2024.01 | `https://github.com/u-boot/u-boot` | `866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e` |
| Armbian firmware | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |

三個提交只在 `bananapim2magic` 的 `current` hook 生效，不修改 `edge`、`legacy` 或其他 Sunxi 板卡。

## 啟動與 DRAM 政策

固定 U-Boot defconfig 使用 600 MHz DRAM，並啟用 SD、額外 MMC slot、EHCI、OHCI、MUSB gadget 與 USB mass-storage 功能。本次不覆寫 DRAM 參數；600 MHz 只代表可重現設定，不代表任一記憶體批次已完成冷啟動或壓力測試。

## 驗證器語意

A33 的 GPU、VPU 與 Crypto 節點省略 `status` 屬性；依 Device Tree 慣例，省略代表節點可用。Sunxi 驗證器新增 `required_present_nodes`：節點必須存在，且 `status` 只能省略、為 `ok` 或 `okay`，任何 `disabled` 或失敗狀態都會拒絕候選。既有 `required_status_nodes` 仍要求明確的 `status = "okay"`，兩種語意不混用。

## L2 軟體門檻

- 驗證 IMG／XZ 同一性、第一階段開機區及 U-Boot 套件 payload。
- 驗證 A33 M2 Magic DTB model、compatible、SD／SDIO／eMMC、USB、Bluetooth UART、音訊、GPU、VPU 與 Crypto 節點。
- 驗證 AP6212 Wi-Fi、Bluetooth 韌體與校準資料雜湊。
- 驗證 Lima、Cedrus、音訊、USB gadget mass-storage、GPIO、I2C 與基礎核心設定。
- 驗證無線、Bluetooth、音訊、USB、GPIO、I2C、SPI 與 V4L2 診斷工具已安裝。

取得實體板後仍需執行 UART、冷啟動、SD／eMMC、無線、Bluetooth、USB host／OTG、音訊、GPU、VPU、排針及長時間穩定性測試。顯示與排針功能必須先補齊裝置樹或 overlay，再建立新候選，不能由本次 L2 結果推論。
