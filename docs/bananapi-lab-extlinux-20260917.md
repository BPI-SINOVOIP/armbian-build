# C3 原配組件與 extlinux

## API 與狀態

四個模組均提供 `prepare(read_file, *, board='bpi-*', kernel_release, output) -> manifest`。
家族入口為 `tools/bpi_lab_rockchip.py`、`tools/bpi_lab_mediatek.py`、`tools/bpi_lab_spacemit.py`；
各自公開 `POLICIES`，鍵值為唯一 `bpi-*` 板名，可供磁碟讀取前檢查家族錯配。
`tools/bpi_lab_extlinux.py` 另提供九款已知 extlinux 板的統一入口、`parse` 與 `resolve_path`。

`read_file('/boot/...')` 必須回傳 `bytes`，並提供根檔案系統加上 `/boot` 掛載的唯讀視圖。
只有 `FileNotFoundError` 表示不存在；其他讀取失敗均阻擋，不把未擷取路徑當成不存在。
回呼負責安全解析符號連結、分割區映射及來源摘要，組件模組不解壓磁碟映像、不掛載、不操作硬體。
`output` 的父目錄須存在，目標目錄必須尚未存在；不覆蓋舊證據，不接受符號連結父目錄。

manifest 至少包含 `status`、`root_uuid`、`blockers` 與固定的 `hardware_validated=false`。
`status=prepared`／`ready=true` 只表示指定原入口的組件與靜態語意通過；
`execution_ready=false`、`boot_config_validated=false` 不會因組件成功而提升。
`components_available` 表示必要組件檢查齊備，不代替原入口、來源認證或實板核定。
`files` 記錄原路徑、證據路徑、大小及 SHA-256；`checks` 只放可序列化結構，原始內容留在 `files/`。

## 原配核對

- 核對 `/etc/armbian-release` 板型、家族、核心格式與 initrd 架構。
- 載入核心須等同 `/boot/vmlinuz-${kernel_release}`；解析 ARM32 zImage、ARM64／RISC-V Image 標頭與內嵌版本。
- 必須擷取 `/boot/config-${kernel_release}`，檢查架構與內建命令列；有 `IKCFG_ST` 時另比對核心內嵌配置。
- initrd 支援 raw 或本倉 legacy 封裝，核對 CRC、CPU、用途、壓縮標記及 `/boot/initrd.img-${kernel_release}`；有界解析 gzip／XZ／newc 的 `init` 與唯一模組版本。
- 以 `fdtget` 核對根節點 `model`／`compatible`、標頭與保留表，保留 `/reserved-memory` 原始屬性及 `/chosen/bootargs`，不把記憶體保留需求丟掉。
- `fdtoverlay` 依原順序產生有效 DTB 並再次核對板型。失敗或缺檔不仿冒成功；不因原 U-Boot 可能跳過或回退而放行。
- 只重用 Allwinner 模組的二進位解壓、newc 與證據工具；不讀其板型政策，也不依賴其核心配置 API。

ARM32 zImage 除 gzip／XZ 外，支援 Linux `lz4_with_size`：以系統 `liblz4` 的
`LZ4_decompress_safe` 解碼，每區塊上限 8 MiB、總量不超過既有展開界限，核對尾端原始大小、
唯一核心版本及內嵌 config。`checks.kernel.payload_compression` 區別核心自解壓與 U-Boot gzip 解壓；
不為 zImage 產生 `kernel_comp_addr_r`。缺少 `liblz4` 明確阻擋，測試另使用 `/usr/bin/lz4` 產生測資。
格式依據為固定 Forge1 核心的 [壓縮規則](https://github.com/armbian/linux-rockchip/blob/c6157104418d012823413c02f9222f3fe123dd25/scripts/Makefile.lib#L443)
及 [解碼實作](https://github.com/armbian/linux-rockchip/blob/c6157104418d012823413c02f9222f3fe123dd25/lib/decompress_unlz4.c)。

ARM32 `CONFIG_CMDLINE_EXTEND=y` 僅接受有內嵌 config 證據的 DT 路徑。
原 `bootargs_template` 不改寫，`checks.kernel_command_line` 與
`runtime_requirements.kernel_cmdline` 另列核心附加值及 `effective_bootargs_template`。
合併次序依 [實際 DT 程式](https://github.com/armbian/linux-rockchip/blob/c6157104418d012823413c02f9222f3fe123dd25/drivers/of/fdt.c#L1110)，
不是僅依 Kconfig 說明推測；超過 ARM32 的 1024 位元組界限、根媒體／記憶體覆寫、重複參數、
`FORCE`／`OVERRIDE` 均阻擋。ARM64／RISC-V 內建命令列政策不放寬。

## extlinux 語意

保留全部 label 與選單資訊，解析大小寫、`LINUX`／`KERNEL`、`DEVICETREE`／`FDT`、overlay 別名、
`DEFAULT`、`MENU DEFAULT`、`TIMEOUT`、`PROMPT`、`KASLRSEED`。
沒有明示預設時記錄第一項候選；多 label 的互動及失敗後備尚未逐項核對，因此明確阻擋，不默選後刪除其他項目。
未知指令、重複欄位、全域 APPEND、衝突預設、混用 FDT／FDTDIR 均阻擋。

APPEND 保持參數順序，要求唯一 `root=UUID=...`，不從 PARTUUID 推導檔案系統 UUID。
未解析變數、引號、`initrd=`、FIT 選擇子及未支援的載入語意均阻擋。
絕對 `/boot/...` 與獨立開機分割區的 `/Image`／`/dtb/...` 分別建模；
相對路徑相對組態目錄，拒絕上層跳轉。命名空間無法唯一判定或混用時不猜測。
FDTDIR 依已核對板型擷取候選 DTB，並把執行期 `fdtfile` 放入強制範本條件；不聲稱知道實板 U-Boot 的預設值。
其他可競爭入口存在或讀取失敗時明確阻擋。原入口及 `pxe_label_override` 條件必須保留。

格式依據為本倉 `lib/functions/rootfs/distro-agnostic.sh`、
`lib/functions/image/partitioning.sh`、`lib/functions/bsp/armbian-bsp-cli-deb.sh`，
以及 [U-Boot 原始文件](https://docs.u-boot.org/en/latest/develop/distro.html)；
RISC-V 標頭依 [Linux 原始文件](https://docs.kernel.org/arch/riscv/boot-image-header.html) 核對。

## 原入口範本

四個模組公開 `validate_template(manifest, *, template, artifact_root)`，這是本輪的等價範本核對 API；
刻意不提供把原入口改寫成直接 `booti` 的 `build_uboot_config`。
範本 `schema` 為 `bpi-lab-original-entry-template-v1`，必須且只能提供以下欄位：

| 欄位 | 核對規則 |
| --- | --- |
| `board`, `kernel_release`, `root_uuid` | 必須與持久 manifest 一致 |
| `entry` | 完整保留原入口種類、路徑、摘要及所選 label |
| `runtime_requirements` | 完整保留命名空間、原韌體環境與外部配對條件 |
| `bootargs_template` | 完整保留參數順序、值及尚待核定的佔位符 |
| `dtb_checks` | 必須等同 `checks.dtb` 與 `checks.overlay_application`，包括保留記憶體 |
| `pairing_sha256`, `firmware_review_sha256` | 外部配對及前置鏈審閱證據摘要，要求 64 位十六進位 |

核對器重新核對磁碟上的 manifest 及每個原配證據摘要；阻擋未準備完成、篡改、遺漏或改寫契約。
回傳 `artifact_contract_verified=true` 只表示檔案／範本一致；不驗證外部摘要的內容或授權，
也不把 `external-review` 佔位符當成已核定值。這不是 RAM 配置、UART 命令或共用 `uboot.render` 輸入。
原腳本／extlinux 的受限執行已由獨立 [原入口執行器](bananapi-lab-original-entry-20260917.md) 接入共用 backend／lifecycle，
使用 `bpi-lab-original-entry-v1` 配置，重新核對實際文件內容、分割區、RAM、保留區及 U-Boot 配對後才傳送。
組件範本本身仍不是執行配置；K3 原廠環境沒有已核定的 vendor 執行 ABI，保持明確阻擋。

## 真來源抽樣

截至 2026-09-17，以下主代理抽樣的 `preparation.json` 與組件清單均為 `prepared`、
`blockers=[]`、`root_uuid_verified=true`；已核對準備清單所引用的組件及 extraction 長度與 SHA-256。

| 板型 | 完整核心版本 | 原入口 | 目前成功證據 |
| --- | --- | --- | --- |
| `bpi-m7` | `6.18.49-current-rockchip64` | `boot.scr`／`rockchip64` | [M7-002](../output/evidence/bpi-multiboard-integrate-20260917-m7-002/preparation.json) |
| `bpi-r3` | `6.12.82-current-filogic` | extlinux | [R3-001](../output/evidence/bpi-multiboard-integrate-20260917-r3-001/preparation.json) |
| `bpi-f3` | `6.18.37-current-spacemit` | extlinux／原配 gzip Image | [F3-002 重播](../output/evidence/bpi-multiboard-integrate-20260917-f3-002-replay/preparation.json) |
| `bpi-sm10` | `6.18.3-current-spacemit-k3-bpi` | K3 原廠 MMC 環境／原配 gzip Image | [SM10-002 重播](../output/evidence/bpi-multiboard-integrate-20260917-sm10-002-replay/preparation.json) |

2026-09-18 追加 Forge1：`6.1.115-vendor-rockchip` 的 LZ4 zImage 與
`CONFIG_CMDLINE="user_debug=31"`／`EXTEND=y` 已於
[新目錄重播](../output/evidence/bpi-c3-forge1-lz4-replay-20260918-001/preparation.json) 通過。
R2 的原環境 DTB 路徑已正確保留；主代理從原 XZ 新擷取的
[R2-004](../output/evidence/bpi-multiboard-integrate-20260917-allboard-bpi-r2-004/preparation.json)
確認該無副檔名路徑實際不存在，故為原映像 `missing_file`，不是 parser 缺口，詳見 MediaTek 文件。

F3／SM10 由各自 `001` 的成功擷取重播，`source_reread=false`，不是再次解壓或重新讀取來源映像；
新 extraction 固定摘要與已解除的舊阻擋詳見 [SpacemiT 抽樣紀錄](bananapi-lab-spacemit-20260917.md#真來源抽樣)。
舊 M7 首次擷取的 `ok=false` 只保留為診斷歷史，不可重播，也不再用來代表目前抽樣狀態。
上述成功均維持 `hardware_validated=false`、`whole_backend_ready=false`、`boot_executed=false`、
`media_written=false`，不能當作實板啟動、安全鏈或完整後端通過證明。

## 本機回歸

```sh
output/evidence/bpi-sram-supervisor/model-venv/bin/python -m unittest discover -s tests -p 'test_bpi_lab_extlinux.py' -v
output/evidence/bpi-sram-supervisor/model-venv/bin/python -m unittest discover -s tests -p 'test_bpi_lab_rockchip.py' -v
output/evidence/bpi-sram-supervisor/model-venv/bin/python -m unittest discover -s tests -p 'test_bpi_lab_mediatek.py' -v
output/evidence/bpi-sram-supervisor/model-venv/bin/python -m unittest discover -s tests -p 'test_bpi_lab_spacemit.py' -v
```

測試使用合成核心／initrd、受控腳本與實際 libfdt 工具。原始內容可含必要技術識別符，不代表實板核定。
2026-09-18 四個組件測試新增 LZ4 區塊／尾碼／大小界限、EXTEND 衝突、R2 原路徑與 CM6 單核心 FIT 正負例。
本次文件核對只讀取現存抽樣證據，沒有重新解壓巨型映像、實板操作、提交或推送。
