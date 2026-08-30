# Banana Pi M7 current 來源與候選守門政策

日期：2026-08-26

## 結論

`bananapim7` current 原先同時追蹤可移動的 Radxa U-Boot branch 與 RKBin `master`。本階段只在 M7 current 固定不可變提交，保留既有板級 DDR v1.11 與 BL31 v1.38；不影響 M7 edge／vendor 或其他 RK3588 板卡。

此修正只建立可重現來源與 L2 候選守門，不能取代實體板冷啟動、記憶體、儲存、網路、無線或加速驗證。

## 固定來源

| 項目 | 來源 | 提交 |
| --- | --- | --- |
| U-Boot | `https://github.com/radxa/u-boot.git` | `39cd993e5d6296635438e84f4576b3a9bf76f86e` |
| RKBin | `https://github.com/armbian/rkbin.git` | `1d3c61008fa823936ae7a59615393f8294b64456` |

U-Boot 固定由 `post_family_config_branch_current` hook 套用，因此 family 載入後的可移動 branch 不會覆蓋它，且 edge／vendor 不會被錯誤綁到 current 提交。

## RKBin 雜湊

| 檔案 | SHA-256 | 候選角色 |
| --- | --- | --- |
| `rk35/rk3588_ddr_lp4_2112MHz_lp5_2736MHz_v1.11.bin` | `61a44b0f53451d228cb30c6330f58fcf5b531ad9900e413fa3dc65747211bc1e` | SD／eMMC `idbloader.img` 的 DDR 輸入 |
| `rk35/rk3588_bl31_v1.38.elf` | `51848cc64e12e0fe82a23e43b4628b5b5805b4ec689b260f27fb409d30d3b30b` | `u-boot.itb` 的 BL31 輸入 |
| `rk35/rk3588_spl_loader_v1.16.113.bin` | `4cc43c2ff29e08b5491b4d52528346aa7da6948128c17e670ff8a000029c9408` | RockUSB／Maskrom 載入器；不宣稱直接寫入 SD／eMMC 主映像 |

上述雜湊已由獨立暫存 clone 在指定 RKBin 提交重新計算，不沿用分支名稱或既有工作樹推論。

## U-Boot 映像布局

M7 使用 `spl-blobs`，SD／eMMC 主映像必須逐段比對：

| 套件 payload | 映像偏移 |
| --- | ---: |
| `idbloader.img` | 32768 bytes |
| `u-boot.itb` | 8388608 bytes |

`rkspi_loader.img` 是獨立 SPI 燒錄產物，不可拿來與主映像的連續區段比較。共用候選驗證器已保留既有 tag／單 payload 相容性，並新增 `commit:`、來源 URL、revision 與多 payload 驗證。

## M7 L2 軟體門檻

- Linux 6.18 `rockchip64` 核心、M7 DTB、initramfs 與板級套件完整封裝。
- SD 4-bit 與 eMMC 8-bit 裝置樹設定符合 M7 DTB。
- GPU、VOP、HDMI0、PCIe、USB host、UART、PWM 與溫度節點處於啟用狀態。
- Panthor／Panfrost、Rockchip DRM、Hantro、RGA、VDEC、NVMe、R8169、BRCMFMAC、Bluetooth、音訊與標準 I/O 核心能力已建置。
- GPIO、I2C、SPI、V4L2、PCIe、NVMe、USB、音訊、網路與無線診斷工具已安裝。
- IMG／XZ、來源提交、設定雜湊、RKBin 證據與 U-Boot 雙 payload 全部通過唯讀守門。

## 不可由 L2 推論

- SD、eMMC、NVMe、SPI-NOR 或 Maskrom 已在實機成功啟動或燒錄。
- 不同記憶體容量在 DDR v1.11 下通過冷啟動與壓力測試。
- 雙 2.5GbE、Wi-Fi 6、Bluetooth 5、USB、HDMI／DP、音訊或相機已可用。
- GPU、VPU、RGA 或 NPU 已完成硬體加速；工具與核心設定存在不是執行證據。
- USB Type-C gadget 或 `g_mass_storage` 已可用。
- 家族通用 overlay 已正確對應 M7 的 40-pin 腳位；官方腳位複用、GRF、電壓域與衝突仍須逐項實機驗證。
