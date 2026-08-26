# Banana Pi E 批 Rockchip 與模組板來源審查

日期：2026-08-26

## 結論

E 批八張板卡涵蓋 RK3308、RK3506、RK3528、RK3568、RK3576 與 RK3588。多數設定目前是由 ArmSoM 載板直接繼承的初始移植，缺少 Banana Pi 專屬裝置樹或載板差異證據；`bananapip2pro` 是本批最乾淨的主線候選。vendor 核心、Radxa U-Boot 與 `rkbin` 多處追蹤可移動 branch，候選映像必須保存實際提交及 blob 雜湊。

## 已直接修正

- `tools/bananapi-board-audit.py` 已將 `BOARDFAMILY="rockchip"` 判定為 `armhf`，因此 `bananapiforge1` 不再被錯列為 `arm64`。
- `config/boards/bananapicm2.wip` 的 `console=ttyS02` 已修正為 `console=ttyS2`。
- 兩項修正都有回歸測試；它們不代表 Forge1 SPI-NAND 或 CM2 載板已通過實機。

## 共通來源風險

- `config/sources/families/rockchip-rk3588.conf` 與 `rk35xx.conf` 的 Radxa U-Boot 使用可移動 branch，vendor 核心也使用可移動 `rk-6.1-rkr5.1` branch。
- `extensions/rkbin-tools.sh` 預設取得 `rkbin` 的 `master`；DDR、BL31、TEE 雖有版本化檔名，內容仍須以提交與 SHA-256 固定。
- vendor 核心設定啟用 GPU、MPP、RGA 與 RKNPU 等驅動，但家族設定沒有固定全部使用者空間元件來源。核心節點存在不能證明 OpenGL、Vulkan、V4L2 或 NPU 實際可用。

## 逐板判定

| 板卡 | 靜態判定 | 主要缺口 | 建議下一步 |
| --- | --- | --- | --- |
| `bananapip2pro` | 本批最乾淨的 current 候選 | `MINILOADER_BLOB` 在目前 binman 情境未使用；仍需固定 `rkbin` | 先建立無顯示 Trixie CLI 候選，驗證 SD、eMMC、PoE、網路與無線 |
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
