# 跨架構救援建置

`tools/build_bpi_lab_rescue.py` 在板上已正常執行的原生 Linux 建置獨立 initramfs，
支援明示 ARM32、ARM64、RISC-V 配置。它不安裝套件、不修改 `/boot`、不寫 SD 或 eMMC，
也不會在本機 x86 主機上假裝完成 ARM／RISC-V 建置。
本輪完成程式及離線回歸，尚未在這些架構的實板執行新建置。

## 與既有入口的關係

沿用 `build_bpi_h618_rescue.py` 的私人 mount namespace、受控 initramfs hook、
Python／SSH 複製與封裝核對。舊 H618 入口及預設模組清單保持原行為；
新入口須指定配置，不會使用 H618 的 Wi-Fi／MMC 預設值。

指定板級配置的新建置使用 `bpi-lab-rescue-v1` 身分及建置報告，避免被共用後端的
舊 0845 隔離規則拒絕。無板級配置的舊 H618 入口仍使用 `bpi-h618-rescue-v1`，
其執行程式及身分內容維持原樣。

一般板型的 `/usr/sbin/bpi-rescue` 為固定薄包裝，設定獨立 schema 後呼叫
`/usr/sbin/bpi_rescue_runtime.py`；後者逐位元保留既有唯讀執行程式，不以文字取代方式改寫原來源。
`ready` 真正核對的新身分檔、盤點結果及執行程式 schema 一致，不只是修改報告名稱。
建置報告新增 `rescue_identity` 與 `runtime_entry_sha256`，封裝必須包含共用執行模組。

正式部署仍須綁定身分檔實際摘要、核心版本、initramfs 摘要、實板 DT、UART 及兩個媒體。
早期使用歷史 schema 的跨架構產物須重新建置及核定，不能只改 JSON 就套用新身分，
更不能套用 0845 的媒體或覆寫授權。

## 板級配置

`bpi-lab-rescue-build-profile-v1` 必須提供全部欄位：

| 欄位 | 條件 |
| --- | --- |
| `board` | 明確 `bpi-*` 板別；不從名稱猜硬體 |
| `architecture` | `arm32`、`arm64` 或 `riscv64` |
| `multiarch` | 分別為 `arm-linux-gnueabihf`、`aarch64-linux-gnu`、`riscv64-linux-gnu` |
| `dt_compatible` | 需與目前板上 DT 根 compatible 完整有序清單一致 |
| `modules` | 非空、無重複的救援必要模組；逐一核對當前核心索引、來源及 vermagic |
| `firmware` | 每項 `{path, sha256}`；路徑相對 `/lib/firmware`，解析後不可越界 |
| `wireless` | 明確布林值；為 true 時必須包含 `regulatory.db` 與 `regulatory.db.p7s` 的摘要 |

模組及韌體需依各板實際來源選定，包括 MMC、Ethernet 或 Wi-Fi 所需驅動。
通過配置核對不表示選擇已涵蓋全部周邊；核心內建驅動、載入依賴、硬體修訂與韌體版本須另行核定。
Linux 4.x 的舊模組索引或非標準 initramfs 環境若不具備前提，會停止建置，不能刪除核對來湊成功。

板級韌體僅讀取已安全開啟的一般檔案，單檔限制 64 MiB；FIFO 等特殊檔案在讀取前拒絕。
建置複製時重新核對配置摘要，且只寫入同一描述元已核對的內容；前檢後遭替換的韌體不能產生成功報告。

## 執行

在板上備妥 `initramfs-tools`、靜態 BusyBox、Python 3、OpenSSH、curl、xz、
wpa_supplicant、iproute2、rfkill、kmod、util-linux、TLS CA 與相符核心模組。
執行檔須為目前架構的小端序 ELF；BusyBox 不得有動態載入器或動態表。
只接受目前 `uname -r` 的核心，不把其他版本模組混入救援。

```sh
sudo python3 -B tools/build_bpi_lab_rescue.py \
  --profile /私有目錄/rescue-profile.json \
  --profile-sha256 配置摘要 \
  --output /獨立工作目錄/全新建置 \
  --busybox /已核對檔案/busybox \
  --busybox-sha256 BusyBox摘要 \
  --authorized-key /私有目錄/專用測試公鑰 \
  --check-only
```

`--check-only` 只檢查原生建置前提，不新增產物。確認後移除該選項才建置；
輸出目錄必須尚未存在。保留原工具及整個 `tools/bpi_h618_rescue/`，不能只複製新入口。
建置程序有期限，逾時會終止整個建置程序群組。

輸出包括 `rescue-initramfs.img`、`SHA256SUMS`、`build-report.json`、封裝清單與建置日誌。
報告保存所用板級配置；封裝核對必需執行檔及身分檔，不攜入 hostkey、原 root 的 machine-id、
NetworkManager 設定或客戶根交接腳本。救援 init 不解讀 `root=`，不掛載持久媒體。

產物仍須先做一套實板救援啟動與恢復核定。正式環境保留固定 SD 引導與救援檔，
客戶映像寫到另行授權的測試媒體；本工具不負責這次初始部署。

## 回歸

```sh
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest discover \
  -s tests -p test_bpi_lab_rescue_build.py -q
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest discover \
  -s tests -p test_bpi_h618_rescue.py -q
```

涵蓋三種 ELF 架構、動態／錯配拒絕、DT 身分、模組隔離、韌體路徑及摘要、
跨架構拒絕、唯讀前檢入口與舊 H618 行為。測試未執行真實建置、掛載、UART 或媒體寫入。

本次身分整合修正後，共 33 項回歸通過；紀錄為
`output/evidence/bpi-multiboard-integrate-20260917/native-rescue-identity-main-reviewed.log`，Ruff 通過。
三種架構配置均生成真 Python 包裝，再載入共用程式執行 `ready`；只有核心與檔案路徑觀測為替身。
測試確認新舊 schema 不能混用、舊執行程式逐位元不變，以及封裝缺少新模組時不得宣告成功。
