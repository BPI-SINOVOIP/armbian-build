# BPI-M4 Berry A1 792 MHz 候選全映像交付

## 交付定位

本批映像將 BPI-M4 Zero A1 收斂的 DDR PHY 參數移植到 BPI-M4 Berry
U-Boot `v2025.04`，供 2 GiB 與 4 GiB M4 Berry 實板平行驗證。

這批映像是工程候選，不是量產放行版本。BPI-M4 Zero 的證據只支持參數
選擇：A1 曾在兩片 2 GiB 與十七片 4 GiB M4 Zero 上辨識容量並進入系統，
但只有 4 GiB `0845` 完成參數窗口與 `memtester 3000M` 三輪。M4 Berry
目前沒有實板通過證據，2 GiB 與 4 GiB 都必須重新驗證。

## 鎖定內容

| 項目 | 值 |
| --- | --- |
| Armbian 板名 | `bananapim4berry` |
| Kernel | current `6.18.32-sunxi64` |
| U-Boot | `2025.04-S3482-P25cb-Hc6a9-Vce89-Be6d8-R448a` |
| Bootloader SHA-256 | `93c3dc0766a85974bf8675ac770bf1ebb15b9b0afdb7b1187fcb774ae9951005` |
| DDR 頻率 | 792 MHz |
| 容量策略 | SPL 自動探測 2 GiB／4 GiB 候選 |
| 產物 | 五個發行版，各含 CLI 與 XFCE，共十組 IMG/XZ |

## 參數

```text
DX_ODT=0x07070707
DX_DRI=0x0e0e0e0e
CA_DRI=0x0d0d
ODT_EN=0xaaaaeeee
TPR6=0x3a808080
TPR10=0x402f6663
TPR11=0x25252523
TPR12=0x110f0f10
DRAM_CLK=792
```

## 產物驗證

在交付目錄執行：

```bash
grep -qx 'status=complete' COMPLETION_STATUS.txt
sha256sum -c SHA256SUMS-XZ
sha256sum -c SHA256SUMS
for image in ./*.img.xz; do xz -t "$image"; done
```

`MATRIX.tsv` 記錄每個 IMG 與 XZ 的大小、SHA-256 與檔名；
`TEST_RECORD_TEMPLATE.tsv` 用於按板號回填實機結果。未壓縮 `.img` 與
壓縮 `.img.xz` 都必須保留，避免外部測試者重複解壓或使用錯誤版本。

## 實機最低測試

每片板先核對 UART 中的 `P25cb` Build ID 與 SPL 容量，再執行五十次完全
斷電冷啟動、80% 以上可用記憶體三輪 `memtester`、八小時 CPU／VM／I/O
組合壓力、SD/eMMC 開機及 HDMI、GPU、USB、Wi-Fi、Bluetooth、GPIO。

任何錯誤都要保存完整 UART、`dmesg`、板號、PCB 版本、DDR/eMMC 完整料號
與映像 SHA-256。只有 2 GiB、4 GiB 各自通過受控測試後，才能提升資格狀態。

## 映像清單

| 發行版 | 模式 | XZ bytes | XZ SHA-256 |
| --- | --- | ---: | --- |
| Bookworm | CLI | 434021980 | `70b334dd74b90f2e4b20ef476ad3739940b5bbd60e5a6a2680ca6ace2edbcba7` |
| Bookworm | XFCE | 972467428 | `e492fdacc3a86be269e2591aa9ce9a63391c295a98d1c172c35b4321721e075c` |
| Jammy | CLI | 454825584 | `04a45c021d86d8d4fbbde48a8cb113861f3b040aad073ce1fcd8e4c5abde82be` |
| Jammy | XFCE | 890304484 | `7773a32c53636f862dc2431edb0c04d8902a0f227c65869a0c309d54cf1f9006` |
| Noble | CLI | 449551848 | `46fcdbdce4ec63faf30b824e9903679542e40ba0b8938dcb15488d0b27a5057e` |
| Noble | XFCE | 917259312 | `ee734887b9f23ae9a5c793e4cf3ee7f70b3df3f0fbf89ab440f8925d9726b813` |
| Resolute | CLI | 458745544 | `c3c0fa0b55286355515348c19bb543a422995b5690048d921d7453927ff57ae6` |
| Resolute | XFCE | 996759348 | `f118b92d6c8601aa340d73876d51d08e0d396565daec12f08072e587da9ee661` |
| Trixie | CLI | 461235528 | `2aa185da82d807551d49210a8bc68cb1633028aa2477e4eaadef7ccce6374229` |
| Trixie | XFCE | 1089227528 | `1ebdcccf146f988d28a6b61acb301721be2d99f595a3a2ca0ddc3f78191a91f1` |

完整檔名、未壓縮 IMG 大小與雜湊仍以同目錄的 `MATRIX.tsv` 及
`SHA256SUMS` 為準；上表可先用 `SHA256SUMS-XZ` 快速核對外部分發檔。
