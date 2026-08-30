# Banana Pi M4 Zero EMAC Noble CLI 工程候選紀錄

- 日期：2026-08-30
- 狀態：完整建置、離線驗證與單片 4 GiB 最終映像實測通過主要硬體路徑；EMAC 實體驗證待擴充板
- 板型：`bananapim4zeroemac`
- 分支：`bpi-integration-20260829`
- 映像來源提交：`069fb20fe17c862498bccd0e7cc5e3dc379c5957`
- 核心：`6.18.48-current-sunxi64`
- 發行版：Ubuntu Noble minimal CLI

## 1. 候選用途與證據邊界

本候選整合 BPI-M4 Zero A1 792 MHz DDR、AC300 internal EPHY、Mali-G31
Panfrost、Cedrus、Crypto Engine、40-pin I/O、板載 BCM/CYW43455 無線功能，
以及額外 USB RTL8821CU 網卡的核心驅動支援。

最終新映像已完成完整原始碼建置、封裝、唯讀內容驗證與實板原生開機閉環。單片
4 GiB 工程樣本已由 SD 冷啟動，並完成四次熱重啟、CPU 與 DDR 壓力、eMMC 與 SD、
Wi-Fi、Bluetooth、Panfrost、Cedrus、Crypto Engine、USB gadget 能力、40-pin I/O
盤點及音訊 PCM 路徑驗證。EMAC 需要尚未取得的額外擴充板，因此本輪只有 DT、驅動
及封裝靜態證據，不包含 PHY、實體連線或吞吐驗證。

本紀錄不代表量產、多片、多容量、電氣或正式發布驗證通過。

## 2. 最終新產物

產物目錄：

```text
output/images/
```

壓縮映像：

```text
Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_noble_current_6.18.48_minimal.img.xz
```

| 產物 | 位元組 | SHA-256 |
|---|---:|---|
| `.img` | 1,543,503,872 | `3d3a4a995df6fc4ea950c9d27e2af1dc888b6673e41f8cd3747f47f2269c67f6` |
| `.img.xz` | 283,627,204 | `0fc90365bc45dd1f74ab7c6914ecb885f744a029ab4b0adf87c886cedf073e61` |

已通過 `xz -t`，且 `.img.xz` 解壓串流的 SHA-256 與 `.img` 完全一致。

## 3. 建置證據

完整映像由下列命令建立，不是替換舊映像的 bootloader：

```bash
./compile.sh build BOARD=bananapim4zeroemac BRANCH=current RELEASE=noble \
  BUILD_MINIMAL=yes BUILD_DESKTOP=no KERNEL_CONFIGURE=no SHARE_LOG=no
```

建置結果：

- 完整編譯並封裝 U-Boot `v2026.01`、Linux `6.18.48`、DTB、核心模組、韌體與 BSP。
- U-Boot 套用 M4 Zero A1 792 MHz DDR 修正。
- Noble minimal 根檔案系統完成組裝；映像採 MBR，ext4 分割區從 sector 8192 開始。
- 建置成功結束，執行時間為 20 分 23 秒。

U-Boot 套件：

```text
output/debs/linux-u-boot-bananapim4zeroemac-current_26.11.0-trunk_arm64__2026.01-S127a-P0d3f-H8076-Vc787-B5da4-R448a.deb
```

套件 SHA-256：

```text
ffdd5159c9ed0c298f0cddb7ac584680208cb00e8944ae4c5b51fb3b3e10d992
```

## 4. 最終映像離線驗證

驗證器以唯讀 loop 裝置及 `mount -o ro,noload` 檢查映像，確認：

- 映像內 U-Boot 與套件內 `u-boot-sunxi-with-spl.bin` 位元完全一致。
- `fdtfile`、Wi-Fi／Bluetooth overlay 與 `cma=256M` 設定正確。
- 板型名稱為 `BananaPi BPI-M4-Zero EMAC`。
- GPU、PWM、EMAC、AC300 EPHY 與相關時鐘設定存在且啟用。
- CPU 被動節流點為 60°C 與 70°C；第二版 cooling map 範圍正確。
- `dwmac-sun8i`、Panfrost、Cedrus、sun8i-ce、板載 Broadcom 與 USB RTL8821CU
  的核心設定及模組存在。
- 四個 BCM/CYW43455 韌體別名均為正確符號連結，目標存在且非空，內容 SHA-256
  與倉庫來源相同。
- Bluetooth、GPIO、I²C、SPI 與 V4L2 使用工具已安裝。
- `bpi-h618-hw-info` 與 `bpi-h618-io-compat-install` 已安裝。

映像內 offset 8 KiB 起的 U-Boot 內容 SHA-256：

```text
57153608a7c7e80b34f1c66dfc51be46434f854817843a5100a0576797e997c7
```

此次執行的 17 個 M4 Zero EMAC 回歸測試、Bash 語法、ShellCheck 與
`git diff --check` 均通過。

可重複執行完整離線驗證：

```bash
./tools/verify-bpi-m4zero-emac-image.sh \
  output/images/Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_noble_current_6.18.48_minimal.img \
  output/debs/linux-u-boot-bananapim4zeroemac-current_26.11.0-trunk_arm64__2026.01-S127a-P0d3f-H8076-Vc787-B5da4-R448a.deb
```

## 5. 最終映像單片 4 GiB 實測結果

實測證據位於：

```text
/home/pi/log/20260830-m4zero-emac-final-image-validation/
```

| 項目 | 結果 | 關鍵數值 |
|---|---|---|
| SD 開機與熱重啟 | 通過 | 最終映像冷啟動成功；壓力測試前後共四次熱重啟，均回到核心 `#3` |
| CPU 熱控第二版 | 通過 | 四核心壓力 180 秒，最高 72.638°C，四個工作零失敗 |
| DDR 壓力 | 通過 | `memtester 2800M 1`，18 類全數成功、回傳碼 0、耗時 5,185 秒 |
| eMMC 唯讀 | 通過 | 30 秒，93.6 MiB/s，`err=0`；未對 eMMC 寫入 |
| SD 寫入與校驗 | 通過 | 1 GiB，寫入 20.7 MiB/s、CRC32C 校驗讀取 21.6 MiB/s |
| 板載 Wi-Fi | 通過 | 5 GHz、433.3 Mbit/s；板端傳送 125 Mbit/s、接收 121 Mbit/s、介面錯誤 0 |
| Bluetooth | 基礎通過 | HCI 5.0、錯誤 0、20 秒掃描發現六個裝置 |
| Panfrost GLES | 通過 | Mali-G31、OpenGL ES 3.1；3840×2160 五項矩陣分數 27，無 GPU fault |
| Cedrus H.264 | 硬體路徑通過 | 1080p30、900 幀、IRQ 增加 900、36.7 秒正常 EOS |
| SHA-256 硬體路徑 | 通過 | `sha256-sun8i-ce` 雜湊正確，IRQ 增加一次 |
| USB 與 40-pin I/O | 能力盤點通過 | UDC、mass-storage 模組、GPIO、I2C、PWM、UART 與 SPI overlay 均存在 |
| 音訊 PCM 路徑 | 通過 | 類比 Codec 與 HDMI 各完成三秒 48 kHz 立體聲資料送出，回傳碼 0 |
| EMAC | 待實體驗證 | 擴充板尚未取得；本輪只有 DT、`dwmac-sun8i` 與封裝靜態證據 |

壓力測試後的健康快照顯示：四核心均在線、沒有失敗服務，也沒有 OOM、記憶體、
MMC、Panfrost、Cedrus、Bluetooth 或 Wi-Fi 執行期錯誤。`memtester` 期間最高 CPU
為 71.828°C、DDR 為 70.046°C，未觸發 95°C 安全中止，swap 全程為 0。完成全部
測試後再次熱重啟，Wi-Fi 自動回連、Bluetooth 錯誤為 0，核心嚴重等級日誌為空。

核心採用 `sha256-sun8i-ce`，AF_ALG 對 4 KiB 零資料算出的 SHA-256 與軟體預期值
一致，SoC Crypto Engine IRQ 同步增加一次，因此可確認硬體雜湊路徑實際執行。

實測中的初始熱控映射曾升至 99.611°C；第一版 cooling map 仍在 96.371°C
安全中止。最終採用的第二版映射在相同類型壓力下可穩定節流，前兩次失敗紀錄不能
刪除，應保留作為修正依據。

Cedrus 測試證明硬體解碼路徑可用，但 30 秒、900 幀串流耗時 36.7 秒，約為
24.5 fps，尚不能宣稱 1080p30 即時播放通過。GPU 分數 27 是 3840×2160 的代表性
五項矩陣結果，不能與 800×600 或完整預設矩陣分數直接比較。

USB 有三組 EHCI、三組 OHCI 與 `musb-hdrc` UDC，核心包含 `g_mass_storage`、
`usb_f_mass_storage` 與 ConfigFS mass-storage；此輪未接 USB host 端，因此不能宣稱
實體 gadget 枚舉通過。GPIO、I2C、PWM、UART 與 SPI 只做無破壞能力盤點，沒有在
缺少外接迴路與負載時切換腳位。音訊測試只證明 PCM 資料路徑接受資料，未包含人工
聽測。

## 6. 燒錄與雜湊驗證

```bash
cd output/images
sha256sum -c \
  Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_noble_current_6.18.48_minimal.img.xz.sha
xz -t \
  Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_noble_current_6.18.48_minimal.img.xz
```

先以 `lsblk` 確認目標裝置，再把 `/dev/sdX` 換成實際 SD 卡：

```bash
xz -dc Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_noble_current_6.18.48_minimal.img.xz \
  | sudo dd of=/dev/sdX bs=16M oflag=direct status=progress
sync
```

## 7. 開機後基礎命令

```bash
bpi-h618-hw-info
cat /proc/device-tree/model
cat /proc/cmdline
free -h
ip -br link
iface="$(ip -br link | awk '$1 != "lo" { print $1; exit }')"
ethtool "$iface"
ethtool -S "$iface"
dmesg -T | grep -Ei 'ac300|dwmac|stmmac|phy|panfrost|cedrus|sun8i-ce|error|fail'
gpiodetect
i2cdetect -l
ls -l /dev/spidev* /dev/ttyS* /sys/class/pwm/pwmchip* 2>/dev/null
```

## 8. 尚未完成的驗證

- 尚未取得 2 GiB 樣本及多片 4 GiB 樣本的 DDR 壓力與冷啟動矩陣。
- EMAC 額外擴充板尚未取得；目前只有 DT、驅動與封裝靜態證據，尚無 PHY 探測、
  實體連線、DHCP、吞吐、錯誤計數與斷線重連證據。
- Bluetooth 尚未測配對、GATT、BLE 廣播、音訊及吞吐。
- USB、GPIO、I²C、SPI、UART 與 PWM 尚未做實體周邊或迴路驗證。
- USB RTL8821CU 目前只有映像內驅動與 USB modalias 證據，未接實體網卡。
- 尚未完成十次斷電冷啟動、長時間影音、複合 I/O 壓力與斷電恢復測試。
- 十映像矩陣尚未展開；目前只有 Noble minimal CLI 工程候選。
- 原有 `bananapim4zero` 板型未改成預設啟用 EMAC，兩種板型不可混用。

完成 EMAC 擴充板實體連線、2 GiB／4 GiB 多片冷啟動與外接 I/O 驗證前，
不得將本候選標示為量產或正式發布通過。
