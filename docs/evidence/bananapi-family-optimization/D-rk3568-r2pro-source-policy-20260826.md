# Banana Pi R2 Pro current 來源與驗證政策

日期：2026-08-26

## 結論

`bananapir2pro` 已具備建立 RK3568 current 候選映像所需的固定來源與 L2 離線守門。此板採用獨立的 `rk3568-bpi-r2-pro` Linux／U-Boot 身分，不得以現有 `bananapicm2.wip` 取代；後者仍是重新命名的 R2 Pro 暫存設定，尚未形成可發布的 CM2 板級支援。

本政策固定 Linux、U-Boot、RKBin 與 Armbian firmware 提交，並逐項驗證 RK3568 DDR、BL31、RockUSB loader、U-Boot 最終設定、GPT、兩段啟動載荷、裝置樹與使用者空間工具。沒有實機證據前，最高只可判定為 L2。

## 固定來源

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| Linux stable | `https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git` | `1f99e9ab748fc5c32120de9c4eca31abfe54a4d5` |
| U-Boot | `https://github.com/u-boot/u-boot` | `866ca972d6c3cabeaf6dbac431e8e08bb30b3c8e` |
| RKBin | `https://github.com/armbian/rkbin` | `46c4793ea2dcea7c8331fce9f07b5c80561a0395` |
| Armbian firmware | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |

RKBin 另以 SHA-256 固定 `rk3568_ddr_1560MHz_v1.21.bin`、`rk3568_bl31_v1.44.elf` 與 `rk356x_spl_loader_v1.21.113.bin`。版本字串中的 1560 MHz 是 Rockchip DDR blob 名稱，不是本次實機記憶體穩定性結論。

## 啟動與分割政策

- U-Boot 使用 `bpi-r2-pro-rk3568_defconfig` 與 `rk3568-bpi-r2-pro.dtb`。
- 映像必須使用 GPT，第一分割區名稱為 `rootfs`，起點為 sector 32768。
- `idbloader.img` 寫入 byte 32768；`u-boot.itb` 寫入 byte 8388608。
- 守門比對套件內 payload 與映像實際位元組，並拒絕超出 16 MiB 保留區的載荷。
- 開機設定採 extlinux，FDT 必須指向 `/boot/dtb/rockchip/rk3568-bpi-r2-pro.dtb`。

## 軟體守門範圍

1. 驗證 SD 4-bit、eMMC 8-bit 與 eMMC 不可移除屬性。
2. 驗證兩個 Gigabit MAC、MT7531 交換器、`lan0` 至 `lan3` 與 CPU 埠標籤。
3. 驗證 SATA、兩個 PCIe 連線、USB host、HDMI、VOP、Panfrost、Hantro、RGA、VDEC、RNG、Crypto、RTC/I2C、UART 與溫度感測節點。
4. 驗證 Linux 核心的 DSA、SATA、MMC、PCIe、DRM、GPU、VPU、USB、GPIO、I2C、SPI 與網路設定。
5. 驗證網路、儲存、PCIe、USB、音訊、GPIO、I2C、SPI、V4L2 與感測工具已安裝。

板級裝置樹把兩個 DWC3 控制器固定為 host，因此本政策不宣稱 USB gadget／OTG。板上也沒有可由此裝置樹證明的整合式 Wi-Fi、Bluetooth 或 NPU，故不加入相關功能聲明。

## L3 實機門檻

- 至少 30 次完整斷電冷啟，保存 UART 全程日誌與失敗率。
- 分別驗證 SD、eMMC、SATA 與可用 PCIe 裝置的啟動或資料路徑。
- 驗證五埠網路、MT7531 DSA、VLAN、橋接、雙向 `iperf3` 與長時間封包負載。
- 驗證 HDMI、GPU、VPU、USB host、音訊、溫度、重新啟動與斷電行為。
- 依原理圖確認實際引出的 GPIO、I2C、SPI、UART 與 PWM，再啟用目前停用的排針節點並逐腳驗證。

本文件只定義可重現來源與驗證門檻，不是實物、量產或硬體相容性證明。
