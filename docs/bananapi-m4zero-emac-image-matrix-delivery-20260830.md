# Banana Pi BPI-M4 Zero EMAC 十映像工程交付紀錄

## 1. 結論

`bananapim4zeroemac` 的五個發行版、CLI／XFCE 共十個映像已完成完整建置、XZ 壓縮、
SHA-256、分割表、ext4、U-Boot、核心、initrd、DTB、模組、韌體、套件與映像角色
唯讀驗證。最終驗證器回傳碼為 0。

本結論只證明產物完整性及預期軟體內容存在，不等同 EMAC 實體連線、十套逐一開機、
多片 DDR 穩定或量產驗證。

## 2. 交付位置

```text
output/images/2026.08/bpi-m4zero-emac-a1-h618-optimized-792-matrix/
```

交付目錄包含：

- 十個 `.img.xz`。
- 十個 `.img.xz.sha`。
- `SHA256SUMS`。
- `IMAGE_MANIFEST.tsv`。
- `BUILD_PROVENANCE.tsv`。
- `VALIDATION_REPORT.txt`。
- `DELIVERY_METADATA_SHA256SUMS`。
- 繁體中文發布說明、燒錄與驗證指南、工程交付紀錄。

交付目錄不保留未壓縮 `.img` 或建置日誌。

## 3. 建置來源

| 映像組 | 數量 | 來源提交 | 說明 |
|---|---:|---|---|
| Bookworm、Trixie | 4 | `b2e663bb8afbde54307b1ee8334ed602293d70f0` | 套件清單與 sysctl 修正後重建 |
| Jammy、Noble、Resolute | 6 | `61bed876ebd608626b1d729c3cac43280d7449ae` | 既有完整產物；內容已含必要套件與新版 sysctl，經最終守門接受 |

`BUILD_PROVENANCE.tsv` 是建置程序記錄的來源提交，不是獨立的密碼學來源證明。
續跑建置器已修正為驗證既有中繼資料、提交存在性、大小、雜湊及解壓內容，並保留
原始 `source_commit`；新建置另要求來源工作樹乾淨、記錄 `userpatches` 指紋，且建置
前後提交與該指紋不得改變。既有十套映像建置時尚未記錄此指紋，因此來源限制仍以前述
程序紀錄邊界為準，`BUILD_PROVENANCE.tsv` 對這十套明確標示為 `unrecorded`，不得推定
成任何實際指紋值。
不得再把沿用產物誤標成目前 HEAD 建置。

十套映像共用：

- 板型：`bananapim4zeroemac`
- 核心：`6.18.48-current-sunxi64`
- U-Boot：`v2026.01`
- 映像內 U-Boot SHA-256：
  `57153608a7c7e80b34f1c66dfc51be46434f854817843a5100a0576797e997c7`
- DDR 工程目標：792 MHz A1 參數
- CMA：256 MiB

## 4. 最終驗證範圍

每套映像皆從交付 `.img.xz` 解壓到系統暫存目錄，不依賴工作目錄中的未壓縮映像。
驗證內容包括：

1. XZ 串流、壓縮大小、壓縮 SHA-256、原始大小及原始 SHA-256。
2. `sfdisk --verify`、唯一 ext4 分割區與 `e2fsck -fn`。
3. 映像 U-Boot 與本機 U-Boot 套件位元同一性。
4. 唯一核心版本及 `vmlinuz`、`Image`、initrd、uInitrd、`boot.cmd`、`boot.scr`、DTB；
   U-Boot 腳本資料表與腳本內容均逐位元核對。
5. initrd、核心模組目錄、`modules.dep` 與模組 `vermagic` 版本一致性。
6. AC300 EPHY、RMII、pinctrl、reset、MDIO mux、NVMEM 校正與 PWM5 2 MHz。
7. Wi-Fi／Bluetooth overlay 的 UART、RTS／CTS、GPIO、電源、速率與全部 fixup。
8. Panfrost、Cedrus、Crypto、Broadcom 無線、RTL8821CU 的設定、模組與韌體。
9. 必要工具、`ethtool` 執行檔、受控 sysctl 與 BSP 套件檔案清單。
10. 發行版代號、板型、current 核心分支、CLI／XFCE 角色與四份公開交付清單。

完整逐套輸出保存在 `VALIDATION_REPORT.txt`。

## 5. 證據限制

目前尚未取得 EMAC 擴充板。相容板型、版本、FPC 方向、接頭與供電規格仍待實物確認，
不得連接未知擴充硬體。EMAC 占用 PA0 至 PA9，AC300 時鐘另占用 PWM5／PA12。
待硬體到手後必須補做 PHY 探測、100 Mbps 全雙工、
DHCP、固定 IP、雙向 `iperf3`、錯誤計數、斷線重連、冷啟動與熱重啟。另需補齊
2 GiB／4 GiB 多片 DDR、十套逐一開機、USB、40-pin 與長時間複合壓力。

只有完成上述實機關卡並保留完整 UART 與量測證據後，才能討論正式發布或量產狀態。

## 6. 保存政策

- 壓縮映像、雜湊、清單、驗證報告與繁體中文文件保留在交付目錄。
- 建置日誌保留在 `output/debug/bpi-m4zero-emac-a1-h618-optimized-792-matrix/`。
- 中繼資料、矩陣狀態與原始映像雜湊保留在
  `.tmp/bpi-m4zero-emac-a1-h618-optimized-792-matrix/`。
- 最終驗證通過後已依 `MATRIX.tsv` 精確核對並移除十個暫存 `.img`，可用空間由
  約 200 GiB 增加至 234 GiB。中繼資料、矩陣清單、狀態檔與原始映像 SHA-256
  記錄均保留。
