# X2 Noble XFCE 擴大板群啟動結果

## 結論

本批共收到 17 份 UART 紀錄。`450600027` 已由使用者確認為缺件的硬體不良
板，`450601162` 則只有 UTF-8 BOM、沒有可判定內容；排除這兩片後，15 片
可判定樣本中有 13 片進入 Linux 使用者空間、2 片失敗：

- 可判定板級功能啟動為 `13/15`，即 `86.7%`。
- 舊 V2 八板矩陣的同一組樣本，本次 X2 為 `8/8`；其中
  `450600146`、`450600826`、`450601075` 已由 V2 失敗變為單次啟動通過。
- `450600667` 在 DDR rank／width 探測的 `rc` 階段持續失敗，74 次 SPL
  啟動均未進入 U-Boot。
- `450600845` 完成 DDR、U-Boot、initrd checksum 與 kernel handoff，之後
  發生 PID 1 結束造成的 kernel panic；現有紀錄不足以把根因歸到 DDR、
  rootfs、SD 卡或使用者空間。

這證明 X2 相對 V2 有明顯改善，但也直接否定「目前 X2 已可視為全板穩定版」
的說法。本批沒有受控重複冷啟動、映像回讀雜湊或記憶體壓力證據，不能納入
G2、G3、G4 或量產通過率。

## 證據來源

原始檔已原樣保存：

```text
docs/evidence/bananapi-m4zero-opi-ddr/hardware/X2-Noble-XFCE-batch-20260817/source-uart-logs.zip
docs/evidence/bananapi-m4zero-opi-ddr/hardware/X2-Noble-XFCE-batch-20260817/source-test-sheet.xls
```

| 原始檔 | SHA-256 |
| --- | --- |
| UART ZIP | `f46f92b7c3ba135e1b0b38d005343ed9e5356c6ce6111f1e4269545e05606c7e` |
| 測試表 XLS | `438e00049cebc513f494ed48d0a9a295d80cffcaffeb3ac689b41c7bdf95c70d` |

ZIP 已通過 `unzip -t`。逐檔結果與雜湊另存於：

```text
docs/evidence/bananapi-m4zero-opi-ddr/hardware/X2-Noble-XFCE-batch-20260817/results.tsv
docs/evidence/bananapi-m4zero-opi-ddr/hardware/X2-Noble-XFCE-batch-20260817/SHA256SUMS
```

所有 15 份非空 UART 都顯示 X2 bootloader 的 `P1f88` Build ID、
`M4ZDDR1` 診斷格式、`clk=792` 與相同 DDR 參數。能進入使用者空間的紀錄
也顯示 Noble 與 Linux `6.18.32-current-sunxi64`。但 UART 沒有記錄完整映像
SHA-256 或 SD 回讀雜湊，所以只能確認軟體特徵一致，不能建立逐位元映像綁定。

交付矩陣中對應的 Noble XFCE XZ 為：

```text
Armbian-unofficial_26.05.0-trunk_Bananapim4zero_noble_current_6.18.32_x2-cross-board-792mhz_xfce_desktop.img.xz
SHA-256 e148b33abc2ca4384bb40f8269d9cd99ae1d863f59f94ee05b89b663d5f97443
```

## 逐板結果

| 板號／標籤 | DRAM | SPL 次數 | 判定 | 最後證據 |
| --- | ---: | ---: | --- | --- |
| `450300156` | 4,096 MiB | 1 | 通過 | 進入登入與 root shell |
| `450600027` | 無 | 0 | 排除 | 確認缺件、硬體不良；UART 只有 3 bytes BOM |
| `450600146` | 4,096 MiB | 1 | 通過 | 完成首次設定並進入 root shell |
| `450600285` | 4,096 MiB | 1 | 通過 | 進入登入與 root shell |
| `450600327` | 4,096 MiB | 1 | 通過 | 進入登入與 root shell |
| `450600444` | 4,096 MiB | 1 | 通過 | 進入登入與 root shell |
| `450600667` | 未識別 | 74 | 失敗 | 296 次 `stage=rc result=fail`，未進 U-Boot |
| `450600760` | 4,096 MiB | 1 | 通過 | 進入登入與 root shell |
| `450600826` | 4,096 MiB | 1 | 通過 | initrd checksum 通過並進入 root shell |
| `450600827` | 4,096 MiB | 1 | 通過 | 進入登入與 root shell |
| `450600845` | 4,096 MiB | 1 | 失敗 | kernel handoff 後 PID 1 結束並 panic |
| `450601023` | 4,096 MiB | 1 | 通過 | 進入登入與 root shell |
| `450601075` | 4,096 MiB | 1 | 通過 | initrd checksum 通過並進入 root shell |
| `450601162` | 無 | 0 | 無法判定 | 檔案只有 3 bytes BOM |
| `test1` | 4,096 MiB | 1 | 通過 | 進入登入與 root shell |
| `test2` | 4,096 MiB | 1 | 通過 | 進入登入與 root shell |
| `測試板1` | 2,048 MiB | 1 | 通過 | 單 Rank fallback 後進入 root shell |

此處的 `13/15` 是「每個具有效紀錄的板號是否至少完成這次啟動」統計，不是
每次 boot attempt 統計。`450600667` 的 74 次自動重設是同一份紀錄內的
連續失敗，不應與其他板的單次紀錄混算成穩定率。

## 與 V2 同板比較

舊 V2 Noble XFCE 受控矩陣包含下列八片：

```text
450600146 450600285 450600327 450600760
450600826 450601023 450601075 測試板1
```

| 版本 | 同八板通過 | 同八板失敗 | 限制 |
| --- | ---: | ---: | --- |
| V2 `P872a` 792 MHz | 5 | 3 | 單次功能矩陣 |
| X2 `P1f88` 792 MHz | 8 | 0 | 本次也只有每板一份有效啟動紀錄 |

X2 對三片舊弱板的單次修正結果成立，但尚未證明它們的冷啟動機率、長時間
資料完整性或溫度／電壓角落穩定性。

## 兩個失敗邊界

### `450600667`

每次 SPL 都使用正確的 X2 profile，但雙／單 Rank 與 x32／x16 四個候選均
在 `rc` 階段五次重試後失敗，然後自動重設。此失敗發生在 DRAM geometry
識別及 U-Boot 之前，是明確的 DDR 初始化邊界。

試算表卻記錄 `450300667`，並註明「板接觸不良（壞板）」；ZIP 檔名則是
`450600667`。未確認兩者是否同一片實物前，不能把這 74 次失敗單獨歸因為
X2 margin，也不能套用表格中的 DDR／eMMC 料號。

### `450600845`

這片完成 4 GiB 雙 Rank geometry、TF-A、U-Boot、initrd checksum 與 DTB
載入，之後在 `Starting kernel` 後只有下列核心結論：

```text
Kernel panic - not syncing: Attempted to kill init! exitcode=0x00000100
```

PID 1 以狀態 1 結束是結果，不是根因。現有 quiet kernel log 沒有保留 init
先前的錯誤，因此不能宣稱是單純使用者空間問題，也不能直接宣稱是 DDR 資料
損壞。試算表也把此板標為 FAIL，但表頭仍是 U0 480 MHz 舊映像名稱；這項
交叉證據必須先確認測試人員是否忘記更新表頭。

## 試算表限制

原始 XLS 最後儲存於本批紀錄時間附近，但表頭與工作表名稱仍指向：

```text
Armbian-unofficial_26.05.0-trunk_Bananapim4zero_noble_current_6.18.32_u0-safe-480mhz_xfce_desktop.img
```

因此本文件只把 XLS 當作板號、DDR、eMMC 與人工備註的輔助來源，不用其
PASS／FAIL 欄位取代 UART 判定。另有以下資料品質問題：

- ZIP 的 `450600667` 與 XLS 的 `450300667` 不一致。
- `test1`、`test2` 沒有正式序號。
- `450600027` 的表格註記為「板無 log 輸出」，UART 也只有 BOM；使用者於
  2026-08-17 進一步確認該板缺件、屬於硬體不良，因此從軟體驗證母體排除。
- `450601162` 表格標示 PASS，但 UART 也是空檔，故本次仍列無法判定。
- `L04`／`LO4`、`BO41`／`B041` 等字元仍需用 BOM 或清晰顆粒照片校正。

## 下一輪最小驗證

1. 先核對 `450600667`／`450300667` 實物序號並排除板接觸、電源、SD 卡與
   UART 接線問題；同板分別執行 U0 480 MHz 與 X2 792 MHz 各 10 次完全
   斷電冷啟動。
2. 對 `450600845` 使用已回讀驗證的同一張 SD，分別重跑 U0 與 X2；X2
   開啟 `loglevel=8 ignore_loglevel initcall_debug`，保留 PID 1 結束前訊息。
3. `450600027` 修復缺件並完成硬體檢查前不再參與 X2 統計；重新收集
   `450601162`，並補齊 `test1`、`test2` 的正式序號。
4. 對本次三片舊 V2 失敗板先做 X2 每片 10 次冷啟動，再執行至少兩小時
   記憶體、MMC、CPU 並行壓力；這些結果通過前，X2 維持候選狀態。
5. 後續每份紀錄在上電前寫入映像 SHA-256、SD 回讀 bootloader SHA-256、
   板號、DDR 料號、電源與測試輪次，避免只靠檔名與 Build ID 推定輸入。
