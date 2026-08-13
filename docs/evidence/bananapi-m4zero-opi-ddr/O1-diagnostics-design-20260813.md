# O1 結構化 DRAM 診斷設計與原廠對照

## 目的與限制

O1 在 O0 Orange Pi 792 MHz profile 上加入固定 UART 診斷，不修改時脈、
TPR、geometry、Rank、自動偵測順序、訓練位、五次重試上限或失敗處理。

UART 輸出會改變初始化之間的時間，因此 O1 只供定位。O1 通過不能外推成
無診斷版本穩定，更不能外推成量產資格。

## 格式版本

所有欄位以 `M4ZDDR1_` 開頭：

| 標記 | 用途 |
| --- | --- |
| `PROFILE0` | 時脈、DRAM 類型與四個訓練功能是否啟用 |
| `PROFILE1` | ODT 與 drive strength |
| `PROFILE2` | TPR6／10／11／12 |
| `BEGIN` | 每次 controller 初始化的 Rank、寬度、Row、Column |
| `RUN` | 即將進入的訓練階段，可定位無逾時迴圈卡死 |
| `STAGE` | 訓練結果及實際嘗試次數 |
| `END` | 該次 controller 初始化結果 |
| `REG` | 最終初始化後的唯讀白名單暫存器 |
| `FINAL` | 最終容量與 geometry |

階段縮寫：`wl` 為 write leveling、`rc` 為 read calibration、`rt` 為
read training、`wt` 為 write training。O0 profile 應顯示：

```text
wl=0 rc=1 rt=0 wt=0
```

## 暫存器白名單

Controller 基址為 `SUNXI_DRAM_CTL0_BASE`：

| 偏移 | 意義 |
| ---: | --- |
| `0x000` | MSTR，裝置類型、寬度與 active ranks |
| `0x004` | controller state |
| `0x010` | mode register command control |
| `0x014` | mode register command data |
| `0x1bc` | DFI status |
| `0x324` | software programming status |

PHY 基址為 `SUNXI_DRAM_PHY0_BASE`：

| 偏移 | 意義 |
| ---: | --- |
| `0x180` | PHY 初始化狀態 |
| `0x184` | read calibration 狀態 |
| `0x188` | write leveling 狀態 |
| `0x258`、`0x25c`、`0x318`、`0x31c` | write leveling lane 結果 |
| `0x26c`、`0x274`、`0x32c`、`0x334` | read calibration lane 結果 |
| `0x840`、`0xa40` | read training 狀態 |
| `0x8e0`、`0xae0` | write training 狀態 |

只在最後一次 controller 初始化完成後讀取一次，不掃描未知 MMIO。

## 解析方式

```bash
./tools/parse-bpi-m4zero-o1-uart.py uart.log > uart.json
```

由標準輸入解析：

```bash
picocom -b 115200 /dev/ttyUSB0 | ./tools/parse-bpi-m4zero-o1-uart.py
```

缺少 `FINAL`、BEGIN／END 不平衡或階段標記位於區塊外時，解析器仍輸出
JSON，但 exit code 為 `2`。只為檢視截斷日誌時可加 `--allow-incomplete`。

## 原廠 boot0 對照

### 有證據的流程

原廠 boot0 `V0.651` 在 1,100 mV、792 MHz 執行 LPDDR4，4 GiB 板辨識為
32-bit、2 Ranks、4096 MiB。UART 可見順序：

```text
auto rank/width -> Lclk memtest -> R_2d -> R_1st -> W_2st -> R_2st
-> RV_C -> simple test -> RTC 保存調校參數
```

最多四輪 DST；失敗時維持 792 MHz 重新掃描及選點，沒有觀察到自動降頻。

### 重算成功率

| 證據組 | 結果 |
| --- | ---: |
| 三片弱板 vendor boot0 測試 | 29/30 |
| `450600826` 的 `v3-sunxi-flash-gpt` | 8/10 |

原廠也有失敗樣本，所以後續移植目標是理解動態策略並提高可觀測性，不是
假設閉源 boot0 對所有板絕對穩定。

### 已觀察調校結果

| 板號／案例 | 最終 TPR6 | TPR11 | 最終 TPR12 |
| --- | --- | --- | --- |
| `450600146` | `0x36808080` | `0x2b2c2c29` | `0x0f0d0f0e` |
| `450600826` Loop2 | `0x36808080` | `0x27272725` | `0x0f0e0e0f` |
| `450601075` | `0x38808080` | `0x26262725` | `0x0f0d0e0f` |
| `1116` | `0x3a808080` | `0x25252523` | `0x110f0f10` |

這些差異支持板間 eye center 與 lane delay 不同，也是固定 Orange Pi profile
無法直接取得 8/8 的合理原因；尚不能據此直接寫入固定板級參數。

### RTC 欄位

RTC 基址為 `0x07000100`：

| 索引 | 位址 | 欄位 |
| ---: | ---: | --- |
| 8 | `0x07000120` | `dram_mr6` |
| 9 | `0x07000124` | `dram_mr14` |
| 10 | `0x07000128` | `dram_tpr6` |
| 11 | `0x0700012c` | `dram_tpr11` |
| 12 | `0x07000130` | `dram_tpr12` |
| 13 | `0x07000134` | `dram_para1` |
| 14 | `0x07000138` | `dram_para2` |
| 15 | `0x0700013c` | `dram_tpr13` |

原廠是否恢復 tuning 參數還受 `dram_tpr13[11]` 控制。此策略屬 O3/O4
研究範圍，O1 不讀寫 RTC。

## 原始證據

```text
/media/pi/SMCI/bpi/m4z/jammy_current_6.18.32_vendor-boot0-v0651-auto-2g-4g-792mhz.img测试情况
/media/pi/SMCI/bpi/m4z/jammy_current_6.18.32_vendor-boot0-v0651-auto-2g-4g-792mhz-v3-sunxi-flash-gpt测试情况
/media/pi/SMCI/bpi/m4z/2026-04-08-bananapi-m4zero-android12/analysis-20260730
/media/pi/SMCI/bpi/m4z/BPI-H618-Android12-source-sparse/longan/brandy/brandy-2.0/spl-pub/board/h618/libsun50iw9p1_sdcard.a
```

`libsun50iw9p1_sdcard.a` SHA-256：

```text
8c563e43895005dd6beb0e8b4f034f8d28c979b3d7fa801ac722bdcdebb5ccc5
```

## 目前硬體狀態

- O1 已由 `1116` 單次 UART 驗證欄位順序、四個初始化區塊、21 筆暫存器
  與最終 4 GiB geometry；證據見 `O1-hardware-1116-20260813.md`。
- 該次進入 kernel 後發生 initramfs 解包錯誤，沒有使用者空間或登入證據。
- O1 尚未在三片弱板執行冷開機矩陣。
- O1 不包含原廠 R/W 眼圖、四輪 DST 或 RTC 恢復。
- 尚未證明任何動態策略能讓三片弱板在 792 MHz 全部穩定。
