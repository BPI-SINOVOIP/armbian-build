# BPI-F3／BPI-CM6 官方格式 Armbian 首批外測指南

> 2026-09-18 更新：CM6 Titan eMMC 已回報 Code 20014 分區表匹配失敗；兩份舊 eMMC 候選撤回待查。先前離線通過不代表 Titan 可燒錄。兩份 SD 仍未驗證；原檔保留追溯。

日期：2026-09-16。

這批候選供他人實機測試，使用 Ubuntu 24.04 Noble、GNOME Wayland，以及按板卡分開配對的 GPU、AI 推論與 VPU 套件。軟體建置與離線檢查不等於實機通過；目前沒有本批候選的 UART 開機、GPU／VPU 工作負載或 AI 效能實測。先確認燒錄、獨立開機、首次登入及桌面，再進行後續 AI 模型環境與效能驗證。

原有 Armbian 映像與安裝流程仍獨立保留。本批「官方格式相容」指採用官方封裝契約，不代表由 Banana Pi／SpacemiT 發布或認證。

## 選擇正確成品

| 板卡 | 媒體 | 成品檔名 |
| --- | --- | --- |
| BPI-F3 | SD | `Armbian_Noble_bpi-f3_gnome_vendor-sd_20260916.img.zip` |
| BPI-F3 | eMMC | `Armbian_Noble_bpi-f3_gnome_titan-emmc_20260916.zip` |
| BPI-CM6 | SD | `Armbian_Noble_bpi-cm6_gnome_vendor-sd_20260916.img.zip` |
| BPI-CM6 | eMMC | `Armbian_Noble_bpi-cm6_gnome_titan-emmc_20260916.zip` |

每份成品應連同自己的 `manifest.json`、`verification.json` 與 `.sha256` 檔保存；四份同名 manifest 分別位於各自交付目錄，不能互換。

SD 的 `.img.zip` 內是整碟 GPT 映像，解壓後用整碟工具寫卡。Titan 的 `.zip` 內是分區映像、`fastboot.yaml`、分區描述與開機載荷，由 Titan 執行 USB 燒錄。**兩者不能改副檔名互換，也不要將 Titan ZIP 當整碟映像寫入。** 官方兩板文件也區分這兩種用途。[F3 官方操作](https://docs.banana-pi.org/en/BPI-F3/GettingStarted_BPI-F3)、[CM6 官方操作](https://docs.banana-pi.org/en/BPI-CM6/GettingStarted_BPI-CM6)

以 8 GB 以上 SD／eMMC 為準備起點，實際可用位元組必須不小於該份 `manifest.json` 的 `minimum_media_bytes`；標稱 8 GB 不保證足夠。解壓縮與 Titan 暫存也須留出空間，依實際成品展開大小安排。

在成品目錄核對雜湊與容量需求：

```bash
sha256sum -c *.sha256
python3 -c 'import json; m=json.load(open("manifest.json")); print(m["artifact"]["name"]); print(m["minimum_media_bytes"]); print(m["board"], m["storage"], m["root_uuid"], m["boot_uuid"])'
```

Linux 可用 `lsblk -b -o NAME,MODEL,SIZE,TYPE,MOUNTPOINTS` 查實際媒體大小。Windows 可在 PowerShell 以 `Get-Disk | Select-Object Number,FriendlyName,Size` 查詢。燒錄會覆寫所選目標，先保存需要保留的資料。

## Titan 工具與官方入口

2026-09-16 查閱時，F3 與 CM6 官方入門頁的安裝連結實際都指向 `1.0.35-beta`，連結顯示的 `latest` 字樣不能當成版本證據：

- [官方 Windows 下載入口](https://download.banana-pi.dev/d/ca025d76afd448aabc63/files/?p=%2FTools%2Fimage_download_tools%2Ftitantools_for_windows-1.0.35-beta.zip)
- [官方 Linux 下載入口](https://download.banana-pi.dev/d/ca025d76afd448aabc63/files/?p=%2FTools%2Fimage_download_tools%2Ftitantools_for_linux-1.0.35-beta.zip)

此處只記錄官網入口，尚未建立本批候選的 Titan 版本相容清單。保存實際下載檔名、SHA-256、工具內顯示版本與作業系統；若改用其他官方版本，須同樣記錄，不能只填「最新版」。F3 官網針對 2 GB DDR 修復情境另註明 Linux `1.0.35-beta` 的問題；遇到相關問題先回報，不自行改寫 EEPROM 或套用別板參數。[F3 官方說明](https://docs.banana-pi.org/en/BPI-F3/GettingStarted_BPI-F3)

## SD 首次測試

1. 核對成品板型，將 SD 版 ZIP 解壓為 `.img`。
2. 依官方流程，在 Etcher 選擇該 `.img`、確認目標卡，完成燒錄與驗證。
3. 斷電後裝卡，接 HDMI、鍵盤、網路與符合板卡規格的電源。CM6 另外記錄載板型號、版本及開機選擇設定。
4. 上電，記錄是否出現首次登入流程。若已有 eMMC 系統，登入後必須核對 `/` 與 `/boot` 的來源及 UUID，確認本次使用 SD；只有桌面出現不能證明啟動來源正確。

首次開機會執行檔案系統檢查、初始化與受控 GPT 根分區擴容，請預留數分鐘，並保留畫面與時間紀錄。舊 Armbian 擴容服務已停用；新服務只在板型、媒體、分區界線及根／boot UUID 全部相符時擴大第六分區。可用 `systemctl status bpi-k1-grow-rootfs.service` 與 `journalctl -b -u bpi-k1-grow-rootfs.service` 查看結果。若長時間無進展，記錄現象；有 UART 設備時附完整上電紀錄，沒有則標示未蒐集。

## Titan eMMC 首次測試

**本候選並未由封裝本身硬鎖 eMMC。** 官方 U-Boot 依硬體開機選擇腳位決定區塊裝置，NOR／NAND 模式可能走其他儲存媒體。燒錄前必須確認該板與 CM6 載板的 eMMC 開機設定，移除 SD、NVMe 與其他可寫入儲存裝置。這份指南不提供未經核實的 DIP／跳線值，按實際硬體版本的官方圖示確認；不確定時先回傳板卡、載板與開關照片。

1. 接妥電源與可傳資料的 USB 線，主機端只連這一片待測板，優先直接接主機 USB。
2. 依對應官方頁進入下載模式。F3 文件標示 `DOWNLOAD/FDL`；CM6 文件使用 `DOWNLOAD/FEL` 字樣，按實際載板標示核對。官方流程為斷電時按住下載鍵再接線／供電，或按住下載鍵後觸發重置。[F3 模式進入方式](https://docs.banana-pi.org/en/BPI-F3/GettingStarted_BPI-F3)、[CM6 模式進入方式](https://docs.banana-pi.org/en/BPI-CM6/GettingStarted_BPI-CM6)
3. 開啟 Titan 的單機燒錄流程，確認工具識別的 `VID:PID` 與實際待測板相符。保存識別畫面；識別失敗先檢查供電、線材、下載模式及主機驅動。
4. 匯入該板的 `titan-emmc` ZIP，等候解壓與配置載入，再執行燒錄。不要另外匯入 MTD／SPI 分區表或使用量產設定功能。
5. 保存完整 Titan 紀錄及完成／錯誤畫面。成功後完全斷電，解除下載狀態，維持 SD 與 NVMe 已拔除，再上電驗證 eMMC 獨立啟動。

若工具無法匯入、找不到分區描述、逾時或燒錄中止，回傳原始錯誤與版本；不要改 ZIP 檔名、刪除元件或手動寫 `boot0` 來掩蓋問題。本輪先取得可追溯的工具與載荷行為，再修正候選。

## 首次 Armbian 登入與桌面

沿用 Armbian 首次登入設定，依畫面完成管理員密碼、一般使用者、語系、鍵盤及網路設定。若顯示標準 Armbian 文字登入提示，預設帳號為 `root`、初始密碼為 `1234`，首次登入必須修改；這不是 Bianbu 的預設帳密。若只有 GDM 登入畫面、尚未完成使用者建立，可切換文字主控台完成首次設定後再回到桌面。

桌面使用剛建立的一般使用者登入 GNOME；在桌面終端機執行：

```bash
printf '%s\n' "$XDG_SESSION_TYPE"
uname -r
cat /etc/armbian-release
cat /etc/bpi-k1-vendor.json
findmnt /
findmnt /boot
lsblk -b -o NAME,SIZE,FSTYPE,LABEL,UUID,PARTUUID,MOUNTPOINTS
```

預期 GNOME 工作階段為 `wayland`，`/boot` 是獨立的 `bootfs`，根分區與開機分區的 UUID 分別對應該成品 manifest。F3 預期核心為 `6.18.37-current-spacemit`，CM6 為 `6.6.36-legacy-spacemit`；實際交付仍以 manifest 為準。

本輪先在交付配套下完成基線，不先執行全面套件升級、切換核心或使用 `armbian-install` 遷移媒體。既有 Armbian 原始分區安裝流程不適用這條官方 GPT 路線。

## 蒐集桌面與加速證據

在已登入的 GNOME 桌面終端機，以一般使用者執行已安裝的蒐集器，保留顯示工作階段的環境：

```bash
evidence_dir="$HOME/bpi-k1-evidence-$(date +%Y%m%d-%H%M%S)"
python3 /usr/local/sbin/collect_bpi_k1_runtime.py --output "$evidence_dir"
sudo dmesg --color=never > "$evidence_dir/dmesg-privileged.txt"
sudo journalctl -b -o short-monotonic > "$evidence_dir/journal-current-boot.txt"
```

蒐集器會記錄核心、板型、掛載、套件、GPU API、VPU 能力、AI provider、溫度及原始命令結果。某個工具缺少、權限不足或沒有圖形工作階段時，輸出會標明原因；不要因工具退出成功就填寫硬體通過。完整保留輸出目錄及 `report.json.sha256`，另外附桌面照片或短錄影。

初測請操作視窗、播放固定本機影片，記錄解析度、音訊、畫面異常、卡頓與崩潰。GPU 報告出現 `llvmpipe`、`softpipe` 或 `lavapipe` 應回報；只見 PowerVR 名稱仍不足以證明桌面合成與實際應用皆使用硬體。VPU 裝置、FFmpeg 解碼器或 GStreamer 外掛存在，也不等於影片實際採用硬體路徑。此輪未量測的效能與編解碼能力保持待測。

K1 的 AI 是 CPU 的 RVV／IME 加速路線，不是獨立 NPU。這批先整合執行環境；後續才固定模型、輸入資料、正確性門檻與效能測法。列出 `SpaceMITExecutionProvider` 只代表可載入，不能據此宣稱模型已加速。真正測試需要保存實際模型執行產生的 ONNX Runtime JSON profile、模型與資料雜湊、provider／算子分配、CPU 回退、結果正確性及時間。

已有真實執行的剖析檔時，可另建一份證據目錄附入；以下路徑須換成實際檔案，蒐集器本身不執行模型：

```bash
python3 /usr/local/sbin/collect_bpi_k1_runtime.py \
  --output "$HOME/bpi-k1-ai-evidence-$(date +%Y%m%d-%H%M%S)" \
  --ai-profile /path/to/onnxruntime_profile.json
```

## 回傳資料與初測判定

請按每個板卡／媒體組合各回傳一份紀錄：

| 欄位 | 要回傳的內容 |
| --- | --- |
| 成品 | 完整檔名、ZIP SHA-256、對應 manifest |
| 硬體 | F3／CM6、板號與版本、RAM 容量、eMMC 容量與型號；CM6 載板型號與版本 |
| 周邊 | SD 型號與容量、電源、散熱、螢幕與解析度、USB 線、接線及開機選擇設定 |
| 燒錄主機 | 作業系統與架構、Titan 下載檔名／SHA-256／實際版本、驅動狀態、完整 Titan 紀錄 |
| 啟動 | 是否移除其他媒體、冷啟動與重新啟動次數、登入與桌面結果、根分區／bootfs UUID |
| 加速 | 完整蒐集器目錄、桌面及影片現象；AI 尚未跑模型則明寫未測 |
| 失敗 | 從上電開始的步驟、首次錯誤、重現頻率、畫面；有 UART 則附完整文字紀錄，沒有則標示缺少 |

建議先各做 3 次完整斷電冷啟動及 1 次重新啟動，記錄每次結果；這只是初次問題篩查，不取代完整穩定性驗收。四個組合分別判定，不因一片板或一種媒體成功就推定其他組合通過。先完成「可燒錄、正確媒體獨立開機、首次登入、GNOME 桌面與基本證據可取得」，再安排完整 GPU／VPU／AI 與長時間驗證。
