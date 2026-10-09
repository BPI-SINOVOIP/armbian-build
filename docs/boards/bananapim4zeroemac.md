# BPI-M4 Zero EMAC 編譯與驗證

## 適用範圍

本板型適用於 BPI-M4 Zero 與相容 FPC24 Ethernet 擴充板。
板型代號為 `bananapim4zeroemac`，目前限定 `current` 核心分支。
不適用於 BPI-M4 Berry，也不是跨板型通用映像。

一般 `bananapim4zero` 板型保持原樣。
本板型繼承官方 M4 Zero 設定，預設啟用 `bananapi-m4-zero-fpc24-eth` overlay。
DDR、PMIC、核心與其他週邊設定沿用此基底，不加入私密實驗系統。

## 來源與變更

- 分支：`bpi-m4zeroemac-integration-20261009`。
- 基底：`813ae7cf3ccd40df1d287733e1838ed0812ceeb8`。
- 核心來源：`725bd2f3c81d54edccb66fead01d4c0c222e2231`，本批為 `6.18.55-current-sunxi64`。
- U-Boot：沿用基底的 `2026.07`，保留官方 M4 Zero 補丁，新增板型專用 SID MAC 修正。

U-Boot 為 EMAC 保留 `ethernet0`，USB Ethernet 使用 `eth1`。
MAC 沿用上游 `get_unique_sid()` 的 SID／CRC32 來源，產生本地管理單播位址。
缺省、無效與已知占位值 `de:ad:be:ef:00:00/01` 會改用 SID 衍生位址。
合法的 `ethaddr`、`eth1addr` 與 `usbnet_hostaddr` 保留。
USB device 的兩個設定須一致：優先採用合法 `eth1addr`；否則採用合法 `usbnet_devaddr`。
兩者都合法但不同時，以 `eth1addr` 覆寫 `usbnet_devaddr`，並同步 USB 驅動資料。
兩者都無效時才使用自動位址；USB device／host 與 EMAC 的自動位址互異。

U-Boot 的 EMAC 節點只用於別名與位址傳遞，仍為 `disabled`。
本修正不代表 U-Boot 已支援 EMAC 網路開機。
兩個合法但重複的自訂 MAC 不會被擅改；使用者仍須維護自訂位址的唯一性。
初始化後再匯入 U-Boot 環境的情況，不在此次自動同步範圍內。

## 取得原始碼

使用符合本倉建置要求的 Linux 主機，依根目錄說明準備環境。
本板型不需要私有倉、實驗控制工具或特定實驗室設備才能編譯。

```bash
git clone --branch bpi-m4zeroemac-integration-20261009 \
  https://github.com/BPI-SINOVOIP/armbian-build.git
cd armbian-build
```

## 編譯單一映像

Debian Trixie 最小命令列版：

```bash
./compile.sh build \
  BOARD=bananapim4zeroemac BRANCH=current RELEASE=trixie \
  BUILD_DESKTOP=no BUILD_MINIMAL=yes KERNEL_CONFIGURE=no \
  KERNELBRANCH=commit:725bd2f3c81d54edccb66fead01d4c0c222e2231 \
  IMAGE_VERSION=26.11.0-trunk-macfix1 MAKE_FOLDERS=macfix1-20261009 \
  COMPRESS_OUTPUTIMAGE=xz,sha
```

XFCE 桌面版將桌面參數改為：

```bash
BUILD_DESKTOP=yes BUILD_MINIMAL=no DESKTOP_ENVIRONMENT=xfce DESKTOP_TIER=mid
```

輸出目錄：`output/images/bananapim4zeroemac/macfix1-20261009/`。
主機若沒有快取，框架會下載來源、工具與套件並編譯。
不要使用內部批次工作才需要的 `DONT_BUILD_ARTIFACTS` 限制新環境。

## 五發行版矩陣

本批使用 `bookworm`、`trixie`、`jammy`、`noble`、`resolute`。
每個發行版各提供 minimal 與 XFCE，共十項。
minimal 是 `BUILD_MINIMAL=yes`，不是 standard CLI。
Bookworm 與 Jammy 的基底發行版配置屬社群支援，必要時使用 `EXPERT=yes`。

本批 Ubuntu 三個發行版另加 `ENABLE_EXTENSIONS=bsp-sysctl-coexist`。
Jammy 的 `systemd` 擁有 `/usr/lib/sysctl.d/50-default.conf`，與基底 BSP 同名檔案衝突。
此擴充將 BSP 副本封裝為 `60-armbian-defaults.conf`，不改參數、不強制覆寫發行版檔案。
設定排序在發行版預設值之後、使用者 `90-*.conf` 之前。
若沿用舊系統的自訂 sysctl，請重新核對；只遮蔽 `50-default.conf` 不會遮蔽此新檔。
需要覆寫 Armbian 預設值時，使用 `/etc/sysctl.d/90-*.conf`。
它只在明確啟用時生效；本批四套 Debian 不啟用，保留已完成的原始映像。
Jammy XFCE 另修正桌面套件定義：X11 測試工具使用該發行版的 `glmark2`、`glmark2-es2`。
不要求 Jammy 未提供的 `pipewire-libcamera`、`gstreamer1.0-libcamera`；不宣稱這兩項整合可用。
此修正只套用於 Jammy XFCE，不改其他發行版的桌面套件定義。
此為建置時調整；日後升級 `armbian-config` 可能覆寫該套件定義。
Ubuntu 的完整命令範例：

```bash
./compile.sh build \
  BOARD=bananapim4zeroemac BRANCH=current RELEASE=jammy EXPERT=yes \
  BUILD_DESKTOP=no BUILD_MINIMAL=yes KERNEL_CONFIGURE=no \
  KERNELBRANCH=commit:725bd2f3c81d54edccb66fead01d4c0c222e2231 \
  IMAGE_VERSION=26.11.0-trunk-macfix1 MAKE_FOLDERS=macfix1-20261009 \
  ENABLE_EXTENSIONS=bsp-sysctl-coexist COMPRESS_OUTPUTIMAGE=xz,sha
```

依同一發行版先 minimal、再 XFCE 的順序建置，可沿用快取。
不要同時對同一工作樹啟動多個建置，也不要以存在檔案代替成功紀錄。
固定核心來源可維持本批核心基線；APT 來源、桌面定義、時間戳與工具鏈仍可能改變。
本文件提供可追溯的編譯流程，不保證不同日期產出逐位元組相同。

## 燒錄與功能核對

先備份目標 SD 資料，確認目標裝置，燒錄完整映像，不只複製檔案。
本批不要求改寫 eMMC；此文件不授權覆寫現有 eMMC 系統。
依映像附帶校驗檔核對 SHA-256，首次開機自行建立安全帳密。

接上 FPC24 擴充板與網路線後執行：

```bash
uname -r
ip -br address
networkctl status end0
cat /sys/class/net/end0/address
ip -s link show end0
```

確認 Ethernet link、DHCP 與 MAC，並在正常重啟後再次核對。
實際介面名稱以 `ip -br address` 為準，不只以能 ping 通判定走的是 Ethernet。
測試 USB、Wi-Fi、GPIO 或其他週邊時，須另外安排對應驗證。

## 已知驗證範圍

本批十套均完成建置、XZ 串流與 SHA-256 核對，以及映像內容唯讀檢查。
離線檢查涵蓋開機區、U-Boot、核心、DTB、EMAC overlay 與首次設定狀態。
以下實機結果只屬於 Trixie minimal，不適用於其他九套。

2026-10-09，本批 Trixie minimal 在一片 4 GiB M4 Zero 樣品完成：

- 完整 SD 燒錄與全映像範圍回讀核對。
- 冷啟動、首次帳號設定、SSH 主機金鑰重新產生。
- Ethernet 100 Mbps 全雙工、DHCP、兩輪各十次 ping，均無丟包。
- 雙向各 60 秒 TCP 傳輸，接收端約 94.2／94.1 Mbps。
- 一次正常重啟，MAC、DHCP 位址與 SSH 主機金鑰維持一致。

反向測試有 172 次 TCP 重傳；介面錯誤與丟棄計數均為 0。
關機時出現 watchdog 無法設定十分鐘逾時的警告，但本次正常重啟完成。
這些觀察仍需後續評估，不宣稱零警告、長期穩定或多板唯一性已驗收。
其餘九套尚未完成實機驗證；後續結果應以對應版本的發布清單為準。
建置成功不等於桌面、GPU、影音、GPIO、Wi-Fi、藍牙或所有硬體功能通過實測。
