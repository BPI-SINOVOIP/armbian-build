# M4 Zero EMAC 客戶系統一次性引導

日期：2026-09-16。依據 [Bookworm 靜態預檢](bananapi-m4zero-bookworm-emmc-preflight-20260916.md)。
本工具開發與測試僅離線；實板操作、電源及回救援由主代理另行管理，不由本工具執行。

## 接口

`tools/bpi_h618_customer_boot.py` 提供：

```python
components, receipt, evidence = load_inputs(components_path, trusted_components_sha256, receipt_path)
result = boot(console, bridge_bytes, components, receipt, records, timeout=300)
```

`console` 借用同一個 `RecordedChannel`；呼叫者管理開啟、關閉與 RX 日誌。
`boot()` 不驗證外部信任來源，須先呼叫 `load_inputs()`；兩者皆驗證中繼資料契約。
`records` 是由呼叫者保存的命令與觀察清單。失敗拋出例外，不自動重試、登入或重啟。

CLI 必填 `--port /dev/ttyUSB0`、`--bridge`、`--components`、`--components-sha256`、
`--deploy-receipt`、`--output`；選填 `--timeout`，預設 300 秒，最多 1800 秒。
輸入只接受一般檔案與無符號連結路徑；輸出須為新目錄，權限 `0700`，檔案 `0600`。
產物為 `uart.bin` 及 `report.json.partial`；後者永遠是觀察工作證據，不是系統通過收據。
CLI 退出碼 `0`／`ok=true` 只表示預期核心版本與 login 已觀察到、連線及證據處理完成。

## 固定守門

- 接受 `bpi-h618-customer-components-v1`、EMAC 板型、三項必要預檢與外部可信清單 SHA。
- 必須有正式成功部署 `receipt.json`，核對來源壓縮／原始大小及 SHA、完整回讀、部署範圍、固定 eMMC CID／容量／控制器，以及部署時 SD 身分與前 4 MiB 前後一致；不重新讀取巨大 XZ。
- 只用共用已核定 A1 橋接，由 SRAM 槽 3 唯讀載入並交接，FIT LBA 2048。操作者須先停在 SRAM 等待狀態。
- `mmc list` 必須明確對應 SD 控制器 `4020000` 為 0、eMMC `4022000` 為 1；`mmc info` 必須是後者及 MMC 類型，`part uuid mmc 1:1` 必須相符。格式不符即停止，不猜裝置編號。
- 從 `1:1` 載入原核心 `40080000`、原 EMAC DTB `4fa00000`、原 Wi-Fi overlay `45000000`、legacy uInitrd `4ff00000`，逐一核對長度與 CRC32；`booti 40080000 4ff00000 4fa00000` 不附加 initrd 大小。
- 套用原 overlay 後，只在 RAM 將 `/soc/mmc@4020000/status` 設為 `disabled`；核對 SD 停用、Wi-Fi MMC1 與 eMMC MMC2 為 `okay`。
- 不 `source` 原 `boot.scr`／fixup、不 `saveenv`、不更新 SPL、不發送區塊寫入指令；任何 `param_*`、額外 overlay、不支援的原環境值均拒絕。

## 差異與限制

保留根 UUID、`rootwait`、ext4、雙 console、`cma=256M`、記憶體 cgroup、原 USB quirk 設定。
原 `verbosity` 改為 `loglevel=6`，新增 `panic=0`；`ubootpart` 明確使用 eMMC PARTUUID。
一次性遮罩擴容與兩項 APT daily 服務。`disp_mode` 保存於原環境證據，但原預檢腳本未將其轉成 bootargs；不另行注入顯示參數。

`system_verified`、`root_cid_verified`、`hardware_validated`、`native_sd_verified`、`original_boot_chain_verified` 一律為 `false`。
部署時 SD 前後一致不代表本次啟動後已重新驗證 SD。CRC32 是 U-Boot 傳輸檢查，不是密碼學認證。
未取得執行中根媒體 CID、UUID、模組及功能證據前，不可宣稱系統驗證通過。
共用 A1 開機鏈與 RAM DTB 變更不能證明客戶原 SPL、DDR、TF-A 或原生 SD 已通過。
Linux 啟動仍可能修改 eMMC 根檔案系統；服務遮罩與 SD 節點停用不是硬體防寫，也不授權其他操作。

## 離線驗證

```text
ruff check tools/bpi_h618_customer_boot.py tests/test_bpi_h618_customer_boot.py
python3 -B -m unittest discover -s tests -p test_bpi_h618_customer_boot.py -v
```

32 項測試涵蓋中繼資料／來源／SD 守門、符號連結、錯誤媒體映射、CRC／長度、overlay 失敗、
ANSI 回顯、錯 nonce、跨階段多讀尾端、總期限、錯核心、無 login，以及證據同步失敗。
本次另唯讀核對指定 Bookworm components SHA `232c6c6a197adc477c28007d8ea7b0d977a1e69277a1122c1d2b998cd279253b`
與主代理產生的正式部署收據通過；沒有在本工具開發中執行實板引導。
