# Banana Pi M1 Super 元件建置證據

## 結論

2026-08-27 已使用固定 Linux、U-Boot 與 RKBin 提交，成功建置 Banana Pi M1 Super 專屬 Linux DTB、U-Boot SPL、U-Boot DTB、FIT 與 `idbloader.img`。建置結果證明專屬 DTS 與 defconfig 能由固定元件來源產生，不代表板子已開機，也不代表完整 Armbian 修補佇列、根檔案系統或映像已通過。

本次依工作範圍不建置完整根檔案系統映像、不接觸實體板。依全系列稽核邊界，本結果是 `L1 元件候選`；候選維持 `.wip` 與禁止公開發布狀態。

五個建置產物與 RKBin 授權檔已保存於 `output/components/2026.08/bananapi-rockchip-rk3528-m1super-vendor`。可攜清單 SHA-256 為 `ef452fbc47115ffc34359c44a202733217ff32e95d946c160f8e4ea1ebc3b22a`，可由 `tools/verify-bananapi-rockchip-m1super-components.sh` 獨立核對。該目錄不含 Linux、U-Boot 或 RKBin 原始碼與建置樹。

`./compile.sh inventory BOARD=bananapim1super BRANCH=vendor` 已成功，證明矩陣解析器可辨識此 `.wip` 板與 vendor 分支。inventory 只展開頂層欄位，不執行板檔 hook，因此來源覆寫另由政策守門與聚焦測試檢查，不能只憑 inventory 結果宣稱完整建置設定已通過。

## 固定輸入

| 元件 | 固定提交或檔案 | SHA-256 |
| --- | --- | --- |
| Linux | `c6157104418d012823413c02f9222f3fe123dd25` | 不適用 |
| U-Boot | `39cd993e5d6296635438e84f4576b3a9bf76f86e` | 不適用 |
| RKBin | `1d3c61008fa823936ae7a59615393f8294b64456` | 不適用 |
| RKBin 授權 | `LICENSE.TXT` | `0b37e1522c36cf4579c45dfb138798c3cb5665fcf6302b95377179fbed38e35c` |
| DDR 載荷 | `rk35/rk3528_ddr_1056MHz_v1.09.bin` | `add4338762e67bf1ff151e06977502d98ec0021c217e4bdfb9a54273d0fc289d` |
| BL31 載荷 | `rk35/rk3528_bl31_v1.17.elf` | `a93b45eb04c6d05aacff1991f4941bf2704a88b77d37b8f7084c58cd8e2c3948` |
| RockUSB 載荷 | `rk35/rk3528_spl_loader_v1.07.104.bin` | `a45957495e21136736f312b94ada0f859ec4fad432f2810283da35f027ec41ca` |

## Linux DTB

把候選 DTS 放入固定 Linux 原始碼後，使用下列命令建置：

```bash
make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- rockchip_linux_defconfig
make -j20 ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- \
  rockchip/rk3528-bananapi-m1-super.dtb
```

產物結果：

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| `arch/arm64/boot/dts/rockchip/rk3528-bananapi-m1-super.dtb` | 100436 | `68c0d6c27d2802abee0b7ab4b0569581048b14fc651d55c103392d81e00f2eb6` |

`fdtget` 驗證 model 為 `Banana Pi M1 Super`，compatible 依序為 `bananapi,bpi-m1-super`、`armsom,sige1`、`rockchip,rk3528`。靜態節點檢查確認 SD、eMMC、SDIO、雙乙太網路相關控制器、HDMI、GPU、VPU、USB、I2C0、I2C1 與 SPI0 為啟用狀態；這些結果只驗證 DTB 內容，不等於實體功能通過。

## U-Boot 元件

把候選 U-Boot DTS 與 defconfig 放入固定 U-Boot 原始碼後，使用固定 RKBin BL31 建置：

```bash
make ARCH=arm CROSS_COMPILE=aarch64-linux-gnu- \
  bananapi-m1-super-rk3528_defconfig
make -j20 ARCH=arm CROSS_COMPILE=aarch64-linux-gnu- \
  BL31=../rkbin/rk35/rk3528_bl31_v1.17.elf \
  spl/u-boot-spl.bin u-boot.dtb u-boot.itb
tools/mkimage -n rk3528 -T rksd \
  -d ../rkbin/rk35/rk3528_ddr_1056MHz_v1.09.bin:spl/u-boot-spl.bin \
  idbloader.img
```

產物結果：

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| `spl/u-boot-spl.bin` | 247972 | `43c518cf0f5c98c7228d22920c47d5d22e151536fa8e8a984b3522d76b2430be` |
| `u-boot.dtb` | 7684 | `b5bdc6143f8a3d2462e12a5a943c0953e85bb7beb9ac499b3d9552540dce9a81` |
| `u-boot.itb` | 1324032 | `7d095910efac37607dbb65389603aa672b77492c4557f5637ab4ad5a68272f6c` |
| `idbloader.img` | 313344 | `513c843f4cb97c3a62508d5b1238b676e29a997eaeeb382a61b808a3198e2c3c` |

`u-boot.dtb` 的 model 與 compatible 與 Linux DTB 一致；`u-boot.itb` 可找到 Banana Pi 專屬身分，且未找到 `Hinlink H28K`。`idbloader.img` 由指定 DDR 載荷與本次 SPL 組合產生，沒有修改 RKBin 二進位。

## 候選入口

```bash
# 固定來源、授權與發布阻擋守門
./tools/check-bananapi-rockchip-m1super-policy.py

# 核對已保存的可攜元件證據
./tools/verify-bananapi-rockchip-m1super-components.sh

# 未來需要完整候選時，使用專屬 OverlayFS 隔離快取建置
./tools/run-bananapi-rockchip-m1super-candidate-isolated-cache.sh

# 對建置結果執行雜湊、分割區、唯讀掛載、DTB 與 U-Boot 載荷驗證
./tools/verify-bananapi-rockchip-m1super-candidate.sh
```

本次沒有執行完整映像建置與驗證入口。`CACHE_OVERLAY_ROOT` 固定在本工作樹的 `.tmp/bananapi-rockchip-m1super-cache-overlay`，避免建置程序寫入共用下層快取；清理仍只能針對這個候選專用路徑。

## 診斷套件

| 範圍 | 預載候選工具 | 使用目的 |
| --- | --- | --- |
| GPIO | `gpiod`、`python3-libgpiod` | 列舉 GPIO 晶片、讀寫線路與撰寫可重現測試。 |
| I2C | `i2c-tools` | 列舉匯流排、掃描位址及讀寫暫存器。 |
| SPI | `spi-tools`、`python3-spidev` | 執行 SPI 傳輸與迴路測試。 |
| SD／eMMC | `mmc-utils` | 讀取 eMMC EXT_CSD 與檢查裝置能力；破壞性命令仍須人工核准。 |
| 有線網路 | `ethtool`、`iproute2`、`iperf3` | 驗證鏈路、介面設定與吞吐。 |
| Wi-Fi／藍牙 | `iw`、`rfkill`、`wireless-regdb`、`bluez`、`bluez-tools` | 驗證射頻封鎖、介面枚舉、掃描與藍牙控制器。 |
| GPU／顯示 | `mesa-utils`、`kmscube`、`libdrm-tests` | 檢查 EGL／OpenGL ES、KMS／DRM 裝置與顯示路徑。 |
| VPU | `v4l-utils`、`ffmpeg`、`vainfo` | 列舉影音節點及辨識可用硬體加速介面；不能只靠軟體解碼成功判定 VPU 通過。 |
| USB／輸入 | `usbutils`、`usb-modeswitch`、`evtest` | 檢查 USB 枚舉、模式切換與輸入事件。 |
| 聲音／感測 | `alsa-utils`、`lm-sensors` | 檢查音訊裝置與可公開的溫度／感測介面。 |

## 已知限制

- 本次元件建置沒有套用 Armbian 的完整 U-Boot 修補佇列，因此 `armbian_uboot_patch_stack_complete=false`。共用的 RK3566 SoC 名稱修補在固定 U-Boot 提交上需要重整，雖與 RK3528 板級實作無直接關係，仍必須在完整候選建置前處理。
- 沒有建立或驗證完整根檔案系統映像，`full_rootfs_image_built=false`。
- 沒有執行上電、UART、冷啟動、SD、eMMC、網路、Wi-Fi、藍牙、GPU、VPU、HDMI、USB 或 40-pin 實機驗證，`hardware_tested=false`。
- Wi-Fi 量產料號證據仍互相矛盾，不能把 `ap6275s` 候選值視為跨板次結論。
- RKBin 只能依其授權條款隨 Rockchip 平台、以未修改形式並附授權檔散布；Armbian 韌體逐檔授權稽核尚未完成。

以上任一限制未解除前，validation 必須維持 `candidate_public_release_approved=false`。
