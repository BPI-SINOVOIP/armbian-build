# Banana Pi 全系列最佳化盤點

更新日期：2026-08-27

本報告由 `tools/bananapi-board-audit.py` 從板卡設定與受版本控制的證據登錄檔產生。建置成功、裝置節點存在及歷史映像均不會自動提升證據等級。

## 摘要

- 板卡總數：48。
- 正式 `.conf`：12；社群 `.csc`：14；開發中 `.wip`：21；停止支援 `.eos`：1。
- 證據分布：L0 6；L1 8；L2 32；L3 1；L4 1；L5 0。
- 未取得實機的板卡最高只能標示 L2；目前 L3／L4 只沿用已納入 Git 的 M4 Zero／M4 Berry 證據。

## 板卡矩陣

| 板卡 | 層級 | 名稱 | 家族 | 架構 | 核心目標 | 顯示 | 批次 | 證據 | 下一門檻 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `bananapi` | 正式 | Banana Pi | `sun7i` | `armhf` | `current,edge,legacy` | 是 | B | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapi6204` | 開發中 | Banana Pi BPI-6204 | `sun8i` | `armhf` | `legacy` | 是 | C | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapiaim7` | 開發中 | Banana Pi AIM7 | `rockchip-rk3588` | `arm64` | `vendor` | 是 | E | L1 可建置 | 完成映像內容與來源同一性守門 |
| `bananapicm2` | 開發中 | Banana Pi CM2（R2 Pro 軟體參考） | `rockchip64` | `arm64` | `current` | 是 | E | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapicm4io` | 正式 | Banana Pi CM4IO | `meson-g12b` | `arm64` | `current,edge` | 是 | A | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapicm5pro` | 開發中 | Banana Pi CM5 Pro | `rk35xx` | `arm64` | `vendor` | 是 | E | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapicm6` | 開發中 | BananaPi BPI-CM6 | `spacemit` | `riscv64` | `legacy` | 是 | F | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapif2p` | 開發中 | Banana Pi F2P | `sunplus-sp7021-bpi` | `armhf` | `legacy` | 是 | F | L1 可建置 | 完成映像內容與來源同一性守門 |
| `bananapif2s` | 開發中 | Banana Pi F2S | `sunplus-sp7021-bpi` | `armhf` | `legacy` | 是 | F | L1 可建置 | 完成映像內容與來源同一性守門 |
| `bananapif3` | 正式 | BananaPi BPI-F3 | `spacemit` | `riscv64` | `legacy,current,edge` | 是 | B | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapiforge1` | 開發中 | Banana Pi BPI-Forge1 | `rockchip` | `armhf` | `vendor` | 是 | E | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapim1plus` | 社群 | Banana Pi M1+ | `sun7i` | `armhf` | `current,edge,legacy` | 是 | C | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapim1super` | 開發中 | Banana Pi M1 Super | `rk35xx` | `arm64` | `vendor` | 是 | E | L1 可建置 | 完成映像內容與來源同一性守門 |
| `bananapim2` | 社群 | Banana Pi M2 | `sun6i` | `armhf` | `current,legacy` | 是 | C | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapim2berry` | 社群 | Banana Pi M2 Berry | `sun8i` | `armhf` | `current,edge,legacy` | 是 | C | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapim2c` | 開發中 | Banana Pi M2C | `unisoc-uis7885-bpi` | `arm64` | `vendor` | 是 | F | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapim2magic` | 社群 | Banana Pi M2 Magic | `sun8i` | `armhf` | `current,edge,legacy` | 是 | C | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapim2plus` | 正式 | Banana Pi M2+ | `sun8i` | `armhf` | `current,edge,legacy` | 是 | B | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapim2pro` | 正式 | Banana Pi M2Pro | `meson-sm1` | `arm64` | `current,edge` | 是 | A | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapim2s` | 正式 | Banana Pi M2S | `meson-g12b` | `arm64` | `current,edge` | 是 | A | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapim2ultra` | 社群 | Banana Pi M2 Ultra | `sun8i` | `armhf` | `current,edge,legacy` | 是 | C | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapim2zero` | 社群 | Banana Pi M2 Zero | `sun8i` | `armhf` | `current,edge,legacy` | 是 | C | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapim3` | 社群 | Banana Pi M3 | `sun8i` | `armhf` | `current,edge,legacy` | 是 | C | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapim4` | 開發中 | Banana Pi M4 | `realtek-rtd139x-bpi` | `arm64` | `legacy` | 是 | F | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapim4berry` | 正式 | BananaPi M4 Berry | `sun50iw9-bpi` | `arm64` | `current,edge` | 是 | R | L4 功能最佳化 | 補齊樣本數、冷啟動與發布門檻 |
| `bananapim4super` | 開發中 | Banana Pi M4 Super | `rk35xx` | `arm64` | `vendor` | 是 | E | L0 已盤點 | 確認建置鏈並建立 Trixie CLI 候選 |
| `bananapim4zero` | 正式 | BananaPi BPI-M4-Zero | `sun50iw9-bpi` | `arm64` | `current,edge` | 是 | R | L3 實機候選 | 補齊加速、I/O、多板與長時間測試 |
| `bananapim5` | 正式 | Banana Pi M5 | `meson-sm1` | `arm64` | `current,edge` | 是 | A | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapim5pro` | 正式 | Banana Pi M5 Pro | `rk35xx` | `arm64` | `edge,vendor` | 是 | B | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapim6` | 開發中 | Banana Pi M6 | `vs680` | `arm64` | `legacy` | 是 | F | L1 可建置 | 完成映像內容與來源同一性守門 |
| `bananapim64` | 社群 | Banana Pi M64 | `sun50iw1` | `arm64` | `current,edge,legacy` | 是 | C | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapim7` | 正式 | Banana Pi M7 | `rockchip-rk3588` | `arm64` | `current,edge,vendor` | 是 | B | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapip2pro` | 開發中 | Banana Pi P2 Pro | `rockchip64` | `arm64` | `current` | 否 | E | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapip2zero` | 社群 | Banana Pi P2 Zero | `sun8i` | `armhf` | `current,edge,legacy` | 是 | C | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapipro` | 社群 | Banana Pi Pro | `sun7i` | `armhf` | `current,edge,legacy` | 是 | C | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapir1` | 停止支援 | Banana Pi R1 | `sun7i` | `armhf` | `current,edge` | 是 | G | L0 已盤點 | 保留最後可用基線，不列入新發布 |
| `bananapir2` | 社群 | Banana Pi R2 | `mt7623` | `armhf` | `current` | 是 | D | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapir2pro` | 社群 | Banana Pi R2 Pro | `rockchip64` | `arm64` | `current,edge` | 是 | D | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapir3` | 開發中 | Banana Pi R3 | `filogic` | `arm64` | `current` | 否 | D | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapir3mini` | 開發中 | Banana Pi R3 Mini | `filogic` | `arm64` | `current` | 否 | D | L1 可建置 | 完成映像內容與來源同一性守門 |
| `bananapir4` | 社群 | Banana Pi R4 | `filogic` | `arm64` | `current` | 否 | D | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapir4lite` | 開發中 | Banana Pi R4 Lite | `filogic` | `arm64` | `current` | 否 | D | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapir4pro` | 開發中 | Banana Pi R4 Pro 8X | `filogic` | `arm64` | `current` | 否 | D | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapir64` | 社群 | Banana Pi R64 | `filogic` | `arm64` | `current` | 否 | D | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bananapism10` | 開發中 | BananaPi BPI-SM10 | `spacemit-k3-bpi` | `riscv64` | `current` | 是 | F | L1 可建置 | 完成映像內容與來源同一性守門 |
| `bananapiw2` | 開發中 | Banana Pi W2 | `realtek-rtd129x-bpi` | `arm64` | `legacy` | 是 | F | L1 可建置 | 完成映像內容與來源同一性守門 |
| `bananapiw3` | 開發中 | Banana Pi W3 | `rockchip-rk3588` | `arm64` | `vendor` | 是 | E | L2 軟體候選 | 執行 UART、啟動與基本周邊實機驗證 |
| `bpi-ai2n` | 正式 | Banana Pi AI2N | `renesas-rzv2n-bpi` | `arm64` | `legacy` | 是 | B | L0 已盤點 | 建立 Trixie CLI 並完成離線守門 |

## 目前開放問題

- `bananapiaim7`：AIM7 與 ArmSoM AIM7 IO 的原理圖差異尚未閉合，既有 DTS 只啟用單 lane PCIe 且 SPI／DSI 停用；GPU、VPU、RGA、NPU 使用者空間與韌體授權仍待完整映像稽核。
- `bananapicm2`：尚未取得 BPI-CM2 實際載板原理圖、連接器映射與供電拓撲；R2 Pro 僅可作同 SoC 軟體參考，不能宣稱 CM2 支援。
- `bananapicm5pro`：Linux 與 U-Boot DTS 仍以 ArmSoM CM5 IO 為 donor；官方產品身分可支持來源關聯，但尚未完成 IO 載板逐網路等同性審查。
- `bananapicm5pro`：RTL8852BS 韌體缺少逐檔再散布授權，外部驅動也尚未完成上游與安全稽核。
- `bananapicm5pro`：完整映像已通過 L2 軟體守門；冷啟動、儲存、網路、40-pin、顯示、GPU、VPU、RGA 與 NPU 尚未實機驗證。
- `bananapif2p`：ISPBOOOT.BIN 與預建工具鏈再散布授權未閉合，且缺少 F2P 專用 eMMC xboot；目前只能保留內部 SD 候選。
- `bananapif2s`：xboot 與預建工具鏈缺少完整可重建來源或明確再散布授權，完整映像只能作內部驗證。
- `bananapim1super`：Wi-Fi 量產 BOM 在 SYN43752、AP6275S 與 RTL8852BS 證據間不一致；RKBin 只可依授權隨 Rockchip 平台散布，Armbian 韌體逐檔授權與完整映像仍待驗證。
- `bananapim2c`：仍採 secure PAC 與 Yocto 混合流程，尚非一般 Armbian SD 首階段啟動。
- `bananapim6`：TZK 與 U-Boot sm.bin 缺少原始碼、重建鏈及逐檔再散布授權，C05 拓撲與實機啟動尚未驗證。
- `bananapir2`：五個 MediaTek 啟動載荷尚未取得可保存的書面再散布授權。
- `bananapir3mini`：ATF MT7986 預編譯 DRAM 物件再散布範圍未確認，eMMC boot0 安裝與 boot partition enable 尚未實機驗證。
- `bananapir4pro`：Linux 6.19.0-rc1 與 ATF MT7988 預編譯 DRAM／eFuse 物件不得視為公開發布核准。
- `bananapism10`：ESOS、PowerVR 與 VPU 韌體授權尚未閉合，載板拓撲與所有啟動媒體仍待實機驗證。
- `bananapiw2`：四個 U-Boot 靜態庫、bluecore.audio、內含工具鏈與外部文件的再散布授權尚未確認。
- `bananapiw2`：完整映像、實體開機、儲存、網路、USB、顯示、音訊及 40-pin I/O 尚未驗證。
- `bananapiw2`：Linux 4.9.119、U-Boot 2015.07 與 246 筆 vendor／DT 編譯警告仍有維護及安全風險。
- `bananapiw3`：尚未完成實機冷啟動、儲存、網路、無線、顯示與硬體加速驗證。
- `bpi-ai2n`：九個 DRP、Codec、相機與 RTL8821CU runtime 資產缺少可核對再散布授權或 ABI 契約，且尚無完整映像與實機證據。

## 欄位品質

- `bananapim1plus`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapim2`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapim2berry`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapim2magic`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapim2ultra`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapim4`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapim4super`：缺少建議欄位 `KERNEL_TEST_TARGET`。
- `bananapim5pro`：缺少建議欄位 `KERNEL_TEST_TARGET`。
- `bananapip2pro`：缺少建議欄位 `KERNEL_TEST_TARGET`。
- `bananapip2zero`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapipro`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapir1`：缺少建議欄位 `BOARD_MAINTAINER, KERNEL_TEST_TARGET`。
- `bananapir3`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapir4`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapir64`：缺少建議欄位 `BOARD_MAINTAINER`。

## 判讀限制

- 核心目標來自板卡設定；實際版本仍須以每次建置中繼資料為準。
- `.conf`、`.csc`、`.wip` 與 `.eos` 是維護層級，不是實機通過證明。
- vendor BSP、PAC、簽章與預建韌體須另外保存來源及授權邊界。
- 每次完成建置或實機驗證後，必須更新證據登錄檔並重新產生本報告。
