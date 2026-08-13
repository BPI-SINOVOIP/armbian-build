# BPI-M4 Zero 三星 DDR 樣本盤點

## 結論

兩張原始照片顯示兩片 BPI-M4 Zero 均採用三星
`K4F6E3S4HM-MGCJ` LPDDR4。顆粒雷射字樣分成兩行：

```text
K4F6E3S
4HMMGCJ
```

兩行合併後才是完整料號。兩片顆粒的頂部追溯碼分別為 `SEC 337` 與
`SEC 322`，因此列為兩個獨立樣本，不因料號相同而合併測試結果。

公開的 Renesas DRAM 相容清單將 `K4F6E3S4HM-MGCJ` 列為 LPDDR4、
`16 Gb`、`32 bits`、`1 rank`。換算後單顆封裝容量為 `2 GiB`。這只用於
建立預期 geometry；板上實際容量、Rank 與可用範圍仍須由 SPL 診斷、
U-Boot `bdinfo` 及 Linux 日誌交叉驗證。

來源：

```text
https://www.renesas.com/ja/document/apn/rz-family-dram-list
```

## 原始照片

| 樣本代號 | 原始檔 | 尺寸 | SHA-256 | 可確認字樣 |
| --- | --- | --- | --- | --- |
| `S337` | `hardware/M4Z-Samsung-20260813/IMG_3687.jpg` | `3024x4032` | `8f36354541536a50f9d5a290262429f80a22f1a8ab708a369fc756d90c63df2e` | `SEC 337`、`K4F6E3S`、`4HMMGCJ` |
| `S322` | `hardware/M4Z-Samsung-20260813/IMG_3686.jpg` | `3024x4032` | `e9912ad7492eeecaa1e6bd153f9d1e8958ea69f21a25300625d87a6e3fa77ab2` | `SEC 322`、`K4F6E3S`、`4HMMGCJ` |

照片底部另有封裝追溯字串，但部分字元受對焦與反光影響，本次不轉錄，
避免把不確定字元寫成正式硬體資料。`SEC 337` 與 `SEC 322` 也只記為頂部
追溯碼；在沒有三星 marking 規範前，不自行解讀為生產日期。

## 證據邊界

- 照片可確認 DDR 製造商與完整功能料號，尚未包含 PCB 版本與板身序號。
- `16 Gb` 是位元容量，等於 `2 GiB`，不是 `16 GiB`。
- `1 rank` 與既有 Rayson 4 GiB 雙 Rank 樣本不同，不能直接沿用後者的
  geometry 結論。
- X2 參數只在 Rayson `0438` 與 `1116` 完成熱重設 M2 驗證；尚未證明
  三星樣本的 792 MHz 冷啟動與 Linux 穩定性。
- 兩片三星樣本即使料號相同，仍須各自完成冷啟動與壓力 Gate。

## 實機驗證順序

1. 先補拍板身序號與 PCB 版本，將 `S337`、`S322` 對應到實體板號。
2. 每片先以同一份 X2 映像執行一次完整 UART 冷開機，核對 SPL 回報為
   `2 GiB`、`x32`、`1 rank`。
3. 任一片未通過 DDR 初始化、initrd 解包或使用者空間交接時，停止該片
   後續壓力測試，保留完整 UART，改用 M4ZLAB2 做單變因 profile 掃描。
4. 兩片皆完成標準啟動後，各執行 `10/10` 完全斷電冷啟動，再進行記憶體、
   MMC 與 CPU 並行壓力測試。

## 實機進度

| 樣本 | X2 G1 | G2 | 短壓力 | 備註 |
| --- | --- | ---: | --- | --- |
| `S337` | 通過 | `1/10` | 180 秒、1.4 GiB、4 CPU 通過 | 正確偵測 2 GiB、x32、1 Rank；SDIO／Bluetooth 有周邊錯誤 |
| `S322` | 待測 | `0/10` | 待測 | 使用同一張 SD 卡進行下一次冷啟動 |

S337 詳細證據：

```text
docs/evidence/bananapi-m4zero-opi-ddr/X2-hardware-S337-G1-20260813.md
```
