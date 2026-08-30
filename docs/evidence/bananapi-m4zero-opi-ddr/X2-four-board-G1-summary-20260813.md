# X2 四板跨容量與供應商 G1 摘要

## 階段結論

同一份 X2 792 MHz 標準映像已在四片 BPI-M4 Zero 完成首次完全斷電冷啟動：

- 兩片 Rayson 4 GiB、x32、雙 Rank 樣本正確識別並進入使用者空間。
- 兩片三星 `K4F6E3S4HM-MGCJ` 2 GiB、x32、單 Rank 樣本正確識別並進入
  使用者空間。
- 四片均正常完成 initrd checksum、initramfs 解包與 rootfs 掛載。
- 0438、S337、S322 的短記憶體／CPU 冒煙測試均通過。

因此 X2 已通過目前四片樣本的跨容量、跨 Rank、跨 DDR 供應商 G1。這不是
G5：每片只有一次冷啟動，G2、G3、G4 尚未完成；三星板的無線周邊也未通過。

## 固定映像

```text
Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_x2-cross-board-792mhz.img
SHA-256 fb665992d6a5becfe2694cade5f2e1367f0eeb18582fdcda8e8d3d446042610b
```

SD 卡寫入前已完成整個映像範圍 SHA-256 回讀與 bootloader 區段逐位元比較。
所有板使用同一張 `SR64G` SD；Linux MMC 編號雖有差異，CID 與 UUID 均已核對。

## 四板矩陣

| 樣本 | DDR 類別 | SPL geometry | G1 | G2 | 短壓力 | systemd | 周邊狀態 |
| --- | --- | --- | --- | ---: | --- | --- | --- |
| `0438` | Rayson 4 GiB | 4,096 MiB、x32、2 Rank、16R/10C | 通過 | `1/10` | 3.0 GiB、4 CPU、180 秒通過 | `running` | `wlan0` 已建立，功能未完整驗證 |
| `1116` | Rayson 4 GiB | 4,096 MiB、x32、2 Rank、16R/10C | 通過 | `1/10` | 待補 | `running` | G1 錯誤掃描無異常 |
| `S337` | Samsung 2 GiB | 2,048 MiB、x32、1 Rank、16R/10C | 通過 | `1/10` | 1.4 GiB、4 CPU、180 秒通過 | `running` | SDIO／Bluetooth 初始化錯誤 |
| `S322` | Samsung 2 GiB | 2,048 MiB、x32、1 Rank、16R/10C | 通過 | `1/10` | 1.4 GiB、4 CPU、180 秒通過 | `degraded` | 無網路介面；SDIO、Bluetooth、vnstat 待修 |

## 證據入口

```text
docs/evidence/bananapi-m4zero-opi-ddr/X2-hardware-0438-G1-20260813.md
docs/evidence/bananapi-m4zero-opi-ddr/X2-hardware-1116-G1-20260813.md
docs/evidence/bananapi-m4zero-opi-ddr/X2-hardware-S337-G1-20260813.md
docs/evidence/bananapi-m4zero-opi-ddr/X2-hardware-S322-G1-20260813.md
```

每份文件包含原始 UART 路徑、提交版壓縮日誌、SHA-256、SPL profile、geometry、
Linux 記憶體、rootfs、systemd 與錯誤掃描結果。

## 尚未完成的 Gate

| Gate | 狀態 | 下一個完成條件 |
| --- | --- | --- |
| G1 標準啟動 | 四片通過 | 保留現有固定映像與證據，不再更改輸入 |
| G2 冷啟動 | 每片 `1/10` | 四片交錯補齊各 `10/10` 完全斷電冷啟動 |
| G3 基礎記憶體 | 未通過 | 執行可用記憶體範圍與資料 pattern 測試 |
| G4 並行壓力 | 未通過 | 記憶體、MMC、CPU 並行至少兩小時 |
| G5 跨供應商與批次 | 未通過 | 四片全數完成 G1 至 G4，並分開處理周邊 Gate |

量產前還需要更多序號、電壓與溫度角落；本摘要只對目前四片實物與固定映像
成立。

