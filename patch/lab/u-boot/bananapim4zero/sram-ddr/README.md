# DDR SPL2 單輪離線框架

本版完成獨立 SRAM 連結、參數前握手、完整參數解析及明確拒絕。**尚未核准上板，不能執行 DDR 訓練。** 原始 driver 與測試程式確實連入 ELF，但 `ddr_preflight()` 固定返回零；建置器還會檢查該函式機器碼，禁止以參數或換組態繞過。

## 固定來源

本機來源：

```text
/media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-opi-ddr/cache/sources/u-boot-worktree/u-boot/v2026.01
```

基底提交為 `127a42c7257a6ffbbd1575ed1cbaa8f5408a44b3`，tree 為 `fe36192530da93b7ae57a2cdc1be2ac9a74ed0a9`。來源有未提交補丁，因此不能只依 HEAD 識別；`source-manifest.json` 固定全部 21 個必要檔案的 SHA-256。研究時對現行 DDRlab `015` 及 A1 `016` 執行 `git apply --reverse --check`，兩者皆通過；此命令沒有套用或回退檔案。

- `dram_sun50i_h616.c`：控制器、PHY 訓練與 PLL5 初始化；保留原始演算法。
- `dram_timings/h616_lpddr4_2133.c` 與 `dram_sun50i_h616.h`：LPDDR4 時序、暫存器配置、執行期時脈。
- `dram_sun50i_h616_lab.c`：沿用完整 15 欄位解析器及資料、位址、rank 邊界、分散窗口與效能測試。
- `dram_dw_helpers.c`、`dram_helpers.c`：容量公式及暫存器完成等待；沒有呼叫舊 bootstrap 或自動探測。
- A1 組態採 `tpr6=0x3a808080`、`tpr10=0x402f6663`、`tpr11=0x25252523`、`tpr12=0x110f0f10`。這些是可追溯候選，不稱為安全參數。

每次建置保存唯讀來源快照 `pristine/`、套用接合補丁的 `source/`、本版程式與建置器快照 `inputs/`。來源的上游著作權、授權標頭及 `Licenses/` 原文逐位元保留，供授權遵循與來源追溯；不是本版新增的外語敘述。接合補丁只修改兩個檔案：停用單輪測試中的 watchdog reload；修正 tiny-printf 的 AArch64 數字暫存區及最小負整數取絕對值。32-byte 暫存區容納 16 位十六進位與 20 位十進位數字，無號減法避免有號溢位。

`build-009/source` 的固定基底是 `25049ad560826f7dc1c4740883b0016014a59789`，缺少新版公開 DDR helper；不得混用。歷史 DDRlab ELF 的 BSS 位於 `0x4ff80000`，不能直接搬移或跳入。舊 `build-bpi-m4zero-ddr-lab.sh` 要求 LAB 開啟、480 MHz，但現行 `015` 已不啟用該組態；本建置器不使用它，也不編整個 Armbian。

## 交接與記憶體

- 外部封包應使用 `version=2`、`kind=2`；本工具僅產生 ELF／裸映像，封包與 loader 由主代理處理。
- 入口 `0x30000`，EL3，MMU／D-cache／I-cache 均關閉；`x0` 指向原 64B 上下文配置，要求 magic `0x31505553`、`abi=2`、`bytes=64`、board `0x06180001`；nonce 接受完整 u32 範圍，包含零。
- context 位址須為原 SPL1 保留區 `[0x48010,0x4fff0)` 中 8-byte 對齊位置；先驗範圍，再複製到負載自己的 BSS。`source` 接受 UART 的 `0xffffffff` 或既有槽索引 0 至 4，僅作來源資訊，不讀寫槽位。
- `image_bytes` 必須等於本 ELF 的裸映像大小，`runtime_bytes` 必須為 `0x18000`。區間 `[0x30000,0x48000)` 共 98,304 bytes，即 96 KiB，不是 98 KiB。
- 程式、向量、唯讀資料、參數及 BSS 在 `[0x30000,0x40000)`；一般堆疊在 `[0x40010,0x47800)`，初值 `0x477f0`；例外堆疊在 `[0x47800,0x48000)`，初值 `0x47ff0`。`0x40000` 放堆疊哨兵。
- 沒有 heap、`gd`、DDR 重定位、舊 SPL 跳轉或返回 SPL1。UART 僅沿用已配置的 `0x05000000`，不重設 pinmux；時基須為 24 MHz。未知 CPU／快取狀態在入口停止，可能無 UART 輸出。

## UART 契約

```text
BPI-SPL2 event=ddr-ready nonce_hex=12345678 abi=2 kind=2 preflight=unverified
R nonce_hex=12345678 id=1 clk=480 dx_odt=0x07070707 dx_dri=0x0e0e0e0e ca_dri=0x0d0d odt_en=0xaaaaeeee tpr0=0 tpr2=0 tpr6=0x3a808080 tpr10=0x402f6663 tpr11=0x25252523 tpr12=0x110f0f10 level=0 passes=1 window=1
BPI-SPL2 event=ddr-params nonce_hex=12345678 id=1 clk=480 dx_odt=0x07070707 dx_dri=0x0e0e0e0e ca_dri=0x0d0d odt_en=0xaaaaeeee tpr0=0 tpr2=0 tpr6=0x3a808080 tpr10=0x402f6663 tpr11=0x25252523 tpr12=0x110f0f10 level=0 passes=1 window=1
BPI-SPL2 event=ddr-result nonce_hex=12345678 id=1 result=blocked reason=pmic_geometry_unverified ddr=off tested_bytes=0
```

`nonce_hex` 必須正好 8 個十六進位字元，對應該輪上下文；其餘 15 欄全數必填，可調整次序。`id` 為非零 u32，時脈 240 至 900 MHz 且為 12 的倍數，暫存器欄位為 u32，`level=0/1/2`、`passes=1`、`window=1..64` MiB。不接受額外 PMIC、電壓、幾何或任意位址欄位。

行上限為 383 個可列印 ASCII bytes，不含 CR／LF。完整無效行輸出 `event=ddr-reject`，reason 為 `nonce_mismatch`、`fields_or_range`、`passes_not_one` 或 `line_encoding_or_length`；錯誤行排空至換行後才能接收下一行，空行忽略。連續 8 個拒絕輸出終態 `reject_limit`。總接收期限 30 秒，半行閒置期限 5 秒；分別輸出終態 `request_timeout`／`line_timeout`。一組有效參數完成後永久停止，沒有 `I`／`Z` 舊命令，也不假稱恢復安全參數。

本版終態必為 `blocked` 或 `error`。若日後獲准開通 driver，舊 lab 的 `M4ZLAB2_TEST`／`ERROR`／`BENCH` 只是次級明細，不能當新握手或本輪通過；新的 `ddr-result` 才是終態。窗口測試不是全容量驗證，效能也不是正確性證據。

## 硬體阻擋

1. 可移植的電源來源已知：`board/sunxi/board.c:i2c_init_board()`、`drivers/i2c/mvtwsi.c`、`arch/arm/mach-sunxi/pmic_bus.c`、`drivers/power/axp_spl.c`。H616 使用 PL0／PL1、`clock_twi_onoff(5, 1)`、R-TWI `0x07081400`、I2C 位址 `0x36`。缺的是從不執行 `clock_init_safe()` 的 SRAM 入口啟動時，該 bus clock／reset／pinmux 的已審初始化與本輪讀取證據；不是找不到開源實作。
2. AXP313 原流程讀 `0x03` 並驗 `(id & 0xc8) == 0x48`，再寫 DCDC2／DCDC3 電壓暫存器 `0x14`／`0x15` 及使能 `0x10`。已驗證 SPL1 `build-009` 沒有執行它。本輪未知的是實際 PMIC 身分、三個暫存器目前值、板級電源軌與設定值的對應及回讀／量測證據；1000／1100 mV 只是既有來源組態，不能直接稱為已配置或已驗證。本版不寫 PMIC。
3. 幾何缺本輪的 ranks、bus width、rows、cols 與容量證據。舊自動探測會依序訓練 32-bit／2-rank 等組合，再對 `0x40000000` 及別名位址實際讀寫，不能先於電源前置執行；`mctl_auto_detect_dram_size()` 內部兩次重初始化還未檢查失敗返回。0845 歷史 4 GiB／2-rank／32-bit／16-row／10-col 不可當成本輪配置。後續應先完成可失敗退出的探測接合層，而非新增可隨意指定幾何的 UART 欄位。
4. 舊 `sunxi_dram_init()` 在探測前會設定 `PRCM_RES_CAL_CTRL` 的 bit 8、清除 `PRCM_OHMS240` 的低 6 bits；直接呼叫 `mctl_core_init()` 不包含這兩步。這些前置寫入尚未納入並審查，CPU／匯流排初始條件及 panic 退出方式也須明定。以上是後續程式工作與待驗項目，並非聲稱既有流程技術上不可移植。
5. 本版不操作 watchdog、SD、eMMC、插座或原系統；錯誤與停止後的外部復原需另由已核准主機流程處理。

## 離線建置

以下只建立新目錄；名稱已存在時須換另一個新名稱。

```bash
python3 -B tools/build_bpi_sram_ddr.py \
  --source /media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-opi-ddr/cache/sources/u-boot-worktree/u-boot/v2026.01 \
  --output output/evidence/bpi-sram-ddr-local-001
env BPI_DDR_BUILD_DIR=output/evidence/bpi-sram-ddr-local-001 \
  python3 -B -m unittest discover -s tests -p test_bpi_sram_ddr_payload.py -v
```

使用本機 `aarch64-linux-gnu-*`、Git 與專案既有 `pyelftools`；ELF／符號由標準函式庫解析，每個 ALLOC 區段均須由唯一 PT_LOAD 涵蓋，裸映像逐位元核對 PT_LOAD 及零填補。QEMU AArch64 使用者模式只執行純參數／邊界測試 harness，不執行 EL3 負載。`build-report.json` 保存來源、編譯器、輸入、命令、產物 hash、ELF 區段與逐函式堆疊用量。單框堆疊檢查不等於完整呼叫鏈或硬體驗證。重建可比對 `spl2-ddr.elf` 與 `spl2-ddr.bin`；沒有 `.sram` 封包，也沒有任何可上板通過聲明。
