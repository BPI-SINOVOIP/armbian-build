# Banana Pi BPI-M4 Zero EMAC 燒錄與驗證指南

## 1. 選擇映像

映像檔名包含發行版與角色：

- `bookworm`、`trixie`：Debian。
- `jammy`、`noble`、`resolute`：Ubuntu。
- `minimal`：CLI 映像。
- `xfce_desktop`：XFCE 桌面映像。

請只使用 `Bananapim4zeroemac` 映像測試 EMAC 衍生板型。原有 BPI-M4 Zero 若沒有
對應擴充硬體，應使用 `Bananapim4zero` 映像。

## 2. 下載後核對

在交付目錄執行：

```bash
sha256sum -c SHA256SUMS
sha256sum -c DELIVERY_METADATA_SHA256SUMS
for image in *.img.xz; do xz -t "$image" || exit 1; done
```

所有項目都必須成功。若雜湊或 XZ 串流失敗，不得燒錄該檔案。

單一映像也可使用對應的 `.img.xz.sha`：

```bash
sha256sum -c Armbian-*.img.xz.sha
```

## 3. 燒錄 SD 卡

先列出裝置並核對容量、型號與掛載點：

```bash
lsblk -o NAME,PATH,SIZE,MODEL,TRAN,TYPE,MOUNTPOINTS
```

將下列 `/dev/sdX` 改成實際 SD 卡整顆裝置。不得填入分割區名稱，也不得在未確認
裝置前執行：

```bash
IMAGE='Armbian-請選擇實際映像檔名.img.xz'
TARGET_DEVICE=/dev/sdX
xz -dc "$IMAGE" \
  | sudo dd of="$TARGET_DEVICE" bs=16M oflag=direct status=progress
sync
sudo eject "$TARGET_DEVICE"
```

## 4. 首次開機紀錄

建議 UART 使用 115200、8N1，從上電前開始記錄。至少保存：

- 板子序號、DDR 型號與容量。
- eMMC 型號、SD 卡型號與電源規格。
- EMAC 擴充板版本與網路對端型號。
- 完整 SPL、U-Boot、核心及登入前日誌。
- 映像檔名與 SHA-256。

登入後先執行：

```bash
cat /proc/device-tree/model
cat /etc/armbian-release
uname -a
bpi-h618-hw-info
free -h
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS
dmesg -T | grep -Ei 'error|fail|timeout|watchdog|mmc|dram|panfrost|cedrus|sun8i-ce|ac300|dwmac|stmmac|phy'
```

板型應為 `BananaPi BPI-M4-Zero EMAC`，核心應為
`6.18.48-current-sunxi64`。

## 5. EMAC 實機驗證

相容 EMAC 擴充板的型號、版本、FPC 方向、接頭與供電規格尚待實物確認。在取得受控
硬體規格前，不得連接未知 FPC 或擴充板。EMAC 會占用 PA0 至 PA9，AC300 時鐘另占用
PWM5／PA12；測試期間不得同時把這些腳位配置給 40-pin 周邊。

確認硬體相容後，接上 EMAC 擴充板與已知正常的網路線並執行：

```bash
ip -br link
dmesg -T | grep -Ei 'ac300|dwmac|stmmac|mdio|phy|ethernet'
iface="$(ip -br link | awk '$1 != "lo" && $1 !~ /^wl/ { print $1; exit }')"
ethtool "$iface"
ethtool -S "$iface"
ip -s link show dev "$iface"
```

必要通過條件：

1. PHY 可正確探測，不得出現 `No PHY found`、reset timeout 或 MDIO 讀寫錯誤。
2. 網路線插入後應協商為 100 Mbps、全雙工；拔除後連線狀態應下降。
3. DHCP 與固定 IP 均可正常使用。
4. 重新插拔網路線至少十次，介面應自動恢復。
5. 冷啟動與熱重啟後都能重新協商。
6. 壓力前後比較 `ethtool -S` 與 `ip -s link`，CRC、drop、timeout 不得持續增加。

以另一台電腦執行 `iperf3 -s`，板端測試雙向流量：

```bash
SERVER_IP=192.0.2.10
iperf3 -c "$SERVER_IP" -t 300 -P 4
iperf3 -c "$SERVER_IP" -t 300 -P 4 -R
ping -c 1000 "$SERVER_IP"
```

應記錄平均、最低吞吐、重傳、封包遺失、CPU 溫度及介面錯誤計數。介面存在或只取得
DHCP 位址不能視為 EMAC 驗證完成。

## 6. DDR 與系統壓力

先確認容量正確，再依可用記憶體保留至少 512 MiB 給系統：

```bash
sudo apt update
sudo apt install -y memtester
free -h
cat /proc/meminfo
AVAILABLE_MIB="$(awk '/MemAvailable:/ { print int($2 / 1024) }' /proc/meminfo)"
if (( AVAILABLE_MIB <= 640 )); then
  printf '可用記憶體不足，停止 memtester。\n' >&2
  exit 1
fi
TEST_SIZE="$(( AVAILABLE_MIB - 512 ))M"
sudo memtester "$TEST_SIZE" 1
```

另執行 CPU、儲存與網路複合壓力，持續監看溫度及核心錯誤。測試容量不得造成 OOM，
也不得寫入需要保留資料的 eMMC 或 SD 分割區。

每個硬體樣本至少完成十次完全斷電冷啟動。單次成功、熱重啟成功或只顯示正確容量，
都不足以證明 DDR 穩定。

## 7. GPU、影片與加密

```bash
lsmod | grep -E 'panfrost|sun8i_ce'
apt-cache search '^glmark2'
command -v glmark2-es2-wayland || command -v glmark2-es2-x11 || command -v glmark2-es2
v4l2-ctl --list-devices
grep -E 'cedrus|sun8i-ce|panfrost' /proc/interrupts
```

XFCE 映像可依實際顯示後端安裝並執行 `glmark2` 對應工具；CLI 映像若沒有圖形工作階段，
不適用此 GPU 顯示測試。GPU 測試需記錄解析度、顯示後端、完整測項與 `dmesg`。
影片硬解需以實際 V4L2 request 路徑及 IRQ 變化確認，不能只以播放器可播放判定。
Crypto Engine 應同時核對
結果與 IRQ 變化。

## 8. Wi-Fi、Bluetooth、USB 與 40-pin

```bash
iw dev
rfkill list
bluetoothctl show
lsusb -t
gpiodetect
gpioinfo
i2cdetect -l
ls -l /dev/spidev* /dev/ttyS* /sys/class/pwm/pwmchip* 2>/dev/null
```

Wi-Fi 與 EMAC 應分別完成雙向吞吐。Bluetooth 至少驗證掃描、配對、重新連線、BLE
與實際目標裝置。GPIO、I2C、SPI、UART、PWM 必須使用外接迴路或已知裝置驗證，
不得只以裝置節點存在判定通過。

## 9. 回報問題

問題回報至少附上：

- 完整映像檔名與 `sha256sum`。
- 板子序號、DDR、eMMC、SD、電源及擴充板資料。
- 完整 UART 日誌，不只擷取錯誤前後數行。
- `dmesg`、`/etc/armbian-release`、`uname -a`、`bpi-h618-hw-info`。
- 可重現步驟、成功率、對照組及所有錯誤計數。

完成 EMAC、DDR 多片與外接 I/O 實機矩陣前，不得把本工程驗證版標示為量產通過。
