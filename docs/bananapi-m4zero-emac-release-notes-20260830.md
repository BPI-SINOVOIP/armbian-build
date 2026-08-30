# Banana Pi BPI-M4 Zero EMAC 十映像發布說明

- 發布日期：2026-08-30
- 板型代號：`bananapim4zeroemac`
- 核心分支：`current`
- Linux 核心：`6.18.48-current-sunxi64`
- U-Boot：`v2026.01`
- 發布狀態：工程驗證版

## 1. 適用範圍

本映像組適用於 Banana Pi BPI-M4 Zero 與專用 EMAC 擴充硬體組合。相容擴充板的
型號、版本、FPC 方向、接頭與供電規格仍待實物確認；在取得受控硬體規格前，不得
連接未知 FPC 或擴充板。新板型使用
獨立 DTB，預設啟用 H618 AC300 internal EPHY 路徑；原有 `bananapim4zero` 板型
行為未被改變，兩種映像不可混用。

本次提供五個發行版，每個發行版各有 minimal CLI 與 XFCE 桌面映像，共十套：

| 發行版 | minimal CLI | XFCE 桌面 |
|---|---|---|
| Debian Bookworm | 已提供 | 已提供 |
| Debian Trixie | 已提供 | 已提供 |
| Ubuntu Jammy | 已提供 | 已提供 |
| Ubuntu Noble | 已提供 | 已提供 |
| Ubuntu Resolute | 已提供 | 已提供 |

CLI 適合伺服器、閘道器、嵌入式服務與自訂產品；XFCE 適合需要圖形桌面、瀏覽器、
顯示測試或一般開發工作的環境。

## 2. 主要整合內容

- BPI-M4 Zero A1 792 MHz DDR 工程目標組態，以及既有容量、Rank 與匯流排寬度自動探測。
- H618 AC300 internal EPHY、EMAC1、RMII、SID 校正與 PWM5 2 MHz 時鐘描述。
- Mali-G31 Panfrost、CMA 256 MiB 與 GPU 電源域設定。
- Cedrus 影片解碼與 sun8i Crypto Engine 核心支援。
- 板載 BCM/CYW43455 Wi-Fi、Bluetooth 與對應韌體別名。
- USB RTL8821CU 的主線 `rtw88_8821cu` 驅動與 USB modalias。
- USB OTG、GPIO、I2C、SPI、UART、PWM 與 40-pin 開發工具。
- EMAC 會占用 PA0 至 PA9，AC300 時鐘另占用 PWM5／PA12；使用 EMAC 時不得重複配置。
- `gpiod`、`i2c-tools`、`python3-libgpiod`、`python3-spidev`、`v4l-utils`、
  `ethtool` 與 H618 板型診斷工具。
- XFCE 映像額外包含 GStreamer 工具與 `libdrm-tests`。

## 3. 已完成的映像驗證

十套壓縮映像均已完成下列離線驗證：

- `.img.xz` SHA-256、XZ 串流與解壓後原始映像 SHA-256 一致。
- 分割表通過 `sfdisk --verify`，ext4 根檔案系統通過 `e2fsck -fn`。
- 映像位移 8 KiB 起的 U-Boot 與建置套件內容位元完全一致。
- `boot.scr` 可解析，腳本資料表及內容與 `boot.cmd` 一致；核心、initrd、uInitrd、
  DTB 與核心模組版本一致。
- 核心模組 `vermagic` 與 `6.18.48-current-sunxi64` 一致。
- EMAC、AC300、RMII、MDIO mux、NVMEM 校正與 PWM5 時鐘 DTB 屬性正確。
- Wi-Fi／Bluetooth overlay 的 UART、GPIO、電源與基礎 DTB fixup 正確。
- Panfrost、Cedrus、Crypto、Broadcom 無線與 RTL8821CU 核心設定、模組及韌體存在。
- 必要套件、板型工具、新版 sysctl 與 CLI／XFCE 映像角色正確。

完整結果位於同一交付目錄的 `VALIDATION_REPORT.txt`；檔案清單與雜湊位於
`IMAGE_MANIFEST.tsv`、`BUILD_PROVENANCE.tsv` 與 `SHA256SUMS`。

## 4. 重要限制

十套映像已通過建置與離線內容驗證，但尚未逐套完成實板開機。EMAC 擴充板尚未取得，
因此目前不能宣稱下列項目已通過：

- AC300 PHY 實際探測、10/100 Mbps 協商與實體連線。
- DHCP、固定 IP、雙向 `iperf3`、錯誤計數與斷線重連。
- EMAC 擴充板冷啟動、熱重啟與長時間壓力。
- 2 GiB／4 GiB 多片樣本的十次斷電冷啟動與全容量 DDR 壓力矩陣。
- 所有 USB、GPIO、I2C、SPI、UART、PWM 外接裝置與實體迴路。

既有 Noble CLI 單片 4 GiB 候選來源提交為
`069fb20fe17c862498bccd0e7cc5e3dc379c5957`；矩陣中的 Noble CLI 來源提交為
`61bed876ebd608626b1d729c3cac43280d7449ae`，兩者不是位元相同產物。前者已完成主要
非 EMAC 硬體路徑驗證，但該結果不能自動外推至矩陣映像、所有記憶體批次或量產硬體。
此發布不得標示為量產認證版本。

## 5. 取得與使用

請先閱讀同一目錄的 `BPI-M4-Zero-EMAC-燒錄與驗證指南.md`，依序完成：

1. 依用途選擇發行版與 CLI／XFCE。
2. 使用 `sha256sum -c SHA256SUMS`、`sha256sum -c DELIVERY_METADATA_SHA256SUMS` 與
   `xz -t` 核對下載內容及交付中繼資料。
3. 以明確確認過的 SD 裝置執行燒錄。
4. 保存完整 UART 開機日誌與硬體樣本資訊。
5. 硬體到手後完成 EMAC 實機驗證表，再決定是否升級發布狀態。
