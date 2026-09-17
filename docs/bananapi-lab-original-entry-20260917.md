# 原入口一次性交接

`tools/bpi_lab_original_entry.py` 借用 lifecycle 已開啟、停止 autoboot 的救援 U-Boot console，
從核定 eMMC 選取原入口。它不開啟 UART、不操作電源、不改寫核心／initrd／DTB、
不呼叫 `saveenv`、`env save`、`mmc write`、`erase`、`partconf`，也不自動重試。
原配 Linux 啟動後可能寫入 eMMC，必須另有明確授權，不能稱為整段唯讀。

## API 與串接

```python
validate_config(config) -> dict
lifecycle_view(config) -> dict
scope_digest(config) -> str
validate_artifacts(config, artifact_root=None) -> dict
build_uboot_config(template, *, artifact_root) -> dict
render(config) -> dict
boot(console, config, records=None, *, timeout=300, monotonic=time.monotonic) -> dict
build_rescue_sd_cid(*, output) -> dict
build_rescue_k3(*, output) -> dict
audit_k3_abi(*, sdk_root=None) -> dict
```

`validate_config` 只檢查欄位及正規化位址，不代表外部證據已驗證。
`validate_artifacts` 讀取實際文件、建置配置、觀測與組件，回傳 `config`、`steps`、
`artifacts_verified=true`、`hardware_validated=false`。
`build_uboot_config` 接受完整執行範本，不是舊的 `bpi-lab-original-entry-template-v1` 摘要範本。
`render` 完成同樣守門後只輸出命令及回應核對規則，不傳送命令。

執行 schema 為 `bpi-lab-original-entry-v1`，核定 ABI 為 `mainline-original-entry-v2025.01-sd-cid-v1`。
現行 `backend.render_family(..., runtime_config=...)` 先核對既有 C3 範本及同一份組件 manifest，
再呼叫本模組的 `build_uboot_config`；`bind_runtime` 綁定本次成功 extraction、原映像路徑與傳輸目標。
`lifecycle.boot_driver` 已依 schema 選擇本模組，`cycle` 直接呼叫選定執行器的 `boot`，
救援本身仍使用既有 `uboot.boot`，不把客戶原入口降級為直接 raw 引導。
`lifecycle_view(config)` 公開生命週期相容投影，僅供身分／RAM 核對，不能把投影送到共用引導器執行。
FIT 投影僅將格式轉為共用 validator 可接受的 `Image`，原配置與真正執行仍保留 `FIT`、原路徑及摘要。
生命週期已改呼叫此 API；專用回歸核對 FIT 投影不會取代實際執行配置。
`boot` 使用既有 `uboot._Runner` 與 `ConsoleSession` 的 nonce、截止時間、完整傳送與原始 RX 記錄契約。
呼叫端必須保留 `records` 及 RX 日誌，也須在成功後執行 Linux root／CID／版本核對及短測。

## 最小範圍

- Rockchip ARM64 的固定來源 `rockchip64`、`rk35xx`、`rk3576` 腳本，以已核對的原 `boot.scr` 執行 `source`。
- Forge1 `rk3506` 與 R2 `mt7623` 原 ARM32 zImage／bootz 腳本已接入，保留原 legacy uInitrd、DTB 及環境匯入。
- 九款 C3 extlinux 板的單一 label，以實際 MMC 分割區及設定路徑執行 `sysboot`；APPEND 不改寫。
- FDT 與 FDTDIR 均保留；FDTDIR 明示原板型的 `fdtfile`，不讓韌體猜測其他 DTB。
- extlinux 可用根檔案系統 `/boot/...` 或獨立 `/boot` 掛載。後者執行路徑為 `/Image`、`/extlinux/extlinux.conf` 等，並核對擷取的 `volume_index`。
- ARM64 raw Image、RISC-V raw／原配 gzip Image；legacy 或 raw initrd 依原 extlinux 保留，Rockchip 腳本要求其原配 legacy initrd。
- gzip 保留原檔，由已核定 U-Boot `booti` 解壓。配置須涵蓋十倍壓縮長度的獨立暫存區、回搬區及含 BSS 的核心範圍。
- CM6 單核心 FIT 保留原 extlinux；由 `sysboot` 原有格式分派呼叫 `bootm`，不另寫直接啟動命令。

overlay、fixup、多 label 回退、互動 PROMPT、extlinux KASLRSEED、kernel uImage、其他 FIT 與 TFTP 均不在此最小 ABI。
已知但未支援者在 UART 傳送前阻擋，不刪除原指令或改用手組 bootargs。
Rockchip 原腳本的 `kaslrseed` 呼叫照原樣執行，其有無及韌體修正屬核定建置的一部分。
K3 `vendor-env` 的軟體缺口與前置安全鏈資格分開列於下節，不把離線已解析的 MMC 分支冒充 K1／mainline 資格。

## 配置契約

所有欄位必須明示，未知欄位拒絕。完整可執行合成範例見測試的 `make`／`qualify`；它不是實板核定。

| 欄位 | 內容 |
| --- | --- |
| `schema` | `bpi-lab-original-entry-v1` |
| `arch uboot ram source files bootargs kernel_release fdt_extra` | 與共用 `bpi-lab-uboot-v1` 同形；`source.type` 限 `mmc`，`files` 為實際媒體路徑，不是證據暫存檔名 |
| `board hardware_id root_uuid` | 原板型、核定實板識別、原根檔案系統 UUID |
| `root_source` | `{partition, partuuid, uuid}`，裝置與 `source.device` 相同，分割區可以不同 |
| `mmc` | `{sd, emmc}`，不同的 U-Boot 裝置編號，`emmc == source.device` |
| `components` | `{manifest, artifact_root, extraction}`；兩份文件參照均為 `{path, sha256}`，路徑為本機絕對路徑 |
| `pairing qualification` | 實際文件的 `{path, sha256}`，並與 `uboot` 內重複摘要相同 |
| `authorization` | `{record, one_shot: true, customer_boot_may_write_emmc: true}` |
| `entry` | `{kind, path, bytes, sha256, address, capacity}`；容量至少為內容加 4096 位元組 |
| `work` | `{address, capacity}`；Rockchip 按實際 `script_profile` 綁定固定 `load_addr`，不是所有 profile 共用同一位址 |
| `decompression` | 未壓縮時為 `null`；gzip 時為獨立 `{address, capacity}` |

`bootargs` 是不可改寫的核對值，不用來取代原 APPEND 或腳本；只把原腳本 `${partuuid}`
解析為已核定 boot 分割區 PARTUUID。其他未解析變數仍阻擋。
腳本 `fdt resize 65536` 由 mainline `cmd/fdt.c` 以十六進位解讀，故 `fdt_extra >= 0x65536`，
另保留共用契約要求的頁面對齊空間。

| 腳本 profile | 環境暫存位址與限制 |
| --- | --- |
| `rockchip64`, `rk35xx` | `work.address=0x09000000` |
| `rk3576` | `work.address=0x48000000`；M5Pro／CM5Pro 不可使用舊錯誤位址 |
| `rk3506` | `work.address=0x02000000`、initrd 位址 `0x02800000`、boot 分割區固定 1 |
| `mt7623` | 原腳本先使用 `kernel_addr_r` 匯入環境，再載入 zImage；兩者容量都須核對，`mmcpart` 明示目標分割區 |

Forge1 同時探測明示及隱含分割區的 PARTUUID，對齊
[主線未指定分割區時選第 1 區的行為](https://github.com/u-boot/u-boot/blob/v2025.01/disk/part.c#L553)。
ARM32 配置的 `kernel.entry` 指定經核定的核心解壓目的位址，實際展開長度必須位於 `ram.kernel_work`。
這是最低範圍核對，BSS、zImage 自搬移及解壓器堆疊仍須由綁定配置的 RAM 審閱涵蓋，不能拿檔案長度代替。
Forge1 的 LZ4 由原 zImage 自解壓，不改成外部 U-Boot gzip 解壓；核心 `CMDLINE_EXTEND` 契約由組件重播核對，
`bootargs` 仍保留原腳本輸出，不把核心稍後附加的 `user_debug=31` 重複送入環境。

### CM6 FIT

原 `files.kernel.format=FIT`，`address` 是完整原容器的載入位址，`entry` 必須等於已驗證 FIT 的
`load`／`entry`。目的區大小使用內嵌 RISC-V `image_size`，包含 BSS；必須位於 `ram.kernel_work`，
且與完整容器 capacity 不重疊。容器、外部 initrd、原 DTB、原 extlinux 各自核對長度與 SHA-256。
只接受組件解析器已驗證的單核心、單預設組態、內嵌未壓縮載荷及 CRC32／SHA-256，
不接納簽章、多載荷、內嵌 DTB／ramdisk、外部資料或其他選擇子。
設定易失性 `verify=yes`，要求救援 `CONFIG_FIT=y`、`CONFIG_CMD_BOOTM=y`，
實際 `.config` 的 `CONFIG_SYS_BOOTM_LEN` 必須涵蓋內嵌載荷搬移長度；仍由
[v2025.01 原分派](https://github.com/u-boot/u-boot/blob/v2025.01/boot/pxe_utils.c#L735)
傳入原外部 initrd／DTB，不替換為 `booti`，也不把 CRC 視為認證或安全啟動核定。

## 文件守門

`pairing` 使用既有 `bpi-lab-pairing-v1`。本模組核對核定狀態、實板識別、雙媒體 CID／大小／控制器；
UART、電源資源及救援套件與部署契約的完整關係仍由 backend 的配對守門負責。

`qualification` 嚴格欄位如下：

```text
schema = bpi-lab-original-entry-qualification-v1
abi = mainline-original-entry-v2025.01-sd-cid-v1
approved = true
record, hardware_id, scope_sha256, pairing_sha256
firmware = {binary: {path, sha256}, config: {path, sha256},
            sd_cid_source: {path, sha256}}
observations = {path, sha256}
review = {sd_rescue_origin, command_abi, volatile_environment,
          no_persistent_writes, secure_chain_compatible, memory_layout,
          immutable_media}
```

`review` 各值必須為 `true`；不是只填摘要。`scope_sha256` 由正規化後的完整配置計算，
僅排除 `qualification` 參照與 `uboot.qualification_sha256`，避免自我摘要循環。
建置二進位檔與 `.config` 都實際讀取核對。配置必須啟用所用 MMC、CID、PARTUUID、FSUUID、
SHA-256、檔案載入、FDT、架構與容器對應的 booti／bootz／bootm、legacy 容器與 source／sysboot 命令，
R2 另須 `CONFIG_CMD_EXT4`，gzip 須 `CONFIG_GZIP`。
`line_limit` 不得大於原配 `CONFIG_SYS_CBSIZE`，不能用核定 JSON 自行放大韌體命令緩衝。

`observations` 必須為 `bpi-lab-original-entry-observation-v1`，包含
`hardware_id firmware_sha256 boot_origin reset version_output bdinfo_output sd_cid_output emmc_cid_output`。
要求 `boot_origin=sd`、`reset=cold`；版本、完整 RAM／LMB 格式及四字組 CID 內容均實際解析，
不是把觀測檔案的摘要當作驗證結果。

### 救援 SD CID 命令

主線 v2025.01 的 [mmc reg](https://github.com/u-boot/u-boot/blob/v2025.01/cmd/mmc.c#L1095)
拒絕 SD；`mmc info` 只有部分身分，不能代替完整 CID。
`build_rescue_sd_cid(output=新目錄)` 產生 `bpi_lab_sd_cid.c` 與 `Makefile.fragment`。
救援建置者須將來源放入受控 U-Boot 的 `cmd/` 並納入 Makefile，重新建置及核定；本 API 不改現有韌體。

`bpi_lab_sd_cid <十進位裝置編號>` 只使用 `find_mmc_device`／`mmc_init`、確認 `IS_SD`，
再輸出 `bpi-lab-sd-cid-v1 device=N` 及四個 `CID[i]: 0x........`；沒有媒體寫入或環境儲存命令。
資格文件的 `sd_cid_source` 必須等同實際受控來源；二進位須含命令及 ABI 標記，
`sd_cid_output` 必須含正確裝置與完整 CID。既有 stock 建置、只有舊四字組替身輸出、
來源變更或裝置不同均在本機守門或交接前阻擋。
來源與二進位字串核對不等於可重現建置證明；來源到 binary 的關係仍屬具名 `command_abi` 審閱，
實際使用者必須保留建置與 SD 冷啟動觀測，不能使用測試合成證據。

2026-09-18 已使用本機固定 v2025.01 來源
`6d41f0a39d6423c8e57e92ebbe9f8c0333a63f72`，以 `git archive` 建立私有副本，
將此命令編入 `mt7623n_bpir2_defconfig` 與 `qemu-riscv64_defconfig`，兩者均完整產出 `u-boot.bin`。
[已提交的建置核對摘要](evidence/bpi-multiboard-integrate-20260917/original-entry-build-audit.json)與
[本機完整建置紀錄](../output/evidence/bpi-original-entry-uboot-build-20260918-001/build-audit.json)
包含 ELF／binary／object／config／來源 SHA-256、ELF 架構、命令註冊與函式符號，並完成反組譯檢查。
這不是字串／mock 編譯；但 RISC-V 是 QEMU 編譯目標，不是 CM6 板級韌體，也沒有執行 QEMU 或實板。
原 BSP 與 Git 快取未修改。私有 Makefile 僅加入 `obj-$(CONFIG_CMD_MMC) += bpi_lab_sd_cid.o`；
配置啟用必要 MMC／CID／hash／FSUUID／sysboot，`CONFIG_SYS_CBSIZE=2048`。
可在各私有來源目錄使用 `make O=../r2 CROSS_COMPILE=arm-linux-gnueabihf- -j4 u-boot.bin`
或 `make O=../riscv CROSS_COMPILE=riscv64-linux-gnu- -j4 u-boot.bin` 重建；不得拿這些本機產物直接核發實板資格。

核定文件是受信任審閱者的授權紀錄，不是密碼學身分或測量開機證明。
雜湊、版本字串和 UART 本身不能證明實際執行的韌體；當次 SD 冷啟動來源、媒體不被並行改動、
環境 callback／韌體副作用及前置安全鏈，必須由 lifecycle 與具名核定維持。
不得以本模組自行產生核定，或以測試的合成核定代替實板證據。

`components.extraction` 必須是 `ok=true` 的成功擷取，透過既有 `SnapshotReader` 驗證檔案與缺檔診斷。
原配組件重新解析後，比對實際 entry、APPEND、DTB、版本、config、initrd、讀取需求與原 manifest；
偽造外部摘要中的 `checks` 或 `prepared` 不能放行。重播只使用小型組件與暫存目錄，不解壓整顆磁碟映像。

固定 DTB 保留區與 RAM 交集必須完整列入 `ram.reserved`，未知動態 LMB 不得碰到載入／解壓區。
主線 [檔案載入](https://github.com/u-boot/u-boot/blob/v2025.01/fs/fs.c#L514) 本身會新增可覆寫 LMB 配置。
runner 僅在本輪長度與 SHA-256 成功後記錄其精確區間，後續 `bdinfo` 才接受相同或相鄰合併的
`flags=none/0` 區間。超出實際檔案長度、未載入工作區、`no-overwrite`、重複區間或不完整清單均阻擋；
不把整個 capacity 當成可忽略的保留區，也不全域關閉 LMB 核對。
移除本輪已核對列後重建剩餘 `reserved.count` 與連續索引，再呼叫共用完整 `_memory`；
不是只呼叫 `_memory_gd`。新負例覆蓋索引缺口、剩餘 DTB 碰撞及不完整清單。
明示停用的 DTB 節點不當作有效保留區。動態保留區與 `cma=` 不被改寫或偽造固定位置；
其精確 DTB 屬性、核心 config 與參數已綁定核定範圍，最終 Linux 分配及可用空間仍屬 `memory_layout`
人工／板級核定與後續 Linux 檢查，不宣稱此模組已模擬 CMA 或證明記憶體充足。

## 執行及證據

先核對 U-Boot 版本、完整 `bdinfo`、必要命令與 SHA-256 零長度已知向量。
以專用唯讀命令讀取配對 SD CID，再選定 eMMC user partition，使用主線 `mmc reg read cid` 讀 eMMC CID；不更動開機分割設定。
核對 boot／root PARTUUID 及根 FSUUID，重新檢查原本缺失的 boot 入口與 fixup。
有錯誤文字的缺檔探測不當作「不存在」。

每個必需檔案使用指定 `mmc dev:part` 和原路徑限量載入，核對位元組數、`filesize` 及 RAM SHA-256。
只設定一次性 RAM 環境，清除會污染原入口的繼承值，隨後再檢查 LMB 及全部載入摘要。
最後只呼叫一次原 `source` 或 `sysboot`；sysboot／腳本會重讀原檔，因此穩定媒體審閱是不可略過的前提。
逾時、部分傳送、nonce 回顯、錯誤版本、返回 prompt、CID／UUID／雜湊不符均停止，不重試或改選其他入口。

成功只回傳 `status=kernel-marker-observed`，並維持
`hardware_validated=false`、`root_verified=false`、`smoke_verified=false`。
無實板操作、硬體通過聲明、提交或推送。

## 離線回歸

```sh
PYTHONPATH=tests output/evidence/bpi-sram-supervisor/model-venv/bin/python -m unittest test_bpi_lab_original_entry -v
PYTHONPATH=tests output/evidence/bpi-sram-supervisor/model-venv/bin/python -m unittest test_bpi_lab_backend_runtimes.RuntimeIntegrationTests.test_original_entry_script_extlinux_split_and_compressed_dispatch test_bpi_lab_backend_runtimes.RuntimeIntegrationTests.test_original_entry_extraction_from_other_source_rejected -v
```

測試使用真 `ConsoleSession`／nonce runner 加可切換失敗的 UART 替身；
涵蓋 M7、R3、RISC-V gzip、獨立 boot 分割區、FDTDIR、原始文件與範圍核對、實際媒體／RAM 核對、
失敗停止、分段 RX、部分 TX、截止時間，以及 K3 在傳送前阻擋。

2026-09-17 已重跑 Franklin 的兩項原入口整合測試並通過：
四組正例為 R3 extlinux、M7 腳本、R3 獨立 boot 分割區及 F3 gzip；另驗證其他來源 extraction 不能混入。
整合路徑經 backend 的原配選擇、lifecycle 分派與真正的原入口 runner，
但傳輸、電源及 Linux 身分仍為離線替身，不代表真板完成冷循環或 root 短測。
真 F3／SM10 最新 `prepared` 抽樣見 [SpacemiT 文件](bananapi-lab-spacemit-20260917.md#真來源抽樣)，
其離線組件結果不自動產生 runtime 核定，尤其不解除 K3 vendor ABI 阻擋。

2026-09-18 收斂回歸：原入口 36 項、兩項原入口 backend 測試與共用 U-Boot 合計 83 項通過。
3P1 的 RK3576 固定位址、SD CID 真命令及本輪 LMB 精確追蹤都有正負例。
新的 [M7／R3／F3 真組件模型](../output/evidence/bpi-original-entry-real-model-20260918-003/summary.json)
與 [CM6 真 FIT 模型](../output/evidence/bpi-original-entry-cm6-fit-model-20260918-001/cm6-fit-model.json)
均通過，原 evidence 不覆寫，`source_reread=false`。
CM6 使用 `6.6.36-legacy-spacemit`，不是先前預期的 `6.6.99-current-spacemit`；
其模型直接走本模組與 `lifecycle_view`，另有 backend FIT 投影回歸核對，不代表 CM6 實板核定。
上述真組件模型的 RAM／CID／核定／UART 核心標記仍為合成值，沒有執行 U-Boot 的 source／sysboot 程式或實板。
早期擴大執行曾遇到 W2 救援 schema 的 `source` 缺欄位及 M4 的 `previous_boot_id` 斷言失敗；
兩者已在後續共用後端整合修正，舊失敗紀錄保留，不拿它取代較新的整合結果。

主代理另以 `BPI_LAB_REAL_C3=1` 執行原入口、共用 U-Boot 與兩項原入口整合測試，85 項通過；
紀錄位於 `output/evidence/bpi-multiboard-integrate-20260917/original-entry-main-reviewed.log`。
真實組件模型包含在本次 85 項內，不重複相加。Ruff 通過；建置來源與產物另逐份重算共 22 筆摘要。

## 明確缺口

- SM10 原 SDK 的 `env_k3.txt` 是文字環境，不是可直接 `source` 的 legacy 腳本。`k3.env` 的
  `mmc_boot → boot_kernel → start_kernel` 與 `k3.c` 的 DT 修正入口已有固定來源，不再以「找不到入口」阻擋。
  但原建置 `CONFIG_CMD_HASH=n`、MMC 命令沒有完整 CID、`CONFIG_SYS_CBSIZE=256`，RISC-V `bdinfo` 也沒有目前需要的堆疊觀測。
  `build_rescue_k3()` 已產生唯讀 eMMC CID 與含真堆疊／完整 LMB／SD boot mode 的 SDK 命令來源，
  但尚未完成可連結的 K3 救援建置；持久環境逐鍵驗證、GPT 名稱／ESP 選擇及 vendor runner 仍未實作完成。
  SDK GCC 在主機缺少 `GLIBC_2.38`，系統交叉組譯器則不支援原配置 `zicbom` 等 ISA；
  失敗日誌與摘要列於建置核對紀錄。沒有刪除 ISA 或更換原配前置鏈來假造成功。
  後續 K3 runtime 另建獨立模組；目前入口仍在 UART 傳送前確切阻擋。
  固定 vendor `booti` 可以作為待完成路徑，但必須保留 `boot_mode=sdcard`、`board_fdt_chosen_bootargs`、
  板級／MAC／framebuffer 修正；僅把 mainline 命令改名不足以證明原入口等價。
- SM10 的 FSBL／ESOS／SBI、RSA／FIT、熔絲及反回滾為上述軟體工作完成後仍獨立存在的資格要求。
  `audit_k3_abi()` 逐檔核對 SDK 來源與 config，分開回報 `software_blockers`、`qualification_requirements`。
- CM6 單核心 FIT 的原入口與 RAM 契約已完成；未涵蓋其他 FIT 類型或完成實板核定。
- Forge1／R2 可核對的原腳本均載入 zImage 並呼叫 bootz，未取得這兩板使用 kernel uImage／bootm 的受控原入口。
  現有 legacy uInitrd 已驗證與保留，但不等同已支援 kernel uImage；不得自行包裝核心或更換原腳本。
- 真 R2-004 從同一原 XZ 新擷取，已確認原環境指定的無副檔名 DTB 不存在；這是原映像問題，合成 ARM32 正例不解除 `missing_file` 阻擋，詳見 [MediaTek 證據](bananapi-lab-mediatek-20260917.md#r2-原映像問題)。
