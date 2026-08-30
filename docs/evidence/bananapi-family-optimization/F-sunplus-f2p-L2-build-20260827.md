# Banana Pi F2P 內部 L2 映像建置證據

## 結論

Banana Pi F2P 的 Debian Trixie minimal CLI 映像已由固定來源提交完整建置，並通過 L2 唯讀內容守門。證據範圍只支持「內部 SD-only 軟體候選的來源與映像內容一致」；沒有進行燒錄、UART、冷啟動或周邊實測，也沒有取得預建 xboot 與工具鏈的對外再散布授權。

板檔維持 `.wip`，`public_release_allowed=false`、`hardware_claims_allowed=false`，F2S eMMC xboot 仍被明確排除。

## 建置身分

| 項目 | 值 |
| --- | --- |
| Armbian 來源提交 | `b43611d8aa22cee547474492b81413568611b343` |
| 來源 tree | `69d79b3d7e80cc6cafd3c02a3244e62a4be1ddd1` |
| 驗證器提交 | `b43611d8aa22cee547474492b81413568611b343` |
| validation SHA-256 | `1268bef1fd85eca59afedd1afcff7a5a8c7fc0e5ba19ba6502383fd0c68b0a75` |
| BSP 提交 | `3eee97bd8fb7582c2d9942a533647c3d78222bb5` |
| Armbian 韌體提交 | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| Linux | `5.4.35-legacy-sunplus-sp7021-bpi` |
| U-Boot | `2019.04` |
| 建置 UUID | `fe55893f-0d1d-415d-9f8a-84f31d297308` |

建置使用 F2P 專用 OverlayFS 上層；共用快取只作唯讀下層，結束後已正常卸載。

## 產物

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| IMG | 1,610,612,736 | `96dfc6e066adbde20c6cff83b8c95301f0cf76ebf0ee87888f13a2cc3587d3d7` |
| XZ | 315,675,248 | `aedf581c5167dc330c5531fa60b78faafac6f46ffbd20e20a376be8330cf1c9b` |

產物目錄為 `output/images/2026.08/bananapi-sunplus-sp7021-f2p-trixie-legacy-cli/`。`CANDIDATES.tsv` SHA-256 為 `8a717e32e41edf95f4649c85140934860d932ed134a96e43224109f55090db87`。

## 唯讀守門結果

- XZ 結構與解壓串流 SHA-256 對應 IMG。
- DOS/MBR 恰有兩個分割區：`8192+524288` 與 `532480+2613248` sectors。
- 根檔案系統與 FAT boot 皆以 `ro,nosuid,nodev,noexec` 掛載；ext4 另使用 `noload`。
- `uEnv.txt` 的 `root=UUID=...` 對應第二分割區，沒有硬編碼 `/dev/mmcblk*`。
- F2P DTB 的身分、雜湊、SD/eMMC 匯流排與受控節點狀態符合契約。
- `u-boot.img` 位於映像位元組偏移 17,408；`ISPBOOOT.BIN` 只作套件與 FAT boot 資產。
- rootfs 與 FAT boot 均未包含 `BPI-F2S-xboot-emmc-boot0-0k.img.gz`。
- 核心設定唯一內容 SHA-256 為 `9ce6aa0972e5a29af20b7b6181425ba02c4e78c634727378232050d0796fcd7c`。
- U-Boot 最終設定 SHA-256 為 `a2db480c77031efaa465ad4a550e16ee4649d371d42f403460a957050a117469`。
- U-Boot payload manifest SHA-256 為 `85563416e4ab85a0d4a3731b40b439fb24d7fc549654eacaf6d990479e80a81b`。
- 最終設定 manifest SHA-256 為 `4dfaee50a78dc67058c25cf64407cd57338854d412fa365816b84d0f11261bea`。

## 未完成項目

- `ISPBOOOT.BIN` 與 BSP 隨附工具鏈尚未完成再散布授權稽核。
- 沒有 F2P 專用 eMMC xboot，禁止 eMMC 安裝與相關支援聲明。
- 尚未在可追溯板號與板修上完成 microSD 冷啟動、UART、雙網路、USB、HDMI、TPM、GPIO、I2C、SPI 與熱穩定性測試。
- Linux 5.4.35 與 U-Boot 2019.04 的安全性與長期維護差距仍須獨立處理。
