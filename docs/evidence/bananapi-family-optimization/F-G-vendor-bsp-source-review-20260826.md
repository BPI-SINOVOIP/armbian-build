# Banana Pi F／G 批 vendor BSP 與封存板來源審查

日期：2026-08-26

## 結論

F 批板卡包含 Sunplus、Realtek、SpacemiT、Synaptics、SpaceMIT K3 與 UNISOC 等互不相容的 vendor 流程，不能以 Armbian 一般 U-Boot／核心映像流程取代原廠封裝鏈。`bananapif2s` 可先作為固定 BSP 提交的 SD 候選；`bananapif2p` 因共用設定硬編碼 F2S xboot 而暫停；`bananapim2c` 必須維持 PAC 混合流程。G 批 `bananapir1` 已停止支援，只保存最後可重現基線，不建立新發布承諾。

## 逐板判定

| 板卡 | 靜態判定 | 主要缺口 | 建議下一步 |
| --- | --- | --- | --- |
| `bananapif2s` | BSP 已固定提交，可建立 legacy SD 基準 | 核心僅 5.4.35；ISPBOOOT／xboot 為預建資產；boot script 固定 `/dev/mmcblk1p2` | 固定資產雜湊與授權，改用 PARTUUID 後建立 SD 候選 |
| `bananapif2p` | 目前禁止建立候選 | 共用 `UBOOT_TARGET_MAP` 硬編碼 `BPI-F2S-xboot-emmc-boot0-0k.img.gz`，沒有 F2P 專用 xboot 證據 | 取得正確 F2P 資產，或明確建立不含 eMMC xboot 的 SD-only 情境 |
| `bananapiw2` | 固定 Realtek BSP 提交，來源可重取 | 4.9.119 核心、預建 `bluecore.audio` 與硬編碼根裝置限制 | 先固定二進位授權／雜湊與 PARTUUID，再做 legacy 基線 |
| `bananapim4` | 固定 Realtek BSP 提交，含 1 GiB／2 GiB DTB | 同為 4.9.119 vendor 核心與預建影音韌體；不能套用 H618 M4 Berry／Zero 結論 | 依記憶體容量分別建置與實機驗證 |
| `bananapicm6` | 可進入一般建置但不可重現 | U-Boot／核心使用可移動 branch；宣告的 `bananapicm6-legacy` 核心補丁目錄不存在；加入預建 `esos.elf` | 固定提交並修正補丁目錄，保存韌體授權與雜湊 |
| `bananapim6` | vendor legacy 初始整合 | 5.4.195 核心、可移動來源、預建信任區資產與固定 `/dev/mmcblk1p2` | 先固定來源與 `bpi-m6-tzk-4MB.bin` 邊界，再建立 SD 基準 |
| `bananapism10` | 來源提交相對固定 | U-Boot 封裝仍複製多個預建啟動 blob，尚無本分支實機證據 | 建立 blob 清單／雜湊／授權後，再建置 K3 Buildroot 混合候選 |
| `bananapim2c` | 一般 Armbian 映像流程不成立 | 原廠 Yocto/PAC 提供簽章啟動鏈、DTB、boot image、modem 韌體與分割區清單 | 維持 `.wip`，以已驗證 PAC 注入 Armbian rootfs，不產生虛假的通用映像 |
| `bananapir1` | 技術上仍有主線設定 | 已標示 `.eos`，不應由單次建置恢復發布狀態 | 保存最後可用來源、映像雜湊與已知限制，不列入新發布矩陣 |

## 最高優先阻擋項目

### F2P 錯板啟動資產

`config/sources/families/include/sunplus_sp7021_bpi_legacy_common.inc` 的 `UBOOT_TARGET_MAP` 無條件封裝 F2S xboot。這不是命名瑕疵，而是可能把錯誤第一階段啟動碼寫入 F2P eMMC 的風險；在取得 F2P 專用檔案或建立明確 SD-only 流程前，不執行 F2P 候選建置。

### M2C PAC 邊界

`config/boards/bananapim2c.wip` 已明確說明簽章啟動鏈、分割區及 modem 韌體來自 vendor Yocto/PAC。此板的最佳化目標是把可重現的 Armbian rootfs 正確放入已驗證 PAC，而不是假設通用 SD 映像可直接啟動。

### 老舊 vendor 核心

Realtek 與 Sunplus 路徑仍依賴 4.9／5.4 核心。即使建置與啟動成功，也不能宣稱具備 current 主線核心的安全維護能力。對外發布前必須明列維護期限、已知漏洞處理方式與無法更新的專有元件。

## 執行順序

1. `bananapif2s` legacy SD-only。
2. `bananapiw2`、`bananapim4` legacy，先完成預建資產守門。
3. 固定來源後的 `bananapicm6`。
4. `bananapim6`、`bananapism10` vendor／legacy 混合流程。
5. 取得正確啟動資產後的 `bananapif2p`。
6. `bananapim2c` 外部 PAC 工作流。
7. `bananapir1` 只做封存，不建立新候選。

缺少實機時最高只能標示 L2；專有 blob 同一性、授權與散布範圍也是發布守門，不能由成功編譯取代。
