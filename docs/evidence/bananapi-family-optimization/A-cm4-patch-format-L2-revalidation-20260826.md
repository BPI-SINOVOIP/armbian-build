# Banana Pi CM4IO 核心補丁格式重驗證

日期：2026-08-26

## 結論

`bananapicm4io` 已由修正後的乾淨來源重新完整建置 Trixie current CLI，建置日誌未再出現 `Failed to parse unidiff`、`Hunk diff line expected`、補丁失敗或致命錯誤。映像通過既有 Meson L2 唯讀守門，因此先前發現的 CM4 核心補丁格式問題已完成來源修正與針對性重驗證。

本結果只證明修補格式、建置產物與映像內容符合受控政策，不代表 CM4IO 已通過實機 eMMC、冷啟動或周邊驗證。

## 可重現基線

| 項目 | 值 |
| --- | --- |
| Armbian 來源提交 | `365c30eadb2e93777b1ed9f6e3c80451f52f59d6` |
| Armbian 來源樹 | `41080f107749d459f2505ac0d39ba3feccef4022` |
| Linux | `6.18.46-current-meson64` |
| U-Boot | `v2026.01` |
| FIP 提交 | `e11ae32f65219e9cba903e9744f216239b41386a` |
| 發行版與設定 | `RELEASE=trixie BRANCH=current BUILD_DESKTOP=no BUILD_MINIMAL=yes` |
| 建置時間 | 32 分 27 秒 |
| 建置狀態 | 完整建置完成 |
| 驗證狀態 | L2 通過 |

建置透過 OverlayFS 使用唯讀既有快取下層，並要求重新產生 U-Boot 與核心。建置結束後已確認隔離快取卸載，且沒有殘留 loop 裝置或唯讀驗證掛載。

## 產物

| 項目 | 值 |
| --- | --- |
| IMG 檔名 | `Armbian-unofficial_26.05.0-trunk_Bananapicm4io_trixie_current_6.18.46_minimal.img` |
| IMG 大小 | 1744830464 位元組 |
| IMG SHA-256 | `4fc76e32024b17b3ca008f7ba46ac0998a0050db378de0f4a0b58e97e2048f55` |
| XZ 大小 | 385597620 位元組 |
| XZ SHA-256 | `bf2e0c524b8aa1921b9f4adad1950fcade200634a8a21c4c9690f9e69f2fe019` |

本機證據位置：

```text
output/images/2026.08/bananapi-cm4io-patchformat-current-cli/
```

該目錄包含 IMG、XZ、個別 SHA-256、`CANDIDATES.tsv`、`COMPLETION_STATUS.json`、`VERIFICATION.tsv`、`VERIFICATION_STATUS.json`、中繼資料、FIP 雜湊清單與完整建置日誌。大型映像與日誌不加入 Git。

## L2 重驗證內容

1. 檢查來源提交、來源樹、驗證設定與 FIP 提交一致。
2. 檢查 IMG／XZ 大小、SHA-256、`xz -t` 與串流解壓同一性。
3. 檢查 MBR、Amlogic 開機區及映像內 U-Boot 套件 payload 與實際寫入區一致。
4. 以唯讀 loop 與 `mount -o ro,noload` 檢查核心、initrd、DTB、overlay 與套件。
5. 檢查 eMMC 為 8-bit、100 MHz HS200，並具有 `no-mmc-hs400` 保守限制。
6. 檢查 GPIO、I2C、SPI、V4L2、Bluetooth 等診斷套件與必要核心設定。
7. 檢查 CPU 調速器為 `ondemand`。
8. 掃描建置日誌，確認沒有補丁剖析、套用或致命建置錯誤。

## 下一門檻

- 至少三片 Hynix eMMC 與一片其他廠牌 eMMC，每片執行 30 次完整斷電冷啟。
- UART 不得出現 CMD12、stop command、`fs_devread` 或檔案系統讀取錯誤。
- 完成 eMMC 寫入校驗、`fio`、非正常斷電恢復、重新啟動與關機測試。
- 逐項實測 Ethernet、USB host／gadget、HDMI、Panfrost、VDEC、Wi-Fi、Bluetooth 與 40-pin。
