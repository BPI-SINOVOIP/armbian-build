# BPI-SM10 L1 空值校準拒絕紀錄

## 結論

2026-08-28 由來源提交 `a54231da374bbb0f83d08378ae077d22ffb37614` 完成第二次 BPI-SM10 Trixie current CLI 完整映像建置。日誌已確認 Armbian firmware 由精確提交 `f50a2a21bcdb77a562b3976930c5c6b521a1df08` 解析，建置來源契約正確；但 L1 唯讀驗證把 Validation 中用於表示「待首張映像量測」的 JSON `null` 轉成字串 `None`，隨後誤判為不合法的 SHA-256。該映像因此不得升級為校準或 L2 證據。

## 拒絕證據

| 項目 | 結果 |
|---|---|
| 建置來源提交 | `a54231da374bbb0f83d08378ae077d22ffb37614` |
| 來源樹 | `756b8108f918239cb68f12c3bfc656f32269f5df` |
| 來源投影 SHA-256 | `3b182f48aac7a05f9baa21801c127d8e6f383ef1cf7933ff3cd7c8089d8353f6` |
| 韌體解析 | 精確 `commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08`，通過 |
| IMG 大小 | `1916796928` bytes |
| IMG SHA-256 | `393196ba53f27c5a54f468accba569cabc44e6dcb72a6f965c4b72eff451e290` |
| XZ 大小 | `478336544` bytes |
| XZ SHA-256 | `d879abaf97c2db18f09c72c8bc18c5eea50f17d3b2409883d81eb0be7d581da6` |
| 拒絕訊息 | `bananapism10 的最終核心設定雜湊格式不符` |

## 根因與修正

共用驗證器的 `board_field_optional`、巢狀板級欄位讀取器與頂層欄位讀取器沒有處理 Python `None`，直接由 `print` 產生文字 `None`。修正後三個讀取器都把 JSON `null` 映射成空字串；因此 L1 可先把映像內實際核心設定寫入 `SM10_CALIBRATION.json`，L2 則仍要求 Validation 提供 64 位 SHA-256 並與映像精確相符。

## 保存與回收政策

此紀錄保存來源與產物雜湊。該次 IMG、XZ、驗證暫存及專用 overlay 不是已通過候選，在確認沒有掛載、程序、開檔或 Docker 引用後回收；正式 L1 校準必須由包含本修正的已推送乾淨提交重新建置。
