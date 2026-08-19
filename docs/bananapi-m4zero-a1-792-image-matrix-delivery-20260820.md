# BPI-M4 Zero A1 792 MHz 完整映像矩陣交付

## 狀態

A1 使用 0845 實測收斂的 DDR lane 參數，已完成五個發行版、CLI 與 XFCE
共十套 IMG/XZ。所有映像共用 `P02e5` bootloader，10 個 XZ 與 37 個矩陣
清單項目均通過 SHA-256 驗證。

```text
資格：A1_0845_M4ZLAB2_PASS_COLD_BOOT_PENDING
SPL benchmark：0845 64 MiB M2 20/20 通過
0845 完全斷電冷啟動：尚未執行
Linux 全容量壓力：尚未執行
跨板共同 Gate：尚未執行
```

因此本矩陣是可燒錄工程候選，不是量產或穩定版聲明。

## 共同參數

```text
DRAM_CLK=792
DX_ODT=0x07070707
DX_DRI=0x0e0e0e0e
CA_DRI=0x0d0d
ODT_EN=0xaaaaeeee
TPR6=0x3a808080
TPR10=0x402f6663
TPR11=0x25252523
TPR12=0x110f0f10
```

| 項目 | 值 |
| --- | --- |
| Build ID | `2026.01-S127a-P02e5-Hc6a9-V3946-Be6d8-R448a` |
| bootloader SHA-256 | `0b9333deac4a63353eb18442c9ef2f7ef269be1d7ef015cae3eee65f1b92a0cf` |
| bootloader 大小 | 873,977 bytes |
| bootloader 偏移 | 8,192 bytes |
| kernel | `6.18.32-current-sunxi64` |
| A1 建置來源提交 | `6e05b3313317936d8e6abbd32a49dbcd9f4e0109` |

## 下載矩陣

輸出目錄：

```text
output/images/2026.08/bpi-m4zero-a1-0845-792-matrix
```

| 發行版 | 類型 | XZ bytes | XZ SHA-256 |
| --- | --- | ---: | --- |
| Bookworm | CLI | 432,902,872 | `264d2a2d4a10e2dcf816bef74e4029aaa3f08a3cd5cec02759feb20e8cdf7af4` |
| Bookworm | XFCE | 973,683,436 | `13b1b2cc72897a8a41a1efb9415cf5d6c869362e864458d133343a3f71a79de4` |
| Jammy | CLI | 454,842,924 | `fee9ea0f72c06e80e681cf2f5d43782a4b039755bd5e4ec1f94da18a37e7fdef` |
| Jammy | XFCE | 888,996,180 | `51436d74c6e65fad7ea80078c07feb4f8365b3a02bf09d90c1ec990948de3b64` |
| Noble | CLI | 448,867,768 | `f5ba10a4725713f13a4e149aef4e6f021750f0d7fce390509707e72788401e41` |
| Noble | XFCE | 916,579,416 | `24bd40359ab1e158a2132c4e15f2b1b2b6f491fd83524bba97a984efccfe70a6` |
| Resolute | CLI | 458,596,604 | `5116fd8515d88be7aa8915f67792cb3d15919b4c65130f51217dec7bb0fc9d17` |
| Resolute | XFCE | 1,001,418,120 | `d03fabdca1693a54faf7e54efba8d924638905a2d4510d0b0cdfaa8d6864960a` |
| Trixie | CLI | 459,723,132 | `d28d7228bd4cd001f058cee4dd5c2cbdab10b2f7313f7b360a545bf22403666a` |
| Trixie | XFCE | 1,084,666,640 | `807c4a5cfcd226d89d5c48ac55d4f17fad76b04a68dac2283eb724692dbecc07` |

每套 XZ 均保留同名未壓縮 IMG。完整 IMG 大小、IMG SHA-256、檔名與 XZ
檔名記錄於 `MATRIX.tsv`；整批檢查使用 `SHA256SUMS` 與 `SHA256SUMS-XZ`。

## 矩陣證據

| 證據 | SHA-256 |
| --- | --- |
| `MATRIX.tsv` | `7043b46d3bd5b3f4889384c3a13f8126c826b136f5582e29feffb54caf2af96f` |
| `SHA256SUMS-XZ` | `a3d9a7bf413768d815cb846230cf6c3de27156125271b723a4b0fd868e09239a` |
| `QUALIFICATION_STATUS.txt` | `89aff7b3d12a5dd079cfa2328aad8e840b141a835ad6e180f59871a74194538a` |

`SHA256SUMS` 會包含本交付文件複本 `README.md`，故不在文件內固定其自身
衍生雜湊，避免自我參照。以同目錄的 `COMPLETION_STATUS.txt` 取得當次
`SHA256SUMS` 雜湊，再執行 `sha256sum -c SHA256SUMS` 驗證全部項目。

矩陣工具逐套驗證來源 XZ、分割區起點、內嵌 bootloader、Build ID、替換區外
內容、IMG SHA-256、XZ 解壓串流與 metadata。Jammy CLI 另與先前單獨 A1
產物執行 `cmp`，IMG 與 XZ 均逐位元一致。

## 測試順序

1. 先用 Jammy CLI 在 0845 執行完全斷電冷啟動與 UART 收集。
2. 進入 Linux 後執行全容量記憶體、CPU、SD／eMMC 並行壓力。
3. Jammy CLI 通過後，再測 Noble XFCE，確認桌面與顯示路徑。
4. 0845 通過後，再把同一映像帶到 0438、1116、2 GiB 單 Rank 與舊 V2
   弱板；不能以 0845 單板結果直接取代 X2。

燒錄前先核對 XZ SHA-256；燒錄後至少回讀 8 KiB 偏移起 873,977 bytes，
確認 SHA-256 等於共同 bootloader 雜湊。
