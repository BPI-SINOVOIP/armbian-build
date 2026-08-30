# Banana Pi M4 Berry H618 全面最佳化與映像交付計畫

日期：2026-08-25

## 目標

以 BPI-M4 Berry A1 DDR 792 MHz 候選為啟動基線，建立適合廚房秤與一般
嵌入式應用的 Armbian 映像。交付內容必須同時涵蓋：

- 2 GiB／4 GiB DDR 與 eMMC／SD 啟動可靠性。
- Mali-G31 Panfrost 圖形加速。
- Cedrus 視訊解碼、Display Engine 與 Crypto Engine 的可驗證能力。
- 40-pin GPIO、I2C、SPI、UART 與 PWM。
- CLI／XFCE 及五個發行版的完整重建映像，而非只替換 U-Boot。
- 可重現命令、校驗碼、測試紀錄與明確的未支援邊界。

## 目前基線

| 項目 | 現況 | 判定 |
| --- | --- | --- |
| DDR | U-Boot 2025.04，A1 792 MHz，2 GiB 實機可啟動 | 保留，不與周邊最佳化混改 |
| 核心 | 6.18.46 current | 正在重建修正版 |
| GPU | Mali-G31、Panfrost、Mesa 硬體繪圖成立 | 暖重啟通過，仍缺完整斷電統計 |
| Cedrus | `/dev/video0`，列出 MPEG-2／H.264／H.265／VP8 | 四種 1080p30 樣本均完成 180 幀硬解 |
| Display Engine | Linux DRM、HDMI 與三個 plane 已初始化 | 基本顯示可用，DE33 縮放與 YUV plane 不完整 |
| Crypto Engine | `sun8i-ce` 已註冊並通過自我測試 | AF_ALG 硬體往返通過 |
| 40-pin | GPIO、I2C 權限與舊介面已實機通過 | SPI／UART／PWM 實體迴路仍待外接設備 |
| U-Boot HDMI | `CONFIG_VIDEO` 未啟用 | H618 主線驅動缺失，列為獨立移植項目 |

## 技術判斷

### U-Boot HDMI

目前 M4 Berry U-Boot 沒有 HDMI 顯示。U-Boot 2025.04 的
`VIDEO_SUNXI` 明確排除 `SUN50I_GEN_H6`，因此不能只開啟 `CONFIG_VIDEO`。
若產品要求上電立即顯示 Logo，必須另外移植 H618 的 DE、TCON、HDMI
PHY、時鐘、重設與電源控制，或評估原廠 U-Boot 顯示程式碼。這項工作
不得影響已驗證的 DDR SPL；Linux 啟動後的 HDMI 正常不受此限制。

### GPU

冷開機曾因 PPU 電源域提供者與 Panfrost 模組載入順序發生 `-110`。
修正方式是將 `CONFIG_SUN50I_H6_PRCM_PPU=y`，Panfrost 仍維持模組。
H618 目前沒有經驗證的 GPU OPP／DVFS，不在本階段超頻。

### Cedrus

H618 採 V4L2 Stateless Request API。標準整合路徑是 GStreamer
`v4l2sl*dec`；一般發行版 FFmpeg 的 `h264_v4l2m2m` 不是同一個介面，
不得宣稱為 Cedrus 硬解。核心修補檔原本把 H616/H618 誤指到 H6 的
648 MHz 能力表，必須改用 H616/H618 的 600 MHz 能力表。

2 GiB 實機的核心預設 CMA 只有 64 MiB，H.265 1080p 緩衝配置會失敗。
M4 Berry 映像改為 `cma=256M`；此值須同時驗證多媒體穩定性與一般可用
記憶體，不能直接擴大到所有 sunxi64 板卡。

### Crypto Engine

`sun8i-ce` 提供 AES、3DES、MD5、SHA 與亂數功能。AF_ALG 以 4 KiB
區塊執行 AES-256-CBC 時，4096 次請求對應 4096 次 CE 中斷且往返內容
一致。大散佈表會因硬體 SG 數限制回退到 ARMv8 AES；因此驗收必須同時
記錄 `/sys/kernel/debug/sun8i-ce/stats`，不能只引用 OpenSSL 或
`cryptsetup benchmark` 數字。

### 40-pin

核心標準介面以 GPIO 字元裝置與 libgpiod 為主。BPI-WiringPi2
`bpi-legacy-io-porting` 的 `da58b589` 與 RPi.GPIO 同名分支的 `c04d27c`
已包含 M4 Berry GPIO 映射，但硬體 PWM 相容層仍標為有限支援。交付時
必須分別標示：

- 核心 GPIO、I2C、SPI、UART、PWM 與 overlay 是否可用。
- libgpiod／Python spidev 是否可用。
- BPI-WiringPi2／RPi.GPIO 相容 API 哪些功能已實機通過。

## 分階段執行

### 第一階段：核心可靠性

1. 內建 PPU 電源域提供者。
2. 修正 H618 Cedrus 能力表指標。
3. 移除已被 6.18.46 上游包含、會造成套用失敗的舊 Cedrus 修補檔。
4. 板級設定加入 `cma=256M`。
5. 重建核心、部署到 2 GiB M4 Berry 並完成暖重啟驗證。

### 第二階段：硬體加速

1. H.264、H.265 8-bit、MPEG-2、VP8 各以固定 1080p30 樣本測試。
2. 記錄 VPU 中斷增量、執行時間、CMA、溫度與核心錯誤。
3. 以 `modetest` 記錄 HDMI、CRTC、plane 與格式；零拷貝與 YUV plane
   另列實驗項目。
4. 以 AF_ALG 完成 AES 正確性、吞吐量、CE 中斷與回退統計。
5. GPU 執行 `glxinfo`、`es2_info` 與固定 `800x600` 的 glmark2。

### 第三階段：40-pin 與工具

1. 補齊 H616 overlay 使用文件與 M4 Berry 實體腳位對照。
2. 新增 PG19／實體 pin 7 的 PWM1 overlay。
3. 所有映像加入 `gpiod`、`i2c-tools`、`python3-libgpiod`、
   `python3-spidev` 與 `v4l-utils`。
4. 桌面映像加入 GStreamer V4L2 Request 與 DRM 診斷工具。
5. 以 `users` 群組及 `0660` 權限開放 GPIO／I2C／SPI／PWM，不採用
   全域可寫的 `0666`。
6. 分別驗證 GPIO 輸入／輸出迴路、I2C 掃描、SPI 迴路、UART 迴路與
   三個 PWM 腳位。
7. 對 BPI-WiringPi2 與 RPi.GPIO 建立版本固定、可重現的安裝與驗收路徑。

## 2026-08-25 執行結果

- 第一階段程式修正、核心建置及暖重啟已完成；完整斷電統計尚未完成。
- H.264、H.265 8-bit、VP8、MPEG-2 均以 1080p30、180 幀樣本完成 EOS，
  每項皆增加 180 次視訊引擎中斷，VE 時脈為 600 MHz。
- H.265 含 B-frame 樣本耗時約 55 秒；無 B-frame 樣本約 6.2 秒。兩者皆
  成功，但前者尚不符合即時播放效能，需繼續處理 GstVideoMeta 複製路徑。
- `cma=256M` 已在 2 GiB 實機保留 256 MiB，未再出現 CMA 配置失敗。
- 一般使用者已可透過 `users` 群組讀取 GPIO 與 I2C 裝置；
  `BPI-WiringPi2` 的 `gpio -v`、`gpio readall` 與 `RPi.GPIO` 輸入測試通過。
- BPI-WiringPi2 首次實測發現板型資訊未初始化崩潰，已於獨立倉庫提交
  `da58b589` 修正並推送；Armbian 一鍵安裝器已固定到該提交。
- GPU、VPU、Crypto、GPIO 與 I2C 已有實機證據；SPI、UART、PWM 外接迴路、
  H.265 10-bit、長時間播放、多板與 4 GiB 測試仍不得宣稱完成。

### 第四階段：完整映像矩陣

完整重建下列十組映像，不沿用舊 6.18.32 映像覆寫 U-Boot：

| 發行版 | CLI／精簡 | XFCE 桌面 |
| --- | --- | --- |
| bookworm | 是 | 是 |
| trixie | 是 | 是 |
| jammy | 是 | 是 |
| noble | 是 | 是 |
| resolute | 是 | 是 |

每個映像必須保留未壓縮 `.img`、壓縮 `.img.xz`、SHA-256、建置參數、
核心與 U-Boot 套件雜湊，以及測試紀錄範本。

### 第四階段完成結果

2026-08-25 已完成十組完整重建映像。輸出包含十個 `.img`、十個
`.img.xz`、個別 SHA-256、建置中繼資料與日誌，沒有殘留 `.partial`。

`verify-bpi-m4berry-h618-optimized-matrix.sh` 已完成二十個 SHA-256、十個
`xz` 串流及十個映像的唯讀掛載內容檢查，全部通過。詳細檔名、容量、
雜湊與限制記錄於
`bananapi-m4berry-h618-optimized-image-matrix-delivery-20260825.md`。

## 驗收門檻

### 啟動與 DDR

- 2 GiB 與 4 GiB 各至少三片板；每片至少 20 次完整斷電冷啟動。
- SPL 正確辨識容量並進入 U-Boot、核心與登入介面。
- 每片執行記憶體壓力、重新啟動及 SD／eMMC I/O 測試。
- 未取得樣本數前只標示候選，不宣稱量產通過。

### GPU

- 每次冷啟動均無 `deferred probe timeout` 與 `error -110`。
- `GL_RENDERER` 為 `Mali-G31 (Panfrost)`，不是 LLVMpipe。
- X11、`800x600`、合成器開啟至少 180 分；關閉合成器至少 240 分。

### VPU

- 四種已列舉格式均須完成 EOS，退出碼為 0。
- 幀數與 VPU 中斷增量合理，無 CMA、Cedrus、V4L2 或 IOMMU 錯誤。
- H.265 另測 8-bit、10-bit、B-frame、seek 與至少兩小時連續播放。
- VP9、AV1、硬體編碼、G2D 與 DI 未完成前不得列入支援規格。

### 顯示與 I/O

- HDMI 至少驗證 800x600、1280x720 與 1920x1080。
- 40-pin 每個 GPIO 對到實體腳位；所有匯流排採實體迴路或已知裝置。
- 一般帳號可在受控群組權限下使用裝置，未授權帳號不可寫入。
- 啟用 overlay 後不得破壞網路、eMMC、Wi-Fi、藍牙與板載 LED。

## 競品與上游參考

- Linux Stateless Decoder API：
  `https://docs.kernel.org/userspace-api/media/v4l/dev-stateless-decoder.html`
- GStreamer V4L2 codecs：
  `https://gstreamer.freedesktop.org/documentation/v4l2codecs/index.html`
- LibreELEC 的 Allwinner V4L2 Request FFmpeg 整合：
  `https://github.com/LibreELEC/LibreELEC.tv/blob/master/packages/multimedia/ffmpeg/package.mk`
- Orange Pi Zero 3 的 H618 規格與軟體宣告：
  `https://www.orangepi.org/html/hardWare/computerAndMicrocontrollers/details/Orange-Pi-Zero-3.html`
- Linux H616 裝置樹：
  `https://github.com/torvalds/linux/blob/master/arch/arm64/boot/dts/allwinner/sun50i-h616.dtsi`

競品頁面只能證明其宣告或整合方向，不能取代 M4 Berry 實機證據。

## 證據與限制

大型與原始紀錄位於：

```text
output/evidence/bpi-m4berry-a1-ddr/M4B-power-on-20260824-224812/
```

目前只有一片 2 GiB M4 Berry 的直接實機證據。完整斷電、4 GiB、多板、
40-pin 實體迴路、不同 HDMI 面板與客戶廚房秤應用仍需硬體配合。這些
限制不阻擋核心、工具、映像與可自動完成的驗證，但會阻擋量產與完整
硬體相容聲明。
