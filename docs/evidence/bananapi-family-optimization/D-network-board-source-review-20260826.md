# Banana Pi D 批網路板來源審查

日期：2026-08-26

## 結論

D 批八張網路板橫跨 MT7623、RK3568、MT7622、MT7986、MT7987 與 MT7988，啟動媒體、分割區及 U-Boot 環境彼此不同，不能以單一 Filogic 映像流程推論全部可用。`bananapir4` 的主線 SD 路徑最適合作為本批第一個候選；`bananapir2pro` 與 `bananapir64` 可在來源修正後進入建置；其餘板卡須先處理自動開機、裝置樹或 vendor/OpenWrt 環境差異。

來源審查只能證明設定與補丁的靜態狀態，不能取代 WAN／LAN、交換器、無線、SFP、PCIe、SATA、各種啟動媒體及長時間網路壓力的實機驗證。

## 已直接修正

- `config/boards/bananapir2pro.csc` 的 `console=ttyS02` 已修正為 `console=ttyS2`，並以盤點回歸測試固定。
- 此修正只排除無效 UART 裝置名稱；尚未證明該載板 UART 接腳、鮑率或實際開機輸出通過。

## 逐板判定

| 板卡 | 靜態判定 | 主要缺口 | 建議下一步 |
| --- | --- | --- | --- |
| `bananapir4` | 本批最接近標準 Armbian SD/extlinux 候選 | SD 複合 DTB 與額外 MT7996 拓撲仍需分板確認 | 先建置 Trixie current CLI，保存啟動鏈與 DTB 內容證據 |
| `bananapir2pro` | U-Boot tag 與主線 DTB 相對固定 | `rkbin` 來源未固定；UART 修正尚未實機驗證 | 固定 blob 雜湊後建立 current 候選 |
| `bananapir64` | 主線 DTB 與 Filogic 路徑可進一步驗證 | 缺少本分支映像與各啟動媒體證據 | 建置 current SD 候選，再驗證 eMMC／SPI |
| `bananapir3` | DTS 與多媒體拓撲已宣告 | 所選 U-Boot 設定停用一般自動開機；SATA 能力與 DTB 組合須確認 | 先修正並測試 U-Boot 自動開機，再建立候選 |
| `bananapir3mini` | 有專用 DTB 與 defconfig | `patch/u-boot/u-boot-filogic/452-add-bpi-r3-mini-defconfig.patch` 明確停用 `CONFIG_AUTOBOOT` | 先建立可回歸的自動開機策略 |
| `bananapir4lite` | 有 SD defconfig 與 DTB | `456-add-bpi-r4-lite-sd-emmc.patch` 主要採 `/dev/fit0`、recovery 與 vendor/OpenWrt 環境，不能直接等同 Armbian GPT/extlinux | 分離 SD/extlinux 與 vendor recovery 情境後再建置 |
| `bananapir4pro` | 有 8X SD defconfig 與專用 DTB | current 核心追蹤可移動 `6.19-mtkdts` branch；環境同樣含 `/dev/fit0` 與 recovery 流程 | 固定核心提交，建立純 SD/extlinux 情境 |
| `bananapir2` | MT7623 主線核心可作長期基準 | DTB 名稱缺少 `.dtb`；U-Boot 補丁移除 `FDT_HIGH` 定義後仍引用；boot script 固定 `/dev/mmcblk1p1` | 暫停候選建置，先修復 U-Boot 編譯與 PARTUUID 根裝置流程 |

## 阻擋發布的來源問題

### R2 U-Boot

`patch/u-boot/v2024.07/board_bananapir2/enable-boot-from-ext4.patch` 刪除 `FDT_HIGH` 巨集，後續環境仍以 `FDT_HIGH` 設定 `fdt_high` 與 `fdt_addr_r`。這是可預期的編譯阻擋，不應靠跳過補丁或使用舊快取掩蓋。`config/boards/bananapir2.csc` 的 `BOOT_FDT_FILE` 也缺少 `.dtb`，兩者修復後仍須重建完整 U-Boot 與映像。

### R3／R3 Mini 自動開機

R3 系列的板級 defconfig 目前含停用 `CONFIG_AUTOBOOT` 的設定。路由器板可能需要安全的維護入口，但正式候選仍須具備可預期的無人值守開機；必須先定義倒數、中斷鍵與 recovery 優先順序，再以冷啟動及斷電回復驗證。

### R4 Lite／R4 Pro 分割區模型

兩板 vendor 補丁包含 FIT、UBI、production、recovery 與 `/dev/fit0` 路徑，而 Armbian 候選預期使用 GPT/extlinux。兩套模型不能混用；第一階段只建立 SD/extlinux 基準，vendor recovery 留在獨立情境，直到分割區轉換及升級回復測試完成。

## 建置與實機順序

1. `bananapir4` current SD。
2. `bananapir2pro` current。
3. `bananapir64` current SD。
4. 修正後的 `bananapir3`、`bananapir3mini`。
5. 分離啟動情境後的 `bananapir4lite`、`bananapir4pro`。
6. 完成 U-Boot 與根裝置修復後的 `bananapir2`。

缺少實機時最高只能標示 L2；WAN／LAN 封包轉送、NAT、VLAN、橋接、SFP、Wi-Fi、PCIe、SATA 與多日壓力必須逐板取得證據。
