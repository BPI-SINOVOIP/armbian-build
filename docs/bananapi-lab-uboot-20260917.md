# 跨架構一次性 U-Boot 引導

日期：2026-09-17。補充計畫已先推送：`8ed9d3f51`。

## 交付界線

`tools/bpi_lab_uboot.py` 提供借用 console 的真實逐行引導函式，以及純離線 `validate`／`render` CLI；不猜 UART、不控制電源，不是含部署、root、smoke、救援及佇列管理的完整後端。本次只做本機回歸，未操作硬體、電源或 UART；替身測試不等於實板驗證，不宣稱 45 板可直接使用。

**不發送儲存媒體寫入命令，不等於整段唯讀。** 核心啟動後仍可能掛載、修復或寫入客戶根檔案系統；即使 bootargs 有 `ro`，也不是全程硬體防寫。呼叫者必須另行核定根媒體身分、備份及啟動授權，不能從本工具的核心標記推論 root 安全或 smoke 成功。

## 介面

```python
validate_config(config)
load_config(path)
validate_artifacts(config, artifact_root)
render(config)
boot(console, config, records=None, *, timeout=300, monotonic=time.monotonic)
```

- `validate_config` 是純函式，拒絕未知欄位、錯誤型別、重疊及溢位，回傳獨立正規化配置。
- `load_config` 只讀至多 64 KiB UTF-8 JSON；拒絕重複鍵、符號連結及特殊檔案。
- `validate_artifacts` 在指定根目錄串流核對原檔大小、SHA-256 及標頭；不替代板上 RAM 核對或完整 DTB 語意審查。
- `render` 輸出附回應核對條件的 JSON 計畫及 `executed: false`，不是供整批貼入 UART 的 shell 腳本。
- `boot` 接受 `ConsoleSession` 相容的 `send(data, timeout=...)`、`expect_regex(pattern, timeout=...)`；後者回傳 `before`、`matched`、`groups`。不開關連線、不送 autoboot 中斷、不登入、不重試。
- `records` 保留每步 `started`／`verified`／`failed`；呼叫者須保存原始 RX、配置摘要及紀錄，並標明真實／替身來源。

runtime 最終只回傳 `status: kernel-marker-observed`、配置摘要、預期核心版本、`root_verified: false` 與 `smoke_verified: false`。不提供泛用 `ok: true` 或實板認證欄位。替身也能產生標記，因此此回傳值不得直接作為硬體收據匯入佇列。

## 配置契約

頂層欄位固定為 `schema`、`arch`、`uboot`、`ram`、`source`、`files`、`bootargs`、`kernel_release`、`fdt_extra`。`schema` 必須為 `bpi-lab-uboot-v1`；`arch` 限 `arm32`、`arm64`、`riscv64`。所有大小均為正整數位元組；位址可用整數或 `0x` 十六進位字串，不能用環境變數。

`uboot` 必填：精確 ASCII `prompt`、完整 `version` 行、`address_bits`（32 或 64，須符合架構）、`line_limit`（256 至 4096）、`pairing_sha256`、`qualification_sha256`、`abi: mainline-v2025.01`。摘要是外部證據引用，不是本工具完成驗真；prompt／版本也不是防偽身分驗證。呼叫者須先完成配對、持有站點鎖，確認 U-Boot 已停在提示且 console 無其他讀寫者。

資格證據須涵蓋 U-Boot 建置及 hush、SHA-256、引導命令、記憶體／DTB 操作與 raw initrd 的 `CONFIG_SUPPORT_RAW_INITRD`，並涵蓋核心、DTB 及完整 RAM 使用範圍。未知 vendor 語意或 `bdinfo` 格式不得用版本相近或模糊匹配放行。

### RAM 與來源

`ram` 固定包含 `banks`、`reserved`、`kernel_work`、`boot`。前兩者為非空陣列，後兩者為單一區間；每個區間固定是 `{"start": ..., "size": ...}`，以左閉右開範圍檢查。

- `banks` 必須與 runtime `bdinfo` 的非空 DRAM bank 起點、大小及順序一致，不根據容量猜起點。
- `reserved` 必須明列 U-Boot 映像、堆疊、malloc、控制 DTB、TF-A／SBI、平台韌體、DMA 等不得覆寫範圍；本工具不會由單一 `relocaddr` 猜完整保留大小。
- `kernel_work` 是已核定的核心完整工作範圍，必須涵蓋載入、搬移目的及解壓使用範圍。zImage 標頭不能描述完整解壓工作區，必須由來源與平台核定資料提供；不能用壓縮檔大小替代。
- `boot` 是已核定引導記憶體窗口，用來明確設定暫存 `bootm_low`、`bootm_size`、`bootm_mapsize`；所有組件與核心工作區須位於其中。
- 檔案載入容量彼此及核心工作區不得重疊，也不能碰到保留區；位址加長度超過架構位寬即拒絕。

runtime 核對 `relocaddr`、`sp start`；若 `bdinfo` 列出 `irq_sp`、`TLB addr`、`fdt_blob`、`new_fdt`，亦核對它們被明示保留區涵蓋。U-Boot DTB 須同時提供可解析的 `fdt_size`。MMC 與 TFTP 均要求完整 LMB 保留表：唯一 `reserved.count`（亦接受 `reserved.cnt`）、從零連續索引、明確起訖與長度、遞增且不重疊的區間；最多 256 列。缺列、重複、未知格式或位址溢位均阻擋。

旗標只接受主線具名的 `none`、`no-map`、`no-overwrite`、`no-notify`；可組合非零旗標，但不得重複、混入 `none` 或使用未知數字旗標。初始表不得碰到工作區。之後每次核對都須等於本次初始表加上已通過實收長度、`filesize` 與完整 SHA-256 核對的載荷，且自身配置必須為 `LMB_NONE`。只使用原檔 `bytes`，不接受 `capacity`、部分範圍或額外位元組；同旗標且恰好相鄰的區間可合併，不跨越間隙。原始區間及旗標不得消失、增加或改變。每次載入核對後及最終交接前均重讀 `bdinfo`，不刪除或釋放 LMB。

MMC 的 `source` 欄位固定為 `type: mmc`、`device`、`partition`、`partuuid`。裝置是明確整數，分割區必須大於零；`partuuid` 接受 DOS 或 GPT 形式。選擇 MMC 後，先獨立執行 `part uuid mmc 裝置:分割區` 並核對唯一完整行；失配、缺少或重複回應均不得載入。**本工具不提供 U-Boot 載入來源 CID 證明，也不是根媒體 CID 證明**：完整載荷 SHA-256 證明核對內容相同，不能辨識持有相同內容與克隆 PARTUUID 的卡片。上層 Linux 的雙媒體 CID 核對亦不等於載入前 CID 核對。本輪不擴增 CID ABI，不假定主線 `mmc reg read cid` 支援 SD。

每次 MMC `load` 都顯式指定裝置、分割區、地址、路徑、最多 `bytes + 1` 位元組及零偏移。多讀一個位元組可辨識過長檔案，且仍受已核定容量保護。`mmc dev` 裝置數使用十進位，檔案系統來源的裝置及分割區使用十六進位，不混用。

TFTP 的 `source` 欄位固定為 `type: tftp`、`ipaddr`、`serverip`、`netmask`、`gatewayip`、`ethact`、`protection: lmb-no-overwrite-v1`。同網段無閘道時明示 `gatewayip: 0.0.0.0`。只支援明確 IPv4 單播；不執行 DHCP、介面自動挑選或主機名稱解析。

一般 `tftpboot` 沒有通用的長度上限參數；`tsize` 宣告及事後 SHA-256 都不是 RAM 防溢位措施。本版因此要求：

1. 核定建置確實啟用逐區塊 `lmb_read_check` 的 TFTP 接收路徑。
2. 每個載入區的 `address + capacity` 後方至少 64 KiB 已由外部可信引導流程保留為 LMB `no-overwrite`，同時列入 `ram.reserved`。本工具不建立或刪除該保留區。
3. 每次下載前重新讀取 `bdinfo`，核對完整保留區數量、範圍及 `no-overwrite` 屬性。缺少就停止，不退回無防護 TFTP。

TFTP 會設定並讀回靜態網路、`ethrotate=no`、`netretry=no`、`autostart=no`，明示伺服器及檔案；下載後核對十進位及十六進位傳輸長度。此路徑是有前置資格限制的真實實作，不代表所有既有板子現已具備該 LMB 配置。

### 組件與格式

`files` 固定包含 `kernel`、`initrd`、`dtb`；只有 `initrd` 可以明示 `null`。每筆非空記錄含 `path`、`bytes`、`sha256`、`address`、`capacity`、`format`，核心另含 `entry`。大小包含所有映像標頭；SHA-256 是整個原檔摘要。路徑與參數只接受限定字元，禁止 shell 片段、展開、引號、換行或上層跳轉。

| 核心格式 | 架構 | 實際命令及限制 |
| --- | --- | --- |
| `zImage` | `arm32` | `bootz`；核對 raw zImage magic 與標頭起訖長度，`entry` 為核定解壓入口 |
| `Image` | `arm64`、`riscv64` | `booti`；只接受未壓縮 raw Image，核對架構 magic、大小、搬移地址及工作範圍 |
| `uImage` | 三種架構 | `bootm`；本版只接受未壓縮、單核心 legacy uImage，核對 Linux 類型、架構、標頭校驗、搬移範圍及入口 |

不接受 FIT、多映像 legacy、壓縮 uImage 核心、壓縮 Image、未知 vendor 容器、任意 `source`／`run`／環境匯入、overlay 或平台 fixup 腳本。來源板型需要這些流程時必須明確阻擋，由專用適配器處理，不得省略它們後宣稱原配開機鏈相容。Image 標頭大小為零亦拒絕，不套用 U-Boot 的歷史猜測值。

initrd 必須明示 `raw` 或 `legacy`：raw 使用 `地址:原檔長度`；legacy 使用包含標頭的地址，不能加 raw 長度繞過標頭。legacy 必須是相同架構的 Linux RAMDisk 類型；raw 不接受 legacy 或 FIT magic。無 initrd 時傳 `-`，不沿用先前環境。

DTB 格式固定為 `dtb`，地址須八位元組對齊。檔案 SHA-256 通過後核對標頭，執行 `fdt list /` 排除 FIT 根節點，並以 `fdt rsvmem print` 要求表內保留區均已納入配置。`/reserved-memory` 及板型 fixup 的完整語意仍須包含於外部核定證據，本工具不宣稱完成 DTB 全面語意驗證。

`fdt_extra` 為 4096 至 1048576 位元組，DTB `capacity` 至少為 `bytes + fdt_extra + 4096`，保留頁面對齊餘量。只對已核對 DTB 做固定 `fdt resize` 並檢查新大小；`fdt_high`、`initrd_high` 暫設為對應位寬全一值，避免不明搬移目的；不執行 `saveenv`。

`bootargs` 是明示的非空 token 陣列，不是 shell 字串；不自動猜 console、root、根檔案系統或加入修復參數。`kernel_release` 是必須觀察到的完整核心版本 token。

## 執行與失敗語意

先等待精確 prompt，再以新隨機標記包住每條內部生成命令；開始及結果標記皆須為完整行，原命令回顯不包含完整標記。每一步都等待回應核對與下一個 prompt，不能一次送完整清單。

載入前以 SHA-256 空字串已知向量測試真正 `hash sha256` 支援。每份組件先核對實收長度、`filesize` 及精確起訖範圍的 SHA-256，才納入自身 LMB 配置；runner 使用本次已驗證狀態，不把計畫中的 `loaded` 陣列當成證據。全部組件載入完成後，仍逐一重算 SHA-256 並讀標頭，避免後續載入破壞先前內容；不以 CRC32 替代。legacy 格式自身的標頭 CRC 檢查只是額外格式檢查，不是檔案 SHA-256 的替代品。

最後才設定並讀回 bootargs 與引導窗口，送出一次 `bootz`／`booti`／`bootm`。`Starting kernel ...` 不算完成；須觀察到行首 Linux 版本標記且版本吻合。回到 U-Boot、錯誤版本、只有回顯、缺少標記或總期限到期都失敗，不登入 root，也不執行 smoke。

總期限涵蓋所有 TX／RX。失敗或逾時不自動送中斷、重啟、再引導或回復 RAM 環境，也不關閉呼叫者的 console。主機停止等待不保證目標命令已停止；上層應保留未知狀態與 RX、隔離站點，由核定恢復程序接手。

## 本機使用與回歸

以下命令只做離線工作，`配對配置.json` 必須由外部可信配對及原配組件資料建立，不提供可冒充任一實板的通用 RAM 地址範本：

```bash
PY=output/evidence/bpi-sram-supervisor/model-venv/bin/python
$PY -B tools/bpi_lab_uboot.py validate --config 配對配置.json
$PY -B tools/bpi_lab_uboot.py validate --config 配對配置.json --artifact-root 原配組件目錄
$PY -B tools/bpi_lab_uboot.py render --config 配對配置.json
$PY -B -m unittest tests.test_bpi_lab_uboot tests.test_bpi_lab_console -q
```

不指定 `--artifact-root` 時只驗證配置，輸出 `artifacts_verified: false`，不假裝重讀了組件。兩種 CLI 模式都輸出 `hardware_verified: false` 與 `executed: false`。機器可讀的 `config_sha256` 綁定正規化後的完整配置。

初版回歸記錄：新工具 35 項，加上既有 console 共 64 項通過。使用合成映像、受控時鐘與傳輸替身，runner／解析器／`ConsoleSession` 均為真正程式；涵蓋格式矩陣、MMC 身分、TFTP 防護、摘要、記憶體、分段 RX、失敗／期限及離線 CLI。合成地址不是板型建議值。

2026-09-18 限定修正的同組回歸共 74 項通過，新增完整 LMB、精確實收範圍、基準表變動、相鄰合併、略過 SHA、後續載入破壞內容與最終交接檢查。原反例「MMC 宣告兩列但只提供一列」及「TFTP 實收 4096 位元組卻宣告 4097 位元組／整個容量」均在核心交接前阻擋；沒有新增實板資格。

### 專用適配器整合

公開函式簽章與配置 schema 不變。內部 `_memory(output, config, loaded=(), *, initial_lmb=None)` 改為要求完整保留表，並回傳已正規化的 `{start, size, flags}` 陣列，`flags` 為主線位元遮罩。非空 `loaded` 必須有本次初始表且已核對長度及 SHA；獨立呼叫此純核對函式本身不會取得 UART 證據。

自行解析 LMB 的原入口／special 適配器可使用 `_memory_gd(output, config)` 保留 DRAM 與 gd 檢查；此函式明確不核對 LMB，不能單獨作為載入安全證明。不得再將刪列後的殘缺保留表傳給嚴格 `_memory`。若保留剩餘 LMB 檢查，必須同步更新數量及索引，或將完整表與已驗證載荷交給共用核對。原入口若只檢查額外腳本／解壓區，不能逕改 gd-only 而遺漏剩餘 LMB 與核心工作區、initrd、DTB 容量的重疊檢查。

`_steps` 新增 `load-sha256` 作為每份載荷的前置 LMB 證據；原本全部載入後的 `sha256` 步驟保留。衍生 runner／步驟轉換器不可把新增步驟當成最終韌體插入時點，也不可略過它後宣稱自身載荷已通過 LMB 核對。

## 依據與限制

本地參考為 `tools/bpi_h618_customer_boot.py`、`tools/bpi_lab_console.py`、`config/bootscripts/boot-sunxi.cmd`、`boot-sun50i-next.cmd`、`boot-sun50iw9.cmd`、`boot-sophgo-sg200x.cmd` 及 `boot-meson-s4t7-legacy.cmd`。只讀取其內容，不執行或複製固定 H618 位址與媒體編號。

命令及格式語意查核限官方來源：[load](https://docs.u-boot.org/en/latest/usage/cmd/load.html)、[bootz](https://docs.u-boot.org/en/latest/usage/cmd/bootz.html)、[booti](https://docs.u-boot.org/en/latest/usage/cmd/booti.html)、[bootm](https://docs.u-boot.org/en/latest/usage/cmd/bootm.html)、[bdinfo](https://docs.u-boot.org/en/latest/usage/cmd/bdinfo.html)。

搬移與 TFTP 安全契約依固定版本原始碼核對：[ARM Image](https://github.com/u-boot/u-boot/blob/v2025.01/arch/arm/lib/image.c)、[RISC-V Image](https://github.com/u-boot/u-boot/blob/v2025.01/arch/riscv/lib/image.c)、[TFTP 接收](https://github.com/u-boot/u-boot/blob/v2025.01/net/tftp.c)、[LMB](https://github.com/u-boot/u-boot/blob/v2025.01/lib/lmb.c)、[LMB 屬性](https://github.com/u-boot/u-boot/blob/v2025.01/include/lmb.h)。這些上游語意不證明任何現場 vendor 建置已相容，資格缺失仍須阻擋。

本輪 LMB 依據：[檔案載入依實際讀取長度配置](https://github.com/u-boot/u-boot/blob/v2025.01/fs/fs.c#L514)、[TFTP 逐區塊核對](https://github.com/u-boot/u-boot/blob/v2025.01/net/tftp.c#L143)、[`lmb_read_check` 呼叫配置函式](https://github.com/u-boot/u-boot/blob/v2025.01/include/lmb.h#L143)、[具名旗標與完整表輸出](https://github.com/u-boot/u-boot/blob/v2025.01/lib/lmb.c#L445)、[同旗標相鄰合併](https://github.com/u-boot/u-boot/blob/v2025.01/lib/lmb.c#L163)。CID 限制依據：[主線在 CID 分支之前拒絕 SD](https://github.com/u-boot/u-boot/blob/v2025.01/cmd/mmc.c#L1077)。
