# BPI-M4 Zero 單一 SPL DDR 實驗器計畫

日期：2026-08-13  
狀態：實作中  
協定版本：`M4ZLAB2`

## 1. 問題與目標

先前以完整 Armbian 映像搜尋 DDR 參數，單次循環包含編譯、封裝、燒錄、
載入 U-Boot、TF-A、核心及 initrd。這種流程無法快速區分 DDR 初始化失敗、
資料損壞與後續啟動鏈問題，也不適合掃描大量參數。

本計畫改為一份固定的 SRAM-resident SPL 實驗器。DDR 時脈、驅動強度、ODT、
TPR、測試窗口及輪數全部由 UART 在執行期下發；搜尋候選時不得重編譯或重寫
SPL。主機端控制器負責產生候選、偵測 UART 逾時、保存原始證據及排名。

最終要分別找出：

1. 保險值：最低風險且通過長測試與重複初始化的設定。
2. 最佳值：通過長測試候選中，實測有效吞吐量最高的設定。
3. 最大容錯值：在參數空間中擁有最大連續通過窗口的中心設定。

上述結果先以單板為單位。量產共用值只能由不同序號、顆粒與批次結果的交集
產生，不能由一片板子的最佳值直接外推。

## 2. 固定啟動流程

```text
H618 BROM
  -> 從 SD 卡 8 KiB 偏移載入同一份 SPL
  -> 以 U0 已驗證的 480 MHz profile 初始化與偵測 geometry
  -> 啟動 M4ZLAB2 UART 伺服器
  -> 接收候選 profile
  -> watchdog 保護下完整重設 DDR controller／PHY／PLL
  -> 執行測試並輸出機器可讀結果
  -> 恢復 480 MHz profile，等待下一個候選
```

實驗器不返回 SPL framework，不載入 U-Boot proper、TF-A、核心或 initrd。
程式碼、資料、堆疊與 UART 控制流程都必須留在 SRAM；DRAM 只作被測物。

## 3. 執行期 profile

每個候選至少包含以下欄位：

| 欄位 | 意義 |
| --- | --- |
| `clk` | DDR 資料時脈 MHz |
| `dx_odt` | DQ ODT 設定 |
| `dx_dri` | DQ 驅動設定 |
| `ca_dri` | CA 驅動設定 |
| `odt_en` | ODT 啟用遮罩 |
| `tpr0`、`tpr2` | 模式及控制旗標 |
| `tpr6` | DQ delay／Vref 相關值 |
| `tpr10` | calibration／training 啟用位元 |
| `tpr11`、`tpr12` | lane delay 相關值 |

H616 的 `ns_to_t()` 原本以 `CONFIG_DRAM_CLK` 在編譯期換算控制器 timing。
實驗器必須改用目前候選的 `clk`；否則只改 PLL 會造成 timing 與實際時脈
不一致。一般 BPI-M4 Zero 開機組態仍維持原行為，變更只在實驗器啟用時生效。

## 4. UART 協定

SPL 使用小型固定欄位協定，不納入完整 U-Boot CLI。主機端提供易讀命令列，
並把每次交換保存成原始 UART 與 JSON Lines。

必要指令：

| 指令 | 功能 |
| --- | --- |
| `I` | 讀取協定、geometry、目前及保險 profile |
| `R` | 下發完整候選、測試層級、輪數及窗口大小後立即執行 |
| `Z` | 主動 watchdog reset |

`R` 必須是單一不可分割記錄，包含候選識別碼；不使用多個 `set` 命令累積
狀態，以免 UART 遺字後把新舊參數混合。SPL 必須先完整解析與檢查範圍，才可
修改硬體。每個輸出記錄都以 `M4ZLAB2_` 開頭並帶相同候選識別碼。

## 5. 失敗復原

錯誤 profile 可能使 PHY 輪詢停止、UART 無法繼續或 CPU 讀到損壞資料。
因此自動搜尋不能假設每個候選都會正常返回。

1. 套用候選前啟動 H618 watchdog。
2. 初始化及長測試期間定期 reload watchdog。
3. 正常完成後先恢復 480 MHz，再停止 watchdog。
4. 初始化卡死時由 watchdog 重啟，同一份 SPL 回到 480 MHz。
5. 主機端以候選開始記錄、UART 逾時及後續重新出現 `READY` 判定該候選失敗。
6. 若硬體 watchdog 也無法復原，才使用主機端 GPIO、繼電器或人工斷電。

候選列表與進度保存在主機，不依賴 DRAM 或 RTC，因此 reset 後可以續跑。

## 6. 測試層級

| 層級 | 內容 | 用途 |
| --- | --- | --- |
| M0 | 原廠 simple write、資料線 walking-bit、Rank 邊界別名 | 快速淘汰 |
| M1 | 五個容量位置、固定圖樣、walking-one／zero、複製比較 | 一般掃描 |
| M2 | 多輪大窗口、跨 Rank、搬移後校驗、讀寫複製 benchmark | 決賽候選 |

四 GiB／兩 Rank 板的必要位置包含：

```text
低位址                 0x40000000 附近
initrd 失敗區域         0x48800000 附近
容量四分之一位置
Rank 邊界下方與上方     0xC0000000 前後
最高有效位址附近
```

所有位址計算使用 64-bit 型別。M0 參考原廠 `dramc_simple_wr_test`；M1／M2
另加入相鄰 Rank 邊界窗口，不能只測 Rank 1 起點。

## 7. 自動搜尋與排名

主機端先執行頻率階梯，再針對固定頻率逐類掃描；不得在同一輪同時任意改動
所有欄位。預設頻率為：

```text
480 528 600 672 720 744 768 792 MHz
```

每輪流程：

1. 以基準 profile 執行 M0，確認測試鏈本身可用。
2. 每個候選至少執行 M0；失敗立即淘汰。
3. M0 通過者執行 M1，保存第一個錯誤位址、預期值及實際值。
4. 各維度找出連續通過區間，不把孤立通過點視為眼圖中心。
5. 各區間中心及邊界候選執行 M2。
6. 以完全斷電冷開機及 Linux 壓力測試驗證最終三類候選。

排名定義：

- 保險值：通過 M2、重複初始化與後續冷開機矩陣的最低頻率共同設定。
- 最佳值：通過相同 gate 後，以讀、寫、複製的保守綜合吞吐量排序。
- 最大容錯值：以連續通過窗口的最小半徑排序，取多維窗口中心。

吞吐量只在同一 SPL、同一 CPU 時脈、同一窗口大小及相同輪數間比較。

## 8. 產物

```text
sunxi-spl-ddr-lab.bin
bpi-m4zero-ddr-lab.py
write-bpi-m4zero-ddr-lab.sh
protocol-fixtures/
session.jsonl
uart-raw.log
candidates.tsv
rankings.json
sha256sums.txt
```

寫入工具只更新 SD 卡 8 KiB 偏移的 SPL 範圍，寫入前後必須保存雜湊並逐位元
回讀。它必須拒絕系統根磁碟、已掛載裝置、分割區路徑及容量不合理的目標。

## 9. 完成條件

軟體交付完成需同時滿足：

1. 一份 SPL 可接收並套用不同 profile，不重編譯。
2. `ns_to_t()` 與 PLL 都使用同一執行期時脈。
3. watchdog 卡死復原及主機端續跑流程已實作。
4. UART 協定 parser、模擬 fixture、排名與中斷續跑測試通過。
5. SPL 大小、SRAM stack、符號與不載入下一階段的證據完整。
6. 建置與寫入工具保存來源提交、命令、SHA-256 及回讀結果。

硬體結論則必須等實機測試。未完成多板矩陣前，只能交付「實驗器」與單板
候選結果，不得宣稱已找到量產保險值、最佳值或最大容錯值。
