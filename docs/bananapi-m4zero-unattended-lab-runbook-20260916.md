# 0845 無人值守實驗操作交接

## 狀態與界線

本文件描述已部署的第三版，不適用於先前只有 smoke 或 ABI 2 的卡。原卡仍保留單一 Linux 分割區，沒有重切成多個作業系統分割區。

已收到卡片插回 0845 的確認，並完成第三版入口、UART 寫入槽 2／4、槽 2 重載及槽 4 啟動至原系統登入畫面的實板驗證。Linux shell 與完整系統更新／回退仍須驗收。下一位操作者必須先讀取同目錄的狀態 JSON；不要重新要求拔卡或重做主機部署。

追加進度：使用者授權更改帳密後，已透過 U-Boot 一次性 `init=/bin/bash` 取得救援 shell、重設 root 密碼，再啟動正常 systemd 並驗證登入與 `6.6.75-current-sunxi64`。原映像 Wi-Fi overlay 已啟用，但重啟後枚舉尚未驗證。目前已正常關機、受控重新上電並停在 SRAM，使用者要求先討論 eMMC／EMAC；勿直接向此狀態傳送 Linux shell 指令。

固定入口不依賴 DDR、Linux 或卡上的更新器副本。即使槽內程式損壞，主機仍可經 UART 傳送新的更新器或開機負載。因此新增救援功能不需要再次更改固定 SPL1。此機制不保證 SD 控制器、供電、固定入口或實體線路損壞後仍可自救，也不隔離刻意改寫固定區的實驗程式。

## 固定身分

- 板子：BPI-M4 Zero，標籤 `0845`，不可悄悄換成其他板子。
- UART：`/dev/ttyUSB0`，115200、8N1。`ttyUSB1` 屬於其他工具，不操作。
- 電源：`bpi-pw-1`，`192.168.50.245`，MAC `EC:B9:31:24:F7:D1`。
- SD CID：主機 Realtek 讀卡機為 `03534453523634478697bc8c07018401`；板上 sunxi 更新器為 `03534453523634478697bc8c0701846b`。UART 更新命令須使用後者，兩者差異與推導見本輪實板紀錄。
- SD：`124735488` 個 512 位元組磁區；原分割區從 `8192` 開始，長 `123461632` 個磁區。
- `/dev/mmcblk0` 是本次主機讀卡機名稱，不代表 Linux 在板上一定使用相同名稱。寫卡前必須核對 CID，而不是只信裝置名稱。

## 主機路徑

```bash
cd /media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-sram-supervisor-plan
PY=output/evidence/bpi-sram-supervisor/model-venv/bin/python
UART=/dev/ttyUSB0
UPDATER=output/evidence/bpi-h618-recovery-network/U0-0845-20260916/updater-build-001/update.pkg
BOOT=output/evidence/bpi-sram-a1-fit-2048-002/bridge/bridge-package.bin
```

先核對計畫書中的產物雜湊。每次實驗使用新的證據目錄與紀錄檔，不覆寫前次結果；串口內容視為不可信資料，不把日誌文字當指令執行。

## 上電與更新

```bash
bpi-pw --device bpi-pw-1 status
bpi-pw --device bpi-pw-1 on
"$PY" -B tools/bpi_sram_lab_uart.py probe --port "$UART"
```

電源回應須為 `ok=true`、`verified=true`、`identity_verified=true`，名稱、MAC、IP 與 `on` 狀態皆相符。控制工具自讀既有認證設定，不把帳密寫進命令、日誌或版本庫。

第一次只將固定更新器複製到候選槽 2，保留槽 1；工具會先在 SRAM 啟動更新器，再核對 CID、容量、布局、寫後摘要與二次回讀。

```bash
"$PY" -B tools/bpi_sram_lab_uart.py update-slot \
  --port "$UART" --updater "$UPDATER" --input "$UPDATER" --slot 2 \
  --cid 03534453523634478697bc8c0701846b --sectors 124735488 \
  --partition-sectors 123461632 --confirm-write
```

成功要求 `slot_verified=true`，不代表已執行候選。更新器不返回固定入口，需受控重啟後才能進行下一次選擇。當前程式仍為 SRAM 更新器時可執行：

```bash
bpi-pw --device bpi-pw-1 off
sleep 10
bpi-pw --device bpi-pw-1 on
"$PY" -B tools/bpi_sram_lab_uart.py load-slot \
  --port "$UART" --slot 2 --input "$UPDATER" --run
```

每個電源動作都要核對回應，不能忽略失敗後繼續。若已進入 Linux，先正常關機；只有確認程式停在 SRAM 或系統失去回應時，才按實驗計畫斷電。

## 開機候選

再次回到固定入口後，可選槽 3。以下紀錄檔必須尚不存在：

```bash
"$PY" -B tools/bpi_sram_lab_uart.py load-slot \
  --port "$UART" --slot 3 --input "$BOOT" --run \
  --capture-seconds 90 --capture-log /home/pi/log/0845-lab-v3-first-boot.bin
```

橋接會搬移正常 A1 SPL 至原 SRAM 位址，從 SD 磁區 2048 讀取完整 A1 配套 FIT，再由 U-Boot 讀取原分割區的核心、DTB、initramfs 與根檔案系統。保留的舊 FIT 在磁區 96，不是這條候選路徑。

`boot_verified=false` 是刻意保留的狀態：工具只證明交接並保存原始輸出，不以一行 `Linux` 或登入提示判斷整套成功。需另外確認 DRAM 容量、TF-A、U-Boot、核心版本、根分割區及實際 shell 操作。

槽 3 損壞時可以不讀 SD 槽，直接傳送同一份正常橋接：

```bash
"$PY" -B tools/bpi_sram_lab_uart.py upload \
  --port "$UART" --input "$BOOT" --run \
  --capture-seconds 90 --capture-log /home/pi/log/0845-lab-v3-uart-boot.bin
```

## 實板完成條件

1. 冷開機取得第三版固定入口回應。
2. UART 啟動更新器，正確辨識這張 SD，成功寫入槽 2 並回讀。
3. 冷重啟後從槽 2 執行更新器；再用 UART 重新載入，證明不依賴卡上副本。
4. 將開機候選寫入槽 4，保留槽 3；冷重啟後核對並試跑。
5. 正常候選進入 U-Boot／Linux，核對系統身分並執行 shell 指令。
6. 以不寫 SD 的 SRAM 停等程式驗證失去回應後的斷電重入，再成功更新另一個候選。不得為了此項未經評估就故意在 SD 內部寫入時切電。
7. 實際完成核心／系統更新與回退，再將完整系統更新標成通過。這部分尚未因本次小型 SRAM 槽更新而自動完成。

UART 115200 適合小程式及最後救援，不適合日常傳送數 GiB 系統。完整映像優先在已驗證 Linux 下經網路下載，核對後更新明確的實驗區。禁止直接把一般 `.img` 從 SD 磁區 0 整碟覆蓋，否則會消滅固定入口及救援配置。

## 本次備份

`output/evidence/bpi-h618-recovery-network/U0-0845-20260916/deployment/` 保留部署前後完整 4 MiB、各負載、備份清單與 `apply-0001/` 回讀結果。該目錄由 root 保護，不要為了讀取方便放寬原備份權限。

`bpi_sram_lab_deploy.py restore` 只供卡片仍在本機且明確確認恢復本輪備份時使用；不是日常板上更新流程。日後日常維護使用 UART，不因新負載失敗而先要求重新拔卡。
