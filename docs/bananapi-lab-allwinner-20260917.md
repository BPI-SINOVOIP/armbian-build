# Allwinner 原配組件離線準備

日期：2026-09-17。對應[跨板計畫 B2／C1](bananapi-multiboard-lab-plan-20260917.md)。

實作入口為 `tools/bpi_lab_allwinner.py`，處理 Allwinner ARM32／ARM64 的 16 板原配組件，包含 H618 M4 Berry、M4 Zero 與 M4 Zero EMAC。CMA 共用候選工具為 `tools/bpi_lab_cma.py`，本次不修改 Amlogic。不操作 UART、電源或媒體，不執行映像腳本，不提供部署及救援生命週期。所有結果的 `hardware_validated`、`ddr_validated`、`boot_chain_validated` 始終為 `false`；`prepared` 不代表實板已可開機。依使用者要求，跨平台軟體完成後才統一安排實板驗證。

## 板型來源

板別由既有 `board-registry.json`、`config/boards/` 及 `config/validation/bananapi-sunxi-*.json` 交叉對應，不從檔名猜測。每次 manifest 記錄實際讀取來源的 SHA-256。可傳入表中正式板別或其 `artifact_board`，不接受其他簡稱。

H618 的 `BOARDFAMILY`、`BOOT_FDT_FILE`、`OVERLAY_PREFIX` 與家族 `BOOTSCRIPT` 必須為唯一的有效單行常值設定，接受單引號、雙引號或無引號；註解不算設定，錯板值、重複賦值、展開、拼接及缺漏均阻擋。將這些已解析賦值替換為固定標記後，另外核對已審閱 shell 結構的摘要，避免將條件內、未呼叫函式內或不同分支中的文字誤認為有效設定；未知結構須重新審閱，不執行 shell。核心版本必須對應家族 `current`／`edge` 分支，且仍在該板 `KERNEL_TARGET` 範圍；引用來源也須符合登錄摘要。這是保守的固定來源核對，不是通用 shell 解譯器。

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
| `bpi-m4b` | `bananapim4berry` | `sun50i-h618-bananapi-m4-berry.dtb` |
| `bpi-m4z` | `bananapim4zero` | `sun50i-h618-bananapi-m4-zero.dtb` |
| `bpi-m4z-emac` | `bananapim4zeroemac` | `sun50i-h618-bananapi-m4-zero-emac.dtb` |

R1 屬封存來源，不在此 16 板。來源表只供組件身分核對，不沿用其中的舊引導位址、媒體偏移、實板授權或 0845 資格。

H618 三板沒有獨立驗證 JSON，改以 `patch/kernel/archive/sunxi-6.18/dt_64/` 的三份本倉 DTS 固定摘要綁定根節點身分，來源變動即阻擋。另核對板型的 `BOOT_FDT_FILE`、`OVERLAY_PREFIX`、`sun50iw9-bpi` 家族與 `boot-sun50i-next.cmd` 宣告。M4 Berry 按實際來源保留 `BiPai,bananapi-m4berry`、`allwinner,sun50i-h616`，不猜成 H618 compatible；M4 Zero 與 EMAC 各自核對完整有序 compatible，不互換。這些只證明組件身分，不核定 DDR、SPL、TF-A、U-Boot 或 H618／0845 救援配對。

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
| `ddr_validated`、`boot_chain_validated` | 固定 `false`，不繼承 H618／0845 資格 |
| `source_image_verified` | 固定 `false`；單憑 callback 無法證明完整映像已核對 |
| `files` | 原映像絕對路徑、證據相對路徑、大小及 SHA-256；有效 DTB 另列 |
| `checks`、`reads`、`commands` | 已通過核對、缺檔／讀取狀態、本機解析命令及輸出摘要 |
| `original_release`、`original_env` | 僅文字解析的原始鍵值 |
| `bootargs_template` | 原環境展開結果；保留待外部核定的引導分割及載入來源欄位 |

CLI／主代理必須再將 `root_uuid` 與實際檔案系統超級區塊 UUID 比對，並把本 manifest 綁定到其完整映像、分割與來源摘要證據。`ready` 不是站點啟用、硬體可開機或媒體寫入授權。

## 實際處理

1. 擷取 `/etc/armbian-release`、`/boot/armbianEnv.txt`、`/boot/boot.cmd`、`/boot/boot.scr`。核對板別、家族、`KERNEL_IMAGE_TYPE`、`INITRD_ARCH`，拒絕重複鍵、shell 展開、未知環境參數及錯配值。release 使用獨立的單純賦值規則，接受引號內分號等文字，例如 `VENDORCOLOR`，仍拒絕命令替換與變數展開；U-Boot 環境不套用 shell 引號規則。
2. 開機腳本必須逐位元組符合本倉 `boot-sunxi.cmd` 或 `boot-sun50i-next.cmd`；`boot.scr` 必須通過標頭與資料 CRC、單項腳本格式，以及與 `boot.cmd` 的內容比對。即使兩份自訂腳本彼此一致，仍阻擋。
3. 按腳本讀取 `/boot/zImage` 或 `/boot/Image`、`/boot/uInitrd`，交叉比對 `/boot/vmlinuz-${kernel_release}` 與 `/boot/initrd.img-${kernel_release}`。所有板均由同一 `read_file` 必讀 `/boot/config-${kernel_release}`，不讀 `/boot/config`，不以本倉核心預設補值；配置上限 1 MiB，核對架構、賦值格式與重複鍵並保存摘要。別名解析由 callback 負責，讀取路徑與實際回傳內容均留下證據。
4. ARM32 驗證 zImage 魔術值及完整長度，再定位 gzip／XZ 壓縮資料，有限解壓並核對唯一內嵌 `Linux version`。ARM64 驗證 raw Image 標頭、大小、位元組序及內嵌版本。若存在 `IKCFG_ST`，必須唯一、可有界解壓且帶 `IKCFG_ED` 結尾，內嵌配置須與原配 config 逐位元組相等；缺少原配 config 不能以內嵌資料替代。不以檔名或外部版本宣告替代內容核對。
5. `uInitrd` 核對用途、架構、兩層 CRC 及本倉封裝標記；實際 payload 另行辨識。支援 newc、gzip／XZ 及前置未壓縮 newc 接壓縮主封存的組合，解析 `init` 存在性及模組版本，絕不執行或解出其中程式。
6. ARM32 以 `.next` 存在與否判斷分支，空檔有效。缺少 `.next` 時另擷取 `script.bin` 並阻擋未實作的舊式 FEX 引導。
7. 按兩種腳本不同的 `fdtdir`／`allwinner` 搜尋次序選擇 DTB。未明示 `fdtfile` 時使用已核對板型來源中的原配名稱並記錄此前提；未明示 `fdtdir` 時記錄採用來源預設目錄。前兩層搜尋均找不到時，不猜實際 U-Boot `deffdt_file` 的後備值。實體 U-Boot 的預設值及自訂狀態仍由站點資格核對。
8. 使用真實 `/usr/bin/fdtget` 解析 DTB 根節點 `model` 及完整有序 `compatible`，並核對 DTB 標頭與總長度。解析標頭所指的 `memreserve` 表，檢查結尾、區段界線、非零長度、64 位元位址溢位與最多 1024 筆限制。另將 `/reserved-memory` 的純固定 `reg` 保留區及動態 CMA 分開記錄，不將 `alloc-ranges` 當固定占用。核心 overlay 依選中 DTB 目錄載入，再依序處理 `/boot/overlay-user/`。真實執行 `/usr/bin/fdtoverlay`，保存 `files/effective.dtb`，重新核對板型、保留表及保留節點；缺檔、套用失敗、改變板型或引入未支援的保留語意均阻擋，不靜默退回原 DTB 宣稱成功。
9. 擷取選中目錄的 `${overlay_prefix}-fixup.scr` 及 `/boot/fixup.scr`。核心 fixup 按板型核對封裝架構：ARM32 為 `2`，ARM64 為 `22`；`boot.scr` 則維持固定 ARM 架構碼 `2`，不混用兩種建置方式。兩者均保留標頭與資料 CRC、單項腳本格式及原文比對。只有已逐項審閱且摘要固定的 A20、H3、A64、H616 核心 fixup，在沒有任何 `param_*` 且不涉及 H3 `pwm` 或 H616 `pwm34` 分支時，才記為不改動 DTB 的離線等效結果。所有 fixup 均不執行；未知腳本、自訂 hook、任何 `param_*`，以及上述會改寫 console 的分支均阻擋。

封裝架構依據：`lib/functions/bsp/armbian-bsp-cli-deb.sh` 使用 `mkimage -C none -A arm -T script` 產生 `boot.scr`；`patch/kernel/archive/sunxi-6.18/patches.armbian/build-scripts-add-scr-fixup-support.patch` 對核心 fixup 使用 `mkimage -C none -A $(ARCH) -T script`。核心建置由 `lib/functions/compilation/kernel-make.sh` 傳入 `ARCH=${ARCHITECTURE}`，而 `config/sources/arm64.conf` 宣告 `ARCHITECTURE=arm64`。真 M64 首次擷取的兩份標頭與此差異一致，不因錯配而略過 CRC。

原始檔保存在 `files/`，解析命令標準輸出及錯誤保存在 `analysis/`。單檔上限 128 MiB，文字 256 KiB，overlay 16 MiB；解壓上限 256 MiB，newc 項目上限 200,000，每類 overlay 最多 32 個，本機解析子程序期限為 30 秒。callback 必須自行在取得 bytes 前限制讀取量。

其他家族共用 `_kernel(blob, arch, release)` 時可以不傳 config，僅做格式／版本驗證，回傳 `kernel_config_verified=false`。傳入 config 且成功比對 IKCONFIG 才回傳 `true`；沒有 IKCONFIG 時仍為 `false`。Allwinner `prepare` 另有必讀檔案與 `kernel_config` 必要核對。只要原始或有效 DTB 有動態 CMA，就必須有核心內嵌配置與原配 config 完全一致的證據；缺少 IKCONFIG 一律阻擋，不以相同版本或 manifest 自行宣告的驗證旗標代替。

## U-Boot 綁定

```python
build_uboot_config(manifest, *, template, artifact_root) -> dict
```

`artifact_root` 是準備時的 `output`。`template` 由主代理提供完整的 `bpi-lab-uboot-v1` 核定配置，包含 RAM banks／保留區／工作區、組件載入位址與容量、入口、媒體或 TFTP 配對、U-Boot 版本與資格摘要。本工具不產生這些資格或預設實板位址。

函式拒絕 `blocked`，重驗持久 manifest 與全部原始組件證據，空 `.next` 也須仍為一般空檔。範本架構與核心版本必須吻合。`bootargs` 必須與原環境展開的有序清單相同，其中 `ubootpart` 由外部提供唯一核定 PARTUUID；ARM32 `ubootsource` 按外部載入來源展開。不用測試載入分割猜原開機分割，不省略空的 `usb-storage.quirks=`；此參數已通過共用驗證器的 ARM32／A64 正例。

只替換組件路徑、長度、摘要及格式，不修改傳入範本。另以共用安全檔案介面重讀核心、核心配置與原始／有效 DTB，重新呼叫 `_kernel` 核對格式、版本及內嵌配置，再在獨立暫存目錄重解析記憶體語意，與 manifest 的核對結果逐項比對。即使同步改寫 config、檔案摘要、CMA 推導結果及驗證旗標，也不能在核心不變時將頁面區塊階數由 11 改成 9 而放行。DTB 指向離線有效 DTB；實際呼叫 `bpi_lab_uboot.validate_config` 正規化外部配置後，逐一核對原始及有效 DTB 的每段 `memreserve`、固定 `reserved_memory` 必須完整包含於某一段已核定 `ram.reserved`，部分涵蓋或完全遺漏均拒絕，不自行新增保留區。舊 manifest 缺少記憶體核對結果時，須重新重播；如果原配配置未擷取，須新擷取，不能將缺欄位當成空表。最後呼叫 `validate_artifacts`，成功回傳正規化配置。這不會呼叫 `boot`、UART 或電源入口。產物若要在核定 MMC／TFTP 使用，仍須由獨立部署程序安排同名路徑並核對媒體內容。

## 動態 CMA

目前只放行已審閱 Linux `6.18.49` 語意、4 KiB 頁面、未啟用大頁與 NUMA、內建 `CONFIG_CMDLINE=""` 的配置。必要旗標為 `CONFIG_CMA=y`、`CONFIG_DMA_CMA=y`、`CONFIG_OF_RESERVED_MEM=y`，頁面區塊階數必須有原配設定依據；未知版本或未覆蓋分支保持阻擋。

- [保留記憶體來源](https://github.com/gregkh/linux/blob/v6.18.49/drivers/of/of_reserved_mem.c)確認：根與保留節點的 cells 必須一致；`size` 用 size cells，`alignment` 用 address cells；CMA 對齊取 DT 指定值與核心最小對齊的較大者。`alloc-ranges` 是依次嘗試的搜尋窗口，不是整段固定占用。
- [CMA 對齊定義](https://github.com/gregkh/linux/blob/v6.18.49/include/linux/cma.h)、[頁面區塊分支](https://github.com/gregkh/linux/blob/v6.18.49/include/linux/pageblock-flags.h)及[階數定義](https://github.com/gregkh/linux/blob/v6.18.49/include/linux/mmzone.h)確認：本次支援分支的最小對齊為 `1 << (CONFIG_PAGE_SHIFT + CONFIG_PAGE_BLOCK_MAX_ORDER)`，不是直接套 `CONFIG_CMA_ALIGNMENT` 或舊版最大 buddy order 公式。
- [預設 CMA 初始化](https://github.com/gregkh/linux/blob/v6.18.49/kernel/dma/contiguous.c)要求 reusable、不得 no-map，且配置大小須符合核心最小對齊。`size` 不必是 DT 指定較大 alignment 的倍數；不擅自向上擴大 size。

原始及有效 DTB 的 `checks` 分別包含 `memreserve`、`reserved_memory`、`dynamic_cma`。只支援單一 `shared-dma-pool`、`reusable`、`linux,cma-default` 動態節點；保留 `declared_alignment=null` 以表達原 DT 未指定，`alignment` 才是推導的有效值。固定節點只接受純 `reg`、可選 `no-map` 及已知狀態／phandle 屬性；混用 `reg`／`size`、未知 compatible、停用狀態、巢狀節點、多個預設 CMA、非空 `ranges`、溢位或錯誤 cells 都阻擋。

外部範本新增 `allwinner_cma`，只在有效 DTB 有動態 CMA 時提供：

```python
template["allwinner_cma"] = {
    "requirements": manifest["checks"]["overlay_application"]["dynamic_cma"],
    "kernel_config_sha256": manifest["files"]["kernel_config"]["sha256"],
    "effective_dtb_sha256": manifest["files"]["effective_dtb"]["sha256"],
    "qualification_sha256": qualification_sha256,
}
```

`qualification_sha256` 必須來自外部實際核定證據，工具不產生資格；上述複製只是結構，不代表核定已完成。綁定時逐項核對有效需求、兩份摘要及資格摘要格式，再將搜尋窗口與 `ram.banks` 取交集，排除 `ram.reserved`、`kernel_work` 及全部組件的完整 `capacity`，最後核對有一段對齊後足夠大的連續空間。不同 banks／不同搜尋窗口／碎片空間不得相加；`ram.boot` 是載入範圍，不整段扣除。有效 DTB 的 overlay 修改以修改後需求為準，原始固定保留區仍逐項核對。

`cma`、`mem`、`memmap`、NUMA CMA、核心／可移動記憶體、`highmem`、`vmalloc`、`reserve_mem`、`crashkernel`、大頁等會改變 RAM 語意的開機參數拒絕綁定。此檢查只證明有足夠候選空間，不選擇 CMA 最終位址，也不證明 Linux 的 zone、其他早期保留區或執行期配置必定成功。回傳共用 U-Boot 配置前移除 `allwinner_cma`，其外部核定範本仍須由呼叫端留存。

### M1 證據與待辦

既有 M1 `/reserved-memory/default-pool` 為 96 MiB，`alloc-ranges` 為 `0x40000000` 起算 `0x10000000`，沒有 `alignment`。舊擷取核心的內嵌配置顯示 `PAGE_SHIFT=12`、`PAGE_BLOCK_MAX_ORDER=11`、`ARCH_FORCE_MAX_ORDER=11`、`CMA_AREAS=7`、`CMA_ALIGNMENT=8`，因此有效最小對齊是 8 MiB。內嵌配置 SHA-256 為 `8cbcb21b91ce7b97e96f670a3b3408b630dfffb4219aaddfd8f2cb18b29886db`；本次只在暫存目錄進行舊證據唯讀檢查，沒有讀取原 XZ 或操作實板。

舊 `output/evidence/bpi-multiboard-components-20260917-m1-003-replay/extraction/extraction.json` 的 SHA-256 為 `aa8b9216a2107e8327fba618b6366c64b5f8c393b98d11c0af34907180c7bda6`。實際重播確認未擷取 `/boot/config-6.18.49-current-sunxi`，狀態為 `failed`／`reader_failed`，不是 `missing`；準備仍為 `blocked`。內嵌配置只供來源語意確認，不能冒充此必要檔案。

新增必讀項只有上述版本化 config，不需要 `/boot/config`。指定原映像為 `/media/pi/SMCI/bpi/google-drive-upload/2026/2026.08/bpi-m1/Armbian-unofficial_26.11.0-trunk_Bananapi_bookworm_current_6.18.49_minimal.img.xz`，SHA-256 為 `8500126b4055d95e1c14083cbe7ff451bfed8e508491d3f21d93ddbe99f213c9`。主代理已完成新擷取，記錄於 `output/evidence/bpi-multiboard-integrate-20260917-m1-001/preparation.json`；其擷取 SHA-256 為 `22374bf4def0442c540ecc4701f89fd66c9c95549f7ace6df3cb5176aee9337e`。本輪只讀取該擷取，在暫存目錄以修正版重播，確認仍為 `prepared`、rootUUID 一致、`kernel_config_verified=true`、CMA 96 MiB／對齊 8 MiB，原有與重播組件均通過 `_verify_memory_evidence`。前後核對 `preparation.json`、`family-result.json`、`components/manifest.json`、`extraction/extraction.json` 四份摘要未變；沒有覆寫真證據、讀取原 XZ 或接觸硬體。站點 RAM／CMA 核定與跨平台完成後的實板驗證仍由主代理統整。

## 驗證及限制

```bash
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest discover -s tests -p test_bpi_lab_allwinner.py
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest discover -s tests -p test_bpi_lab_cma.py
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest discover -s tests -p test_bpi_lab_uboot.py
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest discover -s tests -p test_bpi_lab_prepare.py
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest discover -s tests -p test_bpi_lab_extlinux.py
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m ruff check --no-cache tools/bpi_lab_allwinner.py tools/bpi_lab_cma.py tests/test_bpi_lab_allwinner.py tests/test_bpi_lab_cma.py
```

上述命令通過：Allwinner 79 項、CMA 18 項、U-Boot 35 項、prepare 20 項、extlinux 25 項；Ruff 無錯誤。新增回歸涵蓋 16 板來源對應、H618 三板組件限定／身分錯配／來源變動／`pwm34` 拒絕、原配 config 必讀與 IKCONFIG 一致性、共用 helper 未提供 config 時明列未核對、snapshot 未擷取分類、M1 無 alignment 動態 CMA、錯誤位址家族、對齊與 cells、固定保留區完整涵蓋、碎片與組件容量排除、資格缺漏／摘要錯配、overlay 有效需求、核心旗標及命令列負例、手改 manifest 與原配證據不符拒絕。本輪另補同步偽改 config 與 manifest、無 IKCONFIG 的準備及綁定阻擋、偽造配置驗證旗標、綁定時重驗版本，以及 H618 引號形式、註解、錯板、重複／未知設定、未生效區塊及錯誤核心分支。合成測資的 RAM、媒體與資格摘要都不是實板核定值。

尚不支援任意修改過的開機腳本、參數化 fixup、FEX、FIT、uImage 核心、壓縮 ARM64 Image，以及 gzip／XZ 以外的核心或 initramfs 壓縮。只支援明確 `rootdev=UUID=...`；不猜 `/dev/mmcblk*` 或 PARTUUID 對應的根 UUID。DTB 模型與相容字串採嚴格比對，已知板的另一版 DTB 也可能因差異而阻擋，須另審來源。

內嵌版本與摘要一致不能證明核心、模組、周邊、TF-A／SPL／DDR 或引導韌體可運作；本工具也不核定 Linux 啟動後的寫入行為。完整映像可信度、首次配對、部署、板上 RAM 摘要、短測及故障返回仍是各自獨立的必要條件。
