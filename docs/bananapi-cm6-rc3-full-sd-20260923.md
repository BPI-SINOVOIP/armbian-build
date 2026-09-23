# CM6 rc3 完整 SD 重燒後的實機驗證

2026-09-23，使用者重新燒錄並插回 SD 後，以 `bpi-pw-2` 上電，透過 UART 核對身分，再用已核對主機公鑰的 SSH 驗證。本紀錄屬於新卡的測試階段，與[先前在既有 SD 套用修正的結果](bananapi-cm6-hardware-20260923.md)分開保存。此輪尚不能判定整體驗收通過。

## 成品與開機身分

- 成品：`Armbian_Noble_bpi-cm6_gnome_vendor-sd_20260923-rc3.img`。
- 原始映像 SHA-256：`562cd7820b788df1a35e4763a23d585262684015c0fbb2921fc1fa23c0608766`。
- 實機 `/etc/bpi-k1-vendor.json`：`board=bpi-cm6`、`storage=sd`、`release_id=20260923-rc3`。
- 核心：`6.6.36-legacy-spacemit`；核心污染旗標為零。
- 根分區：`/dev/mmcblk0p6`，UUID `b420f97c-df24-5531-974c-582f4a977d96`；開機分區：`/dev/mmcblk0p5`，UUID `2b3957a6-2286-5464-ba31-8f22284cb925`。
- 根檔案系統擴容至 62,506,397,696 bytes。擴容服務記錄 `partition_changed=false`，不能據此宣稱由該服務修改 GPT。
- 已完成首次帳號、`zh_TW.UTF-8` 與 `Asia/Taipei` 設定。實機 DTB、私有藍牙 helper／韌體／設定及共用舊韌體 SHA-256 均與 rc3 固定資產相符。

本次涵蓋單一 CM6 的 SD 系統；新 eMMC 整包與 F3 未在此輪實測。

## 網路與藍牙

| 項目 | 結果 | 實測依據與限制 |
| --- | --- | --- |
| `eth0` | 基本通訊通過 | 1 Gbps 全雙工、DHCP、指定介面四次 ping 無丟包；8 MiB 上傳及下載各自退出碼為零、SHA-256 一致，收送錯誤與丟棄計數無增。 |
| `eth1` | 基本通訊通過 | 與 `eth0` 相同的獨立驗證。 |
| Wi-Fi | 資料完整性通過；接收丟棄待定位 | 連接使用者指定的 5 GHz 網路；兩輪各 8 MiB 雙向傳輸均完整且雜湊一致，ping 無丟包。兩輪各觀察到 `rx_dropped` 增加 1，因此嚴格檢查仍失敗。另一次十秒閒置也增加 1，隨後十次 ping 無丟包且丟棄計數未再增加，不能直接歸因於測試資料傳輸。 |
| 藍牙乾淨首開 | 未通過自動開啟 | 新卡有 `hci0`，但 HCI 的 rfkill 軟體封鎖為開啟、BlueZ 顯示 `Powered: no`。只解除該 HCI 的封鎖後，BlueZ 自動開啟。 |
| 藍牙解除封鎖後 | 基本功能通過 | 三次關閉／開啟、HCI 版本命令及掃描通過；掃描取得五個裝置。LMP 子版本為 `0x8a4c`。配對、資料傳輸、音訊未測。 |

新卡的藍牙結果不能由先前套件安裝後的冷開機結果取代。離線檢查 rc3 的 SD、eMMC 及準備用根檔案系統，均無 `/var/lib/systemd/rfkill` 狀態目錄；本輪沒有證據支持「映像帶入舊封鎖狀態」。唯讀 `debugfs` 證據另存 `rc3-offline-rfkill-state-check.json`。核心板級 rfkill 註冊與全域初始狀態的交互作用仍需修正及首次開機時序驗證。

後續為相機候選正常關機、斷電五秒再上電後，兩個有線介面均維持 1 Gbps，Wi-Fi 自動連回原測試網路，三介面各兩次 ping 無丟包；藍牙維持 `Powered: yes`，HCI 版本命令成功。這是解除封鎖後的持久狀態驗證，不能把首次開機缺陷改列通過。

## GPU

實機使用 `/dev/dri/renderD128`、GBM／EGL 與 GL ES shader 建立自己的 512×512 離屏 framebuffer，兩種圖樣各完整比對 262,144 個像素，容差為每色彩通道 1；兩次均零不符像素。

程序同時核對 `pvrsrvkm` 核心驅動、實際載入的 `libpvr_dri_support.so`／`libGLESv2_PVR_MESA.so`，以及 `PowerVR B-Series BXE-2-32` renderer。約五秒負載期間 GPU 使用率為 78–80%；前後驅動與韌體均為 `OK`，七項錯誤計數未增加。原有 `Server Errors=1` 保持不變。測試退出碼為零，支持 **GL ES 離屏硬體渲染及像素正確性通過**。

紀錄中的 `frames=81184` 是離屏繪製迴圈計數，不是桌面 FPS 或螢幕更新率。Mesa 另輸出兩個擴充 dispatch 警告，原始證據保留；本輪未驗證該兩項擴充。HDMI 擷取仍為青色畫面並有 EDID 失敗，不能列為桌面顯示通過。

## AI 運算後端

已在板上分別以獨立程序執行浮點矩陣乘法與量化卷積模型。每個 CPU 基準均使用兩份輸入、重複三輪，結果與獨立數學參考完全一致，逐節點追蹤也符合 CPU 後端。

初次 SpaceMIT 測試在建立 session 時出現 `DefaultLogger` 尚未註冊的錯誤，隨後回退為 `CPUExecutionProvider`。實際映射紀錄證實測試程式先匯入 `spacemit_ort`，使系統版與 Python 配套版的 ORT 同時載入。第二版改成官方範例的匯入順序：先 `onnxruntime`、後 `spacemit_ort`，沒有修改板端套件。

第二版初始化成功、只載入 Python 配套版 ORT，但兩個模型的逐節點追蹤仍全部派至 `CPUExecutionProvider`。結果雖與 CPU 及數學參考完全一致，仍判定 **SpaceMIT 後端驗證未通過**。不把 provider 清單的存在當成加速成功，也不據此宣稱獨立 NPU 或 IME 指令已執行。

另外補測固定權重的 `Conv＋Relu`、相同模型搭配 `SPACEMIT_EP_FLOAT16_MODE=1` provider 參數，以及真正的 QDQ 量化卷積。每個後端各執行兩份輸入，CPU 結果均與獨立參考完全一致；三組 SpaceMIT 測試仍全部派至 CPU，沒有 SpaceMIT 的計算節點證據。此版選項讀自 provider 參數，不能只設定同名環境變數。完整結果保存為 v3、v4、v5，未覆蓋先前失敗紀錄。

另以全域 ORT 詳細日誌執行零推論的 session 建立診斷：SpaceMIT EP 初始化與 262,144 bytes TCM 配置成功，但 `IsNodeSupported` 明確拒絕本次 Conv／QDQ 節點。詳細原因未列出，因此不能推定是資料型別、維度或核心驅動故障，也未證明所有模型皆無法加速。K1 的 IME 屬矩陣指令擴充；[官方規格](https://github.com/spacemit-com/riscv-ime-extension-spec)可供核對，本輪尚無特定 IME 指令執行證據。

此處的失敗判定限定於 SpaceMIT EP 派發；CPU 後端內部使用何種 RVV／IME 指令亦未追蹤，不能把回退結果概括為整套 AI 功能故障。完整能力階段分析保存於 `ai-host-prep/ai-acceleration-limitations-20260923.md` 與其 JSON 校驗附件。

## 溫度與測試限制

短測間觀察到 SoC 約 92–93°C；核心 `cpufreq-cpu0` 散熱狀態為 2，CPU 上限已從 1,600,000 kHz 降為 1,228,800 kHz。AI 第二版全程監看溫度，最高 94°C，未觸發本測試設定的 100°C 停止門檻。板端感測器的臨界關機門檻為 115°C，但不能因此把目前狀態當成適合持續效能驗收。

散熱片與風扇的實際安裝狀態尚待使用者確認；目前結果僅作短時間功能判斷，未進行長時間壓力測試或給出效能最佳化結論。

## 兩顆 NVMe

| 裝置 | 實測 | 結果 |
| --- | --- | --- |
| `KINGSTON OM3PGP4128P-AH`，128 GB | 開頭、中間及末尾各直接讀取 32 MiB，逐段重複兩次並比較 SHA-256。 | 三段一致；SMART 無重大警告、媒體錯誤與錯誤紀錄均為零且無增。沒有既有檔案系統，因此未格式化或執行寫入測試。 |
| `Fanxiang S500Pro 256GB` | 相同的三段重複讀取；另於既有乾淨 ext4 建立獨立 256 MiB 暫存檔，寫入、`fsync`，以直接讀取回讀並比對 SHA-256。 | 全部一致；SMART 錯誤無增，測試檔已刪除、分區已卸載，原分區配置保留。 |

SMART 中兩碟原有不安全關機次數為 128／31，本次測試前後未增加。上述工具包含 Python 雜湊與管線處理，量測時間不作為 NVMe 吞吐效能基準，也不代表完整容量寫入驗收。

## 兩路 CSI 相機

兩顆感測器均已實際辨識為 `imx415_spm`，並完成各自單路及雙路同時擷取。資料通道有實證，但雙路停止時有逾時警告，畫質與目標幀率亦未完成驗收，因此不能列為整體相機通過。`/dev/video0` 是編解碼器，本輪使用的是已核對的感測器、CSI 與 ISP 路徑。

固定官方 CM6 DTB 使用 `sensor0` 與 `sensor2`；rc3 的 `sensor2` 及其 I²C 5 未啟用，且 CSI PHY 分流屬性與官方不同。映像也未安裝官方 `k1x-cam`／`k1x-cam-lib` 相機配套。依官方 CM6 配置製作獨立相機 DTB 候選，保留 eth0 修正，只調整八個裝置樹屬性；逐項還原後與原 rc3 的完整排序 DTB 相同。候選 SHA-256 為 `91bb7a9cde6380e16d9dbc12161212aad768cf145de79dd2f51665366e0065c3`。

將官方 `k1x-cam=0.2.34`、`k1x-cam-lib=0.1.8` 解開至板端獨立測試目錄，未安裝套件或改動系統圖形函式庫。原 rc3 的 `sensor0` 已可辨識並擷取；正常冷開機載入候選後，`sensor2` 與 I²C 5 出現，第二顆亦成功辨識。已親自核對固定來源：成功路徑必須讀取並比對 `0x311a=0xe0`，不是單純建立節點或無條件回傳成功。

| 測試 | 實際資料與狀態 |
| --- | --- |
| `sensor0` 單路，原 rc3 DTB | 約 27 秒依設定的幀序號 500 正常停止；實測約 19.4 FPS，取得 1920×1080 NV12 與 3864×2192 RAW。 |
| `sensor2` 單路，相機候選 DTB | 約 27 秒、19.4 FPS；核心主要輸出 `aout0` 記錄 502 幀、502 正常、軟體／硬體錯誤均為零；NV12 與 RAW 完整。 |
| `sensor0＋sensor2` 同時，相機候選 DTB | 兩條實際 CSI／ISP 管線同時執行約 27 秒，各約 19.394 FPS；主要輸出 `aout0`、`aout1` 各 502 幀、502 正常、軟體／硬體錯誤均為零。兩路各保存一份 NV12 與 RAW，回讀 SHA-256 均一致。 |

每份 NV12 為 3,110,400 bytes、每份 RAW 為 11,293,184 bytes；不是保存了全部 500 幀。第 250 幀發出 RAW 請求、第 252 幀收到回呼，符合非同步擷取流程。測試關閉顯示與 tuning server，不依賴 HDMI。已親視 NV12 轉換的檢視圖：第一路偏灰漸層，第二路可見模糊邊界，但均缺少清晰測試場景；曝光、對焦、光線及畫質不能宣告通過。設定為 30 FPS，實測約 19.4 FPS，尚未驗證 30 FPS 目標。

雙路停止時，核心記錄 `pipe1` 等待 `stream off` 逾時，末尾另有已停止管線的 IRQ 訊息；使用者空間雖退出碼為零且完成釋放流程，仍保留為缺陷。第二路首輪串流的測試框架曾因 `/tmp` 與 `/root` 跨掛載點無法 `rename` 而中斷證據整理；原檔已完整保留，修正搬移方式後重跑所得第二輪才作為上表依據。

為確認停止警告是否阻止後續使用，在不重開機、不重新載入驅動的條件下，再開啟 `sensor2` 做短測：設定幀序號 100 停止，約六秒完成；主要輸出記錄 102 幀、102 正常、零軟體／硬體錯誤，另取得新 NV12／RAW，且本次沒有新增停止逾時。這支持第二路可重新使用，不能消除先前雙路停止警告。[固定核心停止路徑](https://github.com/BPI-SINOVOIP/pi-linux/blob/0d0af0d895251383baee939d44e523699e31889f/drivers/media/platform/spacemit/camera/vi/k1xvi/fe_isp.c#L2333)顯示它在等待 shadow close 完成中斷；SDK 關閉兩路的順序是待驗候選原因，尚未套用推測性修改。

候選僅載入本次實機記憶體，成功連回後已還原原 rc3 的 extlinux；**下次重開機會回到原 DTB，第二路不會自動維持啟用**。兩個官方套件仍僅解開在 `/root/cm6-camera-validation/`。本輪未把相機候選重新封裝到已交付 rc3 映像。

## 證據與重現

本機證據根目錄：`/media/pi/SMCI/bpi/cm6-hardware-validation-20260923/rc3-full-sd-reflash-20260923/`。

`測試摘要.json` 提供逐項狀態，`測試證據校驗.json` 固定 24 份指定結果的 SHA-256。結束時確認無遺留測試程序、無 NVMe 測試掛載、無暫存相機校正連結；核心污染旗標為零，沒有失敗的 systemd 單元，板子保留開機。

- 身分與首開：`baseline.json`、`bluetooth-fresh-default.json`、`rfkill-and-wifi-state.json`。
- 網路：`network-summary.json`、`wifi-recheck-network-summary.json`、`wifi-counter-localization.json`。
- GPU：`gpu-gbm-render-validation.json`；程式、固定編譯資訊及主機正負例自測位於 `gpu-host-prep/`。
- AI：`ai-board-validation.json`、`ai-board-results/`，及同名前綴的 v2 至 v5 結果、`ai-global-capability-diagnosis.json`；模型 SHA、runner 及主機自測位於 `ai-host-prep/`。
- 溫度：`thermal-pre-ai-v2.json`、`ai-v2-temperature.json`。
- NVMe：`nvme-health-and-camera-topology.json`、`nvme-data-validation.json`、`nvme-cleanup-check.json`。
- 相機：`camera-storage-baseline.json`、`camera-tools-details.json`、`camera-isolated-runtime-ready.json`、`camera-sensor0-detect.json`、`camera-sensor2-detect.json`、`camera-sensor0-results/`、`camera-sensor2-results/`、`camera-dual-results/`、`camera-reopen-results/`、`camera-host-prep/`。
- 相機候選冷開機：`camera-candidate-cold-uart.bin`、`camera-candidate-boot-state.json`、`camera-cold-connectivity-regression.json`、`extlinux-before-camera.conf` 與 `extlinux-camera-candidate.conf`。

原始紀錄保留於本機；登入資料、Wi-Fi 密碼與其他私有檔案不納入 Git、公開證據清單或雲端交付。既有 rc3 映像與 manifest 保持原樣，不以本次部分通過結果改成整體已驗證。
