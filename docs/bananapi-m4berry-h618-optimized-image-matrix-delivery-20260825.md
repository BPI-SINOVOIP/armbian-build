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
source_commit=16144c5c076c984a0fb0892055be34ab4a11b858
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
| Bookworm | CLI | 1.4 GiB | 310.3 MiB | `7aa9b52e1cc1977cdcddcc3bd520310cff01beb1d75bd9940a8b3684828a2ccc` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_bookworm_current_6.18.46_minimal_a1-h618-optimized-792mhz.img.xz` |
| Bookworm | XFCE | 4.7 GiB | 970.2 MiB | `88acb541fff3197942974975f7c40032d4af3a8258502cdc5dde785b528e9b0b` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_bookworm_current_6.18.46_xfce_desktop_a1-h618-optimized-792mhz.img.xz` |
| Trixie | CLI | 1.5 GiB | 328.1 MiB | `d3908358673497290542e1c2341df965ff91021ea49ed7c01429f8ceeaebda63` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_trixie_current_6.18.46_minimal_a1-h618-optimized-792mhz.img.xz` |
| Trixie | XFCE | 5.3 GiB | 1072.4 MiB | `d8de2c8ca5f854378624075eca2937760fb1974b721268af3a934e10492dfb78` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_trixie_current_6.18.46_xfce_desktop_a1-h618-optimized-792mhz.img.xz` |
| Jammy | CLI | 1.4 GiB | 317.2 MiB | `60fe1153ed3880ff81bd04042f40df7d4fb5ac72f7f548fdb9e16e13500316c4` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_jammy_current_6.18.46_minimal_a1-h618-optimized-792mhz.img.xz` |
| Jammy | XFCE | 4.3 GiB | 908.8 MiB | `950336653ceb5826032c5d7bf6b245d14f8d192e31a754433f976a0b2d5d28dd` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_jammy_current_6.18.46_xfce_desktop_a1-h618-optimized-792mhz.img.xz` |
| Noble | CLI | 1.4 GiB | 312.3 MiB | `90367816f6eadd0cc0fe03ce544f97708abfb7a9785e8a95d6d628c33124eb5e` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_noble_current_6.18.46_minimal_a1-h618-optimized-792mhz.img.xz` |
| Noble | XFCE | 4.7 GiB | 906.7 MiB | `b7c9dd812f67a73692b33cd35fd1a04f4949f61f58d4b7cc75a954bd357c7d4a` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_noble_current_6.18.46_xfce_desktop_a1-h618-optimized-792mhz.img.xz` |
| Resolute | CLI | 1.4 GiB | 327.2 MiB | `2bb307b7fb13431ab88fb933fc1c5439e467bbc10a56c9e646f3f0eb80059875` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_resolute_current_6.18.46_minimal_a1-h618-optimized-792mhz.img.xz` |
| Resolute | XFCE | 5.1 GiB | 988.1 MiB | `5780fdf94ef4c4017cd7c858c0fa700daefff087cf64083215e939a8388fcbe3` | `Armbian-unofficial_26.05.0-trunk_Bananapim4berry_resolute_current_6.18.46_xfce_desktop_a1-h618-optimized-792mhz.img.xz` |

一般桌面用途建議優先驗證 Noble XFCE；無桌面服務或嵌入式專案可優先
驗證 Noble CLI。其他八個映像用於發行版相容性與回歸測試，不代表
所有發行版都已取得相同的實機驗證時數。

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
- `CONFIG_RTW88_8821CU=m`、模組與 USB `0bda:c820` 別名存在。
- 映像沒有將主線 `rtw88_8821cu` 驅動加入黑名單。
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
- 內建 RTL8821CU 改用核心主線 `rtw88_8821cu`，移除舊外掛驅動黑名單。
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
驗證。目前直接實機證據來自一片 2 GiB M4 Berry，已涵蓋 768 MiB
`memtester`、10 分鐘 CPU／記憶體壓力、Panfrost、Cedrus 四種格式、
AF_ALG Crypto、SD／eMMC 唯讀效能、RTL8821CU 重啟自動載入與 Wi-Fi 雙頻掃描。
實測四核最高設定為 1416000 kHz，現版未證明可達 1.5 GHz。

仍須由實驗室或客戶完成：

- 2 GiB／4 GiB 各至少三片、每片至少二十次完整斷電冷啟動。
- SD／eMMC 雙路啟動、記憶體與儲存壓力、長時間重新啟動。
- SPI／UART／PWM 實體迴路及完整 40-pin 電氣驗證。
- H.265 10-bit、長時間播放、尋址與多種實際媒體。
- 目標顯示模式、USB 輸入／資料周邊、Wi-Fi 連線傳輸、Bluetooth／BLE 配對與供電情境測試。
- 高於 1.416 GHz 的 CPU OPP、電壓、散熱與長時間穩定性。
- U-Boot HDMI Logo；完成獨立顯示驅動移植前不得宣稱支援。

完成上述樣本數前，本批映像應標示為「工程驗證候選」，不得標示為量產
通過。
