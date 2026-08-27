# BPI-M6 L2 校準與正式重建契約

## 結論

BPI-M6 已由提交 `9f592fcb5fab6cc3dcfce8ae3a55a8ec7a537956` 完成 L1 完整映像校準。IMG、XZ、MBR 雙分割區、唯讀檔案系統、根 UUID、DTB、最終核心與 U-Boot 設定，以及 TZK／U-Boot 受控重疊區段均通過本機確定性守門。校準證明來源與封裝鏈足以建立一致的內部軟體候選，但不代表實機通過或允許公開發布。

## 校準成品

| 項目 | 數值 |
| --- | --- |
| 來源提交 | `9f592fcb5fab6cc3dcfce8ae3a55a8ec7a537956` |
| 來源 tree | `20dab6e450581f2d31b30f83d09053f1143cf8b7` |
| 固定時間戳 | `1717001894` |
| IMG 大小 | `1895825408` 位元組 |
| IMG SHA-256 | `b2d12235fa42542d653f68945dc870ecae0ea692d7f246b590287a4549ad38a3` |
| XZ 大小 | `315192164` 位元組 |
| XZ SHA-256 | `a7caae9bdb7626a1033b25bb167c050b64600c77a32812fafe6547fd03158192` |
| 映像 DTB SHA-256 | `52c58e8a1413fd644b812480215350410659371083afa9930684df5752625413` |
| 來源契約投影 SHA-256 | `a30c565815b38169f3190253514c6034291d61a0c136ae1244d37eba0f72cb56` |

校準用 IMG 與 XZ 只作為 L2 重建契約的量測來源。L2 正式映像必須由包含本契約的已推送乾淨提交重新建立，不得把 L1 校準檔就地改名或晉級。

## 已通過守門

1. 分割表為 MBR；FAT boot 與 ext4 root 的起始 sector、大小、類型及標籤符合契約。
2. FAT `armbianEnv.txt` 具有唯一 `rootdev=UUID=...` 與 `fdtfile=synaptics/vs680-a0-bananapi-m6.dtb`。
3. DTB、最終核心設定與最終 U-Boot 設定均由映像唯讀讀回並符合固定雜湊。
4. 映像 offset `512` 的 TZK 前段、offset `2097152` 的完整 U-Boot 與其後 TZK 尾段符合固定寫入順序。
5. XZ 串流完整，解壓內容與 IMG 一致。
6. 建置提交、來源 tree、validation、候選矩陣、完成狀態與共用驗證清單已相互綁定。

## L2 重建規則

1. validation 先轉為 `L2 內部軟體候選`、`internal-l2` 與 `current_evidence_level=L2`。
2. 根檔案系統、完整 IMG 與完整 rootfs 旗標設為完成，並把映像 DTB 範圍固定為 `full-image-l2`。
3. 過渡提交不得加入 `image_build_evidence`，中央 `config/bananapi-optimization-status.json` 仍維持 L1。
4. 過渡提交必須先通過測試、提交並推送；隨後安全移除 L1 校準輸出與專用 OverlayFS 上層。
5. 從已推送過渡提交乾淨重建 L2，通過即時物質驗證後才回填正式 IMG／XZ 與證據雜湊，並把中央狀態提升為 L2。

## 保留限制

- `public_release_allowed=false`
- `hardware_claims_allowed=false`
- `opaque_payload_redistribution_verified=false`
- 尚無 BPI-M6 實機冷啟動、儲存、網路、USB、顯示、音訊、40-pin、重啟、關機與壓力測試證據。
- TZK 與 U-Boot `sm.bin` 的原始碼、重建鏈及逐檔再散布授權仍未閉合。

## 正式重建結果

本文件規定的過渡契約已由提交 `ce43f2a3fc93c49e28a4a57ba821b510461b4512` 推送後執行。L1 校準輸出與舊專用 OverlayFS 上層先經掛載、程序、`lsof` 與 Docker 掛載檢查後移除，再由空白專用上層重建。正式 IMG SHA-256 為 `83f87457f639daaac2981791cc10a9a8048bb4606466067ab791299d5c959fac`，XZ SHA-256 為 `6e66d1c7312eae214340297955227e637402eea7306f2ee0e3ac5166f8e3e7db`；L2 即時物質守門與二次讀回均已通過。完整閉合證據記錄於 `I-vs680-m6-L2-build-20260828.md`。
