# F3／CM6 官方刷機格式來源比對（2026-09-18）

本次已從 F3 板廠發布的兩份 eMMC ZIP 直接抽取控制檔；它們與 CM6 入門頁所列版本的通用套件、既有 SpacemiT v2.3 參考套件，四份 `fastboot.yaml` 與三個分區 JSON 均逐位元組相同。這建立了 F3 板廠包與通用配套之間的實際內容證據。

CM6 產品頁目前列出的 Bianbu 板廠檔是 `.img.xz`，未取得另以 CM6 命名的 Titan ZIP。因此不能將上述結果寫成「已核對 CM6 專用 eMMC 發布包」，也不能由控制格式相同推定各板的核心、DTB、U-Boot 或實機行為相同。

## 官網入口與實際版本

| 來源 | 此次核對結果 |
| --- | --- |
| [F3 產品頁](https://docs.banana-pi.org/en/BPI-F3/BananaPi_BPI-F3) | Bianbu 區列出板廠 `BPI-F3_Bianbu_23.10_v1.0.12_20240802` 的 Google Drive 資料夾，並把較新版本導向 SpacemiT 映像庫。 |
| [F3 板廠 v1.0.12 資料夾](https://drive.google.com/drive/folders/1U9kma2_2UK4MTvuVA2TPKY72N6n8r8Xa) | 同時列出 minimal／desktop 的 `.zip` 與 `.img.zip`，以及各自的 `.md5`；不是把整碟映像改名成 eMMC 套件。 |
| [F3 入門頁](https://docs.banana-pi.org/en/BPI-F3/GettingStarted_BPI-F3) | 區分 `.img.zip` 的 SD 映像與 `.zip` 的 Titan eMMC 套件。 |
| [CM6 產品頁](https://docs.banana-pi.org/en/BPI-CM6/BananaPi_BPI-CM6) | Bianbu 區的板廠檔為 `20260109-bianbu-lxqt-v2.3-bpi-cm6.img.xz`；[Drive 檔案](https://drive.google.com/file/d/1e_FEqCNwOYHuUwE1HZ3IPP221RSoVMXv/view)大小為 `1424722976` 位元組。本機先前參考是 `20260106` 版，不能把兩個版本混稱同一檔。 |
| [CM6 入門頁](https://docs.banana-pi.org/en/BPI-CM6/GettingStarted_BPI-CM6) | 同樣區分 SD 與 eMMC 格式，但實際下載連結指向 `/en/BPI-F3/BananaPi_BPI-F3#_system_image`。其中整碟寫入範例使用通用 `bianbu-24.04-desktop-k1-v2.1-release-20250124144655.img`；這不是 CM6 專用 ZIP 的發布證明。 |

此表記錄的是查核日可取得的入口，不主張全網不存在 CM6 專用 ZIP。頁面 HTML 雜湊與下載連結保存在 `official-pages.json`；Google Drive 檔案 ID、檔名、大小與時間保存在 `drive-metadata.json`。

## 直接抽取的 ZIP 證據

| 來源 | ZIP 位元組數 | 本次正式 Range 擷取量 | 證據子目錄 |
| --- | ---: | ---: | --- |
| [F3 板廠 minimal v1.0.12](https://drive.google.com/file/d/1SpzGQbNZDDkCnwbrGXz5BzEA670uPF8i/view) | 212736667 | 2982 | `f3-bpi-v1.0.12-minimal` |
| [F3 板廠 desktop v1.0.12](https://drive.google.com/file/d/1yXuzBlRMnyKgAi3NhRWPnCwxKxFwlidS/view) | 2711973542 | 3050 | `f3-bpi-v1.0.12-desktop` |
| [CM6 教學所列版本的通用 desktop v2.1](https://archive.spacemit.com/image/k1/version/bianbu/v2.1/bianbu-24.04-desktop-k1-v2.1-release-20250124144655.zip) | 2615638690 | 3050 | `cm6-guide-generic-v2.1` |
| [既有通用 LXQt v2.3](https://archive.spacemit.com/image/k1/version/bianbu/v2.3/Bianbu-LXQt-K1-V2.3.0-20251212104943.zip) | 2008730989 | 3050 | `spacemit-v2.3-lxqt` |

四份套件各有 16 個 ZIP 成員；下列控制檔 SHA-256 完全相同：

| 檔案 | SHA-256 |
| --- | --- |
| `fastboot.yaml` | `95642b765cff5b7c6e2439e44b1b7ea70a77fd79ced3e37a907185f383545278` |
| `partition_2M.json` | `a14ca12559299f6fbf750465fcc87727b38445e5c0a45516394e6326396a8feb` |
| `partition_flash.json` | `5f1e483082de5586dcae2b8f979b89a2f0f090cc93bd2f26b80689b654a755b4` |
| `partition_universal.json` | `568d9848097c72ba01ded40534022d35be7dda57ece13863194dd3a442d9568e` |

擷取工具要求 HTTP `206`、正確 `Content-Range` 與完整區段長度；Python `zipfile` 驗證控制檔解壓後 CRC32。各 `retrieval.json` 保留請求偏移、區段雜湊、ZIP 中央目錄、每檔 SHA-256。本次未下載完整映像，沒有重新計算完整 ZIP 雜湊；板廠 minimal `.zip.md5` 的 `ffeee988e840c9dcd6911aaff4fe69b9` 僅記錄為發布者提供值。

為保留 SHA-256 可重查性，抽出的原廠控制檔維持原始位元組；其中既有註解亦未翻譯。本文與新增解說使用繁體中文。

## 20260916 候選的確定差異

本節比較的是已封裝的 `output/bpi-{cm6,f3}-emmc/Armbian_Noble_bpi-{cm6,f3}_gnome_titan-emmc_20260916.zip`，不是後續修正版本。

| 項目 | 四份官方來源 | 兩份 20260916 候選 | 解讀 |
| --- | --- | --- | --- |
| ZIP 頂層 | `fastboot.yaml`、分區 JSON、載荷直接位於根層；只有 `factory/` 是子目錄 | 相同根層位置 | 沒有多包一層目錄的差異。 |
| 套件檔名 | 既有小寫 `bianbu-…`，亦有大寫 `Bianbu-LXQt-…` | `Armbian_Noble_…` | 不能只憑外層檔名差異解釋分區匹配失敗；真正匹配條件應由 Titan 實作核對。 |
| ZIP64 | 桌面原廠包因大型 `rootfs.ext4` 使用 ZIP64 | 同樣使用 ZIP64 | ZIP64 不是候選獨有格式。 |
| `getvar` | 保留 `mtd-size → size0`、`blk-size → size1` | 兩個查詢皆仍存在 | 錯誤原因不是少了這兩個查詢。 |
| `relate_partition` | `['partition_{size0}.json', 'partition_{size1}.json']` | `['partition_universal.json']` | 確定移除了官方模板中的變數。 |
| 分區表 | `partition_2M.json`、`partition_flash.json`、`partition_universal.json` | 只有 `partition_universal.json` | 僅提供 eMMC 分支時應保留其原有模板語法，不能只靠檔案存在判定匹配成立。 |
| universal 的 `bootinfo.image` | `factory/bootinfo_sd.bin` | `factory/bootinfo_emmc.bin` | 這是分區 JSON 唯一語意差異；偏移、大小、`holes`、`hidden`、壓縮設定相同。 |

兩份候選控制檔彼此相同：`fastboot.yaml` SHA-256 為 `181a77ebb289a92b0c5b12a08e25387704a659fec4ae85ec0bba91d00c7747cf`，`partition_universal.json` 為 `dcc90441c298b11815b434f3ca4535cb6cd3fc5d652594680ede69424e353d56`。已讀取 ZIP 成員中的檔案驗證，沒有只比較工作目錄的未封裝副本。

原廠比候選多出的六個成員是 `factory/` 目錄紀錄、`factory/bootinfo_spinor.bin`、`factory/bootinfo_spinand.bin`、`genimage.cfg`、`partition_2M.json`、`partition_flash.json`。此差集不代表應把 SPI 寫入流程加入 eMMC 成品；應讓最終採用的控制流程與載荷引用完整相符。

## 修正依據與驗證界線

主流程已將 eMMC 分支改為 `['partition_{size1}.json']`，並保留原本兩個 `getvar`。這保留官方區塊裝置的變數展開方式，同時避免啟用 MTD 分支。此文件負責來源位元組比對；Titan `MultiFlash` 如何拒絕無變數的固定檔名，另以該工具的實際解析證據判定，不能拿原廠檔案相似度代替執行證明。

恢復原廠 `bootinfo_sd.bin` 引用亦有 U-Boot 實作依據：[固定提交的 `fb_mmc.c`](https://github.com/spacemit-com/uboot-2022.10/blob/db67f9dc4d26a45a29e3be085eb5b783aae8e965/drivers/fastboot/fb_mmc.c) 將 `flash bootinfo` 導向 `fastboot_oem_flash_bootinfo()`，由程式重建 eMMC header，寫入 eMMC 硬體分區 1 的偏移 0，即 `boot0`。因此不是看到 `sd` 字樣就假設只能寫 SD，也不是只憑檔名更換 header。既有細節列於 [格式配套說明](../../../config/spacemit-k1-vendor/README.md)。本次未操作硬體，實際 `boot0` 回讀、Titan 刷寫完成與冷開機仍須實機驗證。

## 證據位置與已執行檢查

全部新增小型證據位於：

```text
/media/pi/SMCI/bpi/f3-cm6-vendor-20260916/reference/board-format-20260918/
```

`probe_zip.py` 可重跑 Range 擷取，必須提供新輸出目錄；`comparison-check.json` 記錄本次已通過的比對：四份來源的四個控制檔逐位元組相同、每檔 SHA-256 符合擷取記錄、候選 YAML 除分區選擇式之外動作一致、候選分區 JSON 除 `bootinfo.image` 外語意一致。四份正式擷取合計 `12132` 位元組，不包含先前探索連線。本次沒有改既有映像、封裝程式或配套鎖。
