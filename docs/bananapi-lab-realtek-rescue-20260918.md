# Realtek 固定 SD RAM 救援

此入口只支援已讀取的 M4／W2 Realtek BSP，並非通用外部命令包裝器。沒有開啟 UART、電源、SSH、原映像或區塊裝置；本文件中的編譯與合成測試不構成任何實板授權。

## 固定介面

`tools/bpi_lab_realtek_rescue.py` 提供 `vendor_source(config)`、`validate_config(config)`、`validate_artifacts(config, artifact_root=None)`、`lifecycle_view(config)`、`boot(console, config, records=None, timeout=300, monotonic=...)` 及 `scope_digest(config)`。`boot` 借用已配對的 ConsoleSession，自己不開設備；須交付完整配置，不能只傳投影。

配置 schema 是 `bpi-lab-realtek-rescue-v1`，必要欄位如下：

| 欄位 | 固定來源與限制 |
| --- | --- |
| `platform` | 原配 `bpi-lab-special-runtime-v1` 完整配置參照，ABI 為 `realtek-lab-v1` |
| `artifact_root`、`files` | `kernel/initrd/dtb/audio` 每項具 `path/image_path/bytes/sha256`；本機原檔與實際 SD 路徑皆須核對 |
| `sd` | `device="sd"`、`index=0`、`partition=1`、另行配對的 `block_device`、`partuuid`、`extraction` 參照及完整 4 MiB `prefix` 摘要 |
| `sd_sources` | `sd_source_names(board)` 所列 BSP 來源的固定參照；客戶來源由 `special_runtime.vendor_files(board)` 核對 |
| `identity` | `{schema, kernel}` 文件參照；內容須逐位元組等於 legacy initrd 內唯一的 `/etc/bpi-rescue.json` |
| `qualification` | 獨立的 `bpi-lab-realtek-rescue-qualification-v1` 參照；產生來源時可為空，執行時不可省略 |

kernel、DTB、audio、RAM 位置及容量沿用已核對的同板配置，只能在原 initrd 槽放入受控救援 initrd。救援 bootargs 移除持久根與 init 設定，固定為 RAM 根及 `/init`。配置不自動製作可用救援系統；操作者仍須提供實際可啟動、含所需網路／SSH／Python 3 的救援 initrd。

SD 的 FAT `sd 0:1` 與 eMMC `mmc 0:1` 是兩個原廠命名空間。SD 探測直接使用 `find_sd_device`／`sd_init`／唯讀 `block_read`，不依賴 eMMC 分割表完整，也不把 SD block descriptor 編號猜成 MMC 編號。受保護 SD 的 CID、容量、控制器來自同一 `platform.pairing`，與部署契約須完全一致。

## 來源與執行

```bash
python3 tools/bpi_lab_realtek_rescue.py source \
  --config "$RESCUE_CONFIG" --config-sha256 "$RESCUE_CONFIG_SHA256"
python3 tools/bpi_lab_realtek_rescue.py check \
  --config "$RESCUE_CONFIG" --config-sha256 "$RESCUE_CONFIG_SHA256"
```

兩個 CLI 都只讀本機一般檔案。`source` 回傳的 `source` 是**完整合併** `bpilab+bpirescue` C，附加至已固定摘要的原始 `common/cmd_boot.c` 一次；不可再次附加 customer C。`check` 還須有完整板級救援核定。正式硬體流程由既有 backend／qualify 取得 UART 與排他後呼叫 `boot`，不是本 CLI 自行操作。

固定命令是 `bpirescue memory/probe/load/hash/boot`。每次載入有精確長度與上限、前後 SD 身分／前綴核對、RAM SHA-256，交接前再檢查完整四載荷；只讀 secure-boot OTP，檢查實際 DRAM／gd／ACPU 狀態。不執行 `gosd` 或重新載入不受界限的原廠腳本，不提供媒體寫入命令。救援與客戶入口互斥且僅能交接一次。

生命週期要求客戶與救援指向同一 platform、pairing、binary/config/map，且救援 identity／SD 前綴符合部署契約。返回 Linux 後仍須經同次 UART RAM 根與媒體身分、SSH hostkey 交接及完整部署唯讀預檢，U-Boot 核心標記本身不算救援通過。

救援 `scope_digest` 以 platform 的 scope 摘要代替其資格檔參照，避免「來源內 scope → 資格 binary → 來源」循環。客戶與救援資格仍各自核對來源及同份 binary/config/map，不因排除資格參照而省略核定。

## 離線證據

| 證據目錄（位於 `output/evidence`） | 真 BSP 結果 |
| --- | --- |
| `bpi-shared-rescue-m4-20260918-001` | 合併 C 編譯、連結、`binary_size_check` 全部回傳 0；`u-boot.bin` 549712 位元組 |
| `bpi-shared-rescue-w2-20260918-001` | 同上，使用 `direct-final`；`u-boot.bin` 535800 位元組 |

每份保存 `build-driver.py`、`build.log`、合併來源 JSON、patch、binary/config/map，以及原 BSP 的 `source-before.json`／`source-after.json`；兩板 `source_unchanged=true`。建置使用獨立 `/tmp/bpi-rescue-bsp-*` 副本，未修改原 BSP。測試配對、RAM 與 SD 摘要明示為合成，結果保留 `fixture_only=true`、`hardware_validated=false`、`deployment_ready=false`。

[可隨 Git 追蹤的主代理核對摘要](evidence/bpi-multiboard-integrate-20260917/realtek-combined-build-audit.json)
列出完整建置證據參照及產物摘要；主代理已重新核對兩份生成來源與實際編入來源一致、全部命令成功、
產物長度及 SHA-256 正確，且原 BSP 前後清單相等。二進位不隨此文件發布為可燒錄版本。

`tests/test_bpi_lab_realtek_rescue.py` 另以真正生成 C 編譯主機替身，兩板各跑 18 種 SD 身分、前綴、短讀、超長、RAM 污損、延遲換卡、交接失敗及一次性限制反例。跨家族 backend 測試涵蓋 M4／W2 的 LABEL 客戶引導與同 vendor SD 救援完整五階段。

C 替身的 `find_mmc_device`、`mmc_init` 一旦被呼叫即失敗，分割查詢也只接受 SD descriptor；成功救援因此證明此入口不需可讀的 eMMC 分割或初始化。這項證據只涵蓋已進入核定 U-Boot 之後，不能外推 ROM／SPL 上電鏈不依賴 eMMC。

實板仍須核定載入 RAM、保留區、W2 direct-final／ACPU 時序、SD block descriptor、實際核心與救援服務，以及 UART／電源／首次帳戶與測試公鑰授權。沒有這些證據仍不能執行、啟用站點或宣稱循環通過。
