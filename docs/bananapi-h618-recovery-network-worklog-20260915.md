# H618 救援與網路整合施工紀錄

日期：2026-09-15。分支：`bpi-h618-recovery-network-20260915`。

## 順序與範圍

1. 先讀取適用規則、既有 SRAM 狀態與 Berry 歷史證據；兩個子任務獨立查找設備線索及可重用工具。
2. 整合討論、來源與 N0 至 N8 計劃，提交 `7e25217e27ee2efa0db5afc057775a06298ac621` 並推送。
3. 審查補明人工選板不能繞過身分與 DDR 相容性，提交 `424adfedbaabe1272be0ef6cb7252efad992118a`；`git ls-remote` 確認一致後，才開始工具實作與設備連線。
4. 舊 Berry 端點無法到達，使用者再授權掃描內網；限定本機實體網卡直連的 `/24`，不掃 VPN、容器或其他子網。
5. 主機端分工實作產物清單與固定唯讀採樣工具，再由主代理執行測試、實際拒絕案例與最終核對。

本輪沒有控制電源、操作 UART、重開機、改寫板上 SD／eMMC、啟動 PXE／DHCP／TFTP 服務、重編映像或刪除既有產物。

## 內網查找結果

- 探測 253 個位址，排除網段位址、廣播位址及本機。SSH 僅探測 TCP 22，不執行全連接埠或漏洞掃描。
- ARP 收到 13 台設備回應；兩台有 SSH 回應。`nmap -Pn` 的主機上線總數不是實際在線統計，不能拿 253 當成在線設備數。
- 兩個 SSH 端點經既有主機金鑰驗證及唯讀命令，分別回報 Darwin 與 BPI-M2 Ultra，不符合本輪 Berry 目標。
- 未找到舊 Berry 的 IP、歷史有線 MAC 或 SSH 主機金鑰；這**不能證明板卡未上電**。可能涉及接線、另一子網、未啟用 SSH、改用其他位址／連接埠或重新燒錄。
- 未猜測密碼、未停用主機金鑰驗證、未接受陌生主機金鑰。正式工具採樣前後，`known_hosts` SHA-256 相同。
- 本機 mDNS 工具不接受預期的介面參數；沒有修改服務設定，改查快取仍無 SSH 名稱線索。

為免揭露內網拓樸，實際位址、命令、端點回應、MAC 與掃描 XML 只保存在本機：

```text
output/evidence/bpi-h618-recovery-network/N1-20260915/
```

這些資料已確認由 Git 忽略，沒有加入提交。公開摘要只記錄必要結果與限制。

## 工具交付

### 固定唯讀採樣

`tools/bpi_h618_observe.py` 僅透過 SSH 傳送固定的 Python 採樣程式，不提供任意命令或自動掃網功能。目標端需已有 Python 3；不會自動安裝套件、建立遠端檔案或升權。

```bash
python3 -B tools/bpi_h618_observe.py \
  --target "$BPI_TARGET" \
  --expected-profile bananapim4berry
```

`BPI_TARGET` 須為已核對的 `user@IPv4`。本工具使用既有 SSH 金鑰認證與 `known_hosts`，但不載入 SSH 別名設定；禁止互動猜密碼、金鑰自動更新、額外轉送與本機登入命令。傳輸截止時間 15 秒，輸出總量上限 1 MiB。

採集軟體 DT、核心、有限發行資訊、MMC 類型／大小／控制器路徑、網卡、掛載與 swap；不採集密碼、私鑰或晶片序號，不從 DT 宣告推定 HW_ID。三種配置的相容字串依本倉 `sunxi-6.18` DTS 固定比對，其他合法 BSP 的不同字串也可能被拒絕，須補來源及測試後再擴充。

成功僅表示軟體宣告符合與採樣欄位齊備；`physical_identity_verified` 與 `hardware_validation` 永遠為 `false`。未知板或不符配置回傳 `rejected`，必要採樣缺漏回傳 `incomplete`，傳輸失敗回傳 `error`，退出碼均不為零。即使只缺欄位，報告仍保留已取得的觀測值，不將缺項隱藏成成功。

DT 配置與 `/etc/armbian-release` 的 `BOARD`、核心系統及架構相衝突時亦拒絕；缺少 `BOARD` 為不完整。`lo` 的速度／驅動本來可能不適用，僅在明確標記 `unavailable` 時列入 `not_applicable_fields`，讀取錯誤仍算缺項。其他介面的缺項不會自動略過。

### 產物清單與核對

`tools/bpi_h618_artifacts.py` 使用 Linux 的 `O_PATH`、目錄描述符及 `/proc/self/fd` 核對一般檔案；不適用於沒有這些介面的主機。可建立新清單或驗證既有清單，不能燒錄、掛載、下載或執行產物。

```bash
python3 -B tools/bpi_h618_artifacts.py index \
  --root "$BPI_BUNDLE" \
  --profile bananapim4berry \
  --source-commit "$BPI_SOURCE_COMMIT" \
  --artifact kernel=Image --artifact dtb=board.dtb

python3 -B tools/bpi_h618_artifacts.py verify \
  --root "$BPI_BUNDLE" \
  --expected-profile bananapim4berry \
  --manifest-sha256 "$BPI_MANIFEST_SHA256"
```

變數須先指向已存在、已核對的檔案與完整提交碼；範例不是可開機套件配方。`index` 只會新建 `manifest.json`，已存在就拒絕，不覆寫原始產物。單檔上限 64 GiB、清單上限 64 KiB；路徑不能越界、重複、經過符號連結或指向裝置與管線。

`index` 記錄當次逐檔讀取的結果，不提供整個目錄的原子快照。後續使用前須再次 `verify`；未來執行器亦須使用已驗證的同一份檔案，不能假定校驗後路徑內容永不改變。

來源提交與板型是呼叫者宣告，工具不自行證明來源真實或韌體相容。必須從可信紀錄取得清單 SHA-256；同一不可信下載點提供的映像與雜湊不能建立信任。檢查通過不代表 XZ 串流有效、組件能開機或可安全更新，所有硬體、開機、來源認證與寫入授權旗標均為 `false`。

## 驗證紀錄

本機測試命令：

```bash
python3 -B -m unittest discover -s tests -p 'test_bpi_h618_*.py' -v
git diff --check
```

測試及最終證據摘要以[狀態表](bananapi-h618-recovery-network-status-20260915.json)為準。測試包含正常與拒絕案例、路徑／型別限制、串流成長、超長 JSON 整數、非同步程序逾時及中文命令列失敗輸出。子代理提出問題後修正，再由主代理重跑；不以子代理描述代替實際結果。

最終結果為 **67 項通過、零失敗、零跳過**：產物工具 28 項、採樣工具 32 項、既有 H618 IO 回歸 7 項。`final-validation.json` 保存工具／測試檔案雜湊與結果；SHA-256 為 `6630c53bce0b8baa5a5fc920a6cb3c94ed19da4ebe7cf915c9ee685b828df1da`。前一輪 60 項結果與原始掃描不覆寫，最終報告另存，避免遺失審查前後差異。

實際離線產物測試使用既有 `build-009` 的 `spl1-egon.bin` 與 `spl2-smoke.bin` 複本，只複製約數十 KiB，未重建或改動原證據。清單 SHA-256：

```text
b14609dcb40417372d8180ac1e43b2a39b26ada6908a692442b13232148398fb
```

實際網路拒絕案例使用已確認的 M2 Ultra 唯讀回應，要求 Berry 配置時工具回傳 `rejected`，沒有啟動任何更新。最後的主機端判定修正另以該已保存回應重播驗證，不冒稱又進行一次實板測試。舊 Berry 端點的連線失敗也保存為非零結果，不計為 Berry 實板驗證通過。

## 下一步

N1 的工具與探索可以交付；Berry 當前在線盤點未完成。需要定位實際 Berry 的供電、網線或目前網段後，再執行固定唯讀採樣。N2 仍須專用 SD 與目前 UART／電源／板號配對，不能因 N1 測試完成直接部署或重置。

可續接者先讀狀態表與本機證據，不重做已完成的工具建置，不重編全部 OS，也不誤用其他在線板卡。後續按計劃逐段更新狀態、提交及推送。
