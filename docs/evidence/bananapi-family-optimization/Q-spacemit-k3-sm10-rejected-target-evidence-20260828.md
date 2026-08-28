# BPI-SM10 U-Boot target 證據拒絕紀錄

## 結論

2026-08-28 由來源提交 `d8fb17178ebdd8275bc1b4e6a612605566d246f0` 完成第三次 BPI-SM10 Trixie current CLI 完整映像建置。固定來源與 L1 空值校準讀取已正確，但自訂 U-Boot 封裝只複製啟動 payload，沒有產生 Armbian 共用套件格式要求的 target 設定與中繼資料。唯讀守門因此拒絕該映像，不得把單獨存在的 `uboot.config` 誤當成完整 target 證據。

## 拒絕證據

| 項目 | 結果 |
|---|---|
| 建置來源提交 | `d8fb17178ebdd8275bc1b4e6a612605566d246f0` |
| 來源樹 | `a4be1ca2b47e20962cfe585f51157cdb598f3451` |
| IMG 大小 | `1916796928` bytes |
| IMG SHA-256 | `3c716978d55d1a9c62cba5e109440c70fe36fda4dc35fa82b8a3dfe3bd0b006d` |
| XZ 大小 | `477742284` bytes |
| XZ SHA-256 | `8906556d8614835c318ce007ab76b19ca986383e60d6955b890f8bde009c9157` |
| `uboot.config` SHA-256 | `ffb244d91c6d9ce59f20eeabee15f0391e5d6417548856cacd4720d87cf69b9c`，內容正確 |
| 缺少檔案 | `u-boot-config-target-1`、`u-boot-metadata-target-1.sh` |
| 拒絕訊息 | `bananapism10 缺少 U-Boot target 設定證據` |

## 根因與修正

自訂 `build_custom_uboot` 路徑直接呼叫 `deploy_built_uboot_bins_for_one_target_to_packaging_area` 後就標記完成，沒有經過共用 `compile_uboot_target` 中負責封裝 `.config` 與 target metadata 的程式區塊。修正新增專用部署函式：先複製受控 payload，再以同一個 target 編號封裝實際 `.config`、payload 清單、target make 值與設定檔名稱。這些檔案是真實建置內容，不是為通過驗證而建立的空白佔位檔。

## 保存與回收政策

此紀錄保存來源與產物雜湊。第三次拒絕映像及專用 overlay 在確認沒有掛載、程序、開檔或 Docker 引用後回收；下一次 L1 建置必須由包含正確 U-Boot target 證據封裝的已推送乾淨提交重新產生。
