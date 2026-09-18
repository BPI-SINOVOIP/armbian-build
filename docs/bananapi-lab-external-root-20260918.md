# 外部根同次採樣與固定 SD 交接保護

本文件對應 D3。無 eMMC 板以固定 SD 保留救援能力，USB／NVMe 僅作核定的客戶測試根。所有收集、封裝、驗證結果均為 `hardware_validated=false`；離線通過不等於實板核定。

## 寫入邊界

- 本工作僅新增 `tools/bpi_lab_external_linux.py`、`tools/bpi_lab_external_guard.py`、各自專用測試及本文件。
- 重用 `bpi_lab_media.inspect(require_idle=False)`、`inspect_sd`、`inventory`、`check_fd`，以及 `bpi_lab_deploy` 的固定 SSH、參照摘要與輸出工具；不修改媒體部署器、後端、佇列或原始 XZ。
- 收集器只開啟唯讀媒體描述符。`protect` 僅在明示的 initramfs 流程設定 SD 核心唯讀狀態，不寫入媒體資料，也不在失敗後恢復可寫。
- 部署寫入及 `external.validate_contract` 仍由 D2 負責；授權、生命週期、隔離與發布由 D4 負責。

## 固定公開介面

```python
external_linux.validate_expected(expected) -> dict
external_linux.program(expected, nonce) -> str
external_linux.validate_observation(observation, expected, nonce, *,
                                   uart_observation=None) -> dict
external_linux.collect(expected, ssh, output, *, nonce, uart_observation,
                       timeout=21600, transport=None) -> dict
external_linux.expected_from_contract(contract, *, architecture,
                                      kernel_release, dt_compatible,
                                      root_label=None, root_fs_type="ext4",
                                      root_growth=None) -> dict

external_guard.build(original_initrd, expected, runtime_bundle, output, *,
                     emulator=None, timeout=120) -> dict
external_guard.validate_manifest(manifest, expected, *,
                                 original_initrd=None) -> dict
```

`program` 回傳可透過 UART 或 SSH 傳送的固定 Python 原始碼，不接受客戶 shell 指令。`collect` 使用 `/usr/bin/python3 -I -B -`，固定 SSH 設定及同次 UART Ed25519 公鑰；傳送長度、輸出上限、退出碼與總期限均需核對。密碼、私鑰內容及遠端錯誤輸出不納入回報。

`original_initrd`、`runtime_bundle`、`emulator` 為 `{path, sha256}` 參照；`runtime_bundle` 指向下節的 JSON 清單。`output` 必須是尚不存在的目錄。`expected_from_contract` 先驗證完整 D2 契約，再擷取 D3 所需欄位，不代替後端對授權與來源的綁定。

## 預期與原始證據

預期格式為 `bpi-lab-external-linux-expected-v1`，固定包含：

| 欄位 | 語意 |
| --- | --- |
| `architecture` | `arm`、`arm64` 或 `riscv64` |
| `kernel_release` | 原配核心精確版本，不推測相容版本 |
| `dt_compatible` | 精確、有序且不重複的 DT 清單 |
| `root` | 原樣 D2 `{uuid, partition_index, start_lba, sectors}` |
| `root_label`、`root_fs_type` | 可選固定 LABEL 與直接 ext2／ext3／ext4 根 |
| `target` | 原樣 D2 `kind/identity/bytes/logical_block_size/physical_block_size/topology` |
| `protected_sd` | 原樣 D2 `cid/bytes/controller/full_sha256` |

首版僅接受 512 位元組邏輯磁區、MBR 主分割與直接 ext 根；不把 USB 橋接器 serial、裝置名稱或容量當成磁碟唯一身分，也不為 USB／NVMe 製造 MMC CID。

未提供 `root_growth` 時根分割大小仍須完全相同。明示提供時，格式為
`{policy: "last-primary-to-media-end", partitions: [{index, start_lba, sectors}, ...]}`，
清單依索引排列，必須包含原始全部 MBR 主分割。根必須是最後一個分割，只能向後增大到
核定媒體末端；UUID、起點、索引及檔案系統不可變，其他分割不可新增、刪除或移動。
後端同時核對原擷取配置及 D2 實際串流解析結果，不能只填一份任意預期清單。

guard 到客戶階段之間可接受此有限增長，收據保留原始及實際大小；同一輪 UART／SSH
仍要求完整一致。採樣中擴根必須拒絕並另次採樣，不把變動隱藏成成功。

`blkid` 必須成功辨識檔案系統或分割表；返回碼 2、空結果或 I/O 診斷均不當成「空碟」。
遇無法辨識的額外媒體會保守停止，不能據此宣稱已排除重複 UUID／LABEL。

原始觀測格式為 `bpi-lab-external-linux-observation-v1`，包含 `nonce`、`boot_id`、`kernel`、`architecture`、`machine`、`dt_compatible`、`host_key`、`sampling_started_ns`、`sampling_finished_ns`。另保留：

- `root/root_after`：實際 `stat(/)` 的裝置號與 inode、對應 mountinfo、根分割與 sysfs 父媒體。guard 前置觀測固定為 `/root`，不能冒充客戶的 `/`。
- `target/target_after`、`protected_sd/protected_sd_after`：完整契約與原始媒體欄位、分割範圍、diskseq、裝置號、sysfs 路徑及使用關係；缺欄位即拒絕。
- `inventory/inventory_after`：所有可見非零容量區塊媒體，最多 256 個，不限 MMC。每個描述符核對容量、UUID、LABEL、檔案系統、`BLKROGET` 與 sysfs 唯讀值。
- `mounts`、`swaps/swaps_after`：原始掛載及 swap 盤點；SD 不得掛載、作 swap 或有 holders／slaves。
- `sd_readback`：從核對過的同一 SD 描述符逐塊讀完整容量後計算的摘要，不能複製契約摘要冒充讀回。
- `guard`：`/run/bpi-external-guard.json` 的原始交接收據，格式 `bpi-lab-external-guard-result-v1`。

UUID／LABEL 必須在全媒體盤點中唯一。guard 收據需綁同一 `boot_id`、預期摘要、固定 guard 來源摘要、交接前時間與每個 SD 描述符的唯讀設定前後數值。UART 與 SSH 要使用同 nonce，核對相同完整身分與同一 guard 收據，且 SSH 採樣不得早於 UART 完成。

blkid 必須成功且沒有錯誤輸出，並明確辨識 `TYPE` 或整碟分割表 `PTTYPE`；後者保留於既有 `fs_type` 欄位，例如 `dos`。rc2、空輸出、僅有 DEVNAME 或 I/O 錯誤一律視為未完成盤點並拒絕，不能用缺少 UUID／LABEL 冒充已證明空白。此規則也會拒絕尚未辨識的額外空白媒體，不以猜測換取通過。

驗證結果格式為 `bpi-lab-external-linux-validation-v1`；收集結果格式為 `bpi-lab-external-linux-collection-v1`，包含 `observation`、`validation`、`expected_sha256`、`collector_sha256`、`uart_observation_sha256`、`ssh_config`、`known_hosts`，寫入 `collection.json`。純解析拒絕重複 JSON 欄位、非有限數值與超界輸出。

## 同架構救援 Python 封裝

既有救援 runtime 必須先提供固定摘要的 newc 封裝與以下 JSON 清單。路徑為 initramfs 內部路徑；雜湊由實際檔案計算，不能套用範例或猜測。

`tools/bpi_lab_external_bundle.py` 已提供擷取入口，不必手工收集相依檔案。
輸入為可信救援 initramfs 與客戶原 initrd 的固定參照；可使用先前原生建置的救援，
不需要操作正在測試的 SD 或重建客戶映像。

```sh
python3 -B tools/bpi_lab_external_bundle.py \
  --original-initrd "$INITRD" --original-initrd-sha256 "$INITRD_SHA256" \
  --rescue-archive "$RESCUE" --rescue-archive-sha256 "$RESCUE_SHA256" \
  --architecture arm64 --python /usr/bin/python3 --stdlib /usr/lib/python3.11 \
  --library-dir /usr/lib/aarch64-linux-gnu --library-dir /usr/lib \
  --emulator "$QEMU" --emulator-sha256 "$QEMU_SHA256" \
  --emulator-guest-base 0x100000000 --output "$BUNDLE_OUTPUT"
```

上例為已驗證的 AArch64 組合，其他架構須指定對應來源與 ABI，不能照抄函式庫目錄。
輸出 `runtime.cpio`、`runtime-bundle.json` 與 `bundle-build.json`；指定模擬器且探測通過後
才另外產生 `runtime-probe.json`。來源救援的 init、金鑰、帳號、網路設定、核心模組不帶入。
Python 同名內容衝突即拒絕；已有函式庫保留原配版本，相依摘要另記來源，
最終仍須實際執行 Python、blkid 與原 shell，不能以檔案存在代替可執行證據。

```json
{
  "schema": "bpi-lab-external-python-bundle-v1",
  "architecture": "arm64",
  "archive": {"path": "/absolute/runtime.cpio", "sha256": "<sha256>"},
  "python": "/usr/bin/python3",
  "blkid": "/usr/sbin/blkid",
  "library_dirs": ["/usr/lib/aarch64-linux-gnu", "/lib/aarch64-linux-gnu"],
  "stdlib_dirs": ["/usr/lib/python3.11"],
  "init_profile": {"init_sha256": "<sha256>", "functions_sha256": "<sha256>"}
}
```

`build` 以相同輸入產生可重現的 `initrd.guard` 與 `guard-manifest.json`。實作解析 newc／crc 或單一 gzip newc，保留原項目內容與權限，以及一般檔案的完整硬連結群組；拒絕串接封裝、不完整項目、路徑跳轉、循環連結、相依缺失、來源碰撞與未知壓縮／uInitrd 容器。呼叫端若有 uInitrd，必須先提供已驗證的內部原始 initrd 參照，不能把容器當 newc 猜讀。

硬連結按來源裝置號與 inode 分組，必須有完整且一致的連結數、中繼資料及最多一份非空資料。
重新封裝維持相同成員關係，只在最後一個成員寫入本體；不將硬連結降格成獨立副本。
刪除成員、修改單一別名內容、非一般檔案硬連結均拒絕。
這比核心可接受的格式更窄，尤其不接受多次覆寫同一群組本體的封裝。

ELF 相依閉包使用 `pyelftools`，核對目標 ELF 類別、機器、載入段、解譯器與所有 `DT_NEEDED`；不接受含糊的 RPATH／RUNPATH 搜尋。原配 `/bin/sh`、目標 Python 3.9 以上及固定 `/usr/sbin/blkid` 都必須實際存在且可執行。Python 探測在目標標準函式庫內匯入固定 guard／collector／media 模組，再執行目標 blkid 與 shell 的固定探測；任一缺失均不發布清單。

`library_dirs` 同時決定探測與實際 hook 的 `LD_LIBRARY_PATH`。固定 `ORDER` 在啟動 hook 前就指派該環境，涵蓋 hook 的 `/bin/sh`、Python 及其 blkid 子程序；探測只將同一組路徑加上暫存根前綴，不另加探測專用目錄。初始 init shell 另外以未設定 `LD_LIBRARY_PATH` 的環境探測，不能依賴尚未到達的 guard 設定。路徑只接受固定安全字元，不能插入 shell 指令。

主機架構不同時，必須明示同架構的 `qemu-arm-static`、`qemu-aarch64-static` 或 `qemu-riscv64-static` 及其摘要；不以主機 x86 Python 取代目標 runtime。探測只執行固定程式，不執行原 init 或客戶命令；QEMU 並非安全沙箱，輸入 runtime 與模擬器仍必須是可信、已固定摘要的救援產物。

模擬器參照可額外明示整數 `guest_base`，CLI 對應 `--emulator-guest-base`。
僅接受 64 位元主機上的有界、64 KiB 對齊位址偏移，不接受任意 QEMU 參數。
此欄位連同模擬器摘要保存在實際探測收據；不改變板級載入位址、DDR 參數或原始程式。
本機 QEMU 6.2 的原 klibc shell 在預設映射崩潰，指定 `0x100000000` 後四項探測通過；
這是本機實證，不推定所有 QEMU 版本有同一原因。
[QEMU 官方說明](https://www.qemu.org/docs/master/user/main.html)將 `-B` 定義為訪客位址偏移，
可用於訪客所需區域與主機保留區衝突的情況。

可重現封裝入口：

```sh
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m tools.bpi_lab_external_guard \
  --original-initrd "$INITRD" --original-initrd-sha256 "$INITRD_SHA256" \
  --expected "$EXPECTED" --expected-sha256 "$EXPECTED_SHA256" \
  --runtime-bundle "$BUNDLE" --runtime-bundle-sha256 "$BUNDLE_SHA256" \
  --emulator "$QEMU" --emulator-sha256 "$QEMU_SHA256" --output "$OUTPUT"
```

## 實際交接順序

已核對的 initramfs-tools `run_scripts` 會載入 `ORDER`，並非自動執行目錄內所有新增檔案。因此衍生層將固定 hook 加入 `/scripts/init-bottom/ORDER` 首項，核對它位於 `mountroot` 後、`exec run-init` 前；只新增腳本而未接入排程不能通過。

來源白名單固定為已核對的 Bookworm、Jammy、Noble、Trixie、Resolute
`init`、`scripts/functions` 精確摘要，存於 `INIT_PROFILES`；不是依發行版名稱放行。
自行改寫流程再重綁摘要、註解中的呼叫或未知版本一律拒絕。
五個 BPI-M1 原始最小映像的抽樣紀錄見
[來源摘要與封裝核對](evidence/bpi-multiboard-integrate-20260917/external-init-profiles.json)。
核對範圍為實際 `mountroot`、載入 `ORDER`、移動 `/run`、`exec run-init` 的交接順序；
沒有執行原 init，也不把五份抽樣當成 444 份映像全部相容。

固定 hook 只在 `rootmnt=/root` 時執行 `protect`：核對真實根與全媒體身分，解析客戶 fstab，拒絕 SD 的 `/boot`、一般掛載及 swap，拒絕首版未支援的裝置解析及加密設定，再逐一設定 SD 整碟與所有分割的 `BLKROSET` 並以 `BLKROGET` 證實。交接前重驗根、媒體、整碟摘要、fstab 與唯讀狀態；收據寫入後 flush／fsync。任何失敗均停止交接，不忽略 Python 退出碼；即使 hook 的 shell 尚未啟動就失敗，父層 `ORDER` 也會停止交接。

清單格式為 `bpi-lab-external-guard-v1`，明示 `derived=true`、`original_boot_chain_verified=false`，綁定原 initrd、runtime bundle／archive、衍生 initrd 位元組數與摘要、hook、完整相依、執行探測及固定來源摘要。`validate_manifest` 重新組合預期封裝並逐位元組核對，不能重綁 `run.py`、移除相依清單或借用另一份原 initrd。

保護範圍是交接客戶服務前的 Linux 區塊唯讀狀態，不是實體防寫或對抗任意 root 程式的安全邊界。原 initramfs 在 `init-bottom` 前已執行的載入或掛載不在本 hook 的時間範圍；遇 SD 已掛載時拒絕交接，不能倒推證明此前沒有寫入。未知前置 boot 鏈、DDR 與板級啟動能力仍保持未知。

## 本機驗證與尚未驗證

```sh
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest discover -s tests -p 'test_bpi_lab_external_*.py'
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest discover -s tests -p 'test_bpi_lab_media.py'
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest discover -s tests -p 'test_bpi_lab_external.py'
/home/pi/.local/bin/ruff check tools/bpi_lab_external_linux.py tools/bpi_lab_external_guard.py tests/test_bpi_lab_external_linux.py tests/test_bpi_lab_external_guard.py
```

D3 使用假 sysfs、記憶體 I/O、小型 cpio 與被替換的程序邊界，測試禁止真實子程序接觸媒體。只有固定 `/bin/sh` 的失敗分支測試會執行本機 shell，將停止迴圈換成有界退出碼，驗證不存在的 hook 不會繼續交接；不執行 init 或客戶程式。涵蓋根／父媒體錯配、重複 UUID／LABEL、假 CID、缺少原始欄位、SD 分割漏設唯讀、摘要不符、同次 UART／SSH 錯配、交接收據缺漏、fstab 指向 SD、熱插拔、假值替身、錯誤 ELF 架構、來源／衍生產物重綁、未知 init 流程及目標 runtime 執行失敗。

Ruff 入口已實際核對：指定 venv 的 `python -m ruff --version` 與 `/home/pi/.local/bin/ruff --version` 都回報 `0.11.11`；前者實際載入 `/home/pi/.local/lib/python3.10/site-packages/ruff`，並非 venv 內獨立安裝。因此文件採用主流程的明確 Ruff 路徑。

早期分工驗證共 106 項通過；後續新增擴根、SSH 首次設定、發布中斷及硬連結回歸。
最終主工作樹數量與退出碼以[交付紀錄](bananapi-multiboard-lab-handoff-20260918.md)為準。

已從本機既有 AArch64 救援與 M4 Zero Bookworm 原配 initrd 產生真實 bundle，
並完成原 shell、目標 Python 固定模組匯入、blkid 及注入函式庫路徑後 shell 的四項 QEMU 探測。
紀錄位於 `output/evidence/bpi-external-runtime-arm64-20260918-003/`；
其中 `bundle-build.json` 的 `runtime_executed=false` 只代表封裝步驟，
真正執行結果另在 `runtime-probe.json`，不得混淆。

續作 E 已完成 ARM32 與 RISC-V 的真實目標 runtime 探測，使用本機固定快取與原配 initrd，
四項探測均通過；來源、命令與受限擷取規則見[跨架構實證](bananapi-lab-runtime-cache-20260918.md)。
每個實站仍須使用自己的可信救援來源、對應核心驅動及固定摘要，不能借用 AArch64 證據。
本輪未上板、未修改原始 XZ、未更動固定 SD／外部實體媒體、未執行硬體資格循環。

## 來源

- newc／crc、壓縮與串接格式依 [Linux initramfs 格式文件](https://www.kernel.org/doc/html/next/driver-api/early-userspace/buffer-format.html)；本實作刻意只接受較窄的單一封裝。
- 執行順序依 [Debian initramfs-tools init 原始碼](https://sources.debian.org/data/main/i/initramfs-tools/0.142%2Bdeb12u3/init) 與 [scripts/functions 原始碼](https://sources.debian.org/data/main/i/initramfs-tools/0.142%2Bdeb12u3/scripts/functions)。Ubuntu 白名單另由本機 `/usr/share/initramfs-tools/init` 及 `scripts/functions` 核對。
- 核心唯讀 ioctl 語意依 [Linux v6.18 block/ioctl.c](https://github.com/torvalds/linux/blob/v6.18/block/ioctl.c)，實際裝置身分與磁區核對沿用 D2 媒體工具。
