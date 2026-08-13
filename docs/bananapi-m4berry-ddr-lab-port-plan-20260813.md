# BPI-M4 Berry DDR 實驗器移植計畫

## 現況

使用者提供兩片 H618 BPI-M4B；本機 Armbian 的正式板名為 BPI-M4 Berry，
已有獨立建置目標：

| 項目 | 現有設定 |
| --- | --- |
| Armbian board | `bananapim4berry` |
| U-Boot | `v2025.04` |
| U-Boot defconfig | `bananapi_m4_berry_defconfig` |
| U-Boot 補丁目錄 | `v2025-sunxi` |
| TF-A | `lts-v2.12.9`、`sun50i_h616` |
| Kernel DTB | `sun50i-h618-bananapi-m4-berry.dtb` |
| UART0 | PH0／PH1、115200 |
| SD | MMC0、4-bit、50 MHz |
| eMMC | MMC2、8-bit、150 MHz |

現有 M4B defconfig 設為 LPDDR4 792 MHz，主要靜態參數為：

```text
DX_ODT=0x07070707
DX_DRI=0x0e0e0e0e
CA_DRI=0x0e0e
ODT_EN=0xaaaaeeee
TPR6=0x48808080
TPR10=0x402f6663
TPR11=0x26262524
TPR12=0x100f100f
```

以上只代表既有原始碼設定，不等於兩片實機已驗證。兩片板的 DDR 完整料號、
容量、Rank 與 PCB 版本尚未盤點。

## 原始碼入口

```text
config/boards/bananapim4berry.conf
config/sources/families/sun50iw9-bpi.conf
patch/u-boot/v2025-sunxi/0008-u-boot-configs-Add-sun50i-h618-bananapi-m4berry-defconfig.patch
patch/kernel/archive/sunxi-6.18/dt_64/sun50i-h618-bananapi-m4-berry.dts
```

## 移植原則

M4ZLAB2 的 H616／H618 DDR 參數、watchdog、記憶體測試及 UART 協定可重用，
但下列內容必須建立 M4B 專用版本：

- U-Boot `v2025.04` 補丁，不套用 M4 Zero 的 `v2026.01` 板級補丁。
- `bananapi_m4_berry_lab_defconfig` 或同等獨立 LAB 目標。
- M4B 自己的 480 MHz bootstrap 與安全恢復 profile。
- `board=bananapim4berry` 韌體識別及主機端拒絕錯板機制。
- M4B 專用建置、SD 寫入、證據及結果目錄。

LAB SPL 停用 MMC 與下一階段載入，避免誤寫或誤啟動 eMMC。只在獨立 SD 卡
的 8 KiB 偏移寫入 SPL，不寫 eMMC。

## 執行順序

1. 記錄兩片板的板號、PCB 版本、DDR 完整料號、容量與 eMMC 料號。
2. 使用原廠已知可用映像，各完成十次完全斷電冷啟動並保存 UART。
3. 使用現有 Armbian M4B current 映像，各完成十次冷啟動，建立未修改基線。
4. 將 M4ZLAB2 原始碼移植到 U-Boot `v2025.04`，先完成乾淨套用、編譯、
   協定與可重現性守門。
5. 兩片板先以 M4B 專用 480 MHz profile 各完成 `M2 10/10`。
6. 測現有 mainline M4B 792 MHz profile，再測原廠 profile；一次只改一組
   完整參數，不把 packed lane 欄位當整數線性掃描。
7. 只對通過 profile 掃描 `TPR6[31:24]` 與個別 `TPR11/12` lane 邊界。
8. 找到兩片共同零失敗窗口後，才建立一般可開機 M4B U-Boot。
9. 最終執行每片五十次冷啟動、initrd、記憶體壓力、SD/eMMC 啟動及重啟。

## Gate

M4B 不沿用 M4 Zero 的通過統計。至少完成兩片板的 480 MHz 保險值、792 MHz
共同窗口、標準映像冷啟動與 Linux 壓力後，才能提出 M4B 候選；任何階段若
缺少完整 UART、映像雜湊或板上 DDR 料號，都只能標記為尚未驗證。
