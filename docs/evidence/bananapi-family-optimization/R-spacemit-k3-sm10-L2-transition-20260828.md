# BPI-SM10 L2 過渡契約

## 結論

2026-08-28 由來源提交 `0959d5305a3bf22e0c0a80af31aba0f2074c12fa` 建置的 BPI-SM10 Trixie current CLI 完整映像通過 L1 唯讀校準守門。該結果足以固定下一次 L2 重建所需的 GPT 大小、最終核心設定與映像內 DTB 雜湊，但不等於正式 L2 已完成，也不代表實機、公開發布或再散布授權通過。

## L1 校準結果

| 項目 | 校準值 |
|---|---|
| 來源提交 | `0959d5305a3bf22e0c0a80af31aba0f2074c12fa` |
| 來源樹 | `f7d62435f0f535346eec07666023300e3d766f99` |
| `SM10_CALIBRATION.json` SHA-256 | `39461c9fe44dd3d0700ae2e60984441bf0b4ddc1642fca75424eb66e7b9a72c6` |
| bootfs | `1:bootfs:24576:524288` |
| rootfs | `2:rootfs:548864:3192832` |
| 最終核心設定 SHA-256 | `2ea6c3b62bd8118b685a10d6c4c22a1718df7a9e533c3e929282fcee90c82445` |
| 映像內 DTB SHA-256 | `a74520d979cc62fcdb12dfddd97c7968900109df6a33ae34c1489d87a34695ba` |
| IMG 大小 | `1916796928` bytes |
| IMG SHA-256 | `071e865aa1f53f29823dcf171c5de091960d21902a945d880916c96456dacfc1` |
| XZ 大小 | `477808188` bytes |
| XZ SHA-256 | `ef6045ef87239cc9b58512da61702f0dbcf2a1e3f7cb51d45d18459927b780a0` |

## 過渡規則

L2 過渡契約把上述三類校準值改為精確值，把狀態明確標成 `l2-transition`，並把 DTB 證據範圍標成 `l1-calibration-image`。結構化 `l1_calibration_evidence` 同時綁定來源提交、來源樹、校準檔、IMG、XZ 與五份清單的雜湊。此狀態仍須符合以下限制：

- `full_image_built`、`rootfs_image_built` 與 `full_rootfs_image_built` 維持 `false`。
- 不得預填 `image_build_evidence`。
- 全域 48 板狀態中的 BPI-SM10 維持 L1 與未結項目。
- 不得執行歷史 L2 映像重驗，也不得把 L1 校準產物改名冒充正式 L2。
- 正式 L2 必須由已推送且乾淨的過渡契約提交重新建置，通過完整唯讀內容、XZ 串流、GPT、啟動載荷與最終設定守門後，才能把 DTB 證據範圍提升為 `full-image-l2`。

## 證據限制

L1 校準與後續 L2 都是軟體建置及唯讀內容證據。BPI-SM10 仍維持 `.wip`，SD 開機只是候選目標；donor 拓撲等價性、U-Boot 控制 DT 身分、實機開機、周邊功能、硬體穩定度，以及 `esos.itb`、`env_k3.txt`、`bianbu.bmp` 的公開再散布授權都尚未閉合。
