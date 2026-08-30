# Banana Pi R1 歷史映像封存 L2 證據

日期：2026-08-27

## 結論

舊板號 `lamobo-r1` 在提交 `556a14dde79770826d825ef845c430c754d55f9f`
留下五個發行版、CLI／XFCE 共十組完整映像、旁車 SHA-256、映像說明與建置
日誌。本次以機器契約重新核對這些既有檔案，十組 SHA-256 與 `xz -t` 均通過，
十份日誌均包含映像完成、SHA-256 計算、重複建置命令及容器成功結尾。另以
唯讀 loop 與 `mount -o ro,noload` 檢查 Trixie CLI 的分割區及必要檔案。

這些證據只足以標示為 **L2（歷史／封存）**。它不是本次新建置，不代表目前仍受支援，
沒有實機驗證，也不得作為公開發布依據。現行板檔
`config/boards/bananapir1.eos` 必須維持 EOS；本次沒有修改板檔或復活 R1 支援。

## 證據來源

| 類別 | 唯讀來源 |
| --- | --- |
| 十組映像、旁車雜湊與說明 | `/media/pi/SMCI/armbian/bpi-v26.2.1/output/images/2026.05/bpi-r1` |
| 十份建置日誌 | `/media/pi/SMCI/armbian/bpi-v26.2.1/output/bananapi-2026/20260520T131850Z/lamobo-r1-*.log` |
| 舊板檔 | `556a14dde79770826d825ef845c430c754d55f9f:config/boards/lamobo-r1.eos` |
| 現行板檔 | `config/boards/bananapir1.eos` |

上述大型證據不加入 Git。Git 只保存其名稱、大小、SHA-256、結構契約、驗證工具
與本文件。

## 歷史映像矩陣

| 發行版 | 型態 | XZ 大小 | 解壓大小 | XZ SHA-256 |
| --- | --- | ---: | ---: | --- |
| Bookworm | CLI | 433316520 | 1723858944 | `87a1cc4dba40a6ee2eb0dd6d126edf6933a9c6a87d9142eab01c2c62f5da1e43` |
| Bookworm | XFCE | 964207956 | 4043309056 | `859a4c108903cbddbfb1cedbfa5c162b08dab75beabf6a2035ea3cd4cf2c486c` |
| Jammy | CLI | 434382396 | 1648361472 | `f980c1a2813f479c96d81997e9ac4fccecb1d3294e81c91f5171e48d2c436212` |
| Jammy | XFCE | 815728700 | 3447717888 | `fe281630763df2b08785322041aaa0a6ecfcccb62441f62322aa002df6d6e7c6` |
| Noble | CLI | 429747620 | 1719664640 | `bc71a6da701fc2734af338a7641b1ce513ab26a8f0f8733a44bf3c102466e26c` |
| Noble | XFCE | 872966448 | 3770679296 | `43dc4c36696a0021d9b2c476504fd5e7666883e45e2f4386c928014bea5ed71a` |
| Resolute | CLI | 445070936 | 1845493760 | `81919ff1f483ff4e672d929cc89aa91fdd915a617c84ec041e2bb2037330e00e` |
| Resolute | XFCE | 932715580 | 4273995776 | `c1693081c3c059c2e610cff1f756904bddb77fcfc90cc143e76adca30ceb3fd7` |
| Trixie | CLI | 444326156 | 1828716544 | `ca2c4ca6d9c6c73e1276bb808d689945bc1ac61abb5d7c0a4c82a67e07e5f8d8` |
| Trixie | XFCE | 1039652336 | 4647288832 | `41869948e883e7bdf49a943769dec25b19b72bd06720792dc5845e985e676f13` |

十組 XZ 合計 6812114648 bytes，契約中的解壓大小合計 28949086208 bytes。
每個 `.img.xz.sha` 的路徑前綴指向當時的 `output/images`，驗證器只正規化其
basename，仍要求記錄的 SHA-256、映像名稱、旁車檔本身大小與旁車檔 SHA-256
全部相符。每份 `.img.txt` 也鎖定大小與 SHA-256，並要求下列欄位一致：

```text
Vendor: Armbian-unofficial
Revision: 26.05.0-trunk
Board: Lamobo-r1
Kernel: Linux 6.18.32 (current)
Build date: 21.05.2026
Sources: git@github.com:BPI-SINOVOIP/armbian-build.git
Sources rev: 556a14dde
```

## 舊新板檔等同性

| 關鍵欄位 | 舊 `lamobo-r1` | 現 `bananapir1` | 判定 |
| --- | --- | --- | --- |
| `BOARD_VENDOR` | `sinovoip` | `sinovoip` | 相同 |
| `BOARDFAMILY` | `sun7i` | `sun7i` | 相同 |
| `INTRODUCED` | `2014` | `2014` | 相同 |
| `BOOTCONFIG` | `Lamobo_R1_defconfig` | `Lamobo_R1_defconfig` | 相同 |
| `KERNEL_TARGET` | `current,edge` | `current,edge` | 相同 |
| `CONFIG_DRAM_CLK` | `384` | `384` | 相同 |

`BOARD_NAME` 與板級函式名稱因官方板號遷移而不同，不屬硬體契約差異。驗證器從
舊提交讀取 `lamobo-r1.eos`，再和工作樹的 `bananapir1.eos` 比較上述六個欄位；
它同時要求現行檔名後綴仍為 `.eos`。

## Trixie CLI 唯讀內容

代表映像為：

```text
Armbian-unofficial_26.05.0-trunk_Lamobo-r1_trixie_current_6.18.32.img.xz
```

驗證器將壓縮串流解到系統暫存目錄的新檔，不覆寫或原地解壓來源檔；接著建立
唯讀 loop、以 `ro,noload` 掛載，完成後卸載、釋放 loop 並移除暫存內容。來源
檔案的 inode、大小及奈秒時間戳在檢查前後必須一致。

| 項目 | 實際值 |
| --- | --- |
| 分割表 | DOS |
| sector 大小 | 512 bytes |
| 分割區 | 1 個，起點 8192，大小 3563520 sectors，型別 `83` |
| 檔案系統 | ext4，標籤 `armbi_root` |
| 掛載屬性 | `ro,norecovery` |
| 核心 | `boot/zImage`，對應 Linux 6.18.32 current sunxi |
| initrd | `boot/uInitrd` |
| 板級 DTB | `boot/dtb-6.18.32-current-sunxi/sun7i-a20-lamobo-r1.dtb` |
| 開機環境 | `overlay_prefix=sun7i-a20`、`rootfstype=ext4` |
| 系統身分 | Debian Trixie、`BOARD=lamobo-r1`、`BOARD_TYPE=eos` |

檢查只讀取映像內容，不執行映像中的程式。

核心來源樹的 DTS 位於 `allwinner/`，但這批歷史 Debian 套件將 DTB 安裝為
`/boot/dtb-6.18.32-current-sunxi/sun7i-a20-lamobo-r1.dtb`。初次完整守門因沿用
來源樹路徑而拒絕通過；契約依唯讀映像中的實際封裝格式修正後，重新執行全部
十組位元與內容檢查，沒有略過前段守門。

## 驗證命令

```bash
python3 tools/verify-bananapi-r1-archive.py
python3 -m unittest tests.test_bananapi_r1_archive
python3 -m json.tool config/validation/bananapi-sunxi-a20-r1-archive.json >/dev/null
```

完整驗證會讀取約 6.35 GiB 的壓縮資料兩次以上，時間取決於儲存裝置與 XZ
解碼速度；Trixie CLI 內容檢查另需約 1.71 GiB 暫存空間及免互動 `sudo`，供
唯讀 loop 與掛載使用。

## 證據限制

- 這批檔案是 2026-05-21 留下的歷史建置結果，不是目前分支重新建置的產物。
- 日誌與位元證據能證明當時流程到達映像封裝完成，不能證明現在仍可由乾淨快取
  重現相同位元。
- 沒有 R1 實體板的 UART、冷啟動、SD、SATA、交換器、網路、HDMI、GPU、USB、
  GPIO、I2C 或 SPI 驗證，因此不得提升為 L3。
- 舊映像沿用 `lamobo-r1` 身分；現行官方板號是 `bananapir1`。板號關鍵硬體
  欄位等同，不代表舊映像已轉換成新板號產物。
- EOS 狀態未改；沒有維護承諾、安全更新承諾、發行授權審查或對外支援結論。
- 歷史／封存 L2 只能作稽核與回歸參考，不得作為公開發布依據。
