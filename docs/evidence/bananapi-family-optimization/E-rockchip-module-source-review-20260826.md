# Banana Pi E 批 Rockchip 與模組板來源審查

> **歷史快照，非目前升級依據。** 本文件保留 2026-08-26 的來源審查過程；CM2 與 M4 Super 的現行 L0 邊界、外部資料需求及接受順序以 `T-three-L0-external-assistance-plan-20260828.md`、對應 `config/validation` 契約與最新全板盤點為準。

日期：2026-08-26

## 結論

E 批八張板卡涵蓋 RK3308、RK3506、RK3528、RK3568、RK3576 與 RK3588。多數設定目前是由 ArmSoM 載板直接繼承的初始移植，缺少 Banana Pi 專屬裝置樹或載板差異證據；`bananapip2pro` 是本批最乾淨的主線候選。vendor 核心、Radxa U-Boot 與 `rkbin` 多處追蹤可移動 branch，候選映像必須保存實際提交及 blob 雜湊。

## 已直接修正

- `tools/bananapi-board-audit.py` 已將 `BOARDFAMILY="rockchip"` 判定為 `armhf`，因此 `bananapiforge1` 不再被錯列為 `arm64`。
- `config/boards/bananapicm2.wip` 的 `console=ttyS02` 已修正為 `console=ttyS2`。
- `extensions/rkbin-tools.sh` 已支援 `RKBIN_GIT_REF`，`bananapip2pro` 固定使用 `rkbin` 提交 `46c4793ea2dcea7c8331fce9f07b5c80561a0395`，不再追隨 `master`。
- 這些修正都有回歸測試；它們不代表 Forge1 SPI-NAND、CM2 載板或 P2 Pro 已通過實機。

P2 Pro 在此提交使用的 RK3308 二進位雜湊如下：

| 用途 | 檔案 | SHA-256 |
| --- | --- | --- |
| DDR 訓練 | `rk33/rk3308_ddr_589MHz_uart2_m1_v1.30.bin` | `6a7e4b63bed0c131a760b4e63ad0e8ecc44f9a6315d0b761ff611af45b061250` |
| BL31 | `rk33/rk3308_bl31_v2.26.elf` | `ae2241f1387f03abc4d7ec6423af126e56029e73183dfd984e5d5ce55d9950f7` |
| miniloader | `rk33/rk3308_miniloader_sd_nand_v1.13.bin` | `ceaa5d81a652cd71e93ae3e74371744129ed4ed41fab365151b4884317456603` |

目前 `BOOT_SCENARIO=binman` 會使用 DDR 與 BL31；miniloader 雜湊保留作來源追溯，但不能宣稱它已被納入本次啟動產物。

## 共通來源風險

- `config/sources/families/rockchip-rk3588.conf` 與 `rk35xx.conf` 的 Radxa U-Boot 使用可移動 branch，vendor 核心也使用可移動 `rk-6.1-rkr5.1` branch。
- `extensions/rkbin-tools.sh` 在板卡沒有指定 ref 時仍預設取得 `rkbin` 的 `master`；除 P2 Pro 外，其餘使用者仍須逐板以提交與 SHA-256 固定。
- vendor 核心設定啟用 GPU、MPP、RGA 與 RKNPU 等驅動，但家族設定沒有固定全部使用者空間元件來源。核心節點存在不能證明 OpenGL、Vulkan、V4L2 或 NPU 實際可用。

## 逐板判定

| 板卡 | 靜態判定 | 主要缺口 | 建議下一步 |
| --- | --- | --- | --- |
| `bananapip2pro` | 本批最乾淨的 current 候選；`rkbin` 已固定 | `MINILOADER_BLOB` 在目前 binman 情境未使用；尚無實機證據 | 先建立無顯示 Trixie CLI 候選，驗證 SD、eMMC、PoE、網路與無線 |
| `bananapicm2` | 可進入 current 靜態建置 | 使用 R2 Pro defconfig 與 DTB，實際代表 CM2 加 R2 Pro 載板；UART 修正未實測 | 明確命名載板組合並建立專用差異證據 |
| `bananapim4super` | vendor 路徑靜態可建置 | 使用 ArmSoM Sige3 DTB；Sige3 的 PD 協商補丁不會自動套到 Banana Pi 板名 | 確認供電需求後共享或新增經驗證的板級補丁 |
| `bananapim1super` | U-Boot／核心拓撲不一致 | 核心使用 Sige1 DTB，U-Boot defconfig 預設 Hinlink H28K DTS | 先新增正確 U-Boot DTS 與 defconfig，禁止直接發布候選 |
| `bananapicm5pro` | vendor 初始移植 | 完整繼承 ArmSoM CM5 IO，沒有 Banana Pi 載板專屬 DTS | 先比對原理圖，再驗證 DRAM、儲存、PCIe、顯示與加速器 |
| `bananapiaim7` | vendor 初始移植 | 完整繼承 ArmSoM AIM7 IO，沒有 Banana Pi 專屬 DTS | 固定來源並比對載板 I/O 後再建置 |
| `bananapiw3` | 有歷史映像建置證據 | BPI DTS 只包住 ArmSoM W3 並改 model／compatible，尚未移植真正 BPI BSP 差異 | 先完成 DTS 差異化，再重建並驗證 SPI、儲存、網路及加速器 |
| `bananapiforge1` | U-Boot 提交已固定，實際為 `armhf` | 核心與 `rkbin` 未固定；DTS 雖有 SPI-NAND，建置框架沒有專用 NAND 打包／燒錄流程 | 先建立 SD 候選與 NAND 產物規格，兩者分開守門 |

## 建置順序

1. `bananapip2pro` current。
2. `bananapicm2` current，保留「CM2＋R2 Pro 載板」限制。
3. `bananapim4super` vendor，先解決 PD 補丁選取。
4. 修正 U-Boot DTS 後的 `bananapim1super`。
5. `bananapicm5pro`、`bananapiaim7` vendor。
6. 完成 BPI DTS 差異化後的 `bananapiw3`。
7. `bananapiforge1` 以獨立 `armhf`／SPI-NAND 工作流處理。

每張板的 DDR 訓練、冷啟動、SD、eMMC、SPI、NVMe、PCIe、網路、顯示、GPU、VPU 與 NPU 必須按實際硬體能力逐項驗證；缺少實機時最高只能標示 L2。
