# Banana Pi M1 Super 完整映像 L2 校準紀錄

日期：2026-08-28

## 結論

提交 `29861f5bec3e1029c891bc785f81a00cea9fd4d3` 已使用 M1 Super 專用 OverlayFS 上層，從固定 Linux、U-Boot、Armbian firmware 與 RKBin 來源完整建置 Debian Trixie vendor 精簡 CLI 映像。校準映像通過 L1 共用唯讀守門；映像內 DTB、GPT、U-Boot 載荷、最終核心與 U-Boot 設定，以及 RKBin 清單均與既有精確契約一致，沒有發現來源或位元組漂移。

本次結果只用來確認正式 L2 重建契約，不是現行分支的正式 L2 物質證據。validation 已切換成 L2 重建契約，但 `full_image_built=false`、`rootfs_image_built=false`，且不攜帶 `image_build_evidence`；中央盤點仍維持 L1。必須先提交並推送本契約，再刪除校準輸出與專用上層，從新提交乾淨重建並完成即時物質驗證，才可提升中央狀態。

## 建置身分

| 項目 | 值 |
| --- | --- |
| 板卡 | `bananapim1super` |
| 分支 | `vendor` |
| 發行版 | `trixie` |
| 使用者空間 | 精簡 CLI |
| 建置提交 | `29861f5bec3e1029c891bc785f81a00cea9fd4d3` |
| 來源樹 | `a2d181d6e79d4b7474985ece1ecb26405c1df3f8` |
| 建置 validation SHA-256 | `3d87ed47d3aed3e821d0435b4420b3faf0324e023359ef3463e9f2551e595b16` |
| 候選矩陣 SHA-256 | `50304907f2f876d9ef0010573b4e40b828d28f1efbe7a253517f9f165fd84423` |
| 固定建置時間戳 | `1787082913` |
| 規範投影 SHA-256 | `5c5d6570f8a9e72f6c150dab4314de9d2bca7afdb89e796f36d9e41247e22d3d` |
| 校準輸出目錄 | `output/images/2026.08/bananapi-rockchip-rk3528-m1super-trixie-vendor-cli` |

## 校準產物

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| IMG | 2,420,113,408 | `b0e90249de31fa606a6af51c0a580af2ab97341bc842a17e3e9eda90404bd8b7` |
| XZ | 468,109,020 | `4eb7728069cd6f193fa516c554f7ced6ffa8375c8bb6f1e177e718144c856852` |

XZ 已通過嚴格結構檢查，解壓串流的大小與 SHA-256 均對應 IMG。這兩個雜湊只識別校準輸出；正式 L2 必須由後續乾淨重建建立自己的雜湊與物質完成狀態。

## 精確契約

| 項目 | 大小 | SHA-256 |
| --- | ---: | --- |
| 映像內 DTB | 不適用 | `68c0d6c27d2802abee0b7ab4b0569581048b14fc651d55c103392d81e00f2eb6` |
| `idbloader.img` | 311,296 | `ecd35b1d69c4b87e2ba170017f58c2f67f44c178dbb7df3488d9b88c26847355` |
| `u-boot.itb` | 1,320,960 | `ee2067f149cfc6c74f84c5c09880673dcda9133d4593ec20e9fc6e328f6bd59a` |
| 最終核心設定 | 不適用 | `24edbbaabf1bd7960e7c2647ec7e96c25e2e9bf4de5a440c30827eb15b162e9e` |
| 最終 U-Boot 設定 | 不適用 | `c56f7986bc9d636d51439509c4ad43b8adc247b97783717de61553bba8c7bf60` |

`idbloader.img` 位於 byte offset `32768`，`u-boot.itb` 位於 byte offset `8388608`。根分割區從 sector `32768` 開始，大小為 `4691968` sectors，類型 GUID 為 `b921b045-1df0-41c3-af44-4c6f280d3fae`；根檔案系統是標籤 `armbi_root` 的 `ext4`。

RKBin 固定提交為 `1d3c61008fa823936ae7a59615393f8294b64456`，清單 SHA-256 為 `79a10a440ef02ceb9353ec8f5f8914d9981a47a83e0f291b700ac168be64e458`。U-Boot 載荷清單 SHA-256 為 `c26193529828daf0c80cb0980dd20b1c06dc802992708a340b4b63bfa622479b`，最終設定清單 SHA-256 為 `e40d737d10a0494a58eedfb5831bf28113ce13a1e618fe78d2c70329ee70e67c`。

## 證據限制

- 本次沒有燒錄 SD 或 eMMC，也沒有 UART、冷啟動、重啟或斷電測試。
- 沒有實機驗證乙太網路、Wi-Fi、Bluetooth、HDMI、GPU、VPU、USB、GPIO、I2C、SPI、音訊或長時間穩定性。
- 量產無線 BOM 與 Armbian firmware 逐檔再散布授權尚未閉合。
- RKBin 預建內容只能依授權隨採用 Rockchip 積體電路的平台散布，不得獨立散布或修改。
- `public_release_allowed=false` 與 `hardware_claims_allowed=false` 維持不變。
