# CM6 官方格式 SD 實機驗證

2026-09-23，透過 `bpi-pw-2`、CM6 UART 與 SSH 實測。板端為 4 GiB RAM、16 GB eMMC 的 CM6；本輪從已完整燒錄並回讀校驗的 64 GB SD 開機，核心為 `6.6.36-legacy-spacemit`。尚未覆寫板上 eMMC 或 NVMe。

## 開機與網路

SD 已完成首次帳號設定、繁體中文與時區設定，根檔案系統擴容至 62,506,397,696 bytes。正常重開機及正常關機後斷電五秒再開機均成功。第一次上電前主機電源清單的 `bpi-pw-2` IP 已失效；依原 MAC 重新探索並更新本機設定後，才開始有效的供電與開機驗證。

原系統的 `eth0` 由 NetworkManager 管理，但核心回報 `phy_poll_reset failed: -110`。`nmcli device connect eth0` 與 `ip link set eth0 up` 均失敗；`eth1` 則正常。這已將故障縮小至 PHY 初始化，不能以新增 NM 自動連線設定取代硬體修正。

比對官方 CM6 BSP 後，只在原 DTB 的 `/soc/pinctrl@d401e000/gmac0_grp/pinctrl-single,pins` 末尾加入 `<0xb8 0 0xb040>`，設定 GPIO45 的重設腳位。其餘裝置樹語義完全保留。

- 原始 DTB SHA-256：`6d8db2aa3dc0a106190052316a0839cf9dda6a64b41ff6fe8fcc317f96a46f67`。
- 修正 DTB SHA-256：`f0b9795fda72868cd2bc9a6db0cf5aa2f15ea494f114a5b42adfc1fa931962c1`。
- 同核心暖重啟及冷開機後，兩埠均成功連接 PHY、協商 1 Gbps 全雙工並取得 DHCP 位址。
- 兩埠各自指定介面完成四次閘道 ping，皆無丟包；另各完成 8 MiB 下載與上傳，內容雜湊一致。介面收送計數確認流量經過指定網口，錯誤與丟包計數皆未增加。這是基本通訊驗收，不是長時間壓力或吞吐效能測試。

[離線修正工具](../tools/bpi_cm6_ethernet_dtb.py) 會核對來源 SHA 與板型、拒絕覆寫，並在還原唯一變更後比對完整 DTB 語義。[官方格式封裝器](../tools/package_bpi_k1_vendor.py) 已接入，將新檔放在 `/boot/bpi-k1-vendor/k1-x_bpi_cm6-eth0-reset.dtb`，並由 extlinux 與核心更新入口使用同一路徑。原 DTB 保留在套件管理目錄。獨立路徑已驗證可避開 `linux-dtb` 同版重新安裝時刪除整個版本目錄的行為；F3 與原有 Armbian 格式不套用本修正。

本輪僅驗證單一 CM6 與 SD 啟動；不可據此宣稱 F3 或新 eMMC 整包已實機通過。原 eMMC rc2 的使用者燒錄／啟動回報另見[原紀錄](bananapi-cm6-connectivity-20260922.md)。

## 藍牙

原系統沒有 HCI 控制器。安裝 `bpi-cm6-bluetooth_0.1.0~20260922rc2_riscv64.deb` 後，工具正確選到 `ttyS1`，建立 Realtek HCI 控制器。暖重啟與冷開機後均可開啟並掃描附近裝置。

進一步測試發現，關閉藍牙後再次開啟可能出現 `0x1003` 逾時；重新啟動 `bpi-cm6-bluetooth.service` 可恢復掃描。第一次重開成功後，閒置再送命令也觀察到延遲，因此不能把問題限定為再次 close／open 的瞬間；這份紀錄不代表已測出「從未重開也會觸發」。

只調整 H5 Reset 時機、只將 UART 的 `power/control` 設為 `on`，均未解決問題，測試後已還原。另一次重現期間的 IRQ 探針量測記錄 20 次進入及完成，最長 266,831 ns、超過 1 ms 次數為零、探針遺漏為零；該次資料不支持以 IRQ 內長時間忙等解釋逾時。DEBUG 模組與探針均已卸載，後續冷開機的核心 taint 為零。這些診斷候選沒有納入正式修正。

固定核心、原 helper、韌體設定檔與 UART 速率，只替換 `spacemit-uart-bt` 2.8 隨附的 `rtl8852bs_fw` 後，連續三次關閉／開啟、35 秒閒置後命令、掃描三個附近裝置及正常關機後真正斷電再開機均通過。控制器 LMP 子版本由 `0x881a` 變為 `0x8a4c`。這支持採用官方配套韌體作為本板的修正方向；韌體為二進位內容，本輪未確認其中哪個內部機制造成或修正舊版延遲。

正式 `bpi-cm6-bluetooth_0.1.0~20260923rc3_riscv64.deb` 已實際安裝。helper 與兩個韌體資產使用 `/usr/lib/bpi-cm6-bluetooth/` 私有目錄，helper 原始碼僅另改兩個搜尋目錄定義；Armbian 共用 `/lib/firmware/rtlbt/rtl8852bs_fw` 已還原為原 SHA-256 `af469c0df2af4d6a8721f442fef356a76ec4b72a605d53faffc1fa0cad595de5`。在此條件下，正式套件已完成三次關閉／開啟與掃描，並另行完成安裝後的真正冷開機驗證。

正式套件冷開機先正常關機，確認 UART 已輸出 `Power down` 才斷電，五秒後再上電。21,323 bytes 的 UART 紀錄確認實際載入獨立的 eth0 修正 DTB；啟動後核對套件版本為 `0.1.0~20260923rc3`，共用舊韌體的雜湊與上述原值相同，私有新韌體與 helper 則與下表一致。核心 taint 為零，藍牙為 `Powered: yes`；HCI 讀取版本命令成功，完整執行耗時 0.063 秒。兩個網口均為 1 Gbps 且有 UP，各指定介面 ping 四次、丟包為零。這次結果直接對應正式私有套件，與前一階段原 helper 的韌體比較分別留證。

| 固定資產 | 容量（bytes） | SHA-256 |
| --- | ---: | --- |
| `rtl8852bs_fw` | 181658 | `a3a203199fd7cafaa5bd22d34b08b7ff5ad3ee0339d7a2bc95e02577d9d3d88a` |
| `rtl8852bs_config` | 33 | `efa8915db59c5bc30aaa23e1f264656bbf425fcaf091db0954c27752a1d8f7a0` |
| 私有 `rtk_hciattach` | — | `51a308418c5a860b6ed536633422423aebd90ce300f844829caf0f298d62b4ab` |
| rc3 Debian 套件 | 3357488 | `f5abe051e30b8a8a3a0510ae5f715729fcc1e226651f92a100883e02a8683339` |

韌體來自[官方 `spacemit-uart-bt_2.8.tar.xz`](https://archive.spacemit.com/bianbu/pool/main/s/spacemit-uart-bt/spacemit-uart-bt_2.8.tar.xz)，來源封存檔為 3,200,420 bytes，SHA-256 為 `a5c23e924d660143dd6d5e5e854f9451d4372f7760a8daffec65e4d42f725fed`。所選韌體與設定均與既有 CM6 板廠母映像一致；[來源鎖](../config/spacemit-k1-connectivity/source-lock.json) 保存成員路徑、大小及雜湊。套件保留原始 `copyright` 作為不可變追溯資料；上游對韌體標示 `__NO_COPYRIGHT_NOR_LICENSE__`，故授權狀態如實記為 `upstream_unspecified`，不能將 helper 的 GPL 套用至韌體。

單一韌體變因證據包括 `bluetooth-official-firmware-toggle-1.json` 至 `-3.json`、`bluetooth-official-firmware-idle-command.json`、`bluetooth-official-firmware-scan-final.json` 與 `official-firmware-cold-*.json`。正式套件證據包括 `bluetooth-rc3-package-install.json`、`bluetooth-rc3-package-toggle-1.json` 至 `-3.json`、`bluetooth-rc3-package-idle-command.json`、`bluetooth-rc3-package-scan.json`，以及 `rc3-package-cold-shutdown.json`、`rc3-package-cold-state.json`、`rc3-package-cold-command.json`，皆位於下述本機證據目錄。配對、資料傳輸與藍牙音訊尚未驗證。

## rc3 交付候選與驗證範圍

`20260923-rc3` 的 CM6 SD 與 Titan eMMC 兩份候選已完成封裝，封裝程序均以退出碼 0 結束。候選納入上述 eth0 DTB 修正與正式 rc3 藍牙套件；F3 未變更。成品位於 `/media/pi/SMCI/bpi/f3-cm6-vendor-20260923-rc3/output/`，下表大小及雜湊取自各自的 `manifest.json`，本次文件更新未重新計算大型成品雜湊。

| 媒體 | 成品檔名 | 容量（bytes） | SHA-256 |
| --- | --- | ---: | --- |
| SD | `Armbian_Noble_bpi-cm6_gnome_vendor-sd_20260923-rc3.img.zip` | 1713117447 | `228cef7811e763cbf0b8f0d57d55ba78f79f87f2f3bf5e7f59d5d53bd94c78a7` |
| eMMC | `Armbian_Noble_bpi-cm6_gnome_titan-emmc_20260923-rc3.zip` | 1713113819 | `06e02aa4bfb309535e7858add46d20745f5fbf65f94ccfd21e4acf974e2d55a3` |

兩份 manifest 的開機設定契約與已安裝加速套件預檢通過；SD 另有 GPT、MBR 與分區範圍離線檢查通過。這些結果只涵蓋封裝與靜態完整性，manifest 仍維持 `candidate_unverified`、`hardware_validation=pending`。

本次硬體測試是在已燒錄的 rc2 SD 系統逐項套用相同修正，已涵蓋正式私有藍牙套件的冷開機；新 rc3 SD 整包尚未重新燒錄，新 rc3 eMMC 整包也尚未經 Titan 重新燒錄驗證。GPU／AI／VPU 負載、藍牙配對／傳輸／音訊仍未驗證；HDMI 僅取得黑青畫面並伴隨 EDID 失敗，不能列為桌面顯示通過。

## 擷取與證據界線

HID 狀態查詢已核對 CH9329 回覆及被控 USB 枚舉。HDMI 擷取有收到 1920×1080 畫格，但畫面尚未清楚呈現桌面；核心同時回報 EDID 讀取失敗。GNOME 服務正在執行、PVR 驅動與韌體狀態可讀，仍不足以證明完整桌面、GPU 效能或 AI 加速通過。

完整本機證據目錄為 `/media/pi/SMCI/bpi/cm6-hardware-validation-20260923/`。原始 UART、SSH、網路與掃描紀錄留在本機；測試帳密獨立存於權限 `0600` 的檔案，不納入交付、Git、Drive 或公開校驗清單。
