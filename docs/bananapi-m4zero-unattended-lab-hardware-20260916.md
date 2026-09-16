# 0845 第三版救援入口實板紀錄

## 追加管理權限恢復

本節為後續 `hardware-002/` 的結果；以下第一批 `hardware-001/` 保留原始時間點，不回寫失敗證據。

- 使用者明確允許更改帳密與測試系統。UART SysRq 未取得核心回應，故經受控斷電重啟進入 SPL1；此次不是已確認同步完成的正常重啟，不冒稱檔案系統檢查通過。
- 從槽 3 啟動 A1 配套開機鏈，在 U-Boot 攔截自動開機，僅於 RAM 設定 `extraargs=init=/bin/bash console=ttyS0,115200 loglevel=7`。未執行 `saveenv`，未改固定入口。
- root 救援 shell 實查核心為 `6.6.75-current-sunxi64`，SD 根分割區 UUID 為 `1e69af2b-2278-40d8-85b0-cc124a655a89`。原 `/etc/shadow` 已在板上受限目錄 `/root/bpi-lab/auth-backup-20260916/` 保存，未將密碼或雜湊發布至版本庫。
- `passwd root` 成功後，主機等候函式因一次讀入包含後續提示而逾時，並非密碼更新失敗；接續核對 `passwd -S root` 後，以 `exec /sbin/init` 啟動正常 systemd，透過串口成功登入 root。原始紀錄 `normal-init-001.bin` 的 SHA-256 為 `aab93911924ffe2581626117c9d9d5276ca89d8db818eef92b46855e34ed8f2d`。
- `uname -a`、SD CID、容量、分割區起點及長度均吻合；記憶體總計 3,927 MiB，根檔案系統約有 51 GiB 可用。可見約 29.1 GiB 的 eMMC `/dev/mmcblk1`，沒有寫入。`systemctl --failed` 為零；核心錯誤層級輸出有兩筆既有顯示 `debugfs` 重複目錄，不宣稱完全無錯誤。
- 原系統只有 `lo`，但隨附 `sun50i-h616-bananapi-m4-sdio-wifi-bt.dtbo`。備份 `/boot/armbianEnv.txt` 後僅新增 `overlays=bananapi-m4-sdio-wifi-bt`，以暫存檔同步後替換、核對 SHA-256：`61ffde93c332e52657e4c5b75400df2428d9938f3f9f19147ee0d01c6371999af`。沒有將一次性救援參數寫入開機檔。
- 已執行 `systemctl poweroff` 並取得卸載、同步及 `reboot: Power down`。受控斷電約十秒後重新上電，尚未從固定 SRAM 入口選擇下一候選；此時使用者改為討論 eMMC／EMAC，所有硬體測試暫停且 UART 已釋放。

一般冷開機登入、Wi-Fi 啟用後連線、完整系統更新／回退及 EMAC 實體連線仍未完成。本次系統核對不代表 DDR 長時間壓力驗證。

## 範圍與結論

使用者確認 SD 已插回 BPI-M4 Zero `0845`。本輪透過 `/dev/ttyUSB0` 與具名電源 `bpi-pw-1` 完成 UART 更新、卡上候選重載及正常開機至登入畫面，全程未拔卡、未重寫 SPL1、未操作 `ttyUSB1` 或其他插座。

原始證據目錄：`output/evidence/bpi-h618-recovery-network/U0-0845-20260916/hardware-001/`。各次失敗與成功分開保存，不覆寫部署備份。

| 項目 | 實際結果 | 證據檔 |
| --- | --- | --- |
| 本機卡片與串口 | `/dev/mmcblk0` 已不存在；`ttyUSB0` 路徑符合且無其他程序持有 | 本輪主機操作紀錄 |
| 固定入口 | `abi=3`、`board=06180001`，六種能力清單核對通過 | `probe-002.log`、`cold-boot-002.bin` |
| UART 更新器 | 成功在 SRAM 執行，宣告 DDR 關閉；SD 容量與分割區相符 | `update-slot2-002.log` |
| 更新槽 2 | `committed=1`、`slot_verified=true`，37,888 位元組完整封包摘要及二次回讀通過 | `update-slot2-002.log` |
| 卡上重載槽 2 | 冷重啟後從 SD 讀取並執行，取得新的 `update-ready` | `reload-slot2-001.log` |
| 更新槽 4 | 重新從 UART 執行更新器，68,608 位元組開機封包提交及回讀通過 | `update-slot4-001.log` |
| 正常開機 | 槽 4 → A1 SPL → 配套 FIT → 原系統登入畫面 | `boot-slot4-001.bin`、`boot-slot4-001.log` |
| Linux shell | 尚未登入成功，不宣告 `uname`、根分割區與健康檢查通過 | `linux-login-001.bin`、`linux-login-002.bin` |
| 故障重入與完整系統回退 | 本輪尚未完成，不能由候選槽更新成功推論 | 尚待實測 |

最後一次電源查詢為開啟，名稱、MAC、IP 與實際狀態核對通過；板子停在 Linux 登入提示，未對執行中的 Linux 直接切電。

20 項證據一致性檢查通過，涵蓋槽位結果、開機紀錄摘要、具名電源身分與尚未驗證的狀態旗標。結果為 `evidence-verification-001.json`，SHA-256：`eeaf2a3ab9c29479acb06b2259b528bd87d465b20a62a19dec202f494412508a`。這是證據檢查數量，不是額外 20 次硬體測試。

## 開機證據

本次輸出包含 A1 的 `clk=792` 設定、DDR 訓練階段通過及 `size_mib=4096`；之後進入 TF-A `v2.12.9`、U-Boot `2026.01`，讀取原卡 `/boot/boot.scr`、DTB、核心與 initramfs，出現 Armbian `25.2.2 Bookworm` 的 `ttyS0` 登入提示。

這證明本輪正常橋接與配套開機鏈已到使用者空間，不是 DDR 長時間穩定性或多板驗證。原卡部署前曾確認核心為 `6.6.75-current-sunxi64`，但本輪尚未取得執行中的 `uname -r`，因此不將該歷史值當成本輪核心驗收結果。

原始開機紀錄長度為 4,955 位元組，SHA-256：

`d957ce493264d64562a72af7bedfc16ff924ed476ac0e097a360b0e94f5749c5`

## CID 差異與嚴格守門

第一次更新被主機拒絕，尚未傳送槽位寫入命令。差異如下：

| 來源 | CID |
| --- | --- |
| 主機 Realtek 讀卡機 | `03534453523634478697bc8c07018401` |
| 板上 sunxi MMC | `03534453523634478697bc8c0701846b` |

兩者前 120 位元完全相同，只有 CRC7 與結束位元所在的最後一個位元組不同。主機實際驅動路徑為 `rtsx_pci_sdmmc`；[Linux v6.6 驅動](https://github.com/torvalds/linux/blob/v6.6/drivers/mmc/host/rtsx_pci_sdmmc.c#L300) 在 R2 回應中將該位元組填成 `1`。板上 U-Boot sunxi 驅動直接讀取四個回應暫存器，保留該位元組。

對前 15 位元組以 CRC7 多項式 `0x09` 計算，得到 CRC7 加結束位元 `0x6b`，與板上回應一致。結合相同容量、MBR 起點與長度，才採用推導後的完整板上 CID 重新執行；沒有直接接受任意新 CID，也沒有遮罩、放寬或移除主機的相等檢查。

日後 `update-slot` 使用尾碼 `6b` 的板上 CID；主機讀卡機部署紀錄與舊備份中的 `01` 保留，不回寫歷史證據。

## 失敗紀錄

1. `probe-001.log`：初次上電後探測逾時；第二次成功。尚不足以斷言是啟動時序問題，後續冷開機改為先確認已就緒再送命令。
2. `update-slot2-001.log`：CID 完整值不符而拒絕，尚未寫入。上節已查明兩端表示差異。
3. `media-001.bin`：直接查詢使用了不符合既有交接的 nonce，被更新器拒絕；之後使用原 nonce 成功只讀查詢，沒有改韌體繞過契約。
4. `linux-login-001.bin`：主機等待英文密碼提示，但系統使用中文提示，因此未送密碼便逾時。
5. `linux-login-002.bin`：接續該登入流程送入先前提供的密碼後，系統回報登入錯誤。未重設密碼、未修改認證設定、未反覆嘗試其他密碼；已詢問目前有效帳密。
6. 使用者另行指定嘗試 `pi` 帳號後，主機正確辨識中文密碼提示並送入指定密碼，系統仍回報登入錯誤，未執行 shell 或 `sudo` 檢查。紀錄為 `linux-pi-login-001.bin`，SHA-256：`8d42651cd48627c556e00fb08db489362ed5856359867c69526e888ceb4c3ffd`。此追加紀錄不包含在先前已封存的 20 項證據檢查中；原報告不覆寫。UART 已釋放，板子保持在登入提示；此回應不能單獨證明帳號不存在。

## 後續執行順序

1. 取得有效登入資料後，直接接回目前 Linux，不重做寫卡及已通過的槽位驗證。核對 `uname -r`、板型、記憶體、根來源、CID、布局、空間、網路及核心錯誤。
2. 正常關機後，驗證不寫 SD 的 SRAM 停等、受控斷電重入與再次更新；不在 SD 寫入中故意斷電。
3. 保留安全系統 A 的 `/boot`、模組與根系統，在同一分割區新增版本化候選目錄；候選使用配套核心、DTB、initramfs 及獨立唯讀根映像，寫入層放 RAM。發布前後都核對摘要與空間，不重新分割。
4. 由主機在 U-Boot 一次性選擇候選 B，不儲存自動重試的預設；驗證 B 的版本與 shell 後正常關機，再回 A 並核對保護資料。

第 3／4 項尚缺受限系統更新交易、候選 initramfs 整合與一次性系統選擇器，不能將既有 `update-slot` 工具宣稱為完整作業系統更新器。槽 3／4 共用 FIT 和原根系統，不是兩套隔離系統。同卡同分割區仍共享檔案系統及 SD 硬體故障風險。
