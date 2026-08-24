# Banana Pi M4 Berry H618 加速與 40-pin 驗證紀錄

日期：2026-08-25

## 結論

目前 2 GiB BPI-M4 Berry 實機已證明下列路徑可用：

| 功能 | 結果 | 直接證據 |
| --- | --- | --- |
| Mali-G31 | Panfrost 硬體繪圖成立 | `GL_RENDERER` 為 `Mali-G31 (Panfrost)` |
| H.264 | 1080p30、180 幀完成 | 180 次 VE 中斷，約 7.1 秒 |
| H.265 8-bit | 1080p30、180 幀完成 | 180 次 VE 中斷；無 B-frame 約 6.2 秒 |
| VP8 | 1080p30、180 幀完成 | 180 次 VE 中斷，約 6.1 秒 |
| MPEG-2 | 1080p30、180 幀完成 | 180 次 VE 中斷，約 6.5 秒 |
| Crypto | AES-256-CBC AF_ALG 往返一致 | 4096 次請求對應 4096 次 CE 中斷 |
| GPIO／I2C | 一般使用者可存取 | `root:users 0660`，`gpiodetect` 通過 |
| BPI-WiringPi2 | 板型與 40-pin 讀取通過 | `gpio -v`、`gpio readall` 正常 |
| RPi.GPIO | M4 Berry 映射可用 | 實體 pin 7 輸入讀取及清理成功 |

這些結果證明核心驅動與使用者空間路徑成立，但不等同多板、4 GiB、量產或
所有外接裝置通過。

## 視訊解碼

核心的 H616/H618 Cedrus 變體原本誤用 H6 能力表。修正後執行中時脈為：

```text
ve=600000000
bus-ve=200000000
mbus-ve=400000000
```

固定樣本結果：

| 格式 | 樣本 | 退出碼 | VE 中斷增量 | 耗時 |
| --- | --- | ---: | ---: | ---: |
| H.264 High | 1920x1080、30 fps、6 秒 | 0 | 180 | 7.1 秒 |
| H.265 Main | 1920x1080、30 fps、含 B-frame | 0 | 180 | 55.4 秒 |
| H.265 Main | 1920x1080、30 fps、無 B-frame | 0 | 180 | 6.2 秒 |
| VP8 profile 0 | 1920x1080、30 fps、6 秒 | 0 | 180 | 6.1 秒 |
| MPEG-2 Main | 1920x1080、30 fps、6 秒 | 0 | 180 | 6.5 秒 |

H.265 含 B-frame 雖完成全部幀，但 GStreamer 顯示需要複製缺少
`GstVideoMeta` 的影格，效能不足即時播放。部分格式在 EOS 後出現
`gst_mini_object_unref` 臨界警告，未造成退出失敗；仍須以長時間播放、
seek、不同解析度、10-bit 及實際播放器繼續驗證。

映像透過 `/boot/armbianEnv.txt` 加入 `cma=256M`。2 GiB 實機確認
`CmaTotal` 為 262144 KiB；此設定只套用 M4 Berry，不擴散到其他 sunxi64
板卡。

## GPU、顯示與 U-Boot HDMI

Panfrost 使用 432 MHz 固定時脈；H618 目前沒有已驗證的 OPP／DVFS，因此
沒有進行超頻。PPU 電源域提供者改為內建核心，處理冷開機模組探測競態。

Linux DRM 已偵測 HDMI、三個 plane 與 32 種模式，包含 800x600、720p、
1080p；目前 plane 只列出 RGB 格式，尚未證明 DE33 的 YUV plane、縮放、
G2D 或 DI 可用。

M4 Berry 的 U-Boot 2025.04 未啟用 `CONFIG_VIDEO`。上游
`VIDEO_SUNXI` 仍明確依賴 `!SUN50I_GEN_H6`，H616/H618 又屬於該世代，
所以無法只切換設定取得 HDMI Logo。需要另案移植 DE、TCON、HDMI PHY、
時鐘、重設與電源控制；此工作不得碰觸已驗證的 DDR SPL。

## Crypto Engine

`sun8i-ce` 已註冊 AES、3DES、MD5、SHA 與亂數。有效的硬體證據來自
AF_ALG 以 4 KiB 區塊執行 8 MiB AES-256-CBC：加解密約 47 MiB/s、內容
往返一致，且 CE 中斷與硬體通道請求同步增加。

64 KiB 區塊會超過硬體散佈表限制並回退軟體 AES；`cryptsetup benchmark`
也未增加 CE 中斷。因此這些較高的吞吐數字不能作為 H618 CE 硬體效能。

## 40-pin 與相容工具

所有映像預裝 `gpiod`、`i2c-tools`、`python3-libgpiod`、
`python3-spidev` 與 `v4l-utils`。udev 規則只授權 `users` 群組為 `0660`，
不採用全域可寫的 `0666`。PWM 是 sysfs 介面，pwmchip 與匯出通道分別
處理權限。

新程式應優先使用 `libgpiod`。舊程式可執行：

```bash
sudo bpi-m4berry-io-compat-install
```

安裝器固定下列來源版本：

```text
BPI-WiringPi2 da58b589a3ca3e44f569850f07ee17de2e294b5f
RPi.GPIO      c04d27c86f65ed824921a457455a09d6820b9e1d
```

舊介面直接存取 `/dev/mem`，必須以 root 操作。首次實機驗證發現
`gpio -v` 的板型初始化錯誤；修正已推送到 BPI-WiringPi2，不再使用舊的
`d5084da`。

可執行下列唯讀盤點：

```bash
bpi-m4berry-hw-info
```

SPI、UART 與 PWM 已有覆蓋層及腳位文件，但仍缺外接迴路或已知裝置，不能
把「裝置節點存在」寫成電氣功能通過。

## 上游與競品判讀

- Linux Stateless Decoder 文件：
  `https://docs.kernel.org/userspace-api/media/v4l/dev-stateless-decoder.html`
- GStreamer V4L2 codecs：
  `https://gstreamer.freedesktop.org/documentation/v4l2codecs/index.html`
- U-Boot 2025.04 sunxi Kconfig：
  `https://github.com/u-boot/u-boot/blob/v2025.04/arch/arm/mach-sunxi/Kconfig`
- Orange Pi Zero 3 官方規格：
  `https://www.orangepi.org/html/hardWare/computerAndMicrocontrollers/details/Orange-Pi-Zero-3.html`

Orange Pi 官方宣告 H618、1／1.5／2／4 GiB、Mali-G31、4K HDMI 與多格式
解碼，證明同 SoC 產品可提供完整體驗；但頁面沒有證明其功能來自主線
U-Boot、主線 Cedrus 或相同使用者空間版本，不能取代本專案實測。

## 證據

原始紀錄位於：

```text
output/evidence/bpi-m4berry-a1-ddr/M4B-power-on-20260824-224812/
```

主要檔案：

- `h618-cedrus-h616-variant-boot.txt`
- `h618-cedrus-codec-matrix-6.18.46-h616-variant.txt`
- `h618-cedrus-mpeg2-6.18.46-h616-variant.txt`
- `h618-crypto-afalg-hardware-6.18.46.txt`
- `h618-gpio-drm-inventory-6.18.46.txt`
- `h618-io-nonroot-validation-6.18.46.txt`
- `h618-legacy-io-compat-install-fixed-v2.txt`
- `h618-legacy-io-runtime-validation.txt`

尚待使用者或實驗室配合的項目是 2／4 GiB 多板冷啟動、SPI／UART／PWM
實體迴路、三種 HDMI 解析度的畫面確認、H.265 10-bit 與兩小時播放、
客戶螢幕與條碼機／觸控裝置整機測試。
