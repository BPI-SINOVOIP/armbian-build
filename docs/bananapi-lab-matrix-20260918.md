# 全部映像的離線準備與續跑

## 目的與界線

逐筆檢查原清單的 444 套映像，不將 45 板抽樣推論成全部 OS／桌面版本通過。
沿用既有家族解析器，檢查原配核心、DTB、initramfs、開機設定與根檔案系統身分。
所有來源唯讀；不掛載映像、不執行映像程式、不重建或替換發布檔，不啟用站點、
不連線板子，也不修改硬體佇列。通過不代表冷啟動、DDR、驅動或耐久測試通過。

G1 盤點有 62 個原來源曾準備成功、1 個原始 R2 阻擋；另 4 個只有 initrd 擷取，
合計 67 個可嘗試重播的來源。這不是當前工具通過數，其他來源仍須完整讀取。

## 固定批次

在本工作樹根目錄執行；輸出目錄必須不存在。第一次建立會固定來源清單、
逐板參數參考、重播索引、程式、板級設定及解析工具摘要。

```bash
PY=output/evidence/bpi-sram-supervisor/model-venv/bin/python
OUT=output/evidence/bpi-lab-matrix-G-20260918/batch-001

"$PY" -B tools/bpi_lab_matrix.py init \
  --catalog output/evidence/bpi-multiboard-lab-20260917/catalog-001.json \
  --catalog-sha256 7ec027496d0e9215ce673f41570875647df077b1766e6190cf6b0b5e41df0fe0 \
  --sample docs/evidence/bpi-multiboard-integrate-20260917/sample-audit.json \
  --sample-sha256 1a308963d152f440cb6e326389943f2d67e0ad2b4771f87f954fc98123fe9e25 \
  --replay-root output/evidence \
  --workers 4 --output "$OUT"
```

保存命令回報的 `plan.json` 絕對路徑與 `sha256`，再執行：

```bash
"$PY" -B tools/bpi_lab_matrix.py run \
  --plan "$OUT/plan.json" --plan-sha256 <固定計畫摘要>
```

順序固定為 ARM32、ARM64、RISC-V；各架構依 OS、板子、角色排序。
一個架構／OS 群組全部結束才換下一組，群組內最多四個工作。
預設單筆原映像上限 16 GiB，啟動群組前預留每個工作兩份原映像的空間，
另保留 32 GiB。暫存完整映像／分割由原讀取器收尾移除，保留必要組件及證據。

## 中斷與重試

- 同一計畫重跑上述 `run`，來源身分、旁檔、工具和產物摘要相符才跳過完成項目。
- 單一批次使用 `flock`，拒絕同時啟動第二個執行者；不要刪除鎖檔繞過限制。
- 缺少完成收據的目錄不是完成。保留舊嘗試，以新的 `attempt-*` 續作；已完整擷取者優先重播。
- 收據先完整落盤再發布；逾時、讀取失敗等可重試問題不發布完成收據。
- 舊擷取先驗來源鏈與組件摘要；若只是查詢覆蓋不足，另記重播失敗，再完整讀取同一 XZ。
- 若工具改變，舊批次拒絕續跑；建立新批次並索引舊擷取，不直接改舊計畫或既有收據。

這是本機可追溯流程，不是抵抗有權任意重寫所有程式與證據之使用者的安全沙箱。
重播只表示使用已核對的擷取，不宣稱本次重新讀過原 XZ；來源真實性及發布權限另行審核。

## 如何判讀

| 狀態 | 含義 |
| --- | --- |
| `prepared` | 當前家族解析器完成該原映像的組件及根身分核對；不是實板通過 |
| `blocked` | 完整讀取證據下仍有來源或受支援語意問題，保留逐筆阻擋原因 |
| `retryable` | 來源身分、讀取、工具或證據出錯；不能跳過當成完成 |

每筆 `jobs/<image_id>/receipt.json` 保存來源、參數、嘗試、產物整體摘要及結果。
只有無可重試項目才產生 `summary.json`；仍有問題則產生獨立 `incomplete-*.json`。
即使 444 筆都檢查完，仍須分列 `prepared` 與 `blocked`，不能說全部可用。

Sunplus 原始版本欄位仍保持 `0`，只記錄二進位實際解析版本；不藉此核准 metadata 候選。
原始 R2 的錯誤 DTB 路徑逐筆核對；F3 衍生候選不是原 444 筆成員，不替代原件或解除阻擋。

## 驗證入口

```bash
"$PY" -B -m unittest discover -s tests -p test_bpi_lab_matrix.py
/home/pi/.local/bin/ruff check tools/bpi_lab_matrix.py tests/test_bpi_lab_matrix.py
```

完整回歸與實際批次結果完成後另記固定摘要；本文件不預先宣告批次成功。
