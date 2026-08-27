# `/media/pi/SMCI` 容量回收紀錄

更新日期：2026-08-28

## 結論

截至 2026-08-28，本計畫可確認已回收約 `83.7 GiB`，範圍只包含可重建快取、候選專用 OverlayFS、失敗輸出及已被正式候選取代且完成 Git 證據閉合的舊候選。最新一次 M1 Super 回收後可用空間為 `136,432,959,488` bytes，約 `127.063 GiB`。建置期間會同時新增快取與映像，因此各時點的可用空間差額不能直接取代逐項刪除量。

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

## 後續追加回收

| 階段 | 約略容量 | 範圍 |
| --- | ---: | --- |
| R3 Mini 正式閉合 | `12 GiB` | 正式 L2 推送後移除候選專用 OverlayFS 上層，保留正式 IMG 與 XZ |
| M1 Super L1 校準 | `19 GiB` | 固定 L2 契約後移除校準 IMG／XZ 與其專用 OverlayFS 上層 |
| M1 Super 正式閉合 | `18.973 GiB` | 提交 `eac5ec7f7` 推送後，移除正式建置專用 OverlayFS 上層及提交 `8c6533a10` 的歷史大型輸出 |

M1 Super 正式閉合的精確增加量為 `20,371,968,000` bytes。刪除前後均重算正式 IMG 與 XZ：IMG SHA-256 為 `192269a97910729304d635e80921b3fef647a2036d4013958c4cd81cbd4752f8`，XZ SHA-256 為 `b3b640fc04116f0193832354bda899aadcb8f894a22e8b6fed4b1d463fa06b63`，兩者保持一致。

本次另移除下列精確路徑：

```text
/media/pi/SMCI/armbian/bpi-v26.2.1-bananapi-optimize/.tmp/bananapi-rockchip-m1super-cache-overlay
/media/pi/SMCI/armbian/bpi-v26.2.1-bananapi-optimize/output/images/2026.08/bananapi-rockchip-rk3528-m1super-trixie-vendor-cli-historical-8c6533a10-20260827
```

執行前已確認遠端分支包含 `eac5ec7f71127fbc208512c6cb0b5f58572fa8d3`，兩個目錄均沒有掛載、建置程序、開啟檔案或容器引用。刪除以精確解析路徑及 `find -xdev -depth -delete` 執行，不跨越檔案系統。

## 強制保留

- 共用 `/media/pi/SMCI/armbian/bpi-v26.2.1/cache` 唯讀下層。
- 正式 IMG、XZ、SHA-256、建置設定、驗證狀態與實機證據。
- M1 Super 正式固定輸出 `output/images/2026.08/bananapi-rockchip-rk3528-m1super-trixie-vendor-cli`。
- M4 Zero／M4 Berry 的 DDR 調校、客戶回報與 UART 原始證據。
- Unisoc 來源、`.repo`、PAC、原廠文件與目前採用的同步基線。
- 尚未整合的 M6、M4 工作樹與 `stash@{0}`。

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
