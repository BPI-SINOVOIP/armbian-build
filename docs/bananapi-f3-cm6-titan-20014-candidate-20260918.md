# F3／CM6 Titan Code 20014 修正候選紀錄

日期：2026-09-18。狀態：`20260918-rc2` 已產生，通過官方匹配程式與操作清單建立的離線重播；**未完成實際 USB 燒錄、eMMC 獨立開機及基本回讀，不能宣稱已修好或發布為可燒錄完成品。**

## 失敗與證據分類

使用者回報本專案的 eMMC 套件無法由原廠工具燒錄。CM6 失敗畫面顯示 `Code 20014`、固定的 `partition_universal.json` 未匹配、`partition table is null`。修正對象是我們的套件，不歸因於使用者操作。

- [失敗畫面](evidence/bananapi-f3-cm6-vendor/user-report-20260918/titan-flash-error-code-20014.jpg)：實際燒錄失敗證據。
- [正確參考畫面](evidence/bananapi-f3-cm6-vendor/user-report-20260918/titan-selected-package.jpg)：原廠工具正確操作／選檔參考，不是第二次失敗，也不是成功燒錄證據。

舊 `20260916` 兩份 eMMC 套件已標記錯誤／不可用。原 ZIP、SHA、manifest 與當時的離線報告保留；目前狀態另存 `release-status.json` 與撤回通知，避免改寫歷史證據。兩份 SD 仍未完成實機驗證，本次沒有重建或宣稱其成敗。

## 已定位的封裝錯誤

原廠 `MultiFlash._check_partition()` 在一般流程先尋找 `relate_partition` 項目內的 `{...}` 變數；沒有變數即回傳失敗，不會因同名 JSON 已存在而成功。舊版產生器把官方模板改成固定檔名，正好觸發這條分支。明確指定分區表的另一個入口會繞過匹配，不能拿那條路徑證明一般單機燒錄正常。

此結論由官方二進位內的 Python 程式直接執行重播得到，舊設定重現相同 `FlashNotMatchError` 與 Code 20014。重播使用隔離、無網路、無 USB、唯讀環境，沒有自行重寫匹配演算法，也沒有呼叫實際寫入方法。

| 項目 | 舊候選 | `20260918-rc2` |
| --- | --- | --- |
| eMMC 匹配式 | `['partition_universal.json']` | `['partition_{size1}.json']` |
| 媒體查詢 | `mtd-size → size0`、`blk-size → size1` | 保留原廠查詢流程 |
| 分區 JSON | `bootinfo.image` 改為 eMMC header | 恢復原廠通用表的 `factory/bootinfo_sd.bin` 引用 |
| MTD 表／SPI 載荷 | 不提供 | 仍不提供，只匹配區塊裝置分支 |
| 封裝檢查 | 檔案雜湊與檔案系統檢查 | 增加必要成員、官方控制流程及分區表契約；固定檔名、缺查詢、缺載荷、額外 MTD 表會被拒絕 |
| 檔名與媒體身分 | 固定 `20260916` | 必須明確傳入 `YYYYMMDD-rcN`，新版檔名與各板 UUID 獨立 |

恢復 bootinfo 引用依據是原廠實際 JSON 與固定 U-Boot 來源中的特殊 `flash bootinfo` 路徑：程式重建 eMMC header 寫入 `boot0`，並非將 SD header 原樣當作 eMMC 開機資訊。這項源碼判讀仍須以實機 `boot0` 與檔案系統回讀驗證。

## 兩板的官方參考

已分別查核官網入口與發布檔：[板廠格式比對](evidence/bananapi-f3-cm6-vendor/board-format-comparison-20260918.md) 保存來源、HTTP Range、ZIP CRC32、SHA-256 與差異。

- F3：直接取得板廠 minimal／desktop v1.0.12 eMMC ZIP 的控制檔。
- CM6：入門頁實際下載入口指向 F3 頁，教學所列通用 v2.1 ZIP 的控制檔已取回比對；CM6 產品頁另列 `20260109` 的 SD `.img.xz`。
- 上述三包與既有通用 v2.3 的四個控制檔逐位元組相同。**尚未取得以 CM6 專用名義發布的 Titan ZIP，不能把它寫成已核對 CM6 專用 eMMC 套件。**

新版仍分別使用 F3／CM6 的 Armbian 根系統、核心、DTB 與加速鎖，分開建立套件與官方程式重播報告。控制格式有內容相同的證據，不代表兩板硬體相容性已驗證。

## 工具版本與驗證結果

使用者照片沒有顯示 Titan 版本；目前尚未取得使用者實際版本與完整日誌。此次程式重播使用官方下載的 Linux `titantools_for_linux-2.2.0-Rc.AppImage`：

| 內容 | SHA-256 |
| --- | --- |
| AppImage | `0bcd63c138813fbeb3234358efd01eb50c3b91c93cc47a52addee53b76eba5e6` |
| 其中的 flashserver | `adde4756c863c9575170c36555bd501c694e241d3d385d9e8c98e9adcd417716` |

本機另一份舊 backend 亦能重現相同錯誤並接受新版模板，但其 GUI 版本未能確定，不將它冒稱為使用者的版本。官方二進位、Python 版本、函式雜湊與命令另列於 `evidence/titan-matcher-20260918/` 及 [Titan 重播說明](evidence/bananapi-f3-cm6-vendor/titan-matcher-20260918.md)。

已完成：

1. 115 項本機回歸，零失敗、零跳過；包含會拒絕舊錯誤設定的負例。
2. 新版兩包的檔案系統、UUID／開機設定、ZIP 完整性與控制契約檢查。
3. 官方匹配程式：舊固定檔名重現 Code 20014；新版在 `size1=universal` 下選中 `partition_universal.json`；未識別到區塊裝置的 `NULL` 狀態仍拒絕。
4. 對新版每板實包使用完整官方 `MultiFlash` 類別，建立並驗證八項操作：GPT、bootinfo、FSBL、env、OpenSBI、U-Boot、bootfs、rootfs；引用檔案均存在，bootfs／rootfs 的 gzip 等級為 5。

上述沒有執行 GUI 對 USB 裝置的完整流程，媒體變數由重播提供。因此**官方程式重播通過不等於實際 Titan 燒錄通過**；目前不存在這兩個新版套件的完整 USB 燒錄日誌、開機日誌或回讀報告。

## 新版絕對路徑與雜湊

交付索引：`/media/pi/SMCI/bpi/f3-cm6-vendor-20260918-rc2/candidates/README.md`。

F3：

```text
/media/pi/SMCI/bpi/f3-cm6-vendor-20260918-rc2/candidates/bpi-f3-emmc/Armbian_Noble_bpi-f3_gnome_titan-emmc_20260918-rc2.zip
SHA256 865fc0cd05b0ee87257c3e637246e127b95b383d47e1befa61dc588699c99b9b
```

CM6：

```text
/media/pi/SMCI/bpi/f3-cm6-vendor-20260918-rc2/candidates/bpi-cm6-emmc/Armbian_Noble_bpi-cm6_gnome_titan-emmc_20260918-rc2.zip
SHA256 f62d471ef88bf042af84bbd7da6aa4e031159488a4447d49a4b3e0cbc4595277
```

舊錯誤檔仍在 `/media/pi/SMCI/bpi/f3-cm6-vendor-20260916/delivery/`，沒有覆寫。新版只列為待實機重測的候選，維持原廠正常選檔與單機燒錄流程。

## 完成修復仍需的實機結果

每板各須保存：實際 Titan 版本與安裝檔雜湊、選取 ZIP 雜湊、完整連線／選表／燒錄日誌；燒錄完成後移除其他媒體，以 eMMC 獨立冷開機，再回傳根／boot UUID、實際板型／核心、分區表與 bootfs 重要檔案回讀。CM6 另記錄載板型號與版本。

所有結果都須對應本次 `20260918-rc2`，不能沿用原廠其他映像或舊候選的成功紀錄。待上述證據齊備後才判定修復完成；GPU／AI／VPU 的實際正確性與效能仍另行驗證。
