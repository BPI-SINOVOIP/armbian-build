# BPI-M4 Zero 跨批次 X2 實機驗證計畫

## 目標與樣本

目前可用樣本為四片 BPI-M4 Zero 與兩片 H618 BPI-M4B：

| 群組 | 樣本 | 狀態 |
| --- | --- | --- |
| BPI-M4 Zero 已知批次 | `0438` | 已完成 M4ZLAB2 熱重設矩陣 |
| BPI-M4 Zero 已知批次 | `1116` | 已完成 M4ZLAB2 熱重設矩陣 |
| BPI-M4 Zero 三星 DDR 批次 | 待記錄板號 A | 現場已確認三星，完整料號待盤點 |
| BPI-M4 Zero 三星 DDR 批次 | 待記錄板號 B | 現場已確認三星，完整料號待盤點 |
| BPI-M4B | 待記錄板號 C | 禁止使用 M4 Zero 映像 |
| BPI-M4B | 待記錄板號 D | 禁止使用 M4 Zero 映像 |

第一階段只驗證四片 BPI-M4 Zero 的同一份 X2 映像。BPI-M4B 另建板級
基線、實驗 SPL、映像及結果目錄，不納入 M4 Zero 通過率。

## 固定輸入

```text
output/images/2026.08/bpi-m4zero-cross-board-792/
Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_x2-cross-board-792mhz.img.xz
```

壓縮映像 SHA-256：

```text
3bff7ae94ffdc6e38fb5241646204dbe6ede9b6556028924bd54626ecc670fbd
```

所有板必須使用同一張已回讀確認的 SD 卡或逐張記錄燒錄回讀雜湊。不得在
測試途中改 bootloader、核心、裝置樹或 rootfs。

## 執行順序

1. 先完成目前 `1116` 的 X2 標準映像啟動，確認從 SPL、TF-A、U-Boot、
   kernel、initrd 到使用者空間完整交接。
2. 依序盤點兩片三星 DDR M4 Zero，記錄板號、PCB 版本、DDR 完整料號、
   顆粒日期碼、電源與 SD 卡識別。
3. 每片新板先做一次 UART 全程冷開機，不通過時停止該板後續壓力測試並
   保存完整失敗邊界。
4. 四片 M4 Zero 各執行十次完全斷電冷啟動，順序交錯，不能把 warm reset
   計入冷啟動。
5. 冷啟動全數通過後才執行 Linux 記憶體壓力、I/O 並行與長時間測試。
6. 四片 M4 Zero 完成後，再切換到 BPI-M4B 獨立工作流。

## 每片板的最低證據

- 板號、PCB 版本及 DDR 顆粒近照。
- 映像、SD 卡回讀區段與 bootloader SHA-256。
- 每次上電的 UART 起訖時間與完整原始日誌。
- `M4ZDDR1_PROFILE*`、geometry、SPL Build ID 與核心版本。
- 是否進入使用者空間、登入時間及 rootfs 裝置。
- `dmesg` 中的 EDAC、page fault、initramfs、ext4、MMC、watchdog 與 panic。
- 壓力工具命令、版本、結束碼及執行時間。
- 失敗時最後一個成功階段與第一個錯誤，不以照片摘要取代原始日誌。

## Gate

| Gate | 最低條件 |
| --- | --- |
| G1 標準啟動 | SPL、TF-A、U-Boot、kernel、initrd、使用者空間完整通過 |
| G2 冷啟動 | 每片 10/10 完全斷電冷啟動 |
| G3 基礎記憶體 | 每片完成可用記憶體範圍測試，無資料錯誤或核心異常 |
| G4 並行壓力 | 記憶體、MMC 與 CPU 並行至少兩小時，無錯誤 |
| G5 跨供應商與批次 | Rayson 與三星四片 M4 Zero 全數通過 G1 至 G4 |

任一板失敗時，不把其他板的通過結果外推到該批次。先以相同映像重現三次，
再回到 M4ZLAB2 對該板執行單變因邊界測試。

三星顆粒在取得完整料號前，不推定其容量、Rank、die 組織或與 Rayson 相同的
最佳 PHY 參數。X2 的名稱只代表 0438 與 1116 兩板候選，不代表已完成跨
DDR 供應商驗證。

## BPI-M4B 分流原則

BPI-M4B 與 BPI-M4 Zero 僅共享 H618 控制器知識、測試協定與主機端工具設計。
下列項目必須重新確認：

- DDR 類型、容量、Rank、資料寬度與 PCB 走線。
- U-Boot 分支、defconfig、DTB、UART 與 MMC 編號。
- PMIC、供電、復位與 watchdog 行為。
- 原廠映像及原廠 boot0 的已知可用頻率與動態調校結果。

完成上述盤點前，不在 BPI-M4B 上執行 M4 Zero SPL 或完整映像。

M4B 專用移植計畫：

```text
docs/bananapi-m4berry-ddr-lab-port-plan-20260813.md
```
