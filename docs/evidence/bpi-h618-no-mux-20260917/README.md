# 0845 最後批次控制腳本快照

這九份 `.py.txt` 是本機原型最後使用版本的逐位元組快照，`manifest.json` 保存長度與 SHA-256。主代理已核對它們與原工作目錄相同；用途是審查與後續移植，不是可直接用於其他板子的執行套件。

原位置為 `output/evidence/bpi-h618-no-mux/T1-0845-20260916/`。腳本的相對路徑、UART、電源、CID、組件位置及既有嘗試目錄綁定本輪 0845；**不要直接在此目錄執行，也不要用於另一片板子**。移植時須先將站點設定分離並重新核定救援與媒體身分，不能僅修改板名。

| 檔案 | 用途 |
| --- | --- |
| `run_remaining_matrix.py.txt` | 最後 `T4-matrix-005` 的略過、原位重試、後續批次及失敗回救援 |
| `customer_cycle.py.txt` | 已核對候選冷啟動與同 UART 首次初始化 |
| `customer_access.py.txt` | 等待首次服務完成，依實際路由設定受限 SSH 公鑰 |
| `return_to_rescue.py.txt` | 正常關機或依固定失敗報告強制回救援 |
| `rescue_network.py.txt` | RAM 網路與經 UART 固定 SSH 主機公鑰 |
| `rescue_cycles.py.txt` | 配對電源核對與既有救援流程 |
| `verify_after_hang.py.txt` | Noble XFCE 異常斷電後的唯讀映像核對 |
| `final_audit.py.txt` | 小型證據彙整、救援返回去重與一致性核對 |
| `finish_state.py.txt` | 唯讀確認電源、UART 釋放與沒有殘留測試程序 |

不包含密碼、私鑰、原始 UART、客戶映像或 eMMC 備份。序列埠提示等技術字串保留原值，以維持匹配語意與可追溯性。快照保留原型註解；實際待跑數量以程式跳過條件與各批次報告為準。

這不是所有早期嘗試版本的重建；先前版本的執行摘要與當時雜湊仍以原始證據為準。可重用底層程式位於專案 `tools/`，跨板適配尚未完成。
