# Banana Pi 主線同步與 BPI-M4 Zero EMAC 完整最佳化計劃

日期：2026-08-29  
狀態：執行中，首套 Noble CLI 已完成離線驗證，待實板 Gate

整合分支：`bpi-integration-20260829`

## 1. 目的

本計劃同時解決兩個長期問題：

1. 讓 `BPI-SINOVOIP/armbian-build` 的 `main` 保持為官方
   `armbian/build` 的乾淨鏡像，使後續能持續快轉同步。
2. 從最新官方 `main` 建立可維護的 Banana Pi 整合分支，重新移植既有
   BPI-M4 Zero DDR 與 BPI-M4 Berry 最佳化，並建立預設啟用 EMAC 的
   BPI-M4 Zero 衍生板型。

最終板型代號固定為：

```text
BOARD=bananapim4zeroemac
BOARD_NAME="BananaPi BPI-M4-Zero EMAC"
BOOT_FDT_FILE="sun50i-h618-bananapi-m4-zero-emac.dtb"
```

本計劃不改變既有 `bananapim4zero` 的預設行為，也不把新硬體能力偽裝成
既有板型，避免映像、裝置樹與實機證據混淆。

## 2. 已確認基線

2026-08-29 已完成以下同步：

| 項目 | 提交碼 |
|---|---|
| BPI 同步前 `main` | `4a09ca32ab703cc8f175513dd85632d48c09bf86` |
| 官方最新 `main` | `eb6801d30cf7da1e93fac84e6e33d600981392b1` |
| BPI 同步後遠端 `main` | `eb6801d30cf7da1e93fac84e6e33d600981392b1` |
| 新整合分支起點 | `eb6801d30cf7da1e93fac84e6e33d600981392b1` |

同步前 BPI `main` 超前官方零筆、落後 1,134 筆，且為官方 `main` 的直接
祖先，因此使用 `--ff-only` 完成同步，沒有重寫歷史。

既有 `bananapi-family-optimization-20260826` 相對同步前官方基線包含 369 筆
自有提交、670 個異動路徑。新版官方與該分支共有 35 個重疊路徑，集中在板型
設定、核心設定、核心 patch series 與建置框架。禁止把該分支整體合併到新
整合分支，必須依功能主題重新移植。

## 3. 分支治理

### 3.1 `main`

- 只接受官方 `armbian/build main` 的快轉同步。
- 不加入 BPI 板型、文件、證據、工具或映像修改。
- 每次同步使用 `git merge --ff-only upstream/main`。

### 3.2 `bpi-integration-20260829`

- 接收已移植、已通過本機守門的 Banana Pi 功能。
- 每個階段完成後獨立提交並推送。
- 不以合併舊整合分支取代主題移植。

### 3.3 主題分支

需要送交官方或高風險的功能，從最新官方 `main` 建立單一目的分支：

```text
feature/bpi-m4zero-ddr
feature/bpi-h618-io
feature/bpi-m4zero-emac
feature/bpi-m4berry-optimization
```

一個主題只處理一個可審查問題。BPI 內部證據、交付映像與客戶文件不混入
送官方的程式碼提交。

## 4. 官方最新狀態

官方 `main` 已包含：

- `CONFIG_AC300_PHY=y`。
- H616/H618 internal EPHY 的 `dwmac-sun8i` 支援。
- BPI-M4 Zero AC300 PHY、MDIO mux、SID 校正資料與 PWM5 2 MHz 時鐘。
- `sun50i-h616-bananapi-m4-zero-fpc24-eth.dtbo`。
- BPI-M4 Zero 類比音訊 codec 啟用。
- M4 Zero EMAC 維持預設停用，透過 FPC24 overlay 啟用。

因此禁止重新加入舊版約 3,933 行的 `sunxi-gmac` 廠商驅動，也禁止使用已知
錯誤的外接 RMII PHY 位址 1 描述。新板型必須使用官方已合併的 AC300 internal
EPHY 路徑。

Orange Pi Zero3 的 `EMAC0 + RGMII` 只作外接千兆 PHY 參考，不是本板 EMAC
實作來源。BPI-M4 Zero 使用 `EMAC1 + AC300 internal EPHY`，經 24-pin FPC
輸出 10/100 Mbps 乙太網路訊號。

## 5. 舊分支移植分類

每項舊修改必須先歸類，再決定處理方式：

| 分類 | 處理方式 |
|---|---|
| 已進官方 | 不移植，記錄對應官方提交 |
| 板型專用且仍有效 | 重新套用到新版檔案結構 |
| 可通用修正 | 建立獨立主題提交並準備官方 PR |
| 舊框架 API | 依新版 API 重寫，不直接 cherry-pick |
| 測試與證據 | 保留於 BPI 整合分支，不送官方 |
| 重複映像或快取 | 通過雜湊與引用稽核後清理 |

優先移植範圍：

1. BPI-M4 Zero A1 792 MHz DDR 設定與診斷。
2. BPI-M4 Zero 主線 `rtw88_8821cu` 修正，移除錯誤黑名單。
3. M4 Berry 的 GPU、Cedrus、Crypto 與 CMA 最佳化。
4. GPIO、I2C、SPI、UART、PWM overlays、套件與權限。
5. BPI-WiringPi2、RPi.GPIO 固定來源相容工具。
6. 新板型 `bananapim4zeroemac`。

## 6. DDR 移植

M4 Zero A1 工程候選維持 792 MHz 與 upstream 容量、Rank、bus width 自動探測：

```text
dx_odt=0x07070707
dx_dri=0x0e0e0e0e
ca_dri=0x0d0d
odt_en=0xaaaaeeee
tpr6=0x3a808080
tpr10=0x402f6663
tpr11=0x25252523
tpr12=0x110f0f10
```

現有 M4 Zero 仍固定 U-Boot `v2026.01`，所以第一階段不在同一提交中升級
U-Boot。先將 A1 補丁移植到官方現有 `v2026.01` 板型 patch，確保與歷史實測
基線可比；另開主題評估 `v2026.07` 移植，兩者不可混成單一變更。

一般映像保留結構化 DDR Build ID，但關閉可互動 DDR lab。DDR lab 只存在於
專用測試產物，不能進入正式交付映像。

## 7. M4 Berry 最佳化移植到新板型

新板型必須納入下列已存在於 M4 Berry 工作的能力，但要改成 H618 共用或
M4 Zero EMAC 專用名稱，不直接複製帶有 Berry 型號判斷的腳本：

- Mali-G31 Panfrost 與 `mali-supply`。
- `CONFIG_SUN50I_H6_PRCM_PPU=y`，避免冷開機 GPU 電源域探測競態。
- Cedrus H616/H618 600 MHz 能力設定。
- `cma=256M`，供影片解碼與顯示緩衝使用。
- Crypto Engine 與 AF_ALG 驗證。
- `gpiod`、`i2c-tools`、`python3-libgpiod`、`python3-spidev`、`v4l-utils`。
- 桌面映像的 GStreamer 工具與 `libdrm-tests`。
- 40-pin GPIO、I2C、SPI、UART、PWM overlays。
- 一般使用者透過受控群組存取裝置節點的 udev 規則。
- 固定提交版本的 BPI-WiringPi2 與 RPi.GPIO 安裝器。

Wi-Fi／Bluetooth 預設 overlay 與 UART1 overlay 的衝突必須由檢查工具與文件
明確阻止，不得默默同時啟用。

## 8. 新板型與 EMAC

新板型使用獨立板型設定與獨立 DTB，並保留既有 M4 Zero 為回歸控制組。

新 DTB 應從共用 M4 Zero 硬體描述衍生，只有下列差異：

- 新 `model` 與新板型 `compatible`。
- 預設啟用 GPU。
- 預設啟用 PWM、PWM5 與 AC300 EMAC。
- 保留 SD、eMMC、USB OTG、RTL8821CU、Bluetooth、HDMI、音訊與 40-pin。

EMAC 不依賴 U-Boot 網路功能，Linux 開機不得因網路未連線失敗。U-Boot TFTP
或 PXE 不列入本階段必要條件，後續若需要必須另建主題並單獨驗證。

FPC24 乙太網路會使用 PA0 至 PA9 與 PWM5／PA12。這些資源啟用後不可再作
其他 pinmux 用途；目前 M4 Zero 40-pin 主要使用 PG、PH、PI，不應直接衝突，
仍須以原理圖與最終 DTB 自動檢查。

## 9. 靜態與建置守門

每個實作階段至少執行：

1. Shell 語法與 ShellCheck。
2. 新增 Python 測試與既有 M4 Zero／M4 Berry 回歸測試。
3. 核心 patch series 完整套用。
4. U-Boot patch series 完整套用。
5. DTB 與 overlays 編譯。
6. 以 `fdtdump`／`fdtget` 檢查最終 DTB，而非只檢查來源文字。
7. 核心設定檢查 `AC300_PHY`、`DWMAC_SUN8I`、Panfrost、Cedrus、Crypto。
8. rootfs 套件、udev、CMA 與預設 overlays 檢查。
9. 既有 `bananapim4zero` 最終 DTB 行為不變檢查。

不得以 GitHub Actions 作為品質證明；所有守門在本機確定性執行並記錄命令、
結果、提交碼與產物雜湊。

## 10. 首套實板驗證

先完整編譯 Noble CLI `current` 映像，完成以下項目後才准許展開十映像矩陣：

- 2 GiB 與 4 GiB DDR 容量、Rank 與全容量壓力測試。
- 完全斷電冷啟動至少 `10/10`。
- SD 與 eMMC 開機及並行 I/O 壓力。
- AC300 PHY 正確綁定，不能出現 `No PHY found` 或 `EMAC reset timeout`。
- 10/100 Mbps 協商、100 Mbps 全雙工、DHCP 與固定 IP。
- 雙向 `iperf3`、錯誤計數、斷線重連、冷啟動與重新啟動。
- GPU Panfrost、GLES、Cedrus、Crypto Engine 與 HDMI。
- RTL8821CU Wi-Fi、Bluetooth、USB OTG。
- GPIO、I2C、SPI、UART、PWM 基礎與實體迴路測試。

EMAC 吞吐的工程目標為接近 Fast Ethernet 線速；不得只用介面存在或能取得
DHCP 位址判定成功。

## 11. 十映像矩陣

首套映像通過後，完整建置：

| 發行版 | CLI | XFCE |
|---|---|---|
| Bookworm | 必須 | 必須 |
| Jammy | 必須 | 必須 |
| Noble | 必須 | 必須 |
| Resolute | 必須 | 必須 |
| Trixie | 必須 | 必須 |

每套只交付 `.img.xz`、對應 SHA-256、繁體中文發行說明與驗證手冊。未壓縮
`.img` 僅在建置與檢查期間保留，矩陣完成並核對壓縮串流後才依空間政策清理。

## 12. 磁碟空間與資料清理

目前 `/media/pi/SMCI` 可用空間約 268 GiB。十映像前必須估算最壞用量並保留
安全餘量。清理只處理可重建快取、重複未壓縮映像與已有新版本替代的交付副本。

清理前必須：

1. 建立候選清單、大小、SHA-256 與引用位置。
2. 確認新版映像已完成壓縮串流和唯讀內容檢查。
3. 確認 Google Drive 交付目錄或正式封存已有唯一保留副本。
4. 不刪除原始實板 UART、測試證據、來源補丁或尚未推送提交。

禁止使用無清單的大範圍刪除命令。

## 13. 階段提交與推送

依下列順序提交，每階段通過本機守門後立即推送：

1. `計劃：建立主線同步與 M4 Zero EMAC 整合流程`
2. `移植：恢復 M4 Zero A1 DDR 與主線 Wi-Fi`
3. `移植：整合 H618 GPU 媒體與 40-pin 基線`
4. `新增：建立 BPI-M4 Zero EMAC 獨立板型`
5. `驗證：加入 M4 Zero EMAC 確定性回歸守門`
6. `交付：完成首套實板候選與驗證工具`
7. `交付：完成十映像矩陣與發行文件`

每次推送後核對遠端分支提交碼，不能只依本機輸出判定成功。

## 14. 官方回送策略

下列項目優先評估送官方 Armbian：

- 不含 BPI 私有流程的通用 H618 GPIO／媒體修正。
- BPI-M4 Zero／M4 Berry 可重現的板型設定與 DT 修正。
- 有多板實證且不依賴 BPI 映像打包工具的 U-Boot DDR 修正。
- 能在官方最新 `main` 獨立通過的測試。

已進官方的 AC300 支援不重複送出。BPI 客戶文件、映像矩陣、原始 UART 與
內部資格資料保留於 BPI 分支，不放入官方 PR。

## 15. 回退與中斷續作

- `main` 保持官方乾淨基線，可隨時重新建立整合分支。
- 既有開發分支全部保留，不改寫、不刪除。
- 每個移植階段有獨立提交，可回退單一主題而不影響其他板型。
- 首套映像未通過時停止十映像矩陣，但繼續保存編譯與失敗證據。
- 任務中斷時，以本文件、遠端提交與測試紀錄恢復，不依賴對話記憶。

## 16. 完成定義

只有同時符合以下條件才視為完成：

1. BPI `main` 可持續快轉官方 `main`。
2. 新整合分支含可追溯的既有 Banana Pi 最佳化移植。
3. `bananapim4zeroemac` 可從乾淨環境完整編譯。
4. AC300 EMAC、DDR、GPU、媒體、無線、USB 與 40-pin 完成實板驗證。
5. 十套映像、SHA-256、發行說明與驗證手冊完整。
6. 所有守門結果和實板限制均如實記錄。
7. 可通用修改已整理成可供官方審查的獨立主題提交。

## 17. 執行進度

截至 2026-08-30 已完成：

- BPI `main` 快轉至官方 `main` 的 `eb6801d30cf7da1e93fac84e6e33d600981392b1`。
- 建立並推送 `bpi-integration-20260829`。
- 移植 M4 Zero A1 DDR、RTL8821CU、H618 GPU／媒體／40-pin 基線。
- 建立 `bananapim4zeroemac`、獨立 DTB 及 Wi-Fi／Bluetooth overlay。
- 實際完成 U-Boot、DTB、Linux 6.18.48 與 Noble CLI 完整映像建置。
- 以唯讀掛載檢查 DTB、overlay、CMA、套件與板型工具。
- 驗證原始映像、XZ 串流及解壓後內容 SHA-256 一致。

尚未完成：

- 2 GiB／4 GiB 多片實板冷啟動與全容量 DDR 壓力。
- SD／eMMC、AC300 EMAC、GPU、Cedrus、無線、USB 與 40-pin 實體驗證。
- 首套實板 Gate 通過後的十映像矩陣。
- 依實證整理可送官方的獨立主題分支。

首套候選的完整紀錄位於
`docs/bananapi-m4zero-emac-noble-cli-candidate-20260830.md`。依第 10、11 與
15 節規則，首套實板 Gate 未完成前不展開十映像矩陣，也不宣稱正式發布。
