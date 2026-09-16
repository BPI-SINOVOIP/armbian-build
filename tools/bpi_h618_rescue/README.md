# H618 獨立救援 initramfs

本工具在板上原生 AArch64 Linux 執行，只建立明確指定的新工作目錄。不是安裝器，不部署核心、DTB 或 initramfs，不更動原 `/boot`、`initrd`、`boot.scr`、`armbianEnv`、SPL／FIT、分割表或 eMMC。必須由主代理另行安排 RAM 中的一次性載入與實板驗證。

## 建置介面

需同時帶入 `tools/build_bpi_h618_rescue.py` 與完整的 `tools/bpi_h618_rescue/`，保持相對位置。原系統須已備妥 `initramfs-tools`、`curl`、`xz`、系統 Python 3、`wpa_supplicant`、`ip`、`rfkill`、OpenSSH、kmod 與 util-linux。工具不執行 APT。

```sh
python3 -B tools/build_bpi_h618_rescue.py \
  --kernel 6.6.75-current-sunxi64 \
  --output /root/bpi-lab/rescue-build-20260916-001 \
  --busybox /root/bpi-lab/rescue-inputs-20260916/bin/busybox \
  --busybox-sha256 61781806ad3650b0b9d2b3fc6971e2bffdca967af1a95375abd0578bafff14fb \
  --authorized-key /root/bpi-lab/rescue-inputs-20260916/rescue.pub
```

範例 SHA-256 是主代理已核對的解包後 **執行檔**（1846504 位元組），不是 `.deb` 的 SHA-256。拒絕動態 BusyBox、非 AArch64、缺少 applet、核心與執行版本／模組目錄／vermagic 不符、缺少現成模組索引及必要核心設定。公鑰可省略，此時 SSH 不啟動；不接受私鑰、公鑰選項或多把公鑰。

工作目錄必須不存在、父目錄已存在、全路徑無符號連結，且不可位於系統路徑。失敗保留現場，再次建置必須換新目錄。一般檢查限時 30 秒；建置限時 600 秒，逾時終止整個程序群組。

`mkinitramfs -d` 與 `TMPDIR` 支援見 [Debian Bookworm 說明](https://manpages.debian.org/bookworm/initramfs-tools-core/mkinitramfs.8.en.html)。本工具另使用私有 mount namespace 遮蔽 `/usr/share/initramfs-tools`，只保留系統 `hook-functions`、排序 helper 與救援 init，避免執行原系統共享 hook。該 namespace 只做目錄 bind mount，不掛載任何區塊裝置；若核心或權限不允許 namespace，建置直接失敗，不取消隔離重試。

## 輸出與大小

- `rescue-initramfs.img`：獨立 gzip initramfs，不附核心、DTB 或引導元件。
- `build-report.json`、`SHA256SUMS`：schema、核心、成品大小與 SHA-256、Python 標準函式庫及韌體大小、來源腳本雜湊、建置狀態。
- `mkinitramfs.log`、`archive-files.txt`：建置與內容核對證據。
- `conf/`、`share/`、`seed/`、`tmp/`：只在新目錄中的私有輸入與暫存；不作為成功指標。

為後續受限更新程式保留 `/usr/lib/python3.x`，排除測試、快取、桌面模組與第三方套件；所有標準擴充模組透過 `copy_exec` 補動態依賴。這會比純 shell 救援包大，實際大小須以板上建置報告為準，尚無真板成品大小證據。只保留 `brcmfmac43455-sdio.*` 與無線法規資料，不帶入其他晶片的整批韌體、桌面或 NetworkManager。

## 救援契約

init 只掛載 `proc`、`sysfs`、`devtmpfs`、`tmpfs` 與 `devpts`。不解析 `root=`，不掛載 SD／eMMC，不啟用 swap，不交接客戶系統。`BPI_RESCUE_READY` 行及 `/run/bpi-rescue-ready.json` 必須包含 `schema=bpi-h618-rescue-v1` 與匹配核心；此標記不代表 Wi-Fi、SSH、DDR 或媒體驗證已通過。

串口直接提供 root shell。模組事件程序先訂閱核心 uevent 再掃描 sysfs，處理冷插拔與熱插拔；只會載入包內模組。載入紀錄位於 `/run/net-events.log`。

主代理透過 UART 將網路認證放到 `/run/wpa_supplicant.conf`，設為 root 所有、權限 `0600`，再執行：

```sh
bpi-rescue wifi --interface wlan0 --config /run/wpa_supplicant.conf
```

認證內容不出現在命令列或建置包；設定、DHCP DNS、PID 與 Wi-Fi 日誌均在 `/run`。不要使用 `wpa_supplicant -K`，也不要把 `/run` 認證或日誌當公開證據。若 DHCP 失敗，保留當次 Wi-Fi 程序與紀錄供串口診斷，不自動重啟系統。

SSH 僅允許 root 專用公鑰，停用密碼、互動認證、PAM、轉送與 tunnel。每次開機在 `/run/ssh` 產生新 Ed25519 hostkey，UART 顯示 `BPI_RESCUE_HOSTKEY`、公鑰與指紋。主機必須由已核對 UART 取得公鑰後建立該次專用 `known_hosts`，使用 `StrictHostKeyChecking=yes` 與 `UserKnownHostsFile` 嚴格綁定；不要靠未核對的 `ssh-keyscan` 或忽略指紋。亂數等待不阻塞串口 shell。

## 下載、核對與盤點

```sh
curl --fail --show-error --location --proto '=https' --proto-redir '=https' \
  --connect-timeout 20 --max-time 600 --output /run/image.img.xz "$IMAGE_URL"
bpi-rescue verify --file /run/image.img.xz --sha256 "$COMPRESSED_SHA256" \
  --raw-sha256 "$RAW_SHA256" --raw-size "$RAW_BYTES"
bpi-rescue inventory
```

下載必須先核對 RAM 容量，完整壓縮檔位於 `/run`，不在 RAM 另存解壓映像。最小核對介面只接受不超過 512 MiB 的壓縮檔及不超過 32 GiB 的可信解壓長度，兩次 XZ 執行各限時 600 秒、解壓記憶體上限 256 MiB。核對包含完整壓縮 SHA-256、`xz --test`、完整解壓串流的長度及 SHA-256。可信雜湊與長度由主機另行提供，不從下載檔自行推定可信值；大型映像須另用主代理既有的受限串流工具，不在本版擴充部署介面。

盤點只讀 sysfs／proc，回報 MMC 類型、CID、容量、控制器實際路徑、唯讀旗標、掛載與 swap，不開啟區塊裝置，不根據 `mmcblk` 編號推定 eMMC。沒有設定整碟硬體防寫；root shell 本身有權限，後續受限更新程式仍須自行核對身分、授權、備份、範圍與回讀。

## 離線回歸

```sh
python3 -B -m unittest discover -s tests -p test_bpi_h618_rescue.py -v
```

測試只用暫存一般檔案、靜態契約與 mock 建置。沒有執行真實 mkinitramfs、SSH、UART、電源或媒體操作；通過不代表實板已建置或可開機。
