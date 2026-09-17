# 六板原配組件與引導配置核對

日期：2026-09-17。此交付只新增 `tools/bpi_lab_special.py`、
`tests/test_bpi_lab_special.py` 與本文件。未修改 platforms、映像讀取器、
共用 prepare／U-Boot、其他代理檔案；未提交、推送、操作硬體或重解壓映像。

## 介面與狀態

```python
from tools import bpi_lab_special

manifest = bpi_lab_special.prepare(
    read_file, board="bpi-f2s", kernel_release="0", output=output,
)
checked = bpi_lab_special.validate(output)
configuration = bpi_lab_special.bootconfig(output, template=template)
```

`prepare(read_file, *, board, kernel_release, output)` 接受 `bpi-f2p`、`bpi-f2s`、
`bpi-ai2n`、`bpi-m4`、`bpi-w2`、`bpi-m6`。亦接受對應 Armbian 板名別名。
`read_file` 接受映像內絕對路徑，回傳 `bytes`；只有 `FileNotFoundError`
表示已確認不存在。未知擷取路徑、權限錯誤、非位元組與超限均阻擋，不冒充缺檔。
輸出父目錄須已存在；輸出目錄不得已存在，不接受任一層 symlink。

- `status=prepared`：原配組件、環境、版本、必要載荷與所支援格式通過離線檢查。
- `status=blocked`：`blockers` 列出階段與具體原因；保留已擷取證據。
- `hardware_validated=false`、`execution_ready=false` 永遠成立。
- `root_uuid` 僅取自原配 `UUID=` 引導設定；Realtek 使用標籤時保持 `null`，另記
  `root_label=BPI-ROOT`、`root_target` 與 `root_fstab_target`。
- `source_image_verified=false`：本工具不接管分割區或超級區塊核對。
- `qualification_blockers` 是引導前置鏈、實板、原廠保護載荷與復原限制，不因離線成功清除。
- `bootconfig` 回傳 `status=validated_offline`、`boot_config_validated=true`；
  不更改原 manifest，不發送命令，且不是共用 U-Boot 執行器的輸入 ABI。

`validate` 重播保存的讀取證據，重新解析並逐一核對來源與派生組件摘要。
手改 manifest 的狀態、UUID、組件或缺檔記錄不能取代重新檢查。

## 六板契約

| 板型 | 核心及版本 | 原配入口與必要載荷 |
| --- | --- | --- |
| F2P、F2S | 驗證 ARM legacy uImage 的兩種 CRC、zImage 標頭及有界 gzip 內嵌完整版本 | 核對 family 產生的完整 `uEnv.txt`；`bootm`，板型專用 DTB、uInitrd |
| AI2N | ARM64 Image magic、大小、位元組序、內嵌版本 | CRC 解包 `boot.scr` 並與 `boot.cmd` 及固定來源相等；`booti`，另需 OpenCV、Codec |
| M4、W2 | 不從 uImage 檔名推定格式，實際檢查 Image 或 legacy 容器 | 內外兩份 uEnv 必須一致並符合已查閱原廠契約；音訊檔、原配 DTB 與 fstab 必須存在 |
| M6 | ARM64 Image 與內嵌版本 | 固定來源 `boot.scr`／`boot.cmd`；`booti` 固定位址及原 CMA、TZ 參數 |

所有 initrd 均驗證 legacy OS／架構／類型／CRC，並解析有界 gzip、XZ 或未壓縮
newc／crc cpio，支援串接段；modules 完整版本須唯一且等於核心。
另要求最後有效的 `/init` 是非空、可執行、非連結的一般檔；缺檔、目錄、無執行權限、
空檔或被後續串接段覆寫為無效入口均拒絕。symlink／hardlink 入口尚未適配。
每檔上限 128 MiB、擷取總量 512 MiB、解壓上限 128 MiB。
不執行或解出 cpio 檔案，不執行 shell／原廠腳本。

DTB 透過既有 `bpi_lab_amlogic` 的結構核對、`dtc`、`fdtget` 驗證；
檢查根 compatible、model、FDT 保留表與 reserved-memory。
F2P 的來源只有 SoC compatible，故另以精確 model 區分 F2S。
Realtek 額外解析 memory、CMA 三元組與 ION 保留區，不能從 `1GB`／`2GB` 檔名推定容量。
不支援的 overlay、額外引導參數與替代入口均具體阻擋。

## Sunplus 版本零

`kernel_release="0"` 只對 Sunplus 啟用解析，不是合法的最終版本。
依原配 uImage 的 CRC 與 zImage 內容取得完整 Linux 版本，核對原配 release、
存在時的 `linux-headers-<完整版本>/include/config/kernel.release`、
`include/generated/utsrelease.h`，再與 initramfs modules 獨立交叉核對。
uImage 的 32-byte 名稱只驗證完整版本的截斷前綴，不能單獨當版本證據。
缺少內嵌版本、gzip 毀損、多版本、headers 錯配或未知路徑證據皆阻擋。
family 的 zImage fallback 若沒有 legacy 外殼，不能交給原配 `bootm`。

本地真實 uImage 已成功解析為 `5.4.35-legacy-sunplus-sp7021-bpi`，
與同一建置樹的 `kernel.release`、`utsrelease.h` 一致。
這只證明核心格式與建置版本，不等同 F2P／F2S 完整原配映像或實板驗證。

真 F2P 首輪擷取的 `extraction/file-0000.bin` 使用 `ARCH=arm`、`INITRD_ARCH=arm`。
原先只接受 `armhf` 而過早阻擋；現只將已確認的 `arm`／`armhf` 正規化為 ARM32，
ARM64 仍只接受 `arm64`，不推測其他別名。此修正不豁免核心、initrd 的獨立架構核對。
未對尚未讀取的檔案作重播假設；主代理後續重新擷取的 F2P-002 已通過完整 prepare，詳見下節。

## 離線引導配置

基本範本欄位為 `board`、`kernel_release`、`source`、`ram`、`addresses`、`bindings`。
`ram` 需有 `banks`、`reserved`、`kernel_work`；每項區間明示 `start`、`size`。
所有必要載荷必須有位址，檢查容量、重疊、核心工作區與 DT 保護區。
`bindings` 只接受腳本確實使用的外部環境，不允許指令注入或任意多餘值。
可重現的完整範本見測試檔的 `template`；其位址和身分摘要是合成測試資料，不可直接拿去實板。

Sunplus 保留 `board`、`sdmmc_on`；AI2N 保留原廠 Ethernet、serial、chipid，
並明示 `ocaaddr`、`codaddr`、`ocabin`、`codbin`。
AI2N 額外韌體只能放入原配 DT 的 OpenCVA／Codec 保留區，不豁免其他載荷。
M6 保留三個固定載入位址，CMA 範圍不得被載荷或核心工作區覆蓋。

### Realtek 專用配置

本機原廠 `cmd_boot.c` 與 `cmd_bootm.c` 已逐段核對，不因共用主線 ABI 不支援而停止。

- M4：`go all` 先執行 `go a`，再 `go k`；核心入口走 `rtk_plat_do_boot_linux`。
- W2：`gosd` 進入 `boot_from_sd`，自行以原廠 `device` 從 FAT `0:1` 載入
  DTB、rootfs、核心、音訊韌體，再啟動音訊並進入核心。
- ARM64 路徑使用原廠 `rtk_call_booti`；軟體配置保留 `go all`／`gosd`，不偷換成通用 `booti`。
- `booti_setup` 依 `bi_dram[0].start + text_offset` 搬移 Image；分別檢查原始載入區、
  `image_size` 讀取範圍與搬移目的工作區，並保護 ACPU／IPC 區域。

Realtek 範本另需 `vendor`：`dram_size`、`secure_mode=non-secure`、空 `hyp_loadaddr`、
`root_identity`。安全模式、HYP、未知 DRAM、變更位址、錯誤標籤或分割區皆拒絕。
M4 必須明示選擇 1GB 或 2GB DTB，不能任選第一個。

`source` 必須是 `vendor-fat`，明示 `device=sd|mmc`、`partition=0:1`、
`partuuid`、`filesystem=vfat`、`identity_sha256`。
`root_identity` 須明示外部檢查所得 `uuid`、`label=BPI-ROOT`、`filesystem=ext4`、
`evidence_sha256`，並與 uEnv、fstab 相符。
這些摘要是呼叫端的證據綁定，不是本工具驗證了其真實內容；因此仍回傳
`source_identity_verified=false`。不得捏造摘要或用範本宣告取代 DiskReader 核對。

若真實 DT 的執行期保留區與原廠暫存載入區相撞，目前會明確拒絕配置。
需要另有原廠記憶體生命週期與音訊啟動時序證據才能新增豁免，不能因原廠腳本存在就忽略重疊。

## 來源

先讀 `config/bpi-lab/platforms.json`、六板 board 設定、對應 family/include、
`boot-renesas-rzv2n-bpi.cmd`、`boot-vs680.cmd`、`config/bootenv/renesas-bpi.txt`。
prepare 保存實際使用的本機來源摘要；Sunplus heredoc、兩種 bootscript、
Realtek 去除根識別後的環境賦值另有固定雜湊契約。

本機 BSP 根目錄為
`/media/pi/SMCI/armbian/bpi-v26.2.1-bananapi-parallel/cache/sources/linux-kernel-worktree/`：

- `5.4__sunplus-sp7021-bpi__armhf`：真 `arch/arm/boot/uImage`、headers，
  `linux-sp/arch/arm/boot/dts/sp7021-bpi-f2{p,s}.dts` 與 `sp7021-ChipC.dtsi`。
- `4.9__realtek-rtd139x-bpi__arm64`、`4.9__realtek-rtd129x-bpi__arm64`：
  `rtk-pack/rtk/<板型>/configs/default/linux/uEnv.txt`、`linux-rtk/arch/arm64/boot/Makefile`、
  真 Image、板型 DTS、`u-boot-rtk/common/cmd_boot.c`、`cmd_bootm.c` 及板型標頭。
- `6.1__bpi-rzv2n__arm64`：`arch/arm64/boot/dts/renesas/bananapi-ai2n.dts`。
- `5.4__vs680__arm64`：`arch/arm64/boot/dts/synaptics/vs680-a0-bananapi-m6.dts`。

板型固定上游來源為 [F2S BSP](https://github.com/BPI-SINOVOIP/BPI-F2S-bsp/tree/3eee97bd8fb7582c2d9942a533647c3d78222bb5)、
[M4 BSP](https://github.com/BPI-SINOVOIP/BPI-M4-bsp/tree/25f5b88ec4ba34029f964693dc34028b26e6c67c)、
[W2 BSP](https://github.com/BPI-SINOVOIP/BPI-W2-bsp/tree/6e6aefc35dc50b1b8231cdb03a995d088f29eb21)。
此輪查閱本機檔案，未聲稱本機帶補丁的內容等同乾淨上游提交。
兩個 Realtek `cmd_boot.c`／`cmd_bootm.c` 的本機實際 SHA-256 記錄於工具
`REALTEK_SOURCES`，隨 manifest 保存，不代表已部署 U-Boot 二進位配對完成。

## 驗證與剩餘

```sh
python3 -m unittest tests.test_bpi_lab_special -v
python3 -m unittest tests.test_bpi_lab_special tests.test_bpi_lab_amlogic tests.test_bpi_lab_uboot -q
ruff check tools/bpi_lab_special.py tests/test_bpi_lab_special.py
```

初版專項 36 項、連同既有 Amlogic／U-Boot 共 133 項通過；ruff 通過。
本次另增 release 架構別名與錯配回歸，結果另記於交付摘要。
涵蓋六板正例、Realtek 專用配方正例、版本零、CRC／標頭／DTB／modules 負例、
根身分與環境錯配、未知原廠格式、腳本篡改、來源 symlink、manifest 偽造、
記憶體重疊、M6 固定位址、Renesas 額外載荷與 Realtek 安全分支拒絕。

2026-09-18 本體補驗：special 專項共 42 項通過，ruff 與 py_compile 通過。
Noether 提供的三個原始反例亦逐一重新執行，現均在正確原因下拒絕：

- ARM64 Image 按主線 `flags`、`text_offset` 與 2 MiB 對齊計算實際搬移目的；
  完整來源讀取區及搬移目的都必須包含於明示 RAM／核心工作區。計算依本機已讀
  `output/evidence/bpi-sram-a1-fit-2048-001/source/arch/arm/lib/image.c`，不猜入口。
- uImage 入口須落在標頭載入位址起算的實際 payload 內且對齊，拒絕 32-bit 載入溢位。
- initramfs 必須包含上述有效 `/init`，不能僅以 modules 版本存在宣稱可交接。

第四項 root UUID 未取得可重現的獨立漏洞，已核對原配環境、唯一 root bootarg、
manifest 重播及 Realtek 外部根身分錯配；不把未重現寫成已修復漏洞。
superblock 與完整 LABEL 清單核對屬共用 prepare／DiskReader，本工具仍不把環境中的
UUID 字串誤報為已驗證實體根媒體。

真 W2 歷史首輪只重播使用者提供的既有擷取：
`output/evidence/bpi-multiboard-integrate-20260917-w2-extraction-001/extraction.json`，
SHA-256 `a8da0f73637ec381153a95dddc633f1a08eaf4746bbdb874ed0bdabe8b98ad4b`。
原來源為 MBR 第一 FAT、第二 ext4 根；不重解壓，不修改舊 extraction。
新增執行輸出為 `output/evidence/bpi-special-w2-replay-20260917-001`。
真核心、initrd、DTB、音訊、內外 uEnv、fstab 均通過；核心是 raw Image，版本為
`4.9.119-legacy-realtek-rtd129x-bpi`，不是 legacy uImage。

另直接唯讀檢查上述本機 BSP 已建置的六板核心與 DTB，全部通過解析；
沒有從壓縮映像取得這些建置檔，也沒有把同家族共用核心當成板型專屬映像證據：

| 本機建置組件 | 核心內嵌版本 |
| --- | --- |
| F2P／F2S 共用核心及各自 DTB | `5.4.35-legacy-sunplus-sp7021-bpi` |
| AI2N | `6.1.107-legacy-renesas` |
| M4 | `4.9.119-legacy-realtek-rtd139x-bpi` |
| W2 | `4.9.119-legacy-realtek-rtd129x-bpi` |
| M6 | `5.4.195-legacy-vs680` |

該次歷史重播阻擋：擷取只證明短版本 headers 路徑不存在，未查過下列完整版本路徑：

```text
/usr/src/linux-headers-4.9.119-legacy-realtek-rtd129x-bpi/include/config/kernel.release
/usr/src/linux-headers-4.9.119-legacy-realtek-rtd129x-bpi/include/generated/utsrelease.h
```

前次完整擷取已由主代理補齊，舊 manifest 未修改：
`output/evidence/bpi-multiboard-integrate-20260917-w2-002/preparation.json`
現為 `status=prepared`、`blockers=[]`；其 `extraction/extraction.json` SHA-256 為
`70ca029062f1a2ffef663f8330e528a4fa86356d560b6998cf8c72f76065b5cc`。
共用 prepare 已將映像內唯一的 `BPI-ROOT` 標籤綁到 ext4 超級區塊 UUID
`17c44a76-58ee-4075-bf9c-b8e6dccde39c`，記為 `root_identity_verified=true`、
`root_uuid_verified=false`。這不是 UUID 引導，也不宣稱實板其他媒體的標籤唯一性；
`unique_on_hardware=false`、`hardware_validated=false`、`whole_backend_ready=false`。
此 W2-002 紀錄沒有後續新增的完整標籤清單契約，保留歷史，不用來通過新 LABEL 閘門。

最新 W2-003 已完整重新讀取同一來源，`preparation.json` 為 `prepared`：
`output/evidence/bpi-multiboard-integrate-20260917-w2-003/extraction/extraction.json` 的實際 SHA-256
為 `f8b931854e2d120f4df0ba60fb0bb7b877018717ceada5470c73ee3675306f0e`，
`filesystem_labels_complete=true`，映像內 `BPI-ROOT` 唯一且仍綁定上述 UUID。
原配 `family-result.json` 的 SHA-256 維持
`66b2454e09706b920e6ecb27f56e1344ca9bcf623c9136772e4f27c16e16b297`。
實際唯讀核對清單與摘要，未修改 W2-002、W2-003 或任何舊 extraction；
仍為 `root_identity_verified=true`、`root_uuid_verified=false`、`unique_on_hardware=false`。
本工具未越界修改共用 prepare 或主 plan。其餘已取得完整映像的核對紀錄如下，
合成測試不是硬體資格。

2026-09-18 補驗：已唯讀核對主代理新增的下列完整擷取 SHA，並以 `special.validate`
重播各自 `components`；四板皆 `prepared`、`blockers=[]`、`hardware_validated=false`。
原配根 UUID 與超級區塊的映像內核對均為真，不代表已在實板啟動。

| 證據目錄尾名 | 完整核心版本 | extraction SHA-256 |
| --- | --- | --- |
| `f2p-002` | `5.4.35-legacy-sunplus-sp7021-bpi` | `7cfcdd55d63076946e32c6cd113ee36aca918e5e1d9c003bfe136da7335dc426` |
| `allboard-bpi-f2s-002` | `5.4.35-legacy-sunplus-sp7021-bpi` | `60fa03280ba17cd1d78c33720ce4565fc0d9bba45eb4bc08cbb0db09267b86e8` |
| `ai2n-001` | `6.1.107-legacy-renesas` | `954d9ff8d6e492599ee20a2a8f40064429e09bf682c8d82cb19ea8bb9fd1cdb4` |
| `m6-001` | `5.4.195-legacy-vs680` | `47e5e76a72d19c5910455d0ede9ed901efc2b90957effa581968619b5ab99550` |

共同前綴為 `output/evidence/bpi-multiboard-integrate-20260917-`，
擷取清單位於 `extraction/extraction.json`。W2-002 的 components 亦再次重播通過，
最新版 W2-003 的同摘要原配 components 另行重播核對；這不豁免最新 LABEL 完整性契約。
F2S 的 `preparation.json` 記錄原 source SHA-256 為
`24a43b034d26e99800e5401a2c737c945fd90690f2435db0d015f32e7f5f1949`，
根 UUID 為 `597f5253-d77e-4ac2-b1b3-9eb6ea20e0de`；本輪只讀取已有擷取與組件，
未重新讀取或重解壓該 source。其 `requested_kernel_release=0` 已由真實核心解出完整版本，
原配 initramfs 與 F2S 專用 DTB 亦通過新審查檢查。
後續一次性 UART 與 Realtek lab 命令來源另階段交付，不屬此三檔本體提交，
不把離線 prepare 狀態升格為硬體資格。

Sunplus xboot／ISPBOOOT、Renesas BL2／FIP、Realtek 安全及 ACPU 鏈、
Synaptics OEM／TZK 均保留獨立實板資格與故障復原要求。
