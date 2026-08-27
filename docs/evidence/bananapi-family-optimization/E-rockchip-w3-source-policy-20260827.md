# Banana Pi W3 RK3588 vendor 來源與候選守門政策

日期：2026-08-27

## 結論

Banana Pi W3 原板檔直接繼承 ArmSoM W3，U-Boot 也使用 ArmSoM defconfig，因此板級來源、身分與驗證邊界不完整。本候選改為自足板檔，並以 Banana Pi 專屬 Linux／U-Boot DTS wrapper 及專用 defconfig 固定板級身分。

目前只建立 L2 軟體候選；本次未建完整映像，也尚未建立實體板 L3 證據。沒有實機冷啟動與介面測試前，不得宣稱硬體介面已通過。

## 固定來源

| 元件 | 來源 | 提交 |
| --- | --- | --- |
| Linux 6.1.115 | `https://github.com/armbian/linux-rockchip.git` | `c6157104418d012823413c02f9222f3fe123dd25` |
| U-Boot 2017.09 | `https://github.com/radxa/u-boot.git` | `39cd993e5d6296635438e84f4576b3a9bf76f86e` |
| RKBin | `https://github.com/armbian/rkbin` | `1d3c61008fa823936ae7a59615393f8294b64456` |

RKBin 的 DDR v1.11、BL31 v1.38、RockUSB loader 與 `LICENSE.TXT` 均由 SHA-256 固定。候選映像還必須安裝相同雜湊的 RKBin 授權檔，讓二進位來源與授權可由唯讀映像守門核對。

## 板級身分與啟動鏈

- Linux DTB：`rockchip/rk3588-bananapi-w3.dtb`
- U-Boot defconfig：`bananapi-w3-rk3588_defconfig`
- U-Boot DTS：`rk3588-bananapi-w3.dts`
- `idbloader.img` 映像偏移：32768 bytes
- `u-boot.itb` 映像偏移：8388608 bytes
- GPT 第一分割區起點：32768 sectors

驗證器必須在 U-Boot payload 找到 `Banana Pi W3` 與 `bananapi,bpi-w3`，並拒絕仍帶有原始 ArmSoM model 的 payload。Linux DTB 同樣必須具有 Banana Pi model 與相容字串。

## L2 軟體守門

- 固定 Linux、U-Boot 與 RKBin 的來源、ref、實際提交及 blob 雜湊。
- 比對 GPT、兩段 U-Boot payload、套件中繼資料、核心設定、DTB 身分與板級工具。
- 驗證 SD、SDIO Wi-Fi、eMMC 的 4／4／8-bit 契約。
- 驗證 PCIe、NVMe、USB host、USB Type-C OTG、SPI-NOR、RTC、音訊、HDMI、HDMI 輸入、GPU、VPU、RGA 與 NPU 的靜態 DT／核心條件。
- 納入 GPIO、I2C、SPI、V4L2、USB、PCIe、NVMe、網路、音訊、Wi-Fi 與 Bluetooth 診斷工具。

## 後續執行入口

一般建置入口：

```bash
./tools/build-bananapi-rockchip-w3-candidate.sh
```

使用唯讀下層快取與專用 OverlayFS 的建置入口：

```bash
./tools/run-bananapi-rockchip-w3-candidate-isolated-cache.sh
```

完整映像產生後的唯讀驗證入口：

```bash
./tools/verify-bananapi-rockchip-w3-candidate.sh
```

## 證據限制

- 沒有實體板冷啟動、重新上電、重啟與長時間運作證據。
- 沒有不同 RAM／eMMC 組合、SD、eMMC、NVMe 或 SPI-NOR 實機啟動證據。
- 沒有雙 2.5GbE、AP6256 Wi-Fi／Bluetooth、USB、Type-C OTG、HDMI／DP、HDMI 輸入、音訊與 RTC 實測證據。
- 核心設定與啟用節點不能證明 GPU、VPU、RGA 或 NPU 已完成硬體加速。
- 40-pin GPIO、I2C、SPI 與 PWM 仍需依正式 pin map 做衝突、電壓域及實體量測驗證。
