# BPI-SM10 finalizer 暫存檔碰撞拒絕紀錄

## 結論

2026-08-28 由來源提交 `e861a1c4e4e52781f8faf972de6843963aa63968` 完成 BPI-SM10 Trixie current CLI L2 過渡映像建置。共用唯讀內容守門通過，專用 finalizer 也已算出材料證據，但最後因原子寫入暫存檔與待提升狀態檔使用同一路徑而回傳失敗。整條驗證命令沒有零退出，因此本次輸出拒絕作為正式 L2 閉合證據。

## 拒絕證據

| 項目 | 結果 |
|---|---|
| 建置來源提交 | `e861a1c4e4e52781f8faf972de6843963aa63968` |
| 來源樹 | `9226bcacf6280f9aeded6af07b19d4a3daaad417` |
| 建置耗時 | `18:40` |
| IMG 大小 | `1916796928` bytes |
| IMG SHA-256 | `8de54b762c23d493c12ba33cd16d0aa078061939bb0da981d0462d2795f2e93e` |
| XZ 大小 | `478637416` bytes |
| XZ SHA-256 | `319fff237158c67ebd662e1b0a3b25bd0708bc05ea5e3f2b61b8aa2a5660b1ca` |
| 共用唯讀守門 | 通過 |
| 專用 finalizer | `FileNotFoundError`，退出碼 `1` |
| 失敗路徑 | `VERIFICATION_STATUS.json.partial` |

## 根因

L2 共用驗證先把待提升狀態寫入 `VERIFICATION_STATUS.json.partial`。專用 finalizer 讀取該檔後，要原子寫入正式 `VERIFICATION_STATUS.json`；原子寫入函式又把暫存檔命名成 `VERIFICATION_STATUS.json.partial`，因此覆寫並移走原本的待提升狀態。後續清理再次呼叫 `unlink()` 時，檔案已不存在而拋出例外。

## 修正與防回歸

原子寫入暫存後綴改為 `.writing`，與協定保留的 `.partial` 分離。新增回歸測試會先建立待提升狀態，再執行正式狀態原子寫入，確認正式檔內容正確、待提升檔仍存在，且 `.writing` 不殘留。

## 保存與回收政策

本紀錄只保存可追溯雜湊與根因。即使 `SM10_MATERIAL_EVIDENCE.json` 和正式狀態檔已在例外前寫出，本次驗證命令仍失敗，不得補稱成功。映像及專用 overlay 會在確認沒有掛載、程序、開檔或 Docker 引用後回收；正式 L2 必須由包含修正的已推送乾淨提交重新建置與驗證。
