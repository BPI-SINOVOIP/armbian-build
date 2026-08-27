# Banana Pi BPI-R3 Mini 完整映像 L2 校準紀錄

更新日期：2026-08-28

## 結論

提交 `f047913c8` 已從乾淨專用 OverlayFS 上層完整建置 Debian Trixie current 精簡 CLI 映像。IMG、XZ、GPT、根檔案系統、映像內 DTB、最終核心與 U-Boot 設定，以及 BL2／FIP 實際位元組均可唯讀解析，足以固定下一次正式 L2 重建的精確來源契約。

這次輸出只屬校準，不是正式 L2 證據。校準資料曾以臨時修正的驗證契約讀取；正式流程必須先提交本文件與精確契約，刪除校準輸出及專用上層，再從該提交乾淨重建並由追蹤中的驗證器通過。不得沿用本次 `R3MINI_CALIBRATION.json` 宣稱 L2。

## 建置身分

| 項目 | 值 |
| --- | --- |
| 板卡 | `bananapir3mini` |
| 分支 | `current` |
| 發行版 | `trixie` |
| 使用者空間 | 精簡 CLI |
| 建置提交 | `f047913c8` |
| 來源樹 | `33d16d8a500f2f0a94ed16f5a6afd335b419d7f2` |
| 固定時間戳 | `1787793187` |
| 映像目錄 | `output/images/2026.08/bananapi-filogic-mt7986-r3mini-emmc-trixie-current-cli` |

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| IMG | 1426063360 | `493e1dd853fc074b14ff9a1b55570527885c89ce9bcf46fe404ca55ee83e1912` |
| XZ | 330278480 | `cf2e88d21638281c2a64ce842fd07ef1a2192fd0e67e3e46526d4ae21337f9f8` |

## 校準後契約

| 項目 | 大小 | SHA-256 |
| --- | ---: | --- |
| `bl2.img` | 204889 | `6a7a83f1406d51227b169af1a30b4d84da42867785021deaf901595531421c8b` |
| `u-boot.fip` | 510681 | `4f25bdd6d6085226a8807615bfc5d13e98b27289a389a9fab90ad417950f949e` |
| GPT 範本 | 17408 | `beb31c2284ec7b8e910faeea8d323f40532b26010e87d0bae851d823705efa1d` |
| 映像內 DTB | 23104 | `5457155de554539c902a22507cbd69ad249fd70a24cf6e24a5753c2b5e8b66ab` |
| 最終核心設定 | 不適用 | `2c6ea26327285e71fa778ccb360269796da043faa69d0fee4e467f1a0c36367e` |
| 最終 U-Boot 設定 | 不適用 | `003fc226041c534893f3aa44b1158fc22fdcab7870db9351a03045c7324025b8` |

U-Boot 明確停用 `CONFIG_CMD_BOOTMENU`。`CONFIG_AUTOBOOT_MENU_SHOW` 因相依選項關閉而不出現在最終 `.config`，因此精確契約檢查前者，不再要求不存在於檔案中的相依符號。

## 分割區校準

| 編號 | 名稱 | 起始 sector | sector 數 |
| ---: | --- | ---: | ---: |
| 1 | `bl2` | 34 | 8158 |
| 2 | `ubootenv` | 8192 | 1024 |
| 3 | `factory` | 9216 | 4096 |
| 4 | `fip` | 13312 | 8192 |
| 5 | 空名稱 | 32768 | 2750464 |

第五分割區為 `ext4`，檔案系統標籤是 `armbi_root`。GPT 分割區名稱確實為空，不得以萬用名稱或自行填入 `rootfs` 取代實際校準結果。

## 證據限制

- 本次沒有寫入 eMMC，也沒有驗證 `boot0`、boot partition enable 或冷啟動。
- 沒有實機網路、Wi-Fi、PCIe、USB、GPIO、重啟、斷電或壓力測試。
- ATF 的 MT7986 預編譯 DRAM 物件再散布範圍仍未釐清，公開發布維持阻擋。
- IMG 與 XZ 雜湊只識別本次校準輸出；正式 L2 重建必須產生自己的物質證據與完成狀態。
