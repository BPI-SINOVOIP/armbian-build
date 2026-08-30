# M4 Berry 十映像來源與同一性守門紀錄

日期：2026-08-26

## 目的

防止既有映像被重新執行工具時錯誤標記為目前 Git 提交，並證明每個
`.img.xz` 解壓後的完整資料與同名 `.img` 完全一致。

## 受測產物

映像目錄：

```text
/media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-opi-ddr/output/images/2026.08/bpi-m4berry-a1-h618-optimized-792-matrix
```

矩陣包含五個發行版的 CLI／XFCE，共十個 `.img` 與十個 `.img.xz`。
全部映像的既有 metadata 均記錄來源提交：

```text
16144c5c076c984a0fb0892055be34ab4a11b858
```

## 執行命令

先以建置器的既有產物守門重新產生九欄矩陣；此步驟不重建映像，也不
改寫既有 metadata：

```bash
OUTPUT_DIR=/media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-opi-ddr/output/images/2026.08/bpi-m4berry-a1-h618-optimized-792-matrix \
  tools/build-bpi-m4berry-h618-optimized-matrix.sh
```

再執行來源、同一性與唯讀映像內容驗證：

```bash
VERIFY_ARCHIVES=no \
  OUTPUT_DIR=/media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-opi-ddr/output/images/2026.08/bpi-m4berry-a1-h618-optimized-792-matrix \
  tools/verify-bpi-m4berry-h618-optimized-matrix.sh
```

`VERIFY_ARCHIVES=no` 只略過重複的旁車 SHA-256 與批次 `xz -t`；每個 XZ
仍會完整串流解壓並與同名 IMG 的 SHA-256 比對，不是抽樣驗證。

## 結果

- 10/10 IMG/XZ 來源與同一性通過。
- 10/10 映像唯讀掛載內容通過。
- 10/10 矩陣列均保留原始來源提交，沒有改寫成目前分支 HEAD。
- `COMPLETION_STATUS.txt` 為 `status=complete`。
- `MATRIX.tsv` 為九欄格式，包含每列 `source_commit`。
- `MATRIX.tsv` SHA-256：
  `f25e27d8bdb319375c7f5ebee13a45671fbbf21ecb16d2706bea3cdf91939ef9`。

驗證器逐一檢查核心設定、M4 Berry DTB、PWM overlay、RTL8821CU 主線驅動、
I/O 工具與各映像必要套件。結果只證明映像來源、內容與離線結構一致；
不取代 4 GiB、多板冷啟動、長時間壓力及外接 I/O 實機門檻。
