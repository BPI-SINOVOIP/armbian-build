# Banana Pi BPI-R3 Mini L2 正式建置證據

日期：2026-08-28

## 結論

`bananapir3mini` 已從乾淨且已推送的提交 `c0200a46b3d7a68545bd78cd397c672576fcf767` 完成 Debian Trixie current 精簡 CLI 正式重建。建置完成狀態與專用驗證狀態皆為 `complete`；原始 IMG、單一 XZ 串流、GPT、唯讀 ext4 根檔案系統、映像內 DTB、核心與 U-Boot 最終設定、BL2、FIP、必要套件及固定韌體來源已重新解析並通過 L2 軟體候選守門。

本結果是內部 L2 軟體候選，不是實機或公開發布證據。一般 IMG 只涵蓋 eMMC user area，空白 eMMC 仍須另外寫入 `boot0`，並啟用 boot partition。

## 來源身分

| 項目 | 值 |
| --- | --- |
| 分支 | `bananapi-family-optimization-20260826` |
| 來源提交 | `c0200a46b3d7a68545bd78cd397c672576fcf767` |
| 來源樹 | `e3f6bb2ba6d8b94d5bebf8653406d81c96d78d55` |
| validation SHA-256 | `c019f2d6d3b3a527844b4efc4671bca5b8f91f40fdf50e282c8e6e5d89abf135` |
| 來源契約投影 SHA-256 | `ad08cf7707aa0dfc69bf997a3da3ebb098f23237db9cb2881d5e592d7c0a6b0c` |
| 候選矩陣 SHA-256 | `dee595c7eecbfd49d8e633e5b56eb806cd4357c3a50e9ab31a7153fa67da7f32` |
| 建置模式 | `formal` |
| 證據等級 | `L2` |

## 正式映像

映像目錄：

`output/images/2026.08/bananapi-filogic-mt7986-r3mini-emmc-trixie-current-cli`

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| `Armbian-unofficial_26.05.0-trunk_Bananapir3mini_trixie_current_6.12.82_minimal.img` | 1426063360 | `feea9600aa7259b3c3e4d45e97b06e501b0e46dcdf992dcb9cc67ddd4a5aadeb` |
| `Armbian-unofficial_26.05.0-trunk_Bananapir3mini_trixie_current_6.12.82_minimal.img.xz` | 331354156 | `7aacfb4a9c8e84e78f969ba1674c4fa51b4204cc52db251a95ca693b919de7e4` |

XZ 已確認只有一個串流，且解壓內容與原始 IMG 相同。兩份正式產物皆保留，不因已有壓縮檔而移除原始 IMG。

## 映像物質證據

| 項目 | 大小或位置 | SHA-256 |
| --- | --- | --- |
| 映像內 DTB | 23104 位元組 | `5457155de554539c902a22507cbd69ad249fd70a24cf6e24a5753c2b5e8b66ab` |
| 最終核心設定 | `boot/config-6.12.82-current-filogic` | `2c6ea26327285e71fa778ccb360269796da043faa69d0fee4e467f1a0c36367e` |
| 最終 U-Boot 設定 | `usr/lib/linux-u-boot-current-bananapir3mini/u-boot-config-target-1` | `c4a0328fdaa6b345c6c15096bf99caf7a5a05c68aad38f2ad9e3e241322d937a` |
| `bl2.img` | 204889 位元組；IMG 位移 17408 | `fcc79a31bc4ea8a1104584991b53ed61834a40171c765a3e3859270ba7509b9e` |
| GPT 範本 | 17408 位元組 | `beb31c2284ec7b8e910faeea8d323f40532b26010e87d0bae851d823705efa1d` |
| `u-boot.fip` | 510681 位元組；IMG 位移 6815744 | `848ee9f1f6f451658ad179f240231dde24831f301ef8e9a43744621bf6d18408` |

第五分割區從 sector `32768` 開始，共 `2750464` sectors；檔案系統為 `ext4`，標籤為 `armbi_root`。封裝內 BL2／FIP 與 IMG 固定偏移的位元組雜湊一致。

## eMMC 契約

- user area 目標為 `/dev/mmcblk0`，正式 IMG 包含 GPT、user-area BL2 複本、FIP 與根檔案系統。
- 冷啟動來源為 `/dev/mmcblk0boot0`；須另寫入相同的 `bl2.img`，其 SHA-256 為 `fcc79a31bc4ea8a1104584991b53ed61834a40171c765a3e3859270ba7509b9e`。
- 寫入前須解除 boot0 唯讀，完成後須執行等效於 `mmc bootpart enable 1 1 /dev/mmcblk0` 的設定。
- 本候選沒有自動安裝授權；SD 不列入支援啟動媒體。

## 驗證結果

- `COMPLETION_STATUS.json`：`complete`。
- `VERIFICATION_STATUS.json`：`complete`，`evidence_level=L2`。
- `material_reparsed=true`、`read_only_content_verified=true`。
- 發布閘門維持 `blocked`；`public_release_authorized=false`。
- `hardware_validation_completed=false`、`hardware_tested=false`。

## 限制與下一步

1. 尚未在空白 eMMC 實測 user area、`boot0` 分離寫入、boot partition enable 與斷電冷啟動。
2. 尚未實測 Ethernet、Wi-Fi、PCIe、USB、GPIO、重新啟動與壓力穩定性。
3. ATF MT7986 預編譯 DRAM 物件的再散布範圍尚未確認，因此不得公開發布。
4. 完成授權確認及實機冷啟動、網路與基本功能驗證後，才能評估提升至 L3。
