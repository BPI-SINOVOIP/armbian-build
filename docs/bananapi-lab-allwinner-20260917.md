# Allwinner 原配組件離線準備

日期：2026-09-17。對應[跨板計畫 B2](bananapi-multiboard-lab-plan-20260917.md)。

實作入口為 `tools/bpi_lab_allwinner.py`，只處理非 H618 的 Allwinner ARM32／A64 原配組件。不操作 UART、電源或媒體，不執行映像腳本，不提供部署及救援生命週期。所有結果的 `hardware_validated` 始終為 `false`；`prepared` 不代表實板已可開機。

## 板型來源

板別由既有 `board-registry.json`、`config/boards/` 及 `config/validation/bananapi-sunxi-*.json` 交叉對應，不從檔名猜測。每次 manifest 記錄實際讀取來源的 SHA-256。可傳入表中正式板別或其 `artifact_board`，不接受其他簡稱。

| 正式板別 | 建置板別 | 原配 DTB |
| --- | --- | --- |
| `bpi-6204` | `bananapi6204` | `sun8i-r40-bpi-6204.dtb` |
| `bpi-m1` | `bananapi` | `sun7i-a20-bananapi.dtb` |
| `bpi-m1p` | `bananapim1plus` | `sun7i-a20-bananapi-m1-plus.dtb` |
| `bpi-pro` | `bananapipro` | `sun7i-a20-bananapro.dtb` |
| `bpi-m2` | `bananapim2` | `sun6i-a31s-sinovoip-bpi-m2.dtb` |
| `bpi-m2b` | `bananapim2berry` | `sun8i-v40-bananapi-m2-berry.dtb` |
| `bpi-m2u` | `bananapim2ultra` | `sun8i-r40-bananapi-m2-ultra.dtb` |
| `bpi-m2m` | `bananapim2magic` | `sun8i-r16-bananapi-m2m.dtb` |
| `bpi-m2p` | `bananapim2plus` | `sun8i-h3-bananapi-m2-plus.dtb` |
| `bpi-m2z` | `bananapim2zero` | `sun8i-h2-plus-bananapi-m2-zero.dtb` |
| `bpi-p2z` | `bananapip2zero` | `sun8i-h2-plus-bananapi-p2-zero.dtb` |
| `bpi-m3` | `bananapim3` | `sun8i-a83t-bananapi-m3.dtb` |
| `bpi-m64` | `bananapim64` | `sun50i-a64-bananapi-m64.dtb` |

R1 屬封存來源，不在此 13 板；H618 及其他 SoC 也不在此入口。來源表只供組件身分核對，不沿用其中的舊引導位址、媒體偏移、實板授權或 0845 資格。

## 準備介面

```python
prepare(read_file, *, board, kernel_release, output) -> dict
```

`read_file(absolute_image_path: str) -> bytes` 由主代理提供，必須唯讀；缺檔引發 `FileNotFoundError`，其他讀取錯誤不能當成缺檔。主代理負責映像內 symlink 解析、讀取界線、完整映像摘要、解壓限制及分割身分，本模組不匯入共用映像讀取器。`kernel_release` 必須是完整核心版本，不從映像檔名補值。

`output` 必須是尚不存在的專用證據目錄，父目錄須已存在；拒絕既存目錄、上層跳轉及父路徑符號連結。無效呼叫參數或目錄建立失敗直接引發例外；目錄建立後的內容不符與讀取失敗則保存 `blocked` manifest，保留已擷取檔案。

回傳值與 `output/manifest.json` 一致，主要欄位如下：

| 欄位 | 語意 |
| --- | --- |
| `schema` | `bpi-lab-allwinner-components-v1` |
| `status` | `prepared` 或 `blocked` |
| `ready` | 組件是否通過本工具全部必要離線核對，並可進入外部範本綁定 |
| `root_uuid` | 原環境合法小寫 `rootdev=UUID=...` 的 UUID；無法解析為 `null` 並阻擋 |
| `blockers` | 含 `code`、`message` 的物件清單，訊息為繁體中文 |
| `hardware_validated` | 固定 `false` |
| `source_image_verified` | 固定 `false`；單憑 callback 無法證明完整映像已核對 |
| `files` | 原映像絕對路徑、證據相對路徑、大小及 SHA-256；有效 DTB 另列 |
| `checks`、`reads`、`commands` | 已通過核對、缺檔／讀取狀態、本機解析命令及輸出摘要 |
| `original_release`、`original_env` | 僅文字解析的原始鍵值 |
| `bootargs_template` | 原環境展開結果；保留待外部核定的引導分割及載入來源欄位 |

CLI／主代理必須再將 `root_uuid` 與實際檔案系統超級區塊 UUID 比對，並把本 manifest 綁定到其完整映像、分割與來源摘要證據。`ready` 不是站點啟用、硬體可開機或媒體寫入授權。

## 實際處理

1. 擷取 `/etc/armbian-release`、`/boot/armbianEnv.txt`、`/boot/boot.cmd`、`/boot/boot.scr`。核對板別、家族、`KERNEL_IMAGE_TYPE`、`INITRD_ARCH`，拒絕重複鍵、shell 展開、未知環境參數及錯配值。release 使用獨立的單純賦值規則，接受引號內分號等文字，例如 `VENDORCOLOR`，仍拒絕命令替換與變數展開；U-Boot 環境不套用 shell 引號規則。
2. 開機腳本必須逐位元組符合本倉 `boot-sunxi.cmd` 或 `boot-sun50i-next.cmd`；`boot.scr` 必須通過標頭與資料 CRC、單項腳本格式，以及與 `boot.cmd` 的內容比對。即使兩份自訂腳本彼此一致，仍阻擋。
3. 按腳本讀取 `/boot/zImage` 或 `/boot/Image`、`/boot/uInitrd`，交叉比對 `/boot/vmlinuz-${kernel_release}` 與 `/boot/initrd.img-${kernel_release}`。別名解析由 callback 負責，讀取路徑與實際回傳內容均留下證據。
4. ARM32 驗證 zImage 魔術值及完整長度，再定位 gzip／XZ 壓縮資料，有限解壓並核對唯一內嵌 `Linux version`。A64 驗證 raw Image 標頭、大小、位元組序及內嵌版本。不以檔名或外部版本宣告替代內容核對。
5. `uInitrd` 核對用途、架構、兩層 CRC 及本倉封裝標記；實際 payload 另行辨識。支援 newc、gzip／XZ 及前置未壓縮 newc 接壓縮主封存的組合，解析 `init` 存在性及模組版本，絕不執行或解出其中程式。
6. ARM32 以 `.next` 存在與否判斷分支，空檔有效。缺少 `.next` 時另擷取 `script.bin` 並阻擋未實作的舊式 FEX 引導。
7. 按兩種腳本不同的 `fdtdir`／`allwinner` 搜尋次序選擇 DTB。未明示 `fdtfile` 時使用已核對板型來源中的原配名稱並記錄此前提；未明示 `fdtdir` 時記錄採用來源預設目錄。前兩層搜尋均找不到時，不猜實際 U-Boot `deffdt_file` 的後備值。實體 U-Boot 的預設值及自訂狀態仍由站點資格核對。
8. 使用真實 `/usr/bin/fdtget` 解析 DTB 根節點 `model` 及完整有序 `compatible`，並核對 DTB 標頭與總長度。解析標頭所指的 `memreserve` 表，檢查結尾、區段界線、非零長度、64 位元位址溢位與最多 1024 筆限制，將保留區記入核對結果；含子節點的 `/reserved-memory` 尚未實作，固定區域與動態 CMA 均明確阻擋，空節點可接受。核心 overlay 依選中 DTB 目錄載入，再依序處理 `/boot/overlay-user/`。真實執行 `/usr/bin/fdtoverlay`，保存 `files/effective.dtb`，重新核對板型、保留表及保留節點；缺檔、套用失敗、改變板型或引入未實作的保留節點均阻擋，不靜默退回原 DTB 宣稱成功。
9. 擷取選中目錄的 `${overlay_prefix}-fixup.scr` 及 `/boot/fixup.scr`。核心 fixup 按板型核對封裝架構：ARM32 為 `2`，A64 為 `22`；`boot.scr` 則維持固定 ARM 架構碼 `2`，不混用兩種建置方式。兩者均保留標頭與資料 CRC、單項腳本格式及原文比對。只有已逐項審閱且摘要固定的 A20、H3、A64 核心 fixup，在沒有任何 `param_*` 且不涉及 H3 `pwm` 分支時，才記為不改動 DTB 的離線等效結果。所有 fixup 均不執行；未知腳本、自訂 hook、任何 `param_*`，以及會改寫 console 的 H3 `pwm` 分支均阻擋。

封裝架構依據：`lib/functions/bsp/armbian-bsp-cli-deb.sh` 使用 `mkimage -C none -A arm -T script` 產生 `boot.scr`；`patch/kernel/archive/sunxi-6.18/patches.armbian/build-scripts-add-scr-fixup-support.patch` 對核心 fixup 使用 `mkimage -C none -A $(ARCH) -T script`。核心建置由 `lib/functions/compilation/kernel-make.sh` 傳入 `ARCH=${ARCHITECTURE}`，而 `config/sources/arm64.conf` 宣告 `ARCHITECTURE=arm64`。真 M64 首次擷取的兩份標頭與此差異一致，不因錯配而略過 CRC。

原始檔保存在 `files/`，解析命令標準輸出及錯誤保存在 `analysis/`。單檔上限 128 MiB，文字 256 KiB，overlay 16 MiB；解壓上限 256 MiB，newc 項目上限 200,000，每類 overlay 最多 32 個，本機解析子程序期限為 30 秒。callback 必須自行在取得 bytes 前限制讀取量。

## U-Boot 綁定

```python
build_uboot_config(manifest, *, template, artifact_root) -> dict
```

`artifact_root` 是準備時的 `output`。`template` 由主代理提供完整的 `bpi-lab-uboot-v1` 核定配置，包含 RAM banks／保留區／工作區、組件載入位址與容量、入口、媒體或 TFTP 配對、U-Boot 版本與資格摘要。本工具不產生這些資格或預設實板位址。

函式拒絕 `blocked`，重驗持久 manifest 與全部原始組件證據，空 `.next` 也須仍為一般空檔。範本架構與核心版本必須吻合。`bootargs` 必須與原環境展開的有序清單相同，其中 `ubootpart` 由外部提供唯一核定 PARTUUID；ARM32 `ubootsource` 按外部載入來源展開。不用測試載入分割猜原開機分割，不省略空的 `usb-storage.quirks=`；此參數已通過共用驗證器的 ARM32／A64 正例。

只替換組件路徑、長度、摘要及格式，不修改傳入範本。DTB 指向離線有效 DTB；實際呼叫 `bpi_lab_uboot.validate_config` 正規化外部配置後，逐一核對原始及有效 DTB 的每段 `memreserve` 必須完整包含於某一段已核定 `ram.reserved`，部分涵蓋或完全遺漏均拒絕，不自行新增保留區。舊 manifest 缺少保留表核對結果時，須以原擷取證據重新執行 snapshot replay，不能將缺欄位當成空表。最後呼叫 `validate_artifacts`，成功回傳正規化配置。這不會呼叫 `boot`、UART 或電源入口。產物若要在核定 MMC／TFTP 使用，仍須由獨立部署程序安排同名路徑並核對媒體內容。

## 驗證及限制

```bash
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest discover -s tests -p test_bpi_lab_allwinner.py
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest discover -s tests -p test_bpi_lab_uboot.py
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m ruff check --no-cache tools/bpi_lab_allwinner.py tests/test_bpi_lab_allwinner.py
```

Allwinner 55 項離線測試包含 13 板的來源對應正例、ARM32／A64 組件及 U-Boot 綁定、真 libfdt overlay 順序與套用失敗、板型／版本／CRC／腳本／環境錯配、release 引號文字與展開拒絕、fixup 與 boot.scr 的架構分離及 A64 fixup 雙重 CRC 拒絕、DTB 保留表解析與 RAM 完整涵蓋、原始與 overlay 後保留節點拒絕、未知參數、自訂 fixup、空 `.next`、證據保留及竄改拒絕。合成測資的 RAM、媒體與資格摘要都不是實板核定值。

尚不支援任意修改過的開機腳本、參數化 fixup、FEX、FIT、uImage 核心、壓縮 ARM64 Image，以及 gzip／XZ 以外的核心或 initramfs 壓縮。只支援明確 `rootdev=UUID=...`；不猜 `/dev/mmcblk*` 或 PARTUUID 對應的根 UUID。DTB 模型與相容字串採嚴格比對，已知板的另一版 DTB 也可能因差異而阻擋，須另審來源。

內嵌版本與摘要一致不能證明核心、模組、周邊、TF-A／SPL／DDR 或引導韌體可運作；本工具也不核定 Linux 啟動後的寫入行為。完整映像可信度、首次配對、部署、板上 RAM 摘要、短測及故障返回仍是各自獨立的必要條件。
