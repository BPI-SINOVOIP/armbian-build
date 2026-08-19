# A1 Noble 四板標準啟動 G1 證據

## 結論

`0845`、`0438`、`0256`、`1116` 四份 UART 均顯示相同的 A1 `P02e5`
bootloader 與 792 MHz DDR 設定。四片板都完成 DDR geometry、TF-A、U-Boot、
核心啟動、Noble 使用者空間登入及正常關機；沒有 kernel panic、Oops、SError、
EXT4 錯誤或 MMC I/O 錯誤。

`0845` 首次啟動完成帳號初始化後執行一次暖重啟，第二次仍正常進入使用者
空間並關機。先前 X2 在此板 kernel handoff 後發生的 PID 1 panic，在這份 A1
紀錄中沒有重現。`0256` 則證明同一 A1 設定能由錯誤的雙 Rank 候選自動退回，
正確辨識 2 GiB 單 Rank geometry。

這批資料可列為四板「標準啟動 G1 通過」，但不能計入受控冷啟動 G2 次數：
原始檔沒有斷電控制紀錄、SD 映像回讀雜湊或 Linux 記憶體壓力結果。因此 A1
仍是工程候選，不是穩定版或量產放行結論。

## 固定 bootloader 身分

```text
U-Boot SPL 2026.01_armbian-2026.01-S127a-P02e5-Hc6a9-V3946-Be6d8-R448a
clk=792
dx_odt=0x07070707 dx_dri=0x0e0e0e0e ca_dri=0x00000d0d
odt_en=0xaaaaeeee
tpr6=0x3a808080 tpr10=0x402f6663
tpr11=0x25252523 tpr12=0x110f0f10
```

四份 UART 都顯示 `Armbian-unofficial 26.05.0-trunk noble`。日誌沒有記錄完整
映像檔名或 SD 回讀 SHA-256，因此只能確定 A1 bootloader 與 Noble 系統，
不能只憑 UART 區分 CLI 或 XFCE，也不能把測試直接綁定到某一個完整 IMG
雜湊。

## 四板結果

| 板號 | 啟動次數 | 最終 geometry | `rc` 結果 | 核心／登入 | 結束 | 判定 |
| --- | ---: | --- | --- | --- | --- | --- |
| `0845` | 2 | 4,096 MiB、x32、2 Rank、16R/10C | `8/8` 一次通過 | `2/2` | 一次暖重啟、一次正常關機 | G1 通過 |
| `0438` | 1 | 4,096 MiB、x32、2 Rank、16R/10C | `4/4` 一次通過 | `1/1` | 正常關機 | G1 通過 |
| `0256` | 1 | 2,048 MiB、x32、1 Rank、16R/10C | 錯誤雙 Rank 候選失敗；單 Rank `4/4` 一次通過 | `1/1` | 正常關機 | DDR／啟動 G1 通過，Bluetooth 待修 |
| `1116` | 1 | 4,096 MiB、x32、2 Rank、16R/10C | `4/4` 一次通過 | `1/1` | 正常關機 | G1 通過 |

`0256` 的第一筆 `M4ZDDR1_END result=fail` 是 geometry 自動探測的重要負向
結果：SPL 先嘗試雙 Rank，read calibration 五次失敗後改用單 Rank，後續四個
階段均一次通過並回報 2,048 MiB。它不是最終 DDR 初始化失敗。

## 警告與限制

| 範圍 | 觀察 | 判定 |
| --- | --- | --- |
| 四板 | U-Boot 顯示 `WDT: Not starting watchdog@30090a0` | watchdog 未由 U-Boot 啟動，未阻擋開機 |
| 四板 | FAT environment 無法讀取，隨後正常掃描 MMC 並載入 boot script | 不列為啟動失敗 |
| `0256` | `Bluetooth: hci1: BCM: Reset failed (-110)` | Bluetooth 周邊 Gate 未通過，與 DDR G1 分開追蹤 |
| `0845` | 第一次暖重啟前兩筆 systemd shutdown timeout 設定失敗 | 暖重啟仍成功，不視為 DDR 錯誤 |
| `0845` | 兩次啟動的 rootfs 分別枚舉為 `mmcblk0p1`、`mmcblk1p1` | UUID 啟動未受影響；後續需以 SD CID 核對裝置 |

這批紀錄沒有執行 `stress`、`stress-ng` 或 `memtester`，也沒有 systemd 失敗
單元查詢與完整核心錯誤掃描命令。UART 未出現相關致命字串，只能證明記錄
期間沒有可見異常，不能取代 G3／G4。

## 原始證據

原始檔位於：

```text
output/images/2026.08/bpi-m4zero-a1-0845-792-matrix
```

| 板號 | 原始大小 | 行數 | 原始 SHA-256 | 提交版解壓 SHA-256 | 提交版壓縮 SHA-256 |
| --- | ---: | ---: | --- | --- | --- |
| `0845` | 64,267 bytes | 1,327 | `40afc6151e2ce9282a2268ba74fb0ecf826d197fc487cd5d234abfd33b62b923` | `793e408b0fc40b08864a5510fb41ce295fb7c293aa7095c26e78e66e3129a215` | `53e0bf1ce5c97d4ab510b493571a38a4f9ec3ca3bb65b342a2fd56664be30ec0` |
| `0438` | 28,183 bytes | 522 | `16ff95044bc6b4905f222d95691c98d8538896b7339b6e18c09d936974952559` | `16ff95044bc6b4905f222d95691c98d8538896b7339b6e18c09d936974952559` | `ff0cafbcc45a648b108f1e7d151634dfd99d5ff22921b3bbc0e03c37fbc278a1` |
| `0256` | 25,344 bytes | 486 | `9478ec46425ac1df8eb3692bdfef5b99893b6233ed32e61a41655b2bd7c1d4ef` | `9478ec46425ac1df8eb3692bdfef5b99893b6233ed32e61a41655b2bd7c1d4ef` | `19f36d5e2682487db125db6ebce0572ff5b70a601fbfa746141a4675339c2494` |
| `1116` | 27,528 bytes | 515 | `6c971cf3e0b7e4e29d59d337ddbc2b0147fc7c8f0f65423b3e714f1392971f9c` | `6c971cf3e0b7e4e29d59d337ddbc2b0147fc7c8f0f65423b3e714f1392971f9c` | `e847810e4d6a04a5e445010bc4784d23a1fc41788214b8dd7c3ff8a174cc8ee1` |

`0845` 原始 UART 含首次開機精靈回顯的測試 Wi-Fi 名稱與密碼，因此原始檔
只留在本機並以原始 SHA-256 追溯。提交版只將兩個網路名稱與一個密碼值替換
為 `[已遮蔽]`，其餘內容與行數不變。其他三份提交版保留全部原始位元組；
所有檔案都保留終端控制字元。

提交版位置：

```text
docs/evidence/bananapi-m4zero-opi-ddr/hardware/A1-Noble-four-board-20260820
```

## 後續 Gate

1. 記錄測試映像檔名、XZ SHA-256、SD CID 及 bootloader 區段回讀 SHA-256。
2. 四板各執行至少 `10/10` 受控完全斷電冷啟動，失敗也必須保留 UART。
3. 執行可用記憶體 pattern 測試，再執行記憶體、CPU、MMC 並行長時間壓力。
4. `0256` 的 Bluetooth reset timeout 另立周邊缺陷，不以 DDR 通過關閉。
5. 完成 2 GiB、4 GiB 與已知弱板共同 Gate 後，才評估 A1 是否取代 X2。
