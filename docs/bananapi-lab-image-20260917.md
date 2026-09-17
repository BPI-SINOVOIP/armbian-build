# 跨平台映像唯讀擷取

## 用途與界線

`tools/bpi_lab_image.py` 提供真正的原映像檔案讀取，不掛載檔案系統、不執行映像內腳本、不操作 UART、電源或實體媒體。家族模組透過 `reader.read_file()` 取得原配組件，不必各自重寫 XZ、分割及符號連結處理。

本版接受一般檔案形式的原始 IMG 或單串流 XZ，且必須是 MBR 單一 `0x83` 主分割及可由 `debugfs` 讀取的 ext 檔案系統。GPT、多分割、延伸分割、FAT、廠商容器及附加 XZ 串流均明確拒絕，不能當作所有平台映像格式已支援。

## 執行

```sh
python3 tools/bpi_lab_image.py /絕對路徑/映像.img.xz \
  --sha256 完整來源SHA256 \
  --output /絕對路徑/全新證據目錄 \
  --path /etc/armbian-release \
  --path /boot/armbianEnv.txt \
  --path /boot/boot.cmd
```

摘要須由操作者指定可信核對值。讀取 `.sha` 旁檔本身不證明來源真偽；本工具核對完整檔案內容，但不宣告來源已經簽章認證。

`--max-raw-bytes` 預設 32 GiB，可在 1 MiB 至 128 GiB 間明示。`--timeout` 預設 1800 秒，解壓、摘要及各查詢共用期限。XZ 解碼記憶體上限 256 MiB，單一擷取檔案上限 256 MiB。衍生分割所需空間另保留 64 MiB；這是檢查當時的空間，不保證其他工作不會同時用掉磁碟。

## 證據與安全

先完整驗證來源 SHA-256，再解壓至本次排他建立的普通暫存分割。解壓重新核對來源檔案身分，記錄原 IMG 及分割 SHA-256、MBR PARTUUID 與超級區塊 UUID；不複製完整 IMG。結束時移除本次暫存分割，保留擷取檔、查詢證據及 `extraction.json`，失敗也保留原因。程序遭 `SIGKILL` 或主機斷電無法保證清理，遺留檔須先核對所屬證據再處理，不能逕自重用。

映像路徑及符號連結只在映像根目錄內解析，不可連到主機 `/etc` 或 `/dev`。連結循環、向上超界、命令字元、裝置節點及過大檔案一律拒絕。來源及祖先不可為符號連結，不接受區塊裝置；輸出目錄必須全新，不覆蓋歷史證據。

外部解析器固定使用 `/usr/sbin/debugfs` 的 `stat`／`cat`，不用 `-w` 或校驗停用選項。依 [e2fsprogs 的 debugfs 手冊](https://git.kernel.org/pub/scm/fs/ext2/e2fsprogs.git/tree/debugfs/debugfs.8.in)，未指定 `-w` 為唯讀開啟。本工具限制輸出與期限，但不是能隔離解析器漏洞的沙箱；不應以高權限處理來路不明的映像。

## 驗證

```sh
python3 -B -m unittest discover -s tests -p test_bpi_lab_image.py
```

測試實際建立小型 ext4，涵蓋原始／壓縮映像、相對與絕對連結、目錄連結、隱藏及空檔、來源不變、查詢逾時／超界、錯誤摘要、截斷／附加串流、非 ext、連結逃逸／循環及既有輸出保護。這些是本機軟體回歸，不是實板開機或儲存壓力測試。

續作另提供 `SnapshotReader`，以固定擷取證據及逐檔摘要重播組件，不再次解壓 XZ；未查詢過的路徑明確阻擋，不假設缺檔。操作方式與不重讀原始映像的證據界線見[組件準備與重播](bananapi-lab-prepare-20260917.md)。
