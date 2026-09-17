# 六板原配一次性 UART 執行器

建立於 2026-09-17，補驗於 2026-09-18。執行入口為
`tools/bpi_lab_special_runtime.py`，回歸為 `tests/test_bpi_lab_special_runtime.py`。
原配組件解析沿用已提交的 special 模組；本文件區分執行器、外層後端接線與實板資格。
本次未操作硬體或修改原廠 BSP；建置使用獨立副本，原始映像與舊失敗證據保留。

## 已實作與界線

| 板型 | 真正執行路徑 | 仍需外部核定 |
| --- | --- | --- |
| F2P、F2S | 主線 ABI 有界 MMC 載入、完整 RAM SHA-256、legacy `bootm` | 相容 U-Boot、ROM／xboot 鏈、實板記憶體及配對 |
| AI2N | 主線 `booti`，另載入 OpenCV／Codec，所有載入結束後核對摘要，交接前再核對 | BL2／FIP、韌體版本與專屬保留區、實板資格 |
| M6 | 主線 `booti`，保留原配固定位址及 CMA 限制 | OEM／TZK、主線相容性及實板資格 |
| M4 | 新 lab 命令限長載入四個原配檔案、兩層完整摘要核對，再呼叫原廠 `do_go_all_fw` | 實板配對 lab 二進位、部署身分、ACPU／IPC／PMIC 與根標籤範圍 |
| W2 | 新 lab 命令直接載入核心最終位置、完整摘要核對，再走原廠音訊、rescue mode、`rtk_call_booti` 順序 | 實板配對 lab 二進位、部署身分、ACPU／IPC 與根標籤範圍 |

最後兩板不是原版 `gosd` 或主線 `booti` 的假替代。新增的是需另行建置與實板核定的
`realtek-lab-v1` 來源契約。兩板已在獨立暫存副本完成真 BSP 的 U-Boot 編譯、連結及大小檢查，
另有主機替身及假 UART 測試；未建置或修改 ROM 前置封裝鏈，未部署或寫入實板。
原版暫存載入區的重疊不豁免；新 `direct-final` 模式完全不寫入該區，詳見來源時序核對。
所有示範配對與 RAM 核定仍是合成資料，不能套用至實板。

所有 runtime 成功只代表 `status=kernel-marker-observed`：
`hardware_validated=false`、`root_verified=false`、`smoke_verified=false`、
`firmware_execution_verified=false`。載荷摘要相等不代表 OpenCV、Codec 或 ACPU 已正常執行。
`root_uuid` 保存原配根的預期身分，不宣稱 Linux 已掛載該媒體。

## 公開介面

```python
from tools import bpi_lab_special_runtime as runtime

runtime.validate_config(config)
runtime.validate_artifacts(config, artifact_root)
view = runtime.lifecycle_view(config)
steps = runtime.render(config)
binding = runtime.backend_binding(config)
records = []
result = runtime.boot(console, config, records, timeout=300, monotonic=clock)
```

新增家族核對入口供有 `direct-final` 的 backend 使用：

```python
recipe = runtime.bootconfig(
    artifact_root, template=template,
    kernel_placement=config["execution"].get("kernel_placement", "original"),
)
```

這只增加一個明示模式，不改下述四個 wrapper 簽章。預設仍由原 `special.bootconfig` 核對；
`direct-final` 則先重播 special 原配組件、核對原 uEnv 位址，再獨立核對新的最終載入位置。
不能先要求原版 `special.bootconfig` 接受相同暫存位址，否則真 W2 仍會被舊路徑擋住。

供 Franklin backend／lifecycle 對接的固定呼叫簽章為
`validate_config(config)`、`validate_artifacts(config, artifact_root=None)`、
`lifecycle_view(config)` 及
`boot(console, config, records=None, *, timeout=300, monotonic=time.monotonic)`。
六板外層均使用 `bpi-lab-special-runtime-v1`，只在 `execution.uboot.abi` 區分
`mainline-v2025.01` 與 `realtek-lab-v1`，不另外建立 Realtek 外層 schema。
`lifecycle_view` 保留 `pairing` 與 `uboot.qualification_sha256` 供身分閘門核對，
但只可用於身分核對；實際執行須保留完整 config，分派到本模組 `boot`，
不能把 view 傳給共用 `uboot.boot` 而丟棄額外韌體及 vendor 契約。
實板可見媒體的 LABEL 唯一性由 Franklin 開機前盤點核對，runtime 的
`root_label_scope_approved=true` 只是必需的外部核定，不是實物觀測結果；
映像內完整標籤清單亦不能取代該實板盤點。

`boot` 借用已配對、已鎖定、有原始 RX 日誌的 `ConsoleSession` 或 lifecycle
`BoundedConsole`。不開關串口、不自行停止 autoboot、不操作電源、不登入 Linux、
不重試。外層必須確認實際 UART 設備與 pairing 的穩定路徑相同，並負責記錄失敗時的
`records`。離線驗證也計入一次性期限；預設 300 秒，上限 1800 秒。

CLI 僅離線工作，沒有開啟 UART 的入口：

```sh
python3 tools/bpi_lab_special_runtime.py validate --config /absolute/runtime.json
python3 tools/bpi_lab_special_runtime.py render --config /absolute/runtime.json
python3 tools/bpi_lab_special_runtime.py vendor-source --config /absolute/runtime.json
```

`vendor-source` 等同 `vendor_source(config)`，回傳待附加於原廠 `common/cmd_boot.c`
的 C 來源與實際來源摘要，不改寫任何 BSP 檔案。不能作為任意位址 RAM stub；
它使用原廠同一編譯單元的 static 入口、IPC 狀態與全域資料。

## 配置與證據

根配置固定為 `schema=bpi-lab-special-runtime-v1`、`board`、`artifact_root`、
`template`、`execution`、`pairing`、`qualification`。未知欄位拒絕。
`template` 保留原 special bootconfig 範本；每次重播 components 並重新產生配方，
不接受 `execution_ready` 或摘要驗證旗標作捷徑。

`execution` 欄位：

- `uboot`：`prompt`、完整 `version`、`address_bits`、`line_limit`、`abi`。
  四板採 `mainline-v2025.01`；Realtek 採 `realtek-lab-v1`，行長最多 640。
- `boot_region`：明示 `start`／`size`，不能從 RAM 容量或板名猜測。
- `capacities`：包含全部角色。每個載入都預留超長偵測位元組；DTB 另保留擴充空間。
- `kernel_entry`、`fdt_extra`：核對格式導出的入口與記憶體工作區。
- `transport`：固定 `kind=mmc-original`、`media=emmc`、`extraction` 參照、
  `image_paths`。不支援來源掃描、TFTP 或 SD CID 降級。
- Realtek 另有 `vendor_sources`：以 `vendor_files(board)` 固定集合逐一綁定本機實際來源，
  包含 `VENDOR_FILES` 及板型專屬 OTP／clock／記憶體標頭、唯讀 OTP 位元公式來源。
  `transport.root_preparation` 綁定實際 prepare 文件，不能只填 root identity 的摘要欄位。
- Realtek 可明示 `kernel_placement=direct-final`；不提供時為 `original`。
  原配 template 地址不變，實際核心 load 地址由已解析 `text_offset` 導出；不接受任意替代地址。

所有證據參照為絕對 `path` 與 `sha256`，逐一讀取實際位元組核對，拒絕 symlink、
裝置檔、重複 JSON 鍵、非有限 JSON 常數及超限資料。原配本機組件也重新讀取核對。
每個邏輯檔案必須對應 extraction 已證明的 `resolved` 媒體路徑、分割區索引、
PARTUUID、長度與摘要。映像內 `/boot` 路徑不直接當作 FAT 上的路徑。
LABEL 根識別還要求 extraction 的 `filesystem_labels_complete=true`，表示所有分割區均已解析。
缺少欄位或值為假都拒絕；W2-002 舊結果不冒充新完整性契約。
W2-003 新證據已核對完整標籤清單與 SHA，仍不宣稱實板可見媒體標籤唯一性。

### 實板資格

資格檔 schema 為 `bpi-lab-special-runtime-qualification-v1`。先用
`scope_digest(config)` 取得排除 qualification 自身參照後的完整配置摘要，避免自我摘要循環。
資格須精確綁定板型、`hardware_id`、`scope_sha256`、實際 `source_evidence` 檔案、
`memory_evidence`、所有 `DEPENDENCIES` 的檔案摘要及 `required_commands(board)`。

必須由有權人員核定 `approved`、`hardware_validated`、`kernel_may_write_root`；
有額外韌體時另需 `firmware_loads_approved`。這些是外部授權條件，不是本工具的實物驗證結果。
測試產生的資格均為明示合成資料，不可升格使用。

Realtek 額外要求 `root_label_scope_approved`，以及 `vendor_build` 中的
`source_sha256`、實際 `binary`、`build_config`、`link_map` 參照。
工具核對這些檔案的內容摘要與生成來源相符；不能僅憑這些參照宣稱編譯可重現、
實際已部署同一二進位或硬體受信任。這些關聯須由實板資格原始紀錄證明。
主機替身測試不會產生可供實板使用的建置核定。

## 命令及 RAM 核對

主線必要命令包含 `version`、`bdinfo`、`help`、`echo`、`setenv`、`printenv`、
`hash sha256`、`base`、`md.b`、`mmc dev`、`mmc reg read cid`、`part uuid`、
`load`、`fdt addr/list/rsvmem print/resize` 與對應 `bootm`／`booti`。
`mmc reg read` 在已讀主線來源明確拒絕 SD，所以此版本只接受固定 eMMC。
`mmc dev N 0` 選擇既定裝置的使用者分割區；沒有 `partconf`、燒錄或 boot partition 設定。

主線核對流程：版本與完整 LMB／DRAM 資料，唯讀 CID 四個字與 PARTUUID，
所有檔案以 `bytes+1` 上限載入，逐一核對回報長度與 `filesize`；全部載入完成後才核對
每份完整 RAM SHA-256，並讀取映像標頭及 FDT 保留表。交接前再次核對 LMB、媒體身分，
重算核心、initrd 與額外韌體摘要。LMB 必須精確等於初始核定表加上本輪成功 `load`
的實收範圍，依原廠 `LMB_NONE` 合併相鄰同旗標區間；不是要求載入後表格完全不變。
多一個位元組、漏項、未知區域、變成 `no-overwrite` 或初始保護區變更均拒絕。
此行為依已讀主線 `fs/fs.c:fs_read_lmb_check` 與 `lib/lmb.c`，假 UART 亦實際新增載入項目。
vendor 的舊 `fs_read` 不走這套主線全域 LMB；其假回應不再捏造 LMB 表，改核對實際 gd／heap。
FDT 只宣稱擴充前原檔摘要相等；不宣稱擴充後或核心修補後的 DTB 全內容仍相同。

`observed_ram_hashes` 只來自同次 UART 成功回報，核對起訖位址、完整長度及 SHA-256，
不是直接把 manifest 的預期摘要複製成驗證結論。所有步驟保留 nonce 配對、狀態、
期限與完整 RX，錯誤、回到 prompt、僅命令回音或核心版本不符都不算成功。

### Realtek 來源所支持的路徑

M4 的 `go all` 呼叫 `go a`／`go k`；`go k` 直接使用 DDR，`go kf` 才會從儲存媒體準備核心。
因此本實作能在交接前自行完成四份載荷核對，最後呼叫原廠 `do_go_all_fw`，
保留 ACPU／IPC、PMIC 與 `rtk_call_booti` 行為。

W2 原 `boot_from_sd` 把 `device` 與 `sd_*` 檔名組成 `fatload`，未傳 bytes 上限；
`fs/fs.c:do_load` 沒有從獨立 `filelen` 環境讀取上限，未傳參數時讀到 EOF。
把長度附加到檔名雖可能影響 argv，仍無法強制每次實收長度相等，且原碼對 initrd 失敗會繼續，
又有 128-byte `sprintf` 暫存區。因此本輪不採此技巧，也不把「先驗證 FAT」誤報為最後 RAM 核對。

生成的 `bpilab` 命令提供：

- `memory`：直接輸出 `gd` 的 banks、monitor、stack、TLB、FDT 及真實 heap 範圍。
  原 `bdinfo` 特別隱藏 Realtek relocation／stack，不能假定它有主線回應。
- `probe`：唯讀 secure-boot OTP bit、ACPU clock gate、音訊狀態、eMMC CID、硬體分割區及 PARTUUID，
  與編入來源的配對比較；已啟動音訊、外部 IR／音訊指標、SD 或錯誤媒體均拒絕。
- `load <role>`：固定 eMMC FAT `0:1` 原檔，直接使用原廠 `fs_read`，上限 `bytes+1`，
  嚴格要求實收長度等於預期，並記錄已完整載入的角色。
- `hash <role>`：實算固定 RAM 載荷的完整 SHA-256，回傳實際摘要與位址。
- `boot`：所有角色已限長載入後，再核對媒體、安全／音訊狀態及全部 RAM 摘要，
  只暫時設定必要環境及清除 HYP。M4 呼叫原入口；W2 採原廠音訊、rescue、等待及核心交接序列，
  不再呼叫會重讀的舊 `gosd`。交接最多一次，禁止第二次使用。

Python runner 執行上述命令並解析實際回應，連最後核心標記前的完整摘要也必須齊全。
`vendor_lab_final_ram_roles` 記錄該次最後核對的角色；不代表核心稍後不會修補 DTB。
實體根標籤跨其他可見媒體的唯一性仍須獨立核定，root 登入後驗證不由此工具取代。

### W2 時序與新模式

真 W2-003 的原暫存範圍為 `0x03000000..0x04438000`，
`image_size=21200896`，與 DT 音訊 `0x02600000..0x03200000` 及後續 ION 區域重疊。
已逐段核對 `cmd_boot.c:boot_from_sd`、`do_go_audio_fw`：先載入核心，再啟動 ACPU 並等待，
最後才由 `cmd_bootm.c:booti_setup` 搬移。僅憑這段來源不能證明閉源音訊韌體在等待期間
不使用重疊區，因此原版 `gosd`／`special.bootconfig` 的拒絕保留，不宣稱時序豁免成立。

新模式直接將同一真 raw Image 限長載入 `0x00280000`，保留完整
`0x00280000..0x016b8000` Image 工作範圍及額外超長偵測容量。
全部載荷完成後及 ACPU 交接前均核對實際 RAM SHA；只在當次 RAM 環境設定
`kernel_loadaddr=280000`。原廠 `booti_setup` 的 `images->ep != dst` 條件因此為假，
不再從原有交疊暫存區搬移，保留 ACPU 啟動與核心交接的原順序。
兩板原廠完整 `booti_setup` 函式另以主機 C 測試實際執行：原地址會搬移，最終地址不再搬移，
錯誤 magic 失敗。真 W2-003 載荷加明示合成配對的 fakeconsole 也通過；不是實板啟動資格。

另查明兩板 `rtk_get_secure_boot_type()` 會因未定義 `CONFIG_CMD_KEY_BURNING` 而固定回傳零。
lab 不再用它作為即時證據，改依原廠 `OTP_JUDGE_BIT` 公式唯讀
`OTP_REG_BASE=0x98017000` 的 `OTP_BIT_SECUREBOOT=3494`，安全 bit 為一立即拒絕。
同時要求 `CLOCK_ENABLE2_reg` 的 ACPU bit 4 未啟用；沒有增加 OTP 或 clock 寫入命令。
來源標頭與公式固定摘要，編譯時亦核對 register 常數，測試涵蓋舊 helper 回報零但 OTP bit 為一。

來源快照、原始與最終範圍、重疊清單及建置核對位於
`output/evidence/bpi-special-runtime-review-20260918-001/report.json`。
該報告明示 `audio_internal_lifetime_proven=false`，不是把原版未知時序假裝成已驗證。

以上都不執行 `saveenv`、`env save`、改寫 bootcmd、燒錄、分割、重設或 boot chain 更新。
核心與原配 initrd 啟動後可能寫入持久根媒體，因此須明示根寫入授權，不能稱為全程唯讀啟動。

## Franklin 接點

共享工作樹的 backend 已接受 special runtime config，lifecycle 已按 schema 選擇
`validate_config`／`boot`，使用 `lifecycle_view` 做身分核對。
`lifecycle_view` 不是可單獨執行的降級配方，尤其不可丟棄 AI2N 韌體或 Realtek lab ABI。
`backend_binding` 另回傳 `requires_runtime_dispatch=true` 與韌體角色供外層核對。

本輪測試直接使用 Franklin 的 `boot_driver`、`customer_view`、`render_family` 及
`BoundedConsole`，確認 AI2N 韌體及兩板 Realtek 兩種合成模式仍由本 runtime 執行，
並保留 pairing／qualification 摘要。合成 DT 沒有真 W2 的原暫存區重疊，不能代替下一項接點測試。
backend 已移除 blanket vendor 拒絕並核對 `realtek-lab-v1`；本次新增的 `direct-final`
仍需 Franklin 將家族配方入口改為上述 runtime `bootconfig`，再保留完整配置的資格核對。
未串接前，真 W2 仍會先被 backend 的原 `special.bootconfig` 拒絕；這是明確尚未完成的
外層接點，不是硬體限制。SD 救援不能使用本工具的 eMMC CID 契約，須另有正式 ABI。
外層接線由共用後端階段另行驗收，不能僅以本模組回歸通過宣告完整五階段完成。

## 驗證與來源

```sh
python3 -m unittest tests.test_bpi_lab_special_runtime -q
python3 -m unittest tests.test_bpi_lab_special_runtime tests.test_bpi_lab_special tests.test_bpi_lab_uboot tests.test_bpi_lab_console tests.test_bpi_lab_backend tests.test_bpi_lab_lifecycle -q
ruff check tools/bpi_lab_special_runtime.py tests/test_bpi_lab_special_runtime.py tools/bpi_lab_special.py tests/test_bpi_lab_special.py
```

2026-09-18 回歸：runtime 專項 29 項，連同 special、U-Boot、console、backend、
lifecycle、Amlogic 共 240 項通過；ruff、py_compile 通過。真 BSP 建置另列於下方，
不是將 UART 替身測試當成目標編譯或實板執行。

runtime 包含六板真正 runner 正例、CID／PARTUUID、長度、SHA、來源、資格、容量、
LMB、後續載入破壞先前韌體、最後交接摘要、回音／期限／錯誤核心、CLI 與 Franklin 分派負例。
Realtek C 測試以本機 `cc` 嚴格警告編譯產生的命令，在匿名主機 RAM 執行，
使用 OpenSSL 實算 SHA-256；兩板各兩種載入模式、各 18 種情境驗證長度、內容、媒體、安全狀態、
完整載入、記憶體範圍及單次交接。不接任何真實 MMC、UART 或電源。
此測試需要本機已核對的 Realtek BSP、C 編譯器與 OpenSSL 開發檔；不是完整 BSP 編譯。

### 真 BSP 離線建置

使用本機原廠 ASDK64 4.9.3，來源複製到各自 `/tmp/bpi-lab-bsp-...`，未修改原 BSP。
執行 `make -j2 V=1` 的 `u-boot.bin System.map u-boot.cfg binary_size_check`，
分別使用 `rtd1395_bananapi_defconfig`、`rtd1296_emmc_bananapi_defconfig`。
建置明示 `CONFIG_CHIP_TYPE=0002`，不把此建置選項當成實板 revision 的偵測結果。
不呼叫預設 `all` 的 flash-writer 封裝；Makefile 的 `rtk-pack` 複製也只落在新暫存樹內。
這是完整 U-Boot 目標的交叉編譯／連結，仍使用 BSP 既有 static vendor libraries，
不是所有 ROM、ACPU、簽章及前置鏈都由公開來源重建。

主機新版 libfdt 曾與 BSP 標頭衝突。最後作法只把 BSP 的三個 libfdt 標頭複製至獨立
include 目錄並交給 `HOSTCC`，另設 `-fcommon`；未修改其原始標頭或目標編譯器。
各目錄均保存完整 `build.log`、`build-driver.py`、`realtek-lab.patch`、產生的 C、
`.config`／`u-boot.cfg`、ELF／bin／link map，以及來源樹前後逐檔摘要。
兩板所有建置步驟回傳零，來源樹前後相等；patch dry-run 與目前 generator 摘要亦核對通過。

主代理另重讀兩板全部產物、逐一核對摘要，確認來源前後清單相等及目前 generator
與建置來源一致；[建置核對摘要](evidence/bpi-multiboard-integrate-20260917/realtek-build-audit.json)
保存命令結果、原始 log 參照及二進位摘要。這不產生實板資格。

| 最新證據目錄尾名 | 載荷／模式 | u-boot.bin SHA-256 |
| --- | --- | --- |
| `bpi-special-bsp-m4-20260918-002` | 合成載荷，`original` | `e3a38009bae59c34cd506a30f5755b60c4760570b9819669daf9102fafeccddf` |
| `bpi-special-bsp-w2-20260918-006` | 真 W2-003 載荷，`direct-final` | `fff180e86245c2c7c1a0af8253fe62f767746ea66394c762af66ccd37e0c374f` |

目錄共同位於 `output/evidence/`。兩份二進位的 CID／RAM 核定均為合成測資，
`fixture_only=true`、`deployment_ready=false`、`hardware_validated=false`；禁止部署。
舊建置與失敗 log 保留歷史，尤其 M4-001／W2-005 尚未修正 OTP 即時檢查，不能當最新交付。

來源為原 special 的板型／family／bootscript 契約，以及本機已固定摘要的 M4、W2
`u-boot-rtk/common/cmd_boot.c`／`cmd_bootm.c`，和 `VENDOR_FILES` 列出的實際 API 來源。
CID 主線格式另逐字核對
`output/evidence/bpi-sram-a1-fit-2048-001/source/cmd/mmc.c`。
沒有依第三方摘要猜測 `uImage` 格式，也沒有新增不明主線／原廠 ABI 兼容宣告。

真實 F2P-002、AI2N-001、M6-001、W2-002 components 已重新以修正後 special 重播通過，
最新 W2-003 另核對完整分割區標籤清單、extraction SHA 與相同的 family-result SHA；
映像證據 SHA 與版本見[原配核對文件](bananapi-lab-special-20260917.md)。
真 F2S `allboard-bpi-f2s-002` 已由 special 重播通過；本輪沒有 M4 完整映像的新資格證據，
也沒有任一板的實板 UART 測試結果。

### Noether 審查修正

三項具體反例已修復於 special，沒有修改共用 parser：主線 ARM64 Image 依已讀
`arch/arm/lib/image.c` 的 flags／text_offset／2 MiB 對齊計算實際搬移目的，完整來源及
目的範圍都須落在 RAM 與核心工作區；uImage entry 必須落在 header 指定的實際 payload
範圍且對齊；initramfs 必須有非空、可執行、非連結的 `/init` 一般檔，串接段後覆寫亦重新判定。
尚未適配的 symlink／hardlink init 不冒充可執行入口。

第四項 root UUID 未取得可重現的獨立漏洞，已補原配 bootargs、根 UUID 變更、
manifest 重播、外部根身分及不完整 LABEL 清單負例；不把「未重現」寫成已修復既有漏洞。
上述修正不新增派生 manifest 欄位，既有真 components 可重新驗證而不修改舊 extraction。
