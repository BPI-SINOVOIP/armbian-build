# Banana Pi 全系列最佳化盤點

更新日期：2026-08-26

本報告由 `tools/bananapi-board-audit.py` 從板卡設定與受版本控制的證據登錄檔產生。建置成功、裝置節點存在及歷史映像均不會自動提升證據等級。

## 摘要

- 板卡總數：48。
- 正式 `.conf`：12；社群 `.csc`：13；開發中 `.wip`：22；停止支援 `.eos`：1。
- 證據分布：L0 46；L1 0；L2 0；L3 1；L4 1；L5 0。
- 未取得實機的板卡最高只能標示 L2；目前 L3／L4 只沿用已納入 Git 的 M4 Zero／M4 Berry 證據。

## 板卡矩陣

| 板卡 | 層級 | 名稱 | 家族 | 架構 | 核心目標 | 顯示 | 批次 | 證據 | 下一門檻 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `bananapi` | 正式 | Banana Pi | `sun7i` | `armhf` | `current,edge,legacy` | 是 | B | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapi6204` | 開發中 | Banana Pi BPI-6204 | `sun8i` | `armhf` | `legacy` | 是 | C | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapiaim7` | 開發中 | Banana Pi AIM7 | `rockchip-rk3588` | `arm64` | `vendor` | 是 | E | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapicm2` | 開發中 | Banana Pi CM2 | `rockchip64` | `arm64` | `current,edge` | 是 | E | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapicm4io` | 正式 | Banana Pi CM4IO | `meson-g12b` | `arm64` | `current,edge` | 是 | A | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapicm5pro` | 開發中 | Banana Pi CM5 Pro | `rk35xx` | `arm64` | `vendor` | 是 | E | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapicm6` | 開發中 | BananaPi BPI-CM6 | `spacemit` | `riscv64` | `legacy` | 是 | F | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapif2p` | 開發中 | Banana Pi F2P | `sunplus-sp7021-bpi` | `armhf` | `legacy` | 是 | F | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapif2s` | 開發中 | Banana Pi F2S | `sunplus-sp7021-bpi` | `armhf` | `legacy` | 是 | F | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapif3` | 正式 | BananaPi BPI-F3 | `spacemit` | `riscv64` | `legacy,current,edge` | 是 | B | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapiforge1` | 開發中 | Banana Pi BPI-Forge1 | `rockchip` | `arm64` | `vendor` | 是 | E | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapim1plus` | 社群 | Banana Pi M1+ | `sun7i` | `armhf` | `current,edge,legacy` | 是 | C | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapim1super` | 開發中 | Banana Pi M1 Super | `rk35xx` | `arm64` | `vendor` | 是 | E | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapim2` | 社群 | Banana Pi M2 | `sun6i` | `armhf` | `current,legacy` | 是 | C | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapim2berry` | 社群 | Banana Pi M2 Berry | `sun8i` | `armhf` | `current,edge,legacy` | 是 | C | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapim2c` | 開發中 | Banana Pi M2C | `unisoc-uis7885-bpi` | `arm64` | `vendor` | 是 | F | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapim2magic` | 社群 | Banana Pi M2 Magic | `sun8i` | `armhf` | `current,edge,legacy` | 是 | C | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapim2plus` | 正式 | Banana Pi M2+ | `sun8i` | `armhf` | `current,edge,legacy` | 是 | B | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapim2pro` | 正式 | Banana Pi M2Pro | `meson-sm1` | `arm64` | `current,edge` | 是 | A | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapim2s` | 正式 | Banana Pi M2S | `meson-g12b` | `arm64` | `current,edge` | 是 | A | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapim2ultra` | 社群 | Banana Pi M2 Ultra | `sun8i` | `armhf` | `current,edge,legacy` | 是 | C | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapim2zero` | 社群 | Banana Pi M2 Zero | `sun8i` | `armhf` | `current,edge,legacy` | 是 | C | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapim3` | 社群 | Banana Pi M3 | `sun8i` | `armhf` | `current,edge,legacy` | 是 | C | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapim4` | 開發中 | Banana Pi M4 | `realtek-rtd139x-bpi` | `arm64` | `legacy` | 是 | F | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapim4berry` | 正式 | BananaPi M4 Berry | `sun50iw9-bpi` | `arm64` | `current,edge` | 是 | R | L4 功能最佳化 | 補齊樣本數、冷啟動與發布門檻 |
| `bananapim4super` | 開發中 | Banana Pi M4 Super | `rk35xx` | `arm64` | `vendor` | 是 | E | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapim4zero` | 正式 | BananaPi BPI-M4-Zero | `sun50iw9-bpi` | `arm64` | `current,edge` | 是 | R | L3 實機候選 | 補齊加速、I/O、多板與長時間測試 |
| `bananapim5` | 正式 | Banana Pi M5 | `meson-sm1` | `arm64` | `current,edge` | 是 | A | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapim5pro` | 正式 | Banana Pi M5 Pro | `rk35xx` | `arm64` | `edge,vendor` | 是 | B | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapim6` | 開發中 | Banana Pi M6 | `vs680` | `arm64` | `legacy` | 是 | F | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapim64` | 社群 | Banana Pi M64 | `sun50iw1` | `arm64` | `current,edge,legacy` | 是 | C | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapim7` | 正式 | Banana Pi M7 | `rockchip-rk3588` | `arm64` | `current,edge,vendor` | 是 | B | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapip2pro` | 開發中 | Banana Pi P2 Pro | `rockchip64` | `arm64` | `current` | 否 | E | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapip2zero` | 社群 | Banana Pi P2 Zero | `sun8i` | `armhf` | `current,edge,legacy` | 是 | C | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapipro` | 社群 | Banana Pi Pro | `sun7i` | `armhf` | `current,edge,legacy` | 是 | C | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapir1` | 停止支援 | Banana Pi R1 | `sun7i` | `armhf` | `current,edge` | 是 | G | L0 已盤點 | 保留最後可用基線，不列入新發布 |
| `bananapir2` | 社群 | Banana Pi R2 | `mt7623` | `armhf` | `current` | 是 | D | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapir2pro` | 社群 | Banana Pi R2 Pro | `rockchip64` | `arm64` | `current,edge` | 是 | D | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapir3` | 開發中 | Banana Pi R3 | `filogic` | `arm64` | `current` | 否 | D | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapir3mini` | 開發中 | Banana Pi R3 Mini | `filogic` | `arm64` | `current` | 否 | D | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapir4` | 社群 | Banana Pi R4 | `filogic` | `arm64` | `current` | 否 | D | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |
| `bananapir4lite` | 開發中 | Banana Pi R4 Lite | `filogic` | `arm64` | `current` | 否 | D | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapir4pro` | 開發中 | Banana Pi R4 Pro 8X | `filogic` | `arm64` | `current` | 否 | D | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapir64` | 開發中 | Banana Pi R64 | `filogic` | `arm64` | `current` | 否 | D | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapism10` | 開發中 | BananaPi BPI-SM10 | `spacemit-k3-bpi` | `riscv64` | `current` | 是 | F | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapiw2` | 開發中 | Banana Pi W2 | `realtek-rtd129x-bpi` | `arm64` | `legacy` | 是 | F | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapiw3` | 開發中 | Banana Pi W3 | `rockchip-rk3588` | `arm64` | `vendor` | 是 | E | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bpi-ai2n` | 正式 | Banana Pi AI2N | `renesas-rzv2n-bpi` | `arm64` | `legacy` | 是 | B | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |

## 目前開放問題

- `bananapim2c`：仍採 secure PAC 與 Yocto 混合流程，尚非一般 Armbian SD 首階段啟動。
- `bananapism10`：原廠啟動二進位與實機開機仍未完成驗證。

## 欄位品質

- `bananapiaim7`：缺少建議欄位 `KERNEL_TEST_TARGET`。
- `bananapicm5pro`：缺少建議欄位 `KERNEL_TEST_TARGET`。
- `bananapicm6`：缺少建議欄位 `KERNEL_TEST_TARGET`。
- `bananapif2s`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapif3`：缺少建議欄位 `KERNEL_TEST_TARGET`。
- `bananapiforge1`：缺少建議欄位 `KERNEL_TEST_TARGET`。
- `bananapim1plus`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapim1super`：缺少建議欄位 `KERNEL_TEST_TARGET`。
- `bananapim2`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapim2berry`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapim2magic`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapim2ultra`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapim4`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapim4super`：缺少建議欄位 `KERNEL_TEST_TARGET`。
- `bananapim5pro`：缺少建議欄位 `KERNEL_TEST_TARGET`。
- `bananapim6`：缺少建議欄位 `KERNEL_TEST_TARGET`。
- `bananapip2pro`：缺少建議欄位 `KERNEL_TEST_TARGET`。
- `bananapip2zero`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapipro`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapir1`：缺少建議欄位 `BOARD_MAINTAINER, KERNEL_TEST_TARGET`。
- `bananapir2`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapir2pro`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapir3`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapir3mini`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapir4`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapir4lite`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapir4pro`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapir64`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapism10`：缺少建議欄位 `KERNEL_TEST_TARGET`。
- `bananapiw2`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapiw3`：缺少建議欄位 `BOARD_MAINTAINER, KERNEL_TEST_TARGET`。

## 判讀限制

- 核心目標來自板卡設定；實際版本仍須以每次建置中繼資料為準。
- `.conf`、`.csc`、`.wip` 與 `.eos` 是維護層級，不是實機通過證明。
- vendor BSP、PAC、簽章與預建韌體須另外保存來源及授權邊界。
- 每次完成建置或實機驗證後，必須更新證據登錄檔並重新產生本報告。
