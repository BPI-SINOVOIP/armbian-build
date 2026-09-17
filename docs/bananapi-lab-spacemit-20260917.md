# C3 SpacemiT 原配組件

API：`tools.bpi_lab_spacemit.prepare(read_file, *, board='bpi-f3', kernel_release, output)`，
以及 `validate_template(manifest, *, template, artifact_root)`。
共用原配核對與範本欄位見 [介面文件](bananapi-lab-extlinux-20260917.md)。

## K1

`bpi-f3` 與 `bpi-cm6` 採各自 U-Boot 修補提供的 extlinux 入口；來源分別留證，不混用板型。
解析 RISC-V Image 標頭、版本、原配 config、RISC-V legacy initrd 及 DTB 根節點。
F3 的既有 compatible 只有 SoC，因此還必須比對 `model`，不能靠單一 compatible 判定板型。
APPEND、FDTDIR 與 label 條件與其他 extlinux 板相同。

F3 與 SM10 真來源的 `Image` 實際為 gzip。僅針對 RISC-V 解碼唯一完整 gzip 串流，
原檔與摘要不變；解碼後再核對標頭、版本及 config。ARM64 不因此接受 gzip。
Linux [標頭定義](https://docs.kernel.org/arch/riscv/boot-image-header.html) 明訂標頭位於解壓後映像；
[head.S](https://github.com/torvalds/linux/blob/v6.18/arch/riscv/kernel/head.S) 使用 `_end - _start`，
包含執行期 BSS，因此另記錄 `image_size` 與解壓檔案大小，不要求兩者相等。
`runtime_requirements.kernel_decompression` 保留壓縮長度、解壓長度、記憶體大小與待核定位址；
SDK `cmd/booti.c` 需要 `kernel_comp_addr_r`／`kernel_comp_size`，還會回搬至原載入位址。
離線解析通過不等於這些記憶體區域或實板解壓 ABI 已核定。

## K3

`bpi-sm10` 不是 K1。解析本倉 `env.bin` 的 CRC32、NUL 分隔鍵、`0xff` 填補及後寫覆蓋語意；
實際非空 `bootcmd` 來自後寫的原廠環境，不能因 `CONFIG_BOOTCOMMAND=""` 就宣告入口未知。
原配 `/boot/env_k3.txt` 必須等同受控來源，`initramfs-generic.img` 必須等同家族複製的 `uInitrd`。

來源核對根目錄為 `SDK_UBOOT`：
`/media/pi/SMCI/bpi/bpi-sm10/sdk/k3-buildroot-sdk-1.0/bsp-src/uboot-2022.10`。
唯讀審閱的提交為 `1b10c8119e1a9b5451a4236f6b384f7c91eed1e2`，
`board/spacemit/k3/k3.c` 與 `k3.env` 另以程式中的固定 SHA-256 核對；不建置或修改 SDK。
來源缺失／變動時明確阻擋，移機時必須提供同份受控來源，不憑可疑原廠字串放行。

已實作 MMC、非 GRUB、非 FIT Image 分支的離線解析，包含原配 gzip 核心：
`import_env_from_bootfs` → `autoboot` → `mmc_boot` → `boot_kernel` → `start_kernel` → `booti`。
保留 `commonargs`、console、loglevel、`rootwait`、`rootfstype`、kernel／DTB／ramdisk 載入位址及大小語意。
`board_fdt_chosen_bootargs` 會加入 `boot_mode`，並以環境中既有參數鍵優先合併 DTB `/chosen/bootargs`；
`ft_board_setup` 的 framebuffer 保留、板號及 MAC 修改保持原廠韌體責任，不假造其結果。

原命令列使用 `root=PARTUUID=${rootfs_guid}` 與 `bootfs=PARTUUID=${bootfs_guid}`，不能改成 UUID。
`root_uuid` 由原映像 `/etc/fstab` 的唯一 ext4 根掛載取得，並明示來源。
範本必須保留 `bootfs`／`rootfs` GPT 名稱、GUID、檔案系統 UUID 對應與執行期裝置條件；
回呼本身不能證明這些分割區條件，必須由磁碟與實板配對層另行核定。

已探測的 bootfs 內若有 `EFI/BOOT/BOOTRISCV64.EFI` 則阻擋，不能繞過 GRUB 改走 raw Image。
`_load_env_from_blk` 以 `CONFIG_SYS_CONFIG_NAME=k3` 載入 `env_k3.txt`，
受控 `autoboot`／`mmc_boot` 不讀 `armbianEnv.txt`；該檔僅記為 `inactive_files`，不匯入或執行。
這只對已核對的 SDK 與預設環境分支成立，不推廣至其他持久環境或自訂 `bootcmd`。
獨立 ESP 仍須由多分割區讀取層核對；NAND、NOR、UFS、NFS、USB、GRUB 與 FIT 核心分支尚未實作。
`CONFIG_RSA_VERIFY` 會跳過外部環境匯入；只支持已核對的非 RSA 建置契約，不證明實板熔絲或安全狀態。
FSBL、ESOS、OpenSBI、持久環境、FIT 驗章、反回滾及授權均是明示前置條件，不修改、不略過。

## 真來源抽樣

2026-09-17 主代理的最新重播已通過，不再以首次組件解析失敗代表目前狀態。
兩份準備清單與組件清單均為 `prepared`、`blockers=[]`、`root_uuid_verified=true`，
extraction 為 `ok=true`；以下新 extraction 摘要已直接讀檔核對。

| 板型 | 最新準備證據 | 完整核心版本 | root UUID |
| --- | --- | --- | --- |
| `bpi-f3` | [F3-002 重播](../output/evidence/bpi-multiboard-integrate-20260917-f3-002-replay/preparation.json) | `6.18.37-current-spacemit` | `eb926a01-3ac7-44cb-8772-528a0f795888` |
| `bpi-sm10` | [SM10-002 重播](../output/evidence/bpi-multiboard-integrate-20260917-sm10-002-replay/preparation.json) | `6.18.3-current-spacemit-k3-bpi` | `28e73b88-44b0-496d-899d-03a3c50c231b` |

- [F3 extraction](../output/evidence/bpi-multiboard-integrate-20260917-f3-002-replay/extraction/extraction.json) SHA-256：`e4ec4ba57465230e8892df59d91fbd359f919126f7b8710ef00bf406fd19250e`。
- [SM10 extraction](../output/evidence/bpi-multiboard-integrate-20260917-sm10-002-replay/extraction/extraction.json) SHA-256：`90c9b1ac05c83a988849190b1d8fbeff3f0bde47ede9b902c8b6797dab0bcb59`。

`replay_of` 分別綁定原 F3-001 摘要 `f86cbd0fbf10af9f5cf0f03a6d741a79840cef85ceba8f478eefc150875927c0`
與 SM10-001 摘要 `87b25e607a4933eac9c2852c9fc827078cc23e54f1a3387cf6edb6533260cb12`。
兩者皆為成功擷取的重播，`source_reread=false`，未重新解壓整顆映像，也未改寫舊證據。

舊阻擋已由實際內容解除：F3／SM10 的 gzip 核心有界解碼後通過標頭、版本、原配及內嵌 config 核對；
F3 的固定版本格式字串不再誤當另一核心版本；SM10 的 `armbianEnv.txt` 保留為未參與受控 MMC 分支的證據。
F3 解碼長度為 53,937,664 位元組、含 BSS 的 `image_size` 為 54,829,056；
SM10 分別為 42,409,472 與 42,991,616 位元組，原 gzip 核心檔案與摘要保持不變。

`hardware_validated=false`、`whole_backend_ready=false`、`boot_executed=false`、`media_written=false` 仍不變。
F3 可交給另行核定的 [mainline 原入口執行器](bananapi-lab-original-entry-20260917.md)；
SM10 只有組件及原 MMC 靜態語意準備通過，K3 vendor 執行 ABI 與安全鏈資格仍明確阻擋。

測試：`tests/test_bpi_lab_spacemit.py`，涵蓋三板、環境 CRC、重複 `bootcmd`、
K3 原配別名、root UUID、GRUB 分支阻擋、DTB 命令列合併與原入口範本。

## CM6 單核心 FIT

`allboard-bpi-cm6-003` 的組件清單實際是 `6.6.36-legacy-spacemit`，不是預期的
`6.6.99-current-spacemit`；本輪以核心內嵌版本與原配檔案為準。
原 extraction SHA-256：`8b796139562187f98488530e4ce896658e30b09a97d2d357c553d73141ef122a`。
[新目錄重播](../output/evidence/bpi-c3-cm6-fit-replay-20260918-001/preparation.json) 已為
`prepared`、`blockers=[]`、`root_uuid_verified=true`、`source_reread=false`；新 extraction SHA-256：
`fcf34e9c0550251f048ad47712508fce37820140c8301536336b65c3b30de96e`。

原 `/boot/Image` 是 FIT，預設 `conf-default` 引用單一 RISC-V Linux `kernel`，
資料未壓縮，load／entry 均為 `0x00200000`，CRC32 為 `64e27351`。
libfdt 完整結構核對後，另核對內嵌 Image 標頭、實際版本與原配 config；原 FIT 位元組不改寫。
外部 initrd／DTB 仍遵循原 extlinux，不用 FIT 描述文字代替內容驗證。

受限範圍只含一個明示預設配置、一個核心及 CRC32／SHA-256；其他配置、簽章、loadables、
內嵌 DTB／ramdisk、外部資料區或未知屬性明確阻擋。
`runtime_requirements.kernel_fit` 明示 `bootm`、固定載入／入口位址及未認證狀態。
主線 [sysboot／PXE 原始程式](https://github.com/u-boot/u-boot/blob/v2025.01/boot/pxe_utils.c#L691)
辨識 FIT 後使用 `bootm`，不能把此 FIT 當 raw Image 交給 `booti`。
本輪只完成組件路徑；獨立執行器的 FIT 搬移／RAM／bootm 支援仍未完成，不混稱僅待硬體。
