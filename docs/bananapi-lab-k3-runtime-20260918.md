# K3 一次性原入口與固定 SD RAM 救援

## 範圍與狀態

本模組包含 `tools/bpi_lab_k3_runtime.py`、`tests/test_bpi_lab_k3_runtime.py`、本文件，以及 `tools/bpi_lab_k3/` 的 C 命令和私有 SDK 修補。共用 backend 與 lifecycle 已接入獨立 K3 分派；未操作硬體或修改原 SDK。

支援板型為 `bpi-sm10`。原配分支保留 K3 的 `Image`／gzip Image、原 initrd、原 DTB、`env_k3.txt`、GPT `bootfs`／`rootfs` 名稱與 GUID、SD 冷啟動的 `boot_mode=sdcard`，最後呼叫 SDK 原 `do_booti()`。固定 SD 救援使用同一份救援 U-Boot，不轉用主線 ABI。

已完成真 BSP 編譯／連結、產物與 ELF 封裝核對，以及正式 Python runner 的離線測試。這不代表 SD 已冷開機，也不代表安全鏈、Linux 根媒體或短測通過；所有結果維持 `hardware_validated: false`。

## 公開介面

```python
SCHEMA = "bpi-lab-k3-runtime-v1"
ABI = "spacemit-k3-lab-v1"
BOARDS = {"bpi-sm10"}
RESCUE_SCHEMA = "bpi-lab-k3-ram-components-v1"

validate_config(config)
lifecycle_view(config)
validate_artifacts(config, artifact_root=None)
build_uboot_config(template, *, artifact_root)
boot(console, config, records=None, *, timeout=300, monotonic=time.monotonic)
scope_digest(config)
backend_binding(config)
render(config)
prepare_rescue(read_file, *, kernel_release, root_uuid, sd_prefix, output)
build_rescue_uboot(*, output, sdk_root=None, toolchain_root=None)
```

外層欄位沿用原入口配置，另加 `purpose: original | sd-rescue`。`validate_config` 只做結構與 RAM 核對；`validate_artifacts` 重新讀取真組件、原始 extraction、資格內容及建置產物，回傳 `config`、`steps`、`artifacts_verified`、`hardware_validated`。`build_uboot_config` 回傳核對後的 K3 配置，不能交給共用 `uboot.boot()`。

`lifecycle_view` 只投影共用身分與 RAM 欄位，保留 vendor ABI。SDK 核心暫存位址與最終搬移入口相隔很遠，投影的核心位址為最終入口；K3 驅動另行核對原暫存區及解壓區，投影不是執行配置。

固定主程式 MMC 編號是 SD `0`、eMMC `2`，不能沿用 SPL 的 eMMC `1`。RAM bank 必須從 `0x102000000` 起始，保留前方 32 MiB 給 ESOS／SBI；實際大小、堆疊、重定位、控制 DTB、framebuffer 與完整 LMB 仍逐次核對。

## 原入口

`purpose=original` 的 `source` 是已配對 eMMC boot 分割區，`root_source` 是原 root 分割區，`entry.kind=vendor-env`、`entry.path=/env_k3.txt`。`entry` 提供獨立 RAM 位址、容量、長度與 SHA-256；`work` 可為 `null`。`authorization.customer_boot_may_write_emmc` 必須為 `true`，因原配 Linux 交接後可能寫入根媒體。

主要守門如下：

- 從成功 extraction 重新執行 `spacemit.prepare()`，逐項比對檔案、版本、核心 config、initrd、DTB、原環境與命令列，不信任外部 `prepared` 旗標。
- 讀取 SD／eMMC 完整 128 位元 CID、媒體類型及容量；不使用 SDK 不存在的 `mmc reg read cid`，也不把產品名稱當 CID。
- 對 eMMC `0xa0000` 起始的原 16 KiB 環境做實讀、CRC32 與 SHA-256，要求完全符合受控 `env.bin`。修改過的持久環境明確停止，不自動恢復或寫回。
- 列出完整 GPT 名稱、索引、GUID、起點與長度，核對 `bootfs`／`rootfs` 及原 extraction 的 `start_lba`／`sectors`；另讀取 root 檔案系統 UUID。
- 依完整 GPT 選擇原 `get_esp_index` 的 ESP 或 bootfs 分支。bootfs 與獨立 ESP 都檢查 `EFI/BOOT/BOOTRISCV64.EFI`；存在 GRUB 或探測含錯誤時停止，不改走另一入口。
- 核對每個將執行的原環境命令值。`env_k3.txt` 經有界讀取、實收長度與 SHA 後才匯入；再核對匯入的每個鍵，執行固定原 `commonargs`、`add_bootarg`、`set_mmc_args`、`set_root_arg`、`detect_dtb`。
- 保留 `part#`、MAC、Wi-Fi、Bluetooth、序號等板級修正值，交接前再次逐值核對。`board_fdt_chosen_bootargs()` 保持環境優先、原 DTB 補缺，以及真正的 SD boot mode；`ft_board_setup()` 未修改。
- 核心、initrd、DTB 依實際 boot 分割區根路徑載入；每次 `fs_size` 必須等於預期，再以 `bytes+1` 上限讀取，實際 SHA 不符即停止。沒有第二次未核對的 `source`／`load` 回退。

原檔實體路徑為 `/Image`、`/initramfs-generic.img`、`/dtb/spacemit/k3-bananapi-sm10.dtb`。原載入位址分別保留 `0x140000000`、`0x130000000`、`0x138000000`。gzip 使用 SDK 的十倍解壓上限，核心暫存與最終區都涵蓋 `image_size` 及 BSS；不拿磁碟長度代替 RAM 長度。

## SD RAM 契約

`purpose=sd-rescue` 只讀 SD `0`，`entry`／`work` 為 `null`，不讀取 eMMC CID、環境或 GPT。必須明示 `root=/dev/ram0`、`rdinit=/init`、`ro`；不接受持久根或其他 init。

`prepare_rescue` 的 `read_file` 使用邏輯 `/boot` 視圖：

| 角色 | 邏輯路徑 | 實際 SD 路徑 |
| --- | --- | --- |
| 核心 | `/boot/bpi-lab/k3/Image` | `/bpi-lab/k3/Image` |
| initrd | `/boot/bpi-lab/k3/initrd` | `/bpi-lab/k3/initrd` |
| DTB | `/boot/bpi-lab/k3/board.dtb` | `/bpi-lab/k3/board.dtb` |
| config | `/boot/bpi-lab/k3/config` | 只供離線核對 |

`sd_prefix` 參數必須是實際前 4 MiB 位元組，不是摘要字典。產生的 manifest 包含：

```json
{
  "schema": "bpi-lab-k3-ram-components-v1",
  "rescue": {
    "schema": "bpi-lab-rescue-v1",
    "kernel": "核心完整版本",
    "identity_sha256": "實際身分檔的 SHA-256"
  },
  "sd_prefix": {"bytes": 4194304, "sha256": "實際前綴的 SHA-256"},
  "sd_prefix_evidence": "sd-prefix.bin",
  "media_prefix": "/bpi-lab/k3"
}
```

`manifest.rescue` 與 `manifest.sd_prefix` 供共用 lifecycle 直接比對部署契約。載入前及交接前，新 C 命令 `bpi_k3 prefix 0` 都實讀 SD 前 4 MiB，使用 32 KiB 暫存分段計算 SHA-256；不寫入 SD。

initrd 必須包含單一完整 newc 封裝，模組版本唯一且符合核心。實讀 `/etc/bpi-rescue.json`，只接受 `bpi-lab-rescue-v1`，拒絕舊 H618 schema。核對原 `/init`、薄 wrapper 的 AST、`bpi_rescue_runtime.py`、`bpi_rescue_cli.py`、`ssh-start` 與 `udhcpc-script` 的實際內容；`net-events` 是子命令，不要求不存在的獨立檔案。重複路徑、已核對檔案的 hardlink／父目錄替換及額外封裝均停止。

兩種用途須使用相同 binary／config 和配對，但各自有 scope 綁定的獨立 qualification。SD 救援不可借用客戶 root 身分或原入口資格。

## 資格與安全鏈

qualification schema 為 `bpi-lab-k3-qualification-v1`，欄位為：

`schema abi approved record hardware_id purpose scope_sha256 pairing_sha256 firmware observations review security gpt fixups`

`firmware` 參照 `binary elf config source patch environment dtb build` 的實際檔案。除了摘要，還核對固定真建置 binary／config、RISC-V ELF、實際連結符號、ELF 重建 binary、原 DTB FIT 外部資料及 CRC、C 來源、SDK 修補、原環境和 build 日誌。外部報告不能自我宣告另一份可執行碼。

`observations` 必須綁定同硬體、同 binary 的 SD 冷啟動，包含完整版本、K3 RAM／LMB／LCS、SD CID，原入口另含 eMMC CID。`gpt` 是原入口完整分割表；SD 救援為 `null`。`fixups` 明示 `part# wifi_addr bt_addr ethaddr eth1addr eth2addr eth3addr serial#` 的值或不存在狀態。

`security` 文件使用 `bpi-lab-k3-security-v1`，必須核定同硬體／binary、完整八位十六進位 `lcs`、`external_kernel_authorized=true`，並附 `fsbl esos sbi lifecycle` 的非空原始證據參照。LCS 直接讀取 SDK bank 6 的 shadow 字組，檢查 reload 結果；不讀密鑰，不呼叫 OTP program。**零值不代表 secure boot 已停用**，沒有安全鏈資格就不送出命令。

`REVIEWS` 的每個項目均須明確核定，包含冷啟動來源、ABI、RAM、穩定媒體、唯讀早期初始化與 FSBL／ESOS／SBI。核定 JSON 與模型測試都不是實板證明；真觀測與授權仍須由獨立流程提供。

## 真 BSP 建置

原 SDK 提交：`1b10c8119e1a9b5451a4236f6b384f7c91eed1e2`，路徑為 `/media/pi/SMCI/bpi/bpi-sm10/sdk/k3-buildroot-sdk-1.0/bsp-src/uboot-2022.10`。

GLIBC 2.38 需求及 `zicbom` 不支援已解決：使用既有官方 `harbor.spacemit.com/bianbu/k3-bsp-builder`，固定 image ID `sha256:9531810450c5953c0675515ab7deee5d9634966e8abccfcd5ea60f8aee94e335`，容器內 GLIBC 2.39，搭配原 SDK GCC 15.2。工具鏈 wrapper、GCC、cc1、as、ld、objcopy 均固定 SHA；未升級宿主 glibc，無需下載。

建置以 `git archive` 建立全新私有副本，`k3_defconfig`、`olddefconfig` 後實際連結 `u-boot.bin`。容器無網路、無 capabilities、唯讀 rootfs、SDK 工具鏈唯讀；只有新證據目錄可寫。`SOURCE_DATE_EPOCH=1777390324`。

`readonly-sdk.patch` 只影響私有副本：停用早期 PMIC 持久旗標清除、自動 fastboot／SD 燒錄／USB 改選、未受控環境匯入與新 MAC／序號寫入，保留現有 TLV 讀取。停用 DDR 訓練寫回與持久環境後端，啟用 hash、SHA、efuse 唯讀狀態所需驅動，命令緩衝改為 2048、自動啟動停用。兩個前置函式宣告補足 GCC 15 檢查；不修改原 `booti`、chosen 或板級 DT 修正函式。

成功證據目錄：`output/evidence/bpi-k3-runtime-build-20260918-002`。

- `build-manifest.json`、`firmware.json`：完整來源、產物與日誌參照。
- `build/u-boot`：真 RISC-V ELF，`11,327,432` 位元組。
- `build/u-boot.bin`：`1,398,176` 位元組；SHA-256 `5122ab8e6150f3ecc0a67092c139aedaa08c608061bd7bdf7eea5662e415f65f`。
- `uboot.config`：SHA-256 `9f9f74698df1bd48a914c491bd5a18a0979d33287c472542c6306c69c3e010e9`。
- `configure.log`、`olddefconfig.log`、`build.log`：真編譯與連結流程；沒有遺留必要建置程序。

私有救援建置不是原 U-Boot 的逐位元組副本，也不是可直接燒錄的完整 SD 安全鏈套件；FSBL／ESOS／SBI 包裝、簽署與實板冷啟動資格不由此工具代辦。

## 測試與整合

```sh
output/evidence/bpi-sram-supervisor/model-venv/bin/python -m unittest discover -s tests -p test_bpi_lab_k3_runtime.py
ruff check tools/bpi_lab_k3_runtime.py tests/test_bpi_lab_k3_runtime.py
```

目前 27 項 K3 測試通過，含真 BSP 的 ELF／binary 驗證、原入口與 SD 救援正式 runner、偽造 binary、LMB count／索引、額外 RAM 衝突、完整 CID、GPT、環境／板級修正、SHA、長度、SD 前綴、舊救援 schema、wrapper／模組變動及核心返回 prompt 等負例。另直接呼叫主 builder 的 `prepare_runtime()`，把包含繁中文件字串的真正 wrapper、runtime 模組與身分檔放入 CPIO 後核對，不只測自行造的 wrapper。沒有硬體操作。

真 SM10 重播使用 `output/evidence/bpi-multiboard-integrate-20260917-sm10-002-replay/extraction/extraction.json`，SHA-256 `90c9b1ac05c83a988849190b1d8fbeff3f0bde47ede9b902c8b6797dab0bcb59`，核心 `6.18.3-current-spacemit-k3-bpi`。直接重用原成功擷取，重新核對 gzip、BSS、config、initrd、DTB、原環境及版本；UART、配對與安全觀測仍是明示模型，原證據未改寫。

新重播記錄另存於 `output/evidence/bpi-k3-runtime-sm10-model-20260918-001/result.json` 及 `records.json`，保留 `model: true`、`hardware_validated: false`，不覆寫既有 sample audit。

供共用後端整合使用的測試介面位於 `test_bpi_lab_k3_runtime`：

```python
case = make_fixture(root, purpose="original")
rescue = make_fixture(other_root, purpose="sd-rescue")
channel = K3Channel(case.c, case.media, case.rows, clock)
```

`make_fixture` 讀取上述真建置，呼叫正式 `validate_artifacts`，不替換 K3 API 或 firmware 守門；可透過 `BPI_K3_FIRMWARE` 指定相同核定產物的 `firmware.json`。`case.c` 是配置，`case.m` 是 manifest，`case.qualify(config)` 重建明示模型觀測與 scope。整合時將兩者 `pairing` 指向同份文件後重新 qualify，並由共用後端核對同 binary、獨立 SD 資格、`rescue` 與 4 MiB `sd_prefix`。不得把這些模型資格用於實板。

## 明確限制

主代理另核對八份產物參照、三份日誌與十份原 SDK 來源，共 21 筆摘要及大小一致；
C 命令與私有建置來源逐位元相同，ELF 確認為 RISC-V 64 位元。
26 項模組測試及八項共用後端整合均通過；五階段正例使用正式 K3 runner，
只替換 UART、電源、SSH 與媒體傳輸邊界，沒有以主線 runner 降級。
紀錄為 `output/evidence/bpi-multiboard-integrate-20260917/k3-runtime-main-reviewed.log`
與 `k3-integration-main-002.log`。

目前軟體範圍是固定 SDK／固定救援 binary、單一原配 MMC raw Image 分支及固定 SD RAM 救援。其他板型、RSA 建置、GRUB、FIT 核心、變更後的持久環境、overlay 或另一套命令 ABI 都明確停止；不是未實作後悄悄改選入口。

仍需取得真正的 SD 冷啟動觀測、完整 CID／RAM／GPT 名稱、LCS 外部核心授權，以及 FSBL／ESOS／SBI 鏈資格。軟體已提供相應核對與阻擋，沒有把缺少 UART 命令或無法連結等軟體問題混稱為「只待硬體」。
