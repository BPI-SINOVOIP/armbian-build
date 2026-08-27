# `/media/pi/SMCI` 容量回收紀錄

更新日期：2026-08-28

## 結論

本階段只回收可重建快取、專用暫存、失敗輸出及已被正式候選取代的舊候選，共約 `33.7 GiB`。回收前可用空間約 `96–97 GiB`；R3 Mini 完整校準建置同時新增約 `13.7 GiB` 專用快取與輸出，因此紀錄時可用空間為 `117 GiB`。數值差額來自建置期間的新增資料，不能用前後可用空間直接取代刪除量。

## 已回收項目

| 類別 | 約略容量 | 範圍 |
| --- | ---: | --- |
| Armbian 專用 OverlayFS 與暫存 | `7.6 GiB` | 一個失敗 R3 上層、Sunplus F2P、Meson 前置快取、M4 Zero 與 M4 Berry 專用 `.tmp` |
| 已拒絕或已取代候選輸出 | `6.2 GiB` | 依 48 板盤點選出的 12 個非正式候選目錄 |
| 文件蒐集專案可重建快取 | `19.9 GiB` 內的一部分 | `banana-pi-doc-benchmark-20260621/.tmp`、`.cache`、GUI `target` 與 `node_modules` |
| Unisoc 燒錄工具暫存 | `19.9 GiB` 內的一部分 | `work/Release/.../Bin/ImageFiles`、`work/sprd_linux_flash_out` |

已刪除的已知專用路徑包括：

```text
/media/pi/SMCI/armbian/bpi-v26.2.1-bananapi-optimize/.tmp/bananapi-sunplus-f2p-cache-overlay
/media/pi/SMCI/armbian/bpi-v26.2.1-bananapi-optimize/.tmp/cache-before-meson-overlay-20260826
/media/pi/SMCI/armbian/bpi-v26.2.1/.tmp/bpi-m4zero-b1-clone.9SyAL3
/media/pi/SMCI/armbian/bpi-v26.2.1/.tmp/verify-v10.OiptXA
/media/pi/SMCI/bpi/banana-pi-doc-benchmark-20260621/.tmp
/media/pi/SMCI/bpi/banana-pi-doc-benchmark-20260621/.cache
/media/pi/SMCI/bpi/banana-pi-doc-benchmark-20260621/apps/bpi-imager-gui/src-tauri/target
/media/pi/SMCI/bpi/banana-pi-doc-benchmark-20260621/node_modules
/media/pi/SMCI/bpi/unisoc/work/sprd_linux_flash_out
```

Unisoc `Bin/ImageFiles` 位於 `work/Release` 之下的工具輸出層，只移除可由正式 PAC 重新展開的檔案；PAC、來源、文件與工具本體均保留。

## 強制保留

- 共用 `/media/pi/SMCI/armbian/bpi-v26.2.1/cache` 唯讀下層。
- 正式 IMG、XZ、SHA-256、建置設定、驗證狀態與實機證據。
- M4 Zero／M4 Berry 的 DDR 調校、客戶回報與 UART 原始證據。
- Unisoc 來源、`.repo`、PAC、原廠文件與目前採用的同步基線。
- 尚未整合的 M1 Super、M6、M4 工作樹與 `stash@{0}`。

## 後續可回收但尚未刪除

下列項目合計約 `300–330 GiB`，目前因重建性或發布證據尚未閉合而保留：

| 類別 | 約略容量 | 刪除前必要條件 |
| --- | ---: | --- |
| Renesas Yocto `tmp` | 超過 `120 GiB` | 固定 manifest、設定、發布映像、套件清單與重建紀錄 |
| SM10 Buildroot `build/host/target` | `36.9 GiB` | 保存來源提交、設定、SDK 與正式映像 |
| 舊候選與測試輸出 | `65.7 GiB` | 逐目錄確認已有正式取代物及雜湊 |
| M4 Zero 建置工作目錄 | `38–42 GiB` | 保留全部 DDR 證據與正式 480／792 MHz 映像 |
| 舊 Unisoc `tmp` | `44.2 GiB` | 確認非目前採用基線且可由固定 manifest 重建 |
| 目前 Unisoc 同步基線 `tmp` | `23.7 GiB` | 完成當前 PAC 與 Yocto 重建驗證後才可處理 |
| Q654 `out` | `17.6 GiB` | 保存產品設定、正式產物與重建命令 |

## 安全規則

任何後續回收都必須只針對解析後的精確路徑，先確認沒有掛載點、執行中程序、容器 bind mount 或未提交工作，再記錄保留物與容量。禁止使用廣域萬用刪除，也禁止修改或清理共用 Armbian cache 下層。
