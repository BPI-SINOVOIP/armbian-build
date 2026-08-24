# Banana Pi M4 Berry H618 最佳化十映像交付紀錄

日期：2026-08-25

## 交付結論

BPI-M4 Berry 的 A1 DDR 792 MHz、H618 硬體加速基線與 40-pin 支援已整合到
Armbian `current`。五個發行版的 CLI／XFCE 共十個映像均由
`compile.sh build` 完整建立，不是替換既有映像的 U-Boot。

交付目錄：

```text
output/images/2026.08/bpi-m4berry-a1-h618-optimized-792-matrix/
```

建置來源：

```text
branch=bpi-m4berry-a1-ddr-port-20260823
source_commit=621a0d5b7a83033c0afbc52b988d87c8f0362f4f
kernel=6.18.46-current-sunxi64
u-boot=2025.04
dram_clock_mhz=792
cma_mib=256
```

目錄總量約 38 GiB，其中十個原始映像共約 32 GiB，十個壓縮映像共約
6.3 GiB。所有 `.img`、`.img.xz`、個別 SHA-256、建置中繼資料、建置日誌、
`MATRIX.tsv` 與 `VALIDATION_REPORT.txt` 均保留。

## 映像矩陣

下表列出適合傳輸的 `.img.xz`；原始映像與兩種完整雜湊以同目錄的
`MATRIX.tsv` 為唯一完整清單。

| 發行版 | 類型 | 原始容量 | 壓縮容量 | 壓縮檔 SHA-256 | 壓縮檔名 |
| --- | --- | ---: | ---: | --- | --- |
| Bookworm | CLI | 1.5 GiB | 312 MiB | `fcfff769222e36b462ce5b07eca70b048853ac6c26f7ced8e9d8f76dcf7a2b23` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_bookworm_current_6.18.46_minimal_a1-h618-optimized-792mhz.img.xz` |
| Bookworm | XFCE | 4.8 GiB | 966 MiB | `b0ebdefbab7c4b94461c77f086b8e07873d3a422c08c0921302ef50beff13fdd` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_bookworm_current_6.18.46_xfce_desktop_a1-h618-optimized-792mhz.img.xz` |
| Trixie | CLI | 1.5 GiB | 329 MiB | `c9736ab312dcf87785673e632168982bce29b30df67fa5925dc698e84c52b070` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_trixie_current_6.18.46_minimal_a1-h618-optimized-792mhz.img.xz` |
| Trixie | XFCE | 5.4 GiB | 1.1 GiB | `48bd37d417d21909010d189de514f7d35ba12589936f11ad32d842d4b9fb3a94` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_trixie_current_6.18.46_xfce_desktop_a1-h618-optimized-792mhz.img.xz` |
| Jammy | CLI | 1.5 GiB | 316 MiB | `73fb1caedbb06b0d2af39d2360ccbdc2bf26e57afb002b9fdf2e6ff15d3f7a3a` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_jammy_current_6.18.46_minimal_a1-h618-optimized-792mhz.img.xz` |
| Jammy | XFCE | 4.4 GiB | 911 MiB | `b6f534691512aed3daf09fb7f1b5f9446176cbbc92b2894e7684a3a3d57ac3ee` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_jammy_current_6.18.46_xfce_desktop_a1-h618-optimized-792mhz.img.xz` |
| Noble | CLI | 1.5 GiB | 314 MiB | `3a9dd4429df3eb1b2a07e84c1d31f61a4ce79f4c28bd37e60777de1dc9f7ac0d` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_noble_current_6.18.46_minimal_a1-h618-optimized-792mhz.img.xz` |
| Noble | XFCE | 4.7 GiB | 907 MiB | `342bad86f2233b1108a388850cc57c5510b4ec9d25e68c4945a4960c0c9725ad` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_noble_current_6.18.46_xfce_desktop_a1-h618-optimized-792mhz.img.xz` |
| Resolute | CLI | 1.5 GiB | 329 MiB | `9b8f6a186e473ac763956b39b3c6fb7edb109d9d663f3bcdc5178063eafec9b7` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_resolute_current_6.18.46_minimal_a1-h618-optimized-792mhz.img.xz` |
| Resolute | XFCE | 5.2 GiB | 994 MiB | `7bbac52b04f7cb1b4e2fb90ed47acc974d66b20ec9da88fa49e2b164fc3a2100` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_resolute_current_6.18.46_xfce_desktop_a1-h618-optimized-792mhz.img.xz` |

廚房秤桌面整機的第一優先驗證映像為 Noble XFCE；無桌面的產品服務基線
可先驗證 Noble CLI。其他八個映像用於發行版相容、回歸與客戶選型，不代表
所有發行版都已取得相同的實機時數。

## 離線驗證

完整驗證命令：

```bash
./tools/verify-bpi-m4berry-h618-optimized-matrix.sh
```

工具先驗證十筆矩陣、十個 `.img`、十個 `.img.xz`、無殘留 `.partial`，再
核對二十個 SHA-256 與十個 `xz` 串流。其後逐一以 `losetup --read-only`
及 `mount -o ro,noload` 掛載十個原始映像，檢查：

- `extraargs` 含 `cma=256M`。
- `CONFIG_VIDEO_SUNXI_CEDRUS=y`。
- `CONFIG_SUN50I_H6_PRCM_PPU=y`。
- `CONFIG_DRM_PANFROST=m`。
- `CONFIG_CRYPTO_DEV_SUN8I_CE=m`。
- 核心映像、M4 Berry DTB、PG19 PWM overlay 與 40-pin 文件。
- 硬體盤點工具、相容層安裝器與受控 udev 權限規則。
- 所有映像的 GPIO、I2C、SPI、V4L2 套件。
- XFCE 映像的桌面、GStreamer V4L2 codecs 與 DRM 測試套件。

最終結果為十個映像全部通過，原始輸出位於：

```text
output/images/2026.08/bpi-m4berry-a1-h618-optimized-792-matrix/VALIDATION_REPORT.txt
```

## 已整合功能

- A1 DDR 792 MHz 候選參數，保留原有 2 GiB／4 GiB 容量辨識流程。
- Mali-G31 Panfrost 硬體繪圖；PPU 電源域提供者內建以避免載入競態。
- Cedrus H.264、H.265 8-bit、VP8、MPEG-2 的 H616／H618 600 MHz 能力表。
- M4 Berry 專屬 `cma=256M`，避免 2 GiB 板的 H.265 緩衝配置失敗。
- `sun8i-ce` Crypto Engine、Linux DRM 與 HDMI。
- GPIO 字元裝置、I2C、SPI、UART、PWM overlay 及受控群組權限。
- 固定提交版本的 BPI-WiringPi2／RPi.GPIO 相容安裝路徑。

Linux 啟動後的 HDMI 已可用；目前 U-Boot 2025.04 未啟用 HDMI 顯示。
上游 `VIDEO_SUNXI` 排除 H6 世代，H618 不能只打開設定取得 U-Boot Logo，
仍需獨立移植 DE、TCON、HDMI PHY、時鐘、重設與電源控制。

## 燒錄前檢查

先在交付目錄核對壓縮檔：

```bash
sha256sum -c Armbian-*.img.xz.sha256
xz -t Armbian-*.img.xz
```

燒錄時必須再次確認目標裝置，以下的 `/dev/sdX` 只是假名：

```bash
xz -dc <映像檔名>.img.xz | sudo dd of=/dev/sdX bs=16M oflag=direct status=progress
sync
```

## 證據限制

離線驗證證明映像完整、設定一致且必要檔案與套件存在；它不等同硬體量產
驗證。目前直接實機證據主要來自一片 2 GiB M4 Berry，已涵蓋 Panfrost、
Cedrus 四種格式、AF_ALG Crypto、GPIO、I2C 與舊介面相容層。

仍須由實驗室或客戶完成：

- 2 GiB／4 GiB 各至少三片、每片至少二十次完整斷電冷啟動。
- SD／eMMC 雙路啟動、記憶體與儲存壓力、長時間重新啟動。
- SPI／UART／PWM 實體迴路及完整 40-pin 電氣驗證。
- H.265 10-bit、長時間播放、seek 與客戶實際媒體。
- 720×1280 顯示、電容觸控、條碼機、Wi-Fi、藍牙與電池供電整機測試。
- U-Boot HDMI Logo；完成獨立顯示驅動移植前不得宣稱支援。

完成上述樣本數前，本批映像應標示為「工程驗證候選」，不得標示為量產
通過。
