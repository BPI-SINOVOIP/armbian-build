# 0845 免拔卡測試的 eMMC 工具

本文件配合[十套測試計畫](bananapi-m4zero-ten-image-no-mux-plan-20260916.md)及[執行紀錄](bananapi-m4zero-ten-image-no-mux-worklog-20260916.md)。不是一般使用者的燒錄教學，也不授權清除未知媒體。工具目前限定本計畫的 userarea 備份／受限寫入，不控制電源、不自動選擇候選、不操作 boot0、boot1、RPMB 或 eFuse。

## 目前證據

- 完整 userarea 唯讀備份已在 0845 完成；實際還原未測，覆寫授權仍待確認。
- 部署器只完成離線測試，沒有執行實體寫入。
- 原 SD 核心 `6.6.75-current-sunxi64` 的網路與傳輸通過；獨立救援已建置，實板開機結果另看執行紀錄。
- 未完成第一套候選端到端前，不能啟動十套自動輪替。

## 身分守門

| 對象 | 本輪核對值 |
| --- | --- |
| 目標 eMMC CID | `d629034339413535311299942ee08c07` |
| eMMC userarea 容量 | `31289507840` 位元組 |
| eMMC 控制器 | `/sys/devices/platform/soc/4022000.mmc` |
| 受保護 SD CID | `03534453523634478697bc8c0701846b` |
| SD 控制器 | `/sys/devices/platform/soc/4020000.mmc` |

以上值限定 0845 本次媒體；不得套到另一片板子。工具動態查找 CID、類型、容量與控制器，拒絕根系統所在媒體、已掛載分割區、swap、holders、符號連結及非整顆 userarea。啟用 SDIO 後裝置編號曾變動，不能用 `mmcblk1` 或 `mmcblk2` 代替身分核對。

## 唯讀備份

```sh
python3 -B tools/bpi_h618_emmc_backup.py \
  --ssh-config "$SSH_CONFIG" --alias "$SSH_ALIAS" \
  --expected-cid d629034339413535311299942ee08c07 \
  --expected-size 31289507840 \
  --expected-controller /sys/devices/platform/soc/4022000.mmc \
  --output-dir "$NEW_BACKUP_DIR" --timeout 7200
```

`SSH_CONFIG` 必須已經由核對過的 UART 主機公鑰建立，保持嚴格 SSH 身分驗證。新目錄必須不存在；工具不自動重試或接續半份備份。本輪已有完整備份，不能只因重新開始工作便再做一次。

遠端只以唯讀排他描述符讀取，gzip level 1 串流輸出，同時計算原始 SHA-256。主機落盤後重新解壓並核對全長與摘要；正式 `manifest.json` 最後發布。只有 CLI 退出 `0`、完整 manifest、兩端摘要及對應產物均符合才算完成；`.partial` 不能代表成功。

發布後同步／關閉失敗會撤回本次正式成功標記，保留失敗工作檔。這是工具修正；舊備份的退出碼 `0` 與原始證據保留不改。備份不是儲存快照，不保證抵抗其他特權程序同時直接寫碟。

復原資料是完整 userarea gzip，不含其他 eMMC 區域。解壓可還原備份位元組，但尚未實際寫回並開機驗證，因此 `restore_verified=false` 必須保持，不能僅因解壓成功便改為已驗證復原。

## 受限部署

部署介面見：

```sh
python3 -B tools/bpi_h618_emmc_deploy.py --help
```

必填資料包括可信完整備份 `--backup-manifest`、原始 XZ `--source`、壓縮及原始 SHA-256、解壓長度、精確 eMMC CID／容量／控制器、受保護 SD CID／控制器、專用 SSH 設定及新的嘗試目錄。只有取得本次 userarea 覆寫授權後，操作者才能加上 `--confirm-overwrite`；命令旗標本身不構成使用者授權。

工具依序執行：

1. 主機核對完整備份紀錄及 gzip 摘要，再完整核對 XZ、解壓長度及摘要；固定同一來源描述符並偵測檔案變更。
2. 遠端確認 `/etc/bpi-rescue.json`、執行核心與 RAM 根系統，不接受原 SD Linux、overlay 根系統或僅放一份標記檔的客戶系統。
3. 受保護 SD 僅開唯讀描述符；目標 eMMC 才可開寫入描述符。兩者 CID 與控制器必須不同。
4. 只寫原始映像的 `[0, raw_size)`；有界解壓與短寫處理，不先把整份原始映像放進 RAM。不修改容量尾端，亦非安全抹除。
5. `fsync` 後使用 `BLKFLSBUF` 刷新並丟棄目標快取，再回讀完整寫入範圍核對 SHA-256。刷新失敗直接拒絕，不能降格為只讀快取。
6. 再次核對媒體與救援狀態、SD 前 4 MiB 摘要不變；SSH 正常退出且所有證據完整才發布 `receipt.json`。

成功收據只表示寫入與回讀一致，`bootable`、`boot_verified`、`restore_verified` 仍為 `false`。斷線或失敗可能已留下部分寫入，`.partial` 記錄可能範圍；不得直接試跑，也不自動重送或切電。

SD 前綴相同不能證明整張 SD 所有資料不變，軟體排他存取也不是硬體防寫。候選開機後仍需依靜態預檢保護安裝器、擴容、套件更新及錯誤 `ubootpart`，不能以部署工具守門代替後續保護。

## 本機回歸

```sh
python3 -B -m unittest discover -s tests -p 'test_bpi_h618*.py' -v
```

本次主代理完成 199 項 H618 工具回歸，其中備份 34 項、部署 35 項、救援建置與執行介面 17 項、一次性啟動工具 7 項；其餘為既有清單、盤點及守門回歸。Ruff 通過，ShellCheck 使用 `-s dash -e SC1091`，僅略過板上外部 `hook-functions` 的靜態載入追蹤，不取消 shell 語法檢查。

固定來源與命令紀錄：`output/evidence/bpi-h618-no-mux/T1-0845-20260916/validation-001/report.json`，SHA-256 `e5116d4cdc4a87a88b778b79da589606f85b341435ba9fa435c51f8795aa6c9e`。離線測試沒有代替真板部署、斷電復原或十套客戶映像驗收。
