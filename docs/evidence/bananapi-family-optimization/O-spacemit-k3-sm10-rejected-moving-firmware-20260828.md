# BPI-SM10 可移動韌體來源拒絕紀錄

## 結論

2026-08-28 由來源提交 `fcb345f2edcc1fc9a044f45c53c1299399e75052` 完成第一次 BPI-SM10 Trixie current CLI 完整映像建置。核心、U-Boot、DTB、根檔案系統、IMG 與 XZ 均成功產生，但建置日誌證明 Armbian firmware 實際由 `refs/heads/master` 解析，而不是由 Validation 指定的精確 `commit:` 解析。因此守門正確拒絕該映像，該次結果不得用於 L1 校準、L2 升級、硬體通過或公開發布聲明。

## 拒絕證據

| 項目 | 結果 |
|---|---|
| 建置來源提交 | `fcb345f2edcc1fc9a044f45c53c1299399e75052` |
| 建置結果 | Armbian 完整映像流程成功，外層來源守門拒絕 |
| 實際韌體解析 | `Fetching SHA1 of 'branch' 'refs/heads/master'` |
| 該次分支解析結果 | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| 拒絕理由 | 來源引用可移動；即使該次結果碰巧等於預期提交，也不能證明可重建性 |
| IMG 大小 | `1916796928` bytes |
| IMG SHA-256 | `736e4a46ea75636eba0398437254347d8112463efb632b2c5f666a6a0c770b2f` |
| XZ 大小 | `480167200` bytes |
| XZ SHA-256 | `e04a823ba93b54a1cbcb80b6b125cbc18cf12bf7740d58ccae18a7d8b9a672a6` |
| 建置日誌 SHA-256 | `bac5342e707d34df02f3c95483045aa80314ac9cb58cf2e0fbc99789d9fcea69` |

## 根因與修正

板檔原先只定義 `ARMBIAN_FIRMWARE_GIT_REF_BOARD`，沒有在 `post_family_config` 階段將固定來源與引用賦值給框架實際使用的 `ARMBIAN_FIRMWARE_GIT_SOURCE` 與 `ARMBIAN_FIRMWARE_GIT_REF`。提交 `8fa520ce4` 已補上兩個全域賦值，並強化政策檢查與回歸測試，使靜態提交字串無法再被誤認為已套用的來源契約。

## 保存與回收政策

此紀錄保存拒絕原因與不可變雜湊；拒絕映像、壓縮檔、建置日誌及該次專用 overlay 不是正式候選，在確認沒有掛載、程序、開檔或 Docker 引用後回收。正式 L1 校準必須從已推送的修正提交與空白專用 overlay 重新建置。
