# Amlogic 原配組件離線準備

日期：2026-09-17。對應[跨板計畫 B3](bananapi-multiboard-lab-plan-20260917.md)。

本工具實際擷取並核對原配核心、initrd、DTB、環境、腳本、overlay 與 fixup，不只是候選清單。不開啟 UART、不操作電源、不寫媒體、FIP 或 bootloader，不套用 H618 SRAM。所有結果均為 `hardware_validated=false`；組件準備不等於完整平台後端、部署、救援或實板驗證。

## 介面與責任

```python
from tools import bpi_lab_amlogic

manifest = bpi_lab_amlogic.prepare(
    read_file,
    board="bananapim5",
    kernel_release="6.18.49-current-meson64",
    output=output,
)
```

- `read_file(absolute_image_path: str) -> bytes`：由共用唯讀映像讀取器提供；只有 `FileNotFoundError` 表示檔案不存在，空檔不等於不存在。相同路徑在一次準備中最多呼叫一次。
- 提供者負責完整來源映像 SHA-256、分割區選擇、映像內 symlink 解析、讀取界限與一致性。適配器不匯入 `bpi_lab_image`，也不直接開啟映像或掛載檔案系統。
- `board` 必須使用下表精確識別符，`kernel_release` 必須由呼叫端明示；不能從板名推測版本。
- `output` 的父目錄必須已存在，`output` 本身必須尚不存在，包括不得為 symlink。既有結果不覆寫、不續填。
- 回傳的字典與 `output/manifest.json` 相同。缺檔、錯配、不支援條件會保存已讀證據並回傳 `blocked`；無效 API 參數、既有輸出目錄或無法保存結果等程式／檔案系統錯誤拋例外。
- 呼叫端仍須把 `root_uuid` 與映像的檔案系統 superblock UUID 比較，並保存來源映像摘要及 partition PARTUUID。適配器不宣稱完成此項核對。

## 生效來源

| 板型識別符 | 建置家族 | 原配 `fdtfile` |
| --- | --- | --- |
| `bananapicm4io` | `meson-g12b` | `amlogic/meson-g12b-bananapi-cm4-cm4io.dtb` |
| `bananapim2pro` | `meson-sm1` | `amlogic/meson-sm1-bananapi-m2-pro.dtb` |
| `bananapim2s` | `meson-g12b` | `amlogic/meson-g12b-a311d-bananapi-m2s.dtb` |
| `bananapim5` | `meson-sm1` | `amlogic/meson-sm1-bananapi-m5.dtb` |

四板的板級設定分別是 `config/boards/bananapicm4io.conf`、`bananapim2pro.csc`、`bananapim2s.conf`、`bananapim5.conf`。兩個家族都載入 `config/sources/families/include/meson64_common.inc`，其中指定 `boot-meson64.cmd:boot.cmd`、`meson.txt` 及 `meson` overlay 前綴。板級設定與家族來源的 U-Boot／FIP 選擇不同，本工具只保存來源證據，從不搬用其中的寫入命令或位址。

根 `compatible` 使用精確板型與 SoC 字串序列核對，依 Linux v6.18 的 [CM4IO](https://github.com/torvalds/linux/blob/v6.18/arch/arm64/boot/dts/amlogic/meson-g12b-bananapi-cm4-cm4io.dts)、[M2Pro](https://github.com/torvalds/linux/blob/v6.18/arch/arm64/boot/dts/amlogic/meson-sm1-bananapi-m2-pro.dts)、[M2S](https://github.com/torvalds/linux/blob/v6.18/arch/arm64/boot/dts/amlogic/meson-g12b-a311d-bananapi-m2s.dts)及 [M5](https://github.com/torvalds/linux/blob/v6.18/arch/arm64/boot/dts/amlogic/meson-sm1-bananapi-m5.dts) 定義。M2S 的 S922X 版本、CM4 的其他底板不當成同一型號放行。

已核對來源的摘要保存在本適配器的 `SOURCE_HASHES`，不修改通用來源 pins。每次準備都保存該板設定、家族設定、共用設定、bootenv、boot-meson64 與已知空操作 fixup 的原文及 SHA-256。來源變更即阻擋，須人工重新核定語意，不會自動信任新的檔案內容。

## 實際處理

1. 擷取 `/etc/armbian-release`、`/boot/armbianEnv.txt`、`boot.cmd`／`boot.scr`。release 僅解析完整單一字面值，接受 `VENDORCOLOR="247;16;0"` 這類引號內分號；拒絕雙引號內的變數／指令展開與引號外指令，不執行 shell。個別無效行不抹掉已解析板型，避免連鎖誤報。環境依 `env import -t` 保留原始值；重複鍵、未知環境鍵與 `param_*` 明確阻擋。
2. 核對 release 的 `BOARD`、`BOARDFAMILY`、`LINUXFAMILY`、`ARCH`；可選的核心版本欄位若存在也必須一致。`boot.scr` 核對 legacy 標頭／資料 CRC、OS、架構、類型、長度表，且解碼內容必須與原配 `boot.cmd` 及已核定來源逐位元組相同。
3. 按來源判別分支：只要 `/boot/zImage` 存在，即使空檔，原腳本就走 legacy 分支。本版保存它及其他已讀組件，但阻擋，絕不悄悄改用現代 Image。替代的 `boot.ini`、extlinux、`uEnv.txt`、`aml_autoscript`、`s905_autoscript`、customscript 與根目錄替代入口存在也會保存並阻擋。
4. 現代分支核對 ARM64 raw Image magic、大小、位元組序與唯一 `Linux version` 內嵌版本。不能把壓縮核心、FIT 或 vendor 容器冒充 raw Image。
5. `uInitrd` 核對完整 legacy CRC、ARM64／ramdisk 類型；依真實內容解析 raw newc／CRC cpio、gzip、XZ 及串接的早期封存檔。核對 cpio 結構、名稱界限、CRC、尾段，要求內部 `lib/modules` 或 `usr/lib/modules` 版本唯一且符合核心。僅在記憶體解析，不把封存路徑寫入主機。
6. `uInitrd` 的壓縮欄位另外保存；原配建置腳本固定使用 `mkimage -C gzip`，因此不把此標記當成實際 payload 壓縮格式證明。未支援的 zstd／LZ4 等內容會阻擋。
7. DTB 核對標頭、區塊範圍與對齊、保留表、完整長度；使用主機 `dtc` 解析，`fdtget` 讀取根板型、SoC、保留記憶體，拒絕 FIT。靜態 `reg`／memreserve 納入 `reservations`；有限識別標準的單一 `shared-dma-pool`、`reusable`、`linux,cma-default`、明示 `size`／`alignment` 動態 CMA，另記 `dynamic_cma`。未知動態類型、未知屬性、`no-map` 與 CMA 混用、錯誤 cells／對齊仍阻擋。
8. 依原腳本順序擷取核心 overlay，再擷取使用者 overlay。安全且沒有其他阻擋時，真正呼叫 `fdtoverlay` 合併，保存 `derived/board.dtb`，重新驗證格式、板型及記憶體保留區。缺任一 overlay、合併失敗或改變板型都不回退為「成功」。
9. 始終檢查核心 `${overlay_prefix}-fixup.scr` 與 `/boot/fixup.scr`，沒有 overlay 也不略過。核心 fixup 只有在 CRC 正確且解碼摘要等於目前四個 meson64 核心分支共用的純註解來源時才標成 `verified-noop`；自訂或其他 fixup 一律保存、解碼及阻擋，不執行。

單檔核心／initrd 上限 256 MiB，設定 64 KiB，腳本 1 MiB，DTB／overlay 8 MiB，原始擷取總量 768 MiB；initrd 展開上限 512 MiB，XZ 記憶體上限 128 MiB。超限檔只保留大小與 SHA-256，不保存巨量內容。`read_file` 已先回傳 bytes，所以提供者仍須限制讀入前的大小。

主機需有 `/usr/bin/dtc`、`/usr/bin/fdtget` 及使用 overlay 時的 `/usr/bin/fdtoverlay`。子程序以參數陣列執行、固定環境、30 秒期限，不呼叫 shell；任何映像內腳本都不作為可執行檔啟動。

## Manifest

| 欄位 | 含意 |
| --- | --- |
| `schema` | `bpi-lab-amlogic-v1` |
| `status` | `prepared` 或 `blocked`；前者只表示此限定適配器的組件準備完成 |
| `root_uuid` | 有效 `rootdev=UUID=...` 的小寫 UUID，否則 `null`；不代表已核 superblock |
| `components_available` | 只有 `status=prepared` 才為 `true` |
| `components` | 核對通過組件的 `path`、`bytes`、`sha256`、`format` 與版本／板型／保留區資訊 |
| `components.dtb.dynamic_cma` | CMA 的節點、類型、大小、對齊及可選 `alloc_ranges`，沒有偽造的配置位址 |
| `blockers` | `[{"stage": "...", "reason": "..."}]`，不支援與失敗原因均保留 |
| `hardware_validated` | 永遠 `false` |
| `boot_config_validated` | `prepare` 永遠回傳 `false`；沒有 RAM／媒體／U-Boot 核定配置就不能升格 |
| `files` | 逐檔相對路徑、長度、SHA-256、角色；包含來源、原始檔及衍生檔 |
| `reads` | 映像內路徑的 `captured`／`absent`／`error`／`over-limit` 記錄 |
| `sources` | 此板生效建置來源與各自摘要 |
| `bootargs_pattern` | 原配命令列陣列；只有 `{partuuid}` 留給外部核定值 |
| `overlays`、`fixups` | 實際指定 overlay 的順序與 fixup 處理結果 |

`blocked` 時 `components` 仍可包含已核對的核心、initrd 或 DTB，但不能因此當成一次性引導資格。沒有通過核對的原始檔仍在 `files/` 中；未找到的路徑沒有偽造空檔。

## 核定配置接線

```python
checked = bpi_lab_amlogic.validate_boot_config(output, template=approved_template)
config = checked["config"]
```

`approved_template` 使用現有 `bpi-lab-uboot-v1` 配置形狀，必須完整提供架構、核心版本、U-Boot 配對與資格摘要、RAM banks／保留區／工作區、載入媒體、bootargs、`fdt_extra`。三個 `files` 項目須提供 `address`、`capacity`，核心另須 `entry`；只有 `path`、`bytes`、`sha256`、`format` 由已擷取組件填入。沒有內建實板 RAM 位址或 MMC 編號。

bootargs 必須等價於原配腳本與環境，包含唯一、明示的 `ubootpart=...`。MMC template 的 `source.partuuid` 必須相符；TFTP 也必須由外部提供原配引導分割區 PARTUUID，不能推測。`rootdev` 不自動改成新的測試媒體，變更部署根媒體需另行核定。

函式先從保存的原始檔及存在性證據重新執行準備，拒絕 symlink、特殊檔、摘要變更與僅修改 manifest 狀態的「解除阻擋」，再核對 DTB 的所有靜態保留範圍。只有 `reads` 明記 `absent` 才當作不存在，缺少讀取紀錄仍阻擋。接著真正呼叫 `bpi_lab_uboot.validate_config` 與 `validate_artifacts`。回傳含 `config`、`artifacts_verified=true`、`boot_config_validated=true`、`hardware_validated=false`、`executed=false`；不改寫原 manifest，不產生 UART 動作。

若 `components.dtb.dynamic_cma` 非空，template 另須有適配器專用的明確核定區塊：

```python
approved_template["amlogic_cma"] = {
    "requirements": manifest["components"]["dtb"]["dynamic_cma"],
    "qualification_sha256": approved_cma_evidence_sha256,
}
```

上例僅表示資料形狀；不能未審核就把 manifest 複製成授權。需求必須逐項相同、核定證據須有 SHA-256。適配器在送入通用驗證器前取出此區塊，另於回傳的 `amlogic_cma` 保存；通用 schema 不變。

依 [Devicetree 保留區定義](https://github.com/devicetree-org/dt-schema/blob/main/dtschema/schemas/reserved-memory/reserved-memory.yaml)與 [DMA pool 定義](https://github.com/devicetree-org/dt-schema/blob/main/dtschema/schemas/reserved-memory/shared-dma-pool.yaml)，`size` 是動態需求，`alloc-ranges` 是可分配範圍，不是整片 firmware 靜態保留區。因此不能把 CMA 的大小當作某個已配置地址，也不能略過 firmware 的 `reg`。本工具在外部核定 RAM 中扣除靜態保留區、核心工作區及全部組件容量，檢查指定對齊與允許範圍內仍有足夠連續空間，但不選擇或寫入 CMA 地址。會改變此語意的 `cma`、`numa_cma`、`cma_pernuma`、`mem`、`memmap` 核心參數仍阻擋。

容量核對不保證 Linux 實際 CMA 分配成功；核心設定、其他早期配置及 DMA 限制仍需外部核定與實測。Linux 的分配職責見[核心 CMA 實作](https://github.com/torvalds/linux/blob/v6.18/kernel/dma/contiguous.c)。

這些是結構與內容核對，不是來源簽章驗證。呼叫端須保存完整映像摘要、產物目錄及配對證據，不可把可任意修改的本機 JSON 當成授權來源。通用 U-Boot 的 `mainline-v2025.01` ABI 限制仍然有效；來源板設定使用的其他版本不會自動取得這個 ABI 資格。

## 本機回歸

```sh
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest discover -s tests -p test_bpi_lab_amlogic.py -v
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m ruff check --no-cache tools/bpi_lab_amlogic.py tests/test_bpi_lab_amlogic.py
```

62 項小型測試通過，涵蓋四板、獨立載入、直接腳本匯入、缺檔／空檔、來源漂移、編譯腳本不同步、CRC／格式／版本／板型錯配、封存路徑、解壓限額、真實 overlay 合併順序及失敗、fixup、未知參數、證據保存／竄改與外部 RAM／媒體／ABI 核定。另含真 release 引號語法、CMA 與 firmware 分類、外部 CMA 核定及容量／範圍拒絕。既有 U-Boot 35 項回歸及本工具 Ruff 亦通過。不把合成 template 位址放入工具預設，也不把 fixture 成功計入硬體通過數。

M5 真映像的 CLI 實驗由主代理統一執行並保存；首次 `001` 曾因獨立匯入路徑失敗，修正後新增獨立載入回歸。`002` 在完整來源摘要核對後遇到 CLI 與適配器板名不同，改由 CLI 明確轉換 `bpi-*` 識別符並在解壓前檢查；本適配器 API 不變。

`003` 保存於 `output/evidence/bpi-multiboard-components-20260917-m5-003/components/manifest.json`，原結果因 release 引號內分號及標準動態 CMA 被過度阻擋。修正後僅重播其已擷取檔案，逐檔核對原大小／SHA-256，缺檔依 `reads` 明記的 `absent` 判定，不再解壓 XZ，也不改寫 `003`。

此次暫存重播結果為 `prepared`、零 blockers、`root_uuid=9e2682e8-0bee-40e2-b42e-8eb1ef084b87`、三個組件可用。核心版本為 `6.18.49-current-meson64`。靜態區間仍保留 `0x05000000+0x00300000` 與 `0x05300000+0x02000000`；另記 256 MiB、4 MiB 對齊的動態 CMA，尚無核定地址或完整實板 template。暫存 manifest 的 SHA-256 為 `29ae116ce0b54bfcd4281046a9eef8efdcdef5f94cb1c286a1dc38e21765fd76`；可持久留存的重播與來源關聯證據由主代理共用流程負責。此結果只證明原配組件準備，不宣稱完整 CLI、引導或硬體通過。

## 保留缺口

- 不支援 legacy `zImage`／unzip 分支、FIT、vendor 容器、extlinux 或自訂啟動入口；檔案存在性介面也不能證明任意未列入範圍的韌體入口不存在。
- 不支援任意 fixup、`param_*`、標準限定 CMA 以外的動態保留記憶體、zstd／LZ4 initrd。內嵌版本核對不等於全部模組 ELF ABI、init 可執行性或 initramfs 功能驗證。
- 核心與 DTB 沒有通用內建 CRC；SHA-256 是本次擷取內容證據，不替代整體來源映像摘要與信任根。
- 配對、媒體授權、RAM／U-Boot 建置能力、完整部署、正常與故障返回救援仍需後續工具及逐板實體資格。本工具不是完整後端。
