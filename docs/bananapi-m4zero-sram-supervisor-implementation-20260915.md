# BPI-M4 Zero SRAM 救援原型實作紀錄

## 1. 狀態與界線

使用者於 2026-09-15 指示「實做」，由已推送的[總計劃](bananapi-m4zero-sram-supervisor-multislot-plan-20260915.md)建立獨立實作分支 `bpi-m4zero-sram-supervisor-20260915`。原整合分支、D1 封存、客戶映像及實體設備不變。

這是 P1／P2 的離線原型，不是可直接取代既有 bootloader 的正式映像。第一版實際提供：

- 不呼叫 DDR 初始化的 SPL1；SRAM 中的命令、驗證、堆疊與緩衝。
- 固定協定的 UART 查詢、XMODEM／CRC 載入、一次性交接。
- SD 五個固定槽的唯讀載入；不提供 SD 寫入或任意位址命令。
- 依新 ABI 重建的 SPL2 交接測試程式；不初始化 DDR、不啟動 Linux。
- 可重建工具、封包工具、明確指定串口的主機工具、離線回歸與二進位稽核。

**未完成：實板 BootROM／UART／SD 驗證、SRAM 更新器、中繼資料 A／B、DDR 實驗器移植、多 OS 及無人值守電源迴圈。** 不能把現有測試卡覆寫成此原型，也不能把模型的通過結果當成實板通過。

可續接狀態見 [JSON 工作清單](bananapi-m4zero-sram-supervisor-status-20260915.json)。每次接手先核對分支、工作清單及最近的建置證據，不能依舊對話猜測卡片或板號。

## 2. 固定來源與建置

| 項目 | 固定值 |
| --- | --- |
| U-Boot | `v2024.04`，提交 `25049ad560826f7dc1c4740883b0016014a59789` |
| 來源樹 | `2ccf5ff0294135c081c77f6fd9e1a6e697cd527b` |
| 本機 Git 封存 SHA-256 | `d4c25dae69c1d796f5dd20d2e3d8a41198483f389f5453f7dfbc68ba897f8226` |
| 已用工具鏈 | `aarch64-linux-gnu-gcc 11.4.0`、Binutils `2.38` |
| 最終固定時間 | `SOURCE_DATE_EPOCH=1789401600`，臺北時間 2026-09-15 00:00 |
| 修補與新增原始碼 | `patch/lab/u-boot/bananapim4zero/sram-supervisor/` |

上述封存雜湊是本機 `git archive` 的固定輸出，不是上游發行壓縮包或簽章。來源 Git 只讀；工具不下載、不同步、不修改原 Git 物件或工作樹。一般 `.git` 與 bare Git 均可使用，但提交、來源樹與封存三項都必須相符。

從本工作樹根目錄執行，輸出目錄必須尚不存在：

```bash
python3 -B tools/build_bpi_sram_supervisor.py \
  --source-git /media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-opi-ddr/cache/git-bare/u-boot/.git \
  --output "$PWD/output/evidence/bpi-sram-supervisor/build-new" \
  --jobs 4
```

相依工具包括 Git、GNU Make、主機 C 編譯器、AArch64 GCC／Binutils 及 U-Boot Kconfig 所需的主機工具。工具會記錄實際工具版本與雜湊，來源封存先驗證再解開。所有手動修改在受版本管理的修補集完成，不修改封存的建置來源來冒充可重現結果。

產物包含 `spl1-egon.bin`、`spl1.elf`、`spl1.map`、`spl1.config`、`spl2-smoke.bin`、反組譯、堆疊用量、`build.log` 及 `build-report.json`。失敗建置另存新編號，不覆蓋以前的證據。

`spl1-egon.bin` 包含 sunxi 媒體啟動標頭，**不等於可直接交給 SPL1 載入的 SPL2 封包**。第一次部署還需要專用 SD 的布局、備份與實板檢查，此文件刻意不提供覆寫 `/dev/mmcblk*` 的命令。

## 3. 初版 SRAM 與生命週期

區間均為左閉右開；以下是原型固定契約，實際映像大小與配置須由建置稽核核對。

| 區域 | 範圍 | 用途 |
| --- | --- | --- |
| sunxi 標頭與 SPL1 | `0x20000` 至 `0x30000` | 媒體標頭 96 位元組；SPL1 連結起點 `0x20060` |
| SPL2 載入／執行區 | `0x30000` 至 `0x48000` | 上限 96 KiB，包含負載、填補與執行期需求 |
| 載入區末端哨兵 | `0x48000` 至 `0x48010` | 防止寫入越過 SPL2 區域 |
| SPL1 狀態 | `0x48010` 至 `0x4fff0` | BSS、封包標頭、1024 位元組接收緩衝、上下文與 MMC 狀態 |
| 狀態區哨兵 | `0x4fff0` 至 `0x50000` | 偵測狀態區邊界破壞 |
| 堆疊下界哨兵 | `0x50000` 至 `0x50010` | 偵測部分堆疊越界，不能取代最壞堆疊分析 |
| 初期配置器／gd／堆疊 | `0x50010` 至 `0x58000` | 初始 SP `0x58000`，配置器預算 8 KiB；實際 SP 依 gd 與呼叫深度計算 |

最終 `build-009` 的 SPL 專用 `GD_SIZE=416`：`gd` 為 `[0x55e60,0x56000)`，初期配置器為 `[0x56000,0x58000)`，堆疊自 `0x55e60` 向下，扣除下界哨兵後預算約 23.6 KiB。不要拿非 SPL 的通用標頭中 `GD_SIZE=448` 代替此值。BSS 實際為 `[0x48010,0x48cc0)`。

SPL1 收包時只覆寫 SPL2 區，不覆寫自身程式、BSS 或堆疊。交接前重新驗證 SHA-256、哨兵、EL3 及 MMU／資料快取／指令快取關閉；`x0` 指向 64 位元組上下文，`x18` 清零，避免 SPL2 誤用前階段 `gd`。上下文包含 ABI、板型、nonce、來源槽、長度與負載摘要。

測試用 SPL2 自行清除 BSS、使用 `SP=0x47ff0`，程式及 BSS 限制在 `0x40000` 以下。它不返回 SPL1；執行後停止，後續必須重新上電再進入救援。

例外向量留在 SPL1 並在初始化時設定，例外進入受控停止。上游 AArch32 至 AArch64 的轉換入口仍保留；離線 AArch64 命令模型不驗證此轉換或 BootROM。64 KiB 配置上限不能解讀成 BootROM 已實證接受該大小。

本原型未載入 DTB，未啟用 `SPL_OF_CONTROL`。上游整體 Kconfig 可能仍列出通用 `DRAM_CLK` 等欄位，不能據此判定實際執行 DDR；稽核以關閉 H616 DDR 驅動、排除 DDR 符號及實際呼叫路徑為準。

## 4. 封包 ABI 與 UART

封包為 512 位元組標頭加負載，整數採小端序：

| 位移 | 內容 |
| --- | --- |
| `0..7` | `BPISRAM1` |
| `8`、`12` | 版本 `1`、標頭長度 `512` |
| `16` | 板型 `0x06180001`，僅代表此實驗 ABI，不是實體板序號 |
| `20`、`24`、`28` | 負載長度、執行期長度、固定入口 `0x30000` |
| `32`、`36` | 旗標 `0`、種類 `1`，目前只接受交接測試程式 |
| `40..71` | 負載 SHA-256 |
| `72..507` | 保留，必須全零 |
| `508..511` | 前 508 位元組的 CRC32 |

負載上限 `97792` 位元組；執行期上限 `98304` 位元組且須 16 位元組對齊。負載後必須補零至**下一個** 512 位元組界線；即使已對齊，仍多加 512 位元組。這樣避免成熟 XMODEM 實作把負載尾端 `0x1a` 當作文字 EOF。韌體也在救援組態停用舊有 `0x1a` 截尾處理，並逐一檢查填補區。

```bash
python3 -B tools/bpi_sram_package.py pack \
  --input output/evidence/bpi-sram-supervisor/build-new/spl2-smoke.bin \
  --output output/evidence/bpi-sram-supervisor/build-new/spl2-smoke.sram \
  --runtime-size 0x18000
```

工具限定 Linux 一般檔案，拒絕區塊裝置、符號連結、覆蓋既有輸出及路徑競態。SHA-256 是完整性檢查，不是簽章或可信啟動；任意能重新計算雜湊的程式仍可能危害設備，必須限制可信來源。

### 控制命令

使用 `115200`、`8N1`、無流量控制。每行以換行結束，nonce 為十進位 32 位元無號整數：

| 命令 | 功能 |
| --- | --- |
| `I <nonce>` | 回報 ABI、板型及能力 |
| `U <nonce> <package_bytes>` | 輸出 `loading` 後接收 XMODEM／CRC 封包 |
| `S <nonce> <slot>` | 唯讀載入固定槽 `0..4` |
| `R <nonce>` | 僅能執行同一 nonce 已成功載入且重新驗證的程式 |

主機工具沿用 `pyserial` 與 `lrzsz` 的 `sx`，不是自行重寫 XMODEM。韌體雖用 `CONFIG_SPL_YMODEM_SUPPORT` 連入上游 `xyzModem` 模組，**線上協定實際是 XMODEM／CRC，不是 YMODEM**。

下列命令只供後續已完成首次部署、明確配對的實驗 UART 使用；`<UART>` 必須換成核對過的裝置，不得猜測 `ttyUSB` 編號：

```bash
python3 -B tools/bpi_sram_uart.py probe --port '<UART>'
python3 -B tools/bpi_sram_uart.py upload --port '<UART>' \
  --input output/evidence/bpi-sram-supervisor/build-new/spl2-smoke.sram --run
```

主機不探索其他裝置、不控制電源、不自動重試，也不寫 SD。`--run` 會移交執行權；未指定時只載入。錯誤命令、失敗載入或 nonce 不符會取消執行資格。韌體從成功交接後不接受下一條命令，不能把重新開啟串口當成板子已重新啟動。

## 5. SD 唯讀槽與待驗事項

固定扇區大小 512 位元組，要求卡容量至少 64 MiB；MMC 裝置限定插槽 0 的 SD，不接受 eMMC。五個槽起點為 `LBA=6144 + slot * 2048`，也就是 3、4、5、6、7 MiB，各預留 1 MiB 間隔，目前單次讀取的封包上限小於 128 KiB。

這只是 P2 的受限唯讀地址，不是完整多 OS 分割表。未實作可變布局、中繼資料世代、安全槽晉升、資料寫入或斷電交易。後續 P3 仍須凍結完整卡片布局並驗證保留區，不得逕自把這五槽稱為已完成的更新器或 OS 槽。

MMC 使用上游 sunxi PIO 路徑，救援組態限制最高 24 MHz、關閉高速能力與寫入。修補包含 FIFO 無進展逾時、最後一批 FIFO 拷貝上限、10 秒整體操作期限，以及每次載入重新初始化並清除前次時脈故障旗標。期限涵蓋驅動的時脈／FIFO／中斷／忙碌輪詢；軟體期限仍以 CPU、時基及 MMIO 能繼續執行為前提，不會解決匯流排實體卡死。

UART 初始化的 TEMT 等待在 20 毫秒後繼續重設 FIFO 與配置，傳送等待亦設上限；UART 本身損壞時仍無法保證可握手。實體 SD 初始化失敗、卡片拔除、控制器錯誤及重試恢復仍須上板；模型中的「SD 初始化失敗後可查詢」只是函式回傳失敗的狀態機測試。

## 6. 離線驗證方式

```bash
python3 -B tests/test_bpi_sram_build.py
python3 -B tests/test_bpi_sram_package.py
python3 -B tests/test_bpi_sram_core.py
python3 -B tests/test_bpi_sram_uart.py
```

執行模型另用隔離的 Python 環境安裝 `unicorn==2.1.4`，並使用 `pyelftools`、`pyserial`、主機 `sx`：

```bash
BPI_SRAM_BUILD=output/evidence/bpi-sram-supervisor/build-new \
  output/evidence/bpi-sram-supervisor/model-venv/bin/python -B \
  tests/test_bpi_sram_execution.py
```

這套測試直接載入最終 `spl1-egon.bin`，ELF 只提供定位符號，不重新實作封包解析器。SHA-256、XMODEM、命令狀態機及 SPL2 本體都是產物中的指令。時基、主機字元 I/O、排程與 MMC 失敗則由測試替身提供。另以虛擬串口連接真實主機工具與 `sx`，驗證傳輸後的交接回應。

另一案例從 AArch64 `reset` 開始，使用真實早期配置器、gd、BSS 清除與例外向量設定；UART／GPIO／時基初始化使用替身或有限寄存器模型，不驗證 AArch32 轉換、BootROM 或實際電性。最大長度封包、超量資料、缺少 EOT、RAM 載入後被改寫等情況也列入回歸。

一次執行所有稽核與模型並保存結果：

```bash
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B \
  tools/validate_bpi_sram.py \
  --build-dir output/evidence/bpi-sram-supervisor/build-new \
  --output-dir output/evidence/bpi-sram-supervisor/validation-new
```

驗證輸出目錄必須尚不存在。先稽核最終二進位與 ELF、來源及配置一致性，通過才執行測試；每項都有命令、結束碼、原始測試輸出與雜湊，最後產生 `validation-report.json`。這些命令只使用一般檔案與虛擬串口，不開啟真實 UART。

模型不是 H618 SoC 模擬器，不包含 BootROM、真實時脈、GPIO 電性、DMA／MMC 控制器或 SD FTL；通過結果不能外推實板穩定度。[來源：Unicorn 2.1.4 的 ARM64 指令模擬介面](https://pypi.org/project/unicorn/2.1.4/)

最終 `validation-001` 已針對 `build-009` 由主流程完整執行：

| 組別 | 結果 |
| --- | --- |
| 建置器契約 | 19 項通過 |
| 稽核器回歸 | 26 項通過 |
| 主機封包與一般檔案防護 | 42 項通過 |
| 真實 C 解析核心，包含 ASan／UBSan | 13 項通過 |
| UART 主機工具 | 34 項通過 |
| 最終二進位 AArch64 執行模型及虛擬串口整合 | 17 項通過 |
| 抽取原始 C 驅動函式的主機模型 | 10 項通過、44 個子情境；不是 AArch64 ABI 或實板證據 |
| 二進位／配置／來源一致性稽核 | 8 組檢查通過 |

合計 **161 項回歸測試，零跳過**。全部命令結束碼為零，驗證前後來源及建置報告雜湊不變。另完成 Python AST、Ruff 指定錯誤規則、Markdown／JSON 解析及 Git 差異檢查。每次修補後必須重新建置及重跑，不拿較早產物的模型結果當作新版本證據。

結果位於 `output/evidence/bpi-sram-supervisor/validation-001/validation-report.json`，SHA-256 為 `fa6b547f305f8e8ba85238c5b84441b051db14671d2c4d555d3ff081fcc767af`。版本為 `unicorn 2.1.4`、`pyelftools 0.31`、`pyserial 3.5`，主機傳輸使用實際 `sx`。

## 7. 建置過程

| 次數 | 結果與修正 |
| --- | --- |
| `build-001` | DDR Kconfig 必填值導致非互動建置反覆提示；停止該程序。修正救援組態的 DDR 驅動選入條件，並加上缺漏設定直接失敗。失敗紀錄保留。 |
| `build-002` | SPL1 連結時發現 MMC 所需的 legacy block driver 表遭移除；改為只保留必要表，不保留一般 SPL 啟動方法。 |
| `build-003` | 真實交叉編譯成功；模型測試確認載入及 SPL2 交接。配置稽核再發現上游強制 `SPL_STACK_R`，另有固定時間晚於主機 UTC 的警告，不列為最終交付。 |
| `build-004` | 關閉堆疊重定位，修正固定時間，建置無警告；後續審查再補早期例外遮罩並移除未使用的 DT 名稱。 |
| `build-005` | ELF 模型可執行，但二進位稽核發現 `objcopy` 漏掉例外向量與 MMC 驅動表；禁止交付實板。另將 SPL2 BSS 對齊改成明確區段邊界。 |
| `build-006` | 補齊匯出區段，八項二進位／來源稽核通過；獨立審查另指出 SD 整體期限、時脈故障恢復及 UART 初始化等待仍需補強。 |
| `build-007` | 新增期限修補有重疊補丁區塊，`git apply --check` 拒絕；未進入編譯，修補合併後另建新編號。 |
| `build-008` | 整合三項驅動恢復修正，編譯及八項稽核通過；最終可燒錄檔直接進入 17 項執行模型回歸。 |
| `build-009` | 同一來源、修補及工具鏈再次乾淨建置；`spl1-egon.bin`、`spl1.bin`、`spl1.config`、`spl2-smoke.bin` 與 `build-008` 逐位元組相同。 |

可燒錄二進位 40960 位元組，SHA-256 為 `aef7a1a8c4eb84eb73b4fee519b93561ef5722fc0fc442049e308836695f7f76`。SPL2 負載 583 位元組，SHA-256 為 `4efc809a5e5d6980447781c0cf865b6a35b8ee5ca360135143cc1f4b8deec96d`。目前只是離線產物，不代表已核准燒錄或符合量產需求。

本地交付位置為本工作樹的 `output/evidence/bpi-sram-supervisor/build-009/`，建置報告 SHA-256 為 `035e64d20e81b40dabade718f6c5ed3b8a24e3ad58064ec1cd23241136b559f0`。封裝後的 `spl2-smoke.sram` 共 1536 位元組，SHA-256 為 `8f98a2e2f612341b0d108f147cd6c9dcfbb957c4c0577c5d6e171c6e898bd062`。

原始建置報告包含絕對來源路徑與工具鏈雜湊；Git 保存可重建原始碼、文件與經整理的結果，不提交大型建置樹或虛擬環境。此目標不接入日常 Armbian 映像建置，也未改變任何正式板型的 DDR 參數。

## 8. 下一個實板門檻

1. 核對專用實驗 SD 的實際裝置身分、容量、掛載與備份；保留目前 6.6.75／A1／D1 對照卡，不在這些卡上直接改分割表。
2. 首次部署只放 SPL1 與交接測試封包，不放 DDR 實驗器；先驗證無 DDR 握手與 UART 載入，紀錄完整冷啟動及載入結果。
3. 驗證五個唯讀槽、損壞標頭、壞 SHA-256、截斷與取消、SD 錯誤、UART 中斷及重新上電回救援。
4. 通過 P2 硬體門檻後才開發／啟用 P3 寫入路徑；後續再接既有 DDR 參數工具、6.6.75 對照與多 OS。

首次部署尚未執行，不能假稱已消除人工拔卡需求。這一階段先建立可驗證的救援底座，之後才讓 AI 透過 UART 更新不穩定的 SPL2。
