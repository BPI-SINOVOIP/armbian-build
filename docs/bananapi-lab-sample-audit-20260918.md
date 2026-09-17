# 全板唯讀抽樣紀錄

日期：2026-09-18。本表是每板一份既有映像的原配組件核對，不是硬體測試通過表。

45 板各有一份抽樣，44 份準備通過、R2 一份阻擋。涵蓋新增完整來源讀取與既有擷取證據重播；沒有重新建置或修改原映像，沒有啟用站點。其餘同板 OS／桌面映像仍需各自核對與實測，不能從這份抽樣推論全部 444 套通過。

[機器可讀清單](evidence/bpi-multiboard-integrate-20260917/sample-audit.json) 保存每份實際來源、SHA-256、準備／擷取證據、OS 與完整核心版本。下表版號以解析原配核心及元件所得為準，不只取檔名。

| 板型 | 架構 | 抽樣核心 | 結果 |
| --- | --- | --- | --- |
| `bpi-6204` | `arm32` | `6.12.90-legacy-sunxi` | 原配組件通過 |
| `bpi-ai2n` | `arm64` | `6.1.107-legacy-renesas` | 原配組件通過 |
| `bpi-aim7` | `arm64` | `6.1.115-vendor-rk35xx` | 原配組件通過 |
| `bpi-cm4io` | `arm64` | `6.18.49-current-meson64` | 原配組件通過 |
| `bpi-cm5pro` | `arm64` | `6.1.115-vendor-rk35xx` | 原配組件通過 |
| `bpi-cm6` | `riscv64` | `6.6.36-legacy-spacemit` | 原配組件通過 |
| `bpi-f2p` | `arm32` | `5.4.35-legacy-sunplus-sp7021-bpi` | 原配組件通過 |
| `bpi-f2s` | `arm32` | `5.4.35-legacy-sunplus-sp7021-bpi` | 原配組件通過 |
| `bpi-f3` | `riscv64` | `6.18.37-current-spacemit` | 原配組件通過 |
| `bpi-forge1` | `arm32` | `6.1.115-vendor-rockchip` | 原配組件通過 |
| `bpi-m1` | `arm32` | `6.18.49-current-sunxi` | 原配組件通過 |
| `bpi-m1p` | `arm32` | `6.18.46-current-sunxi` | 原配組件通過 |
| `bpi-m1super` | `arm64` | `6.1.115-vendor-rk35xx` | 原配組件通過 |
| `bpi-m2` | `arm32` | `6.18.46-current-sunxi` | 原配組件通過 |
| `bpi-m2b` | `arm32` | `6.18.46-current-sunxi` | 原配組件通過 |
| `bpi-m2m` | `arm32` | `6.18.46-current-sunxi` | 原配組件通過 |
| `bpi-m2p` | `arm32` | `6.18.49-current-sunxi` | 原配組件通過 |
| `bpi-m2pro` | `arm64` | `6.18.49-current-meson64` | 原配組件通過 |
| `bpi-m2s` | `arm64` | `6.18.49-current-meson64` | 原配組件通過 |
| `bpi-m2u` | `arm32` | `6.18.46-current-sunxi` | 原配組件通過 |
| `bpi-m2z` | `arm32` | `6.18.49-current-sunxi` | 原配組件通過 |
| `bpi-m3` | `arm32` | `6.18.46-current-sunxi` | 原配組件通過 |
| `bpi-m4` | `arm64` | `4.9.119-legacy-realtek-rtd139x-bpi` | 原配組件通過 |
| `bpi-m4b` | `arm64` | `6.18.49-current-sunxi64` | 原配組件通過 |
| `bpi-m4z` | `arm64` | `6.18.49-current-sunxi64` | 原配組件通過 |
| `bpi-m4z-emac` | `arm64` | `6.18.49-current-sunxi64` | 原配組件通過 |
| `bpi-m5` | `arm64` | `6.18.49-current-meson64` | 原配組件通過 |
| `bpi-m5pro` | `arm64` | `7.0.14-edge-rockchip64` | 原配組件通過 |
| `bpi-m6` | `arm64` | `5.4.195-legacy-vs680` | 原配組件通過 |
| `bpi-m64` | `arm64` | `6.18.46-current-sunxi64` | 原配組件通過 |
| `bpi-m7` | `arm64` | `6.18.49-current-rockchip64` | 原配組件通過 |
| `bpi-p2pro` | `arm64` | `6.18.49-current-rockchip64` | 原配組件通過 |
| `bpi-p2z` | `arm32` | `6.18.49-current-sunxi` | 原配組件通過 |
| `bpi-pro` | `arm32` | `6.18.49-current-sunxi` | 原配組件通過 |
| `bpi-r2` | `arm32` | `6.6.153-current-mt7623` | 原映像缺檔 |
| `bpi-r2pro` | `arm64` | `6.18.46-current-rockchip64` | 原配組件通過 |
| `bpi-r3` | `arm64` | `6.12.82-current-filogic` | 原配組件通過 |
| `bpi-r3mini` | `arm64` | `6.12.82-current-filogic` | 原配組件通過 |
| `bpi-r4` | `arm64` | `6.12.82-current-filogic` | 原配組件通過 |
| `bpi-r4lite` | `arm64` | `6.17.0-rc1-current-filogic` | 原配組件通過 |
| `bpi-r4pro` | `arm64` | `6.19.0-rc1-current-filogic` | 原配組件通過 |
| `bpi-r64` | `arm64` | `6.12.82-current-filogic` | 原配組件通過 |
| `bpi-sm10` | `riscv64` | `6.18.3-current-spacemit-k3-bpi` | 原配組件通過 |
| `bpi-w2` | `arm64` | `4.9.119-legacy-realtek-rtd129x-bpi` | 原配組件通過 |
| `bpi-w3` | `arm64` | `6.1.115-vendor-rk35xx` | 原配組件通過 |

## 已確認的原映像問題

R2 的 Bookworm minimal 原環境指定 `/boot/dtb/mediatek/mt7623n-bananapi-bpi-r2`，完整映像擷取確認該路徑不存在。工具不自動添加 `.dtb`、改 DTB 或重包映像；R2 可用原配範例的軟體回歸與此來源阻擋分開記錄。修正產品映像須另留來源改動、重新建置及新的雜湊，不可覆寫這份證據來消除失敗。

F2P／F2S 抽樣已解析到 `5.4.35-legacy-sunplus-sp7021-bpi`，但這不解除舊佇列全部 20 套檔名版號為 `0` 的問題；每套仍須內容核對。CM6 抽樣實際採 `6.6.36-legacy-spacemit` 的 FIT，不能用另一張映像或 current 分支版號代替。

## 工具與實板界線

- `prepared` 只代表該份原配組件與來源核對通過，不表示已有可部署資格、原廠完整啟動鏈通過或平台所有功能可用。
- 原入口的 FIT／原廠 ABI、共用後端的階段證據與中斷恢復仍依[總計畫](bananapi-multiboard-lab-plan-20260917.md)逐項驗收，不能用本表代替。
- UART、電源、媒體配對與備份授權、救援首次部署、單套循環、故障返回及長時間壓力測試均需實板證據。
- 舊失敗目錄保留；最新結果依清單的明示證據選取，不用「任一舊版本曾通過」覆蓋較新的失敗。
