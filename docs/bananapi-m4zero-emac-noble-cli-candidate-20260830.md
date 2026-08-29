# Banana Pi M4 Zero EMAC Noble CLI 工程候選紀錄

- 日期：2026-08-30
- 狀態：離線驗證通過，待實板驗證
- 板型：`bananapim4zeroemac`
- 分支：`bpi-integration-20260829`
- 來源提交：`b2065e29df402bf11c8bda8a11825c87cc939acc`

## 1. 候選用途

本映像用來執行第一輪實板 Gate。它整合 BPI-M4 Zero A1 792 MHz DDR、
Mali-G31 Panfrost、Cedrus、Crypto Engine、RTL8821CU、40-pin I/O 與預設啟用
的 AC300 internal EPHY。

本紀錄只能證明完整建置、封裝與離線內容檢查已通過，不能取代實板、量產、
電氣或長時間穩定性驗證。

## 2. 產物

目錄：

```text
output/images/
```

壓縮映像：

```text
Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_noble_current_6.18.48_minimal.img.xz
```

SHA-256：

```text
b19d9d97ee716371aa1cd258b62aced7bc750ba7e5b89f6508690549c436df9d
```

未壓縮映像 SHA-256：

```text
d8816cfdb2c15a5d9a4bcd35fb4267aefc12275d513f2fac087838aef744a301
```

壓縮檔為 284,103,900 位元組；未壓縮映像為 1,543,503,872 位元組。已通過
`xz -t`，且 XZ 解壓串流的 SHA-256 與未壓縮映像完全一致。

## 3. 建置證據

完整映像由下列命令建立，不是替換既有映像的 bootloader：

```bash
./compile.sh build BOARD=bananapim4zeroemac BRANCH=current RELEASE=noble \
  BUILD_MINIMAL=yes BUILD_DESKTOP=no KERNEL_CONFIGURE=no SHARE_LOG=no
```

建置結果：

- U-Boot `v2026.01` 完整套用 M4 Zero A1 DDR patch。
- Linux `6.18.48-current-sunxi64` 完整編譯與封裝。
- Noble minimal 根檔案系統、核心、DTB、韌體與 BSP 套件完整組裝。
- 原始映像採 MBR，第一個 ext4 分割區從 sector 8192 開始。

U-Boot 套件：

```text
output/debs/linux-u-boot-bananapim4zeroemac-current_26.11.0-trunk_arm64__2026.01-S127a-P0d3f-H8076-Vc787-B5da4-R448a.deb
```

U-Boot 套件 SHA-256：

```text
ffdd5159c9ed0c298f0cddb7ac584680208cb00e8944ae4c5b51fb3b3e10d992
```

## 4. 離線內容驗證

映像已使用唯讀 loop 裝置及 `mount -o ro,noload` 檢查，確認：

- `fdtfile=sun50i-h618-bananapi-m4-zero-emac.dtb`。
- `overlays=bananapi-m4-zero-emac-sdio-wifi-bt`。
- `extraargs=cma=256M`。
- 最終 DTB 的 GPU、EMAC1、PWM 與 PWM5 均為 `okay`。
- 板型識別為 `BananaPi BPI-M4-Zero EMAC`。
- AC300 clock、PWM 2 MHz 與 SID 校正描述保留。
- Wi-Fi／Bluetooth overlay 不會覆寫衍生板型名稱。
- `gpiod`、`i2c-tools`、`python3-libgpiod`、`python3-spidev`、`v4l-utils`、
  `bluez`、`bluez-tools` 與 `rfkill` 均已安裝。
- `bpi-h618-hw-info` 與 `bpi-h618-io-compat-install` 已安裝。

BPI 回歸測試共 26 項通過，另已通過 Python 語法、Bash 語法、ShellCheck 與
`git diff --check`。

## 5. 燒錄前驗證

```bash
cd output/images
sha256sum -c \
  Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_noble_current_6.18.48_minimal.img.xz.sha
xz -t \
  Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_noble_current_6.18.48_minimal.img.xz
```

先用 `lsblk` 確認目標裝置，再把 `/dev/sdX` 換成實際 SD 卡：

```bash
xz -dc Armbian-unofficial_26.11.0-trunk_Bananapim4zeroemac_noble_current_6.18.48_minimal.img.xz \
  | sudo dd of=/dev/sdX bs=16M oflag=direct status=progress
sync
```

## 6. 實板 Gate 順序

每項均須保留完整 UART 與 Linux 日誌；任何一項失敗時先停止擴大樣本，不直接
進入十映像建置。

1. 使用 2 GiB 與 4 GiB 板各確認容量、Rank、DDR Build ID 及完整開機。
2. 每種容量執行至少十次完全斷電冷啟動，不能只用 `reboot`。
3. 執行全容量記憶體壓力，並同步進行 SD／eMMC I/O。
4. 確認 AC300 綁定、100 Mbps 全雙工、DHCP、固定 IP 與斷線重連。
5. 執行雙向 `iperf3`，記錄吞吐、封包錯誤、丟棄與重新協商次數。
6. 驗證 Panfrost、GLES、Cedrus、Crypto Engine、HDMI 與長時間播放。
7. 驗證 RTL8821CU、Bluetooth／BLE、USB OTG 與 USB host。
8. 驗證 GPIO、I2C、SPI、UART、PWM 的實體迴路與 pinmux 衝突。

## 7. 開機後基礎命令

```bash
bpi-h618-hw-info
cat /proc/device-tree/model
cat /proc/cmdline
free -h
ip -br link
ethtool eth0
ethtool -S eth0
dmesg -T | grep -Ei 'ac300|dwmac|stmmac|phy|panfrost|cedrus|sun8i-ce|error|fail'
gpiodetect
i2cdetect -l
ls -l /dev/spidev* /dev/ttyS* /sys/class/pwm/pwmchip* 2>/dev/null
```

映像已內建 `ethtool`，`iperf3` 與 `memtester` 則由驗證人員依測試需求安裝：

```bash
sudo apt update
sudo apt install -y iperf3 memtester
```

雙向網路測試需要另一台電腦執行 `iperf3 -s`，板端再執行：

```bash
iperf3 -c <伺服器位址> -t 60 -P 4
iperf3 -c <伺服器位址> -t 60 -P 4 -R
```

## 8. 目前限制

- 尚無本候選的 2 GiB／4 GiB 實板開機與 DDR 壓力證據。
- 尚無 AC300 FPC24 實體連線、吞吐及錯誤計數證據。
- 尚無本候選的 GPU、Cedrus、無線、USB 與 40-pin 實體證據。
- 十映像矩陣尚未展開；目前只有 Noble CLI 工程候選。
- `bananapim4zero` 原板型沒有被改成預設啟用 EMAC；兩種映像不可混用板型名稱。

完成第 6 節後才能把本候選提升為矩陣基線。完成多板、多容量與長時間測試前，
不得標示為量產通過。
