# Banana Pi 全系列最佳化盤點

更新日期：2026-08-28

**歷史快照，非現行發布狀態。** 本報告只呈現指定日期已納入 Git 的證據，不得取代最新候選狀態、實機驗證或對外發布核准。

本報告由 `tools/bananapi-board-audit.py` 從板卡設定與受版本控制的證據登錄檔產生。建置成功、裝置節點存在及歷史映像均不會自動提升證據等級。

## 摘要

- 板卡總數：48。
- 正式 `.conf`：12；社群 `.csc`：14；開發中 `.wip`：21；停止支援 `.eos`：1。
- 證據分布：L0 3；L1 1；L2 44；L3 0；L4 0；L5 0。
- 未取得實機的板卡最高只能標示 L2；目前沒有板卡達到完整 L3／L4／L5 門檻。

## 板卡矩陣

| 板卡 | 層級 | 名稱 | 家族 | 架構 | 核心目標 | 顯示 | 批次 | 證據 | 下一門檻 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `bananapi` | 正式 | Banana Pi | `sun7i` | `armhf` | `current,edge,legacy` | 是 | B | L2 軟體候選 | 完成 SD 冷啟動、GbE、SATA、USB 與 40-pin 外接迴路實測 |
| `bananapi6204` | 開發中 | Banana Pi BPI-6204 | `sun8i` | `armhf` | `legacy` | 是 | C | L2 軟體候選 | 完成 eMMC、SATA、GMAC、CAN、雙 RTC、UART、USB、工控 I/O 與長時間實測 |
| `bananapiaim7` | 開發中 | Banana Pi AIM7 | `rockchip-rk3588` | `arm64` | `vendor` | 是 | E | L2 軟體候選 | 完成 AIM7 與 AIM7 IO 載板差異、RKBin／韌體散布授權、冷啟動、儲存、網路、顯示、GPU、VPU、RGA、NPU、PCIe、USB 與排針實測 |
| `bananapicm2` | 開發中 | Banana Pi CM2（R2 Pro 軟體參考） | `rockchip64` | `arm64` | `current` | 是 | E | L0 已盤點 | 取得 CM2 載板原理圖與連接器映射，建立專屬 DTS 後再建置候選 |
| `bananapicm4io` | 正式 | Banana Pi CM4IO | `meson-g12b` | `arm64` | `current,edge` | 是 | A | L2 軟體候選 | 以 Hynix eMMC 完成多輪冷啟動、重新啟動、關機、網路與 USB 實測 |
| `bananapicm5pro` | 開發中 | Banana Pi CM5 Pro | `rk35xx` | `arm64` | `vendor` | 是 | E | L2 軟體候選 | 完成載板等同性與 RTL8852BS 授權審查，再做冷啟動及全介面實測 |
| `bananapicm6` | 開發中 | BananaPi BPI-CM6 | `spacemit` | `riscv64` | `legacy` | 是 | F | L2 軟體候選 | 完成 SD／eMMC 冷啟動、網路、USB、顯示與加速器實測 |
| `bananapif2p` | 開發中 | Banana Pi F2P | `sunplus-sp7021-bpi` | `armhf` | `legacy` | 是 | F | L2 軟體候選 | 閉合 ISPBOOOT.BIN 與工具鏈授權，再完成 microSD 冷啟動、網路、USB、顯示、TPM 與 GPIO 實測；eMMC 維持禁止直到取得專用 xboot |
| `bananapif2s` | 開發中 | Banana Pi F2S | `sunplus-sp7021-bpi` | `armhf` | `legacy` | 是 | F | L2 軟體候選 | 閉合 xboot 與工具鏈授權，再完成 SD／eMMC、網路、USB、顯示與 40-pin 實測 |
| `bananapif3` | 正式 | BananaPi BPI-F3 | `spacemit` | `riscv64` | `legacy,current,edge` | 是 | B | L2 軟體候選 | 完成 SD／eMMC、GbE、PCIe、USB、GPU、VPU、NPU 與 40-pin 實測 |
| `bananapiforge1` | 開發中 | Banana Pi BPI-Forge1 | `rockchip` | `armhf` | `vendor` | 是 | E | L2 軟體候選 | 完成冷啟動、雙網路、USB gadget、顯示、CAN、音訊與 40-pin 實測 |
| `bananapim1plus` | 社群 | Banana Pi M1+ | `sun7i` | `armhf` | `current,edge,legacy` | 是 | C | L2 軟體候選 | 完成 SD、GbE、SATA、Wi-Fi、Bluetooth、USB 與 40-pin 實測 |
| `bananapim1super` | 開發中 | Banana Pi M1 Super | `rk35xx` | `arm64` | `vendor` | 是 | E | L2 軟體候選 | 釐清量產無線 BOM 與韌體授權，再完成 SD／eMMC、網路、無線、顯示、GPU、VPU、USB 與 40-pin 實測 |
| `bananapim2` | 社群 | Banana Pi M2 | `sun6i` | `armhf` | `current,legacy` | 是 | C | L2 軟體候選 | 完成 SD、eMMC、Wi-Fi、Bluetooth、HDMI、USB 與 40-pin 實測 |
| `bananapim2berry` | 社群 | Banana Pi M2 Berry | `sun8i` | `armhf` | `current,edge,legacy` | 是 | C | L2 軟體候選 | 完成 SD／eMMC、SATA、GbE、無線網路、USB、顯示與 40-pin 實測 |
| `bananapim2c` | 開發中 | Banana Pi M2C | `unisoc-uis7885-bpi` | `arm64` | `vendor` | 是 | F | L0 已盤點 | 整理 41 組差異與 6,751 個未分類檔，閉合簽署鏈後建立可重放映像 |
| `bananapim2magic` | 社群 | Banana Pi M2 Magic | `sun8i` | `armhf` | `current,edge,legacy` | 是 | C | L2 軟體候選 | 完成 SD／eMMC、Wi-Fi、Bluetooth、OTG、音訊與 Lima／Cedrus 實測 |
| `bananapim2plus` | 正式 | Banana Pi M2+ | `sun8i` | `armhf` | `current,edge,legacy` | 是 | B | L2 軟體候選 | 完成無線網路、Bluetooth、HDMI、USB、GPIO 與長時間負載實測 |
| `bananapim2pro` | 正式 | Banana Pi M2Pro | `meson-sm1` | `arm64` | `current,edge` | 是 | A | L2 軟體候選 | 完成 SD／eMMC、GbE、HDMI、USB 與 40-pin 的實機回歸矩陣 |
| `bananapim2s` | 正式 | Banana Pi M2S | `meson-g12b` | `arm64` | `current,edge` | 是 | A | L2 軟體候選 | 完成 SD／eMMC、網路、USB、顯示與重新啟動實機回歸 |
| `bananapim2ultra` | 社群 | Banana Pi M2 Ultra | `sun8i` | `armhf` | `current,edge,legacy` | 是 | C | L2 軟體候選 | 完成 SD／eMMC、SATA、GbE、無線網路、USB、顯示與 40-pin 實測 |
| `bananapim2zero` | 社群 | Banana Pi M2 Zero | `sun8i` | `armhf` | `current,edge,legacy` | 是 | C | L2 軟體候選 | 完成 SD、Wi-Fi、Bluetooth、USB OTG、HDMI、Lima、Cedrus 與 40-pin 實測 |
| `bananapim3` | 社群 | Banana Pi M3 | `sun8i` | `armhf` | `current,edge,legacy` | 是 | C | L2 軟體候選 | 完成 SD／eMMC、GbE、Wi-Fi、Bluetooth、USB OTG、HDMI 與音訊實測 |
| `bananapim4` | 開發中 | Banana Pi M4 | `realtek-rtd139x-bpi` | `arm64` | `legacy` | 是 | F | L2 軟體候選 | 閉合不透明啟動與音訊載荷、工具鏈再散布授權，再完成 SD／eMMC、網路、USB、HDMI、GPU、VPU、無線與 40-pin 實測 |
| `bananapim4berry` | 正式 | BananaPi M4 Berry | `sun50iw9-bpi` | `arm64` | `current,edge` | 是 | R | L2 軟體候選 | 以 2／4 GiB 多板完成冷啟動、Wi-Fi、Bluetooth、GPU、VPU、USB、40-pin 與長時間壓力實測 |
| `bananapim4super` | 開發中 | Banana Pi M4 Super（ArmSoM Sige3 donor-only） | `rk35xx` | `arm64` | `vendor` | 是 | E | L0 已盤點 | 取得原理圖、量產 BOM 與 PCIe lane 資料，完成專屬 DTS 後再建置候選 |
| `bananapim4zero` | 正式 | BananaPi BPI-M4-Zero | `sun50iw9-bpi` | `arm64` | `current,edge` | 是 | R | L2 軟體候選 | 以 2／4 GiB 多批次板完成冷啟動、DDR 壓力、儲存、網路、顯示、媒體與 40-pin 實測 |
| `bananapim5` | 正式 | Banana Pi M5 | `meson-sm1` | `arm64` | `current,edge` | 是 | A | L2 軟體候選 | 以多家 eMMC 樣品完成冷啟動、重新啟動、關機、HDMI、USB 主機與網路實測 |
| `bananapim5pro` | 正式 | Banana Pi M5 Pro | `rk35xx` | `arm64` | `edge,vendor` | 是 | B | L2 軟體候選 | 完成儲存、網路、無線、顯示、GPU、VPU、RGA、NPU 與 40-pin 實測 |
| `bananapim6` | 開發中 | Banana Pi M6 | `vs680` | `arm64` | `legacy` | 是 | F | L2 軟體候選 | 固定最終核心設定與專有啟動載荷邊界，完成來源一致的完整映像守門 |
| `bananapim64` | 社群 | Banana Pi M64 | `sun50iw1` | `arm64` | `current,edge,legacy` | 是 | C | L2 軟體候選 | 完成 SD／eMMC、GbE、Wi-Fi、Bluetooth、USB OTG、HDMI、Lima 與 Cedrus 實測 |
| `bananapim7` | 正式 | Banana Pi M7 | `rockchip-rk3588` | `arm64` | `current,edge,vendor` | 是 | B | L2 軟體候選 | 完成儲存、網路、顯示、媒體、NPU、USB 與 40-pin 實測 |
| `bananapip2pro` | 開發中 | Banana Pi P2 Pro | `rockchip64` | `arm64` | `current` | 否 | E | L2 軟體候選 | 完成 SD／eMMC、SDIO、網路、音訊、USB 與 40-pin 實測 |
| `bananapip2zero` | 社群 | Banana Pi P2 Zero | `sun8i` | `armhf` | `current,edge,legacy` | 是 | C | L2 軟體候選 | 完成 SD／eMMC、Ethernet、USB、顯示、Lima、Cedrus 與 40-pin 實測 |
| `bananapipro` | 社群 | Banana Pi Pro | `sun7i` | `armhf` | `current,edge,legacy` | 是 | C | L2 軟體候選 | 完成 SD 冷啟動、GbE、SATA、USB 與 40-pin 外接迴路實測 |
| `bananapir1` | 停止支援 | Banana Pi R1 | `sun7i` | `armhf` | `current,edge` | 是 | G | L2 軟體候選 | 保留最後可用封存基線與安全限制，不列入新發布 |
| `bananapir2` | 社群 | Banana Pi R2 | `mt7623` | `armhf` | `current` | 是 | D | L2 軟體候選 | 取得五個啟動載荷的再散布核准，再完成 eMMC、網路、SATA、USB 與交換器實測 |
| `bananapir2pro` | 社群 | Banana Pi R2 Pro | `rockchip64` | `arm64` | `current,edge` | 是 | D | L2 軟體候選 | 完成 SD／eMMC、雙網路、SATA、PCIe、USB、顯示、媒體與 40-pin 實測 |
| `bananapir3` | 開發中 | Banana Pi R3 | `filogic` | `arm64` | `current` | 否 | D | L2 軟體候選 | 完成 SD／NOR、雙網路、SFP、SATA、PCIe、USB、無線與長時間流量實測 |
| `bananapir3mini` | 開發中 | Banana Pi R3 Mini | `filogic` | `arm64` | `current` | 否 | D | L2 軟體候選 | 釐清 ATF MT7986 DRAM 物件再散布範圍，再做空白 eMMC 的 boot0 分離寫入、boot partition enable、冷啟動及網路功能實測 |
| `bananapir4` | 社群 | Banana Pi R4 | `filogic` | `arm64` | `current` | 否 | D | L2 軟體候選 | 完成 SD／NOR、2.5GbE、SFP、PCIe、USB、Wi-Fi 7 與長時間流量實測 |
| `bananapir4lite` | 開發中 | Banana Pi R4 Lite | `filogic` | `arm64` | `current` | 否 | D | L2 軟體候選 | 先驗證候選核心穩定性，再完成 2.5GbE、SFP、PCIe、USB 與長時間流量實測 |
| `bananapir4pro` | 開發中 | Banana Pi R4 Pro 8X | `filogic` | `arm64` | `current` | 否 | D | L2 軟體候選 | 完成候選核心與預編譯 ATF 邊界審查，再做網路、SFP、PCIe、USB 與無線實測 |
| `bananapir64` | 社群 | Banana Pi R64 | `filogic` | `arm64` | `current` | 否 | D | L2 軟體候選 | 完成 SD、GbE、DSA、SATA、PCIe、USB、Wi-Fi、Bluetooth 與 GPIO90 實測 |
| `bananapism10` | 開發中 | BananaPi BPI-SM10 | `spacemit-k3-bpi` | `riscv64` | `current` | 是 | F | L1 元件可建置 | 機器化預建載荷身分，固定最終核心設定並完成來源一致的完整映像守門 |
| `bananapiw2` | 開發中 | Banana Pi W2 | `realtek-rtd129x-bpi` | `arm64` | `legacy` | 是 | F | L2 軟體候選 | 閉合四個靜態庫、bluecore.audio 與工具鏈再散布授權，再完成 SD／eMMC、SATA、PCIe、網路、USB、顯示、音訊及 40-pin 實測 |
| `bananapiw3` | 開發中 | Banana Pi W3 | `rockchip-rk3588` | `arm64` | `vendor` | 是 | E | L2 軟體候選 | 完成冷啟動、儲存、網路、無線、顯示、GPU、VPU、RGA 與 NPU 實測 |
| `bpi-ai2n` | 正式 | Banana Pi AI2N | `renesas-rzv2n-bpi` | `arm64` | `legacy` | 是 | B | L2 軟體候選 | 完成 SD 冷啟動、雙網路、USB、PCIe、顯示、相機、Panfrost、DRP-AI 與 40-pin 實測 |

## 目前開放問題

- `bananapiaim7`：AIM7 與 ArmSoM AIM7 IO 的原理圖差異尚未閉合，既有 DTS 只啟用單 lane PCIe 且 SPI／DSI 停用；GPU、VPU、RGA、NPU 使用者空間與韌體授權仍待完整映像稽核。
- `bananapicm2`：尚未取得 BPI-CM2 實際載板原理圖、連接器映射與供電拓撲；R2 Pro 僅可作同 SoC 軟體參考，不能宣稱 CM2 支援。
- `bananapicm5pro`：Linux 與 U-Boot DTS 仍以 ArmSoM CM5 IO 為 donor；官方產品身分可支持來源關聯，但尚未完成 IO 載板逐網路等同性審查。
- `bananapicm5pro`：RTL8852BS 韌體缺少逐檔再散布授權，外部驅動也尚未完成上游與安全稽核。
- `bananapicm5pro`：完整映像已通過 L2 軟體守門；冷啟動、儲存、網路、40-pin、顯示、GPU、VPU、RGA 與 NPU 尚未實機驗證。
- `bananapif2p`：ISPBOOOT.BIN 與預建工具鏈再散布授權未閉合，且缺少 F2P 專用 eMMC xboot；目前只能保留內部 SD 候選。
- `bananapif2s`：xboot 與預建工具鏈缺少完整可重建來源或明確再散布授權，完整映像只能作內部驗證。
- `bananapim1super`：Wi-Fi 量產 BOM 在 SYN43752、AP6275S 與 RTL8852BS 證據間不一致；RKBin 只可依授權隨 Rockchip 平台散布，Armbian 韌體逐檔授權仍待確認。
- `bananapim1super`：現行整合提交的完整映像已通過內部 L2 軟體守門；SD／eMMC 冷啟動、網路、無線、顯示、GPU、VPU、USB 與 40-pin 仍無實機證據。
- `bananapim2c`：仍採 secure PAC 與 Yocto 混合流程，尚非一般 Armbian SD 首階段啟動。
- `bananapim2c`：41 組追蹤差異尚未整理成可重放修補集，55 個專案共有 6,751 個未分類未追蹤檔，Unisoc 遠端也需要授權。
- `bananapim2c`：chipram、簽署與 PAC 工具、modem、Trusty、GPU、NPU、VPU、GNSS 及其他預建內容缺少完整逐項再散布授權。
- `bananapim4`：bluecore.audio、六個未以固定 MIPS 工具鏈重建的啟動影像及內含工具鏈缺少完整逐項再散布授權。
- `bananapim4`：Linux 4.9.119 與 U-Boot 2015.07 已停止受維護，核心建置仍有 230 個 vendor 警告。
- `bananapim4`：已有完整 Armbian 內部 L2 映像，但尚無 SD、eMMC、網路、USB、HDMI、GPU、VPU、無線與 40-pin 實機證據。
- `bananapim4super`：官方 M4 Super 無線模組為 SYN43752，Sige3 donor DTS 為 AP6275S；同一官方頁面的 PCIe 規格也同時出現 x1 與 x2，必須取得原理圖及 PCB 資料後才能建立真正板級描述。
- `bananapim6`：TZK 與 U-Boot sm.bin 缺少原始碼、重建鏈及逐檔再散布授權，C05 拓撲與實機啟動尚未驗證。
- `bananapir1`：板卡維持 EOS；歷史映像沒有目前來源重建、實機開機、安全更新、授權審查或公開發布證據。
- `bananapir2`：五個 MediaTek 啟動載荷尚未取得可保存的書面再散布授權。
- `bananapir3mini`：ATF MT7986 預編譯 DRAM 物件再散布範圍未確認，eMMC boot0 安裝與 boot partition enable 尚未實機驗證。
- `bananapir4pro`：Linux 6.19.0-rc1 與 ATF MT7988 預編譯 DRAM／eFuse 物件不得視為公開發布核准。
- `bananapism10`：ESOS、PowerVR 與 VPU 韌體授權尚未閉合，載板拓撲與所有啟動媒體仍待實機驗證。
- `bananapiw2`：四個 U-Boot 靜態庫、bluecore.audio、內含工具鏈與外部文件的再散布授權尚未確認。
- `bananapiw2`：完整映像已通過 L2 軟體守門；實體開機、儲存、網路、USB、顯示、音訊及 40-pin I/O 尚未驗證。
- `bananapiw2`：Linux 4.9.119、U-Boot 2015.07 與正式建置的 229 筆 vendor 警告仍有維護及安全風險；initramfs 與 APT 輸入也未達逐位元重現。
- `bananapiw3`：尚未完成實機冷啟動、儲存、網路、無線、顯示與硬體加速驗證。
- `bpi-ai2n`：九個 DRP、Codec、相機與 RTL8821CU runtime 資產缺少可核對再散布授權或 ABI 契約；完整映像已通過內部 L2 軟體守門，但仍無實機證據。

## 欄位品質

- `bananapim1plus`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapim2`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapim2berry`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapim2magic`：缺少建議欄位 `BOARD_MAINTAINER`。
- `bananapim2ultra`：缺少建議欄位 `BOARD_MAINTAINER`。
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
