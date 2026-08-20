<div class="cover" markdown="1">
<div class="cover-band"></div>
<div class="cover-content" markdown="1">
<p class="cover-kicker">BANANA PI M4ZERO｜工程交付報告</p>

# Banana Pi M4Zero A1<br>移植修正與 DDR 調適報告

<p class="cover-subtitle">從 X2 反證、0845 參數窗口收斂，到十套工程映像與四板標準啟動覆蓋</p>

<div class="cover-status">交付狀態｜A1 792 MHz 工程候選；後續 Gate 未完成</div>

| 文件欄位 | 內容 |
| --- | --- |
| 報告日期 | 2026-08-20 |
| 證據基準提交 | `65864f36712b8fce0b56b86f725c8b0447816a22` |
| 標準 bootloader Build ID | `2026.01-S127a-P02e5-Hc6a9-V3946-Be6d8-R448a` |
| 目標時脈 | 792 MHz |
| 對象 | 主管、韌體／硬體工程團隊、合作夥伴 |
| 文件語言 | 繁體中文 |

<p class="cover-note">本報告只陳述儲存庫內可追溯的 source、Git 歷史、測試紀錄與 evidence。文件不構成量產、穩定版、法規或對外品質承諾。</p>
</div>
</div>

<section class="management-summary" markdown="1">

# 管理摘要

## 決策結論

Banana Pi M4Zero A1 已完成一組可追溯的 **792 MHz 工程候選**。0845 板上的原 X2 設定在 M2 測試五次中出現一次明確資料位元錯誤（4/5），足以判定該設定在這片板上的 DDR lane 裕量不足，不能再把先前的啟動異常只歸因於 rootfs。A1 保留 `TPR6=0x3a808080`，將 lane packed 參數改為 `TPR11=0x25252523`、`TPR12=0x110f0f10`；0845 的已觀察 TPR6 通過窗口為 `0x30..0x42`，兩側刻意失敗邊界為 `0x2e` 與 `0x44`。

## 已完成且可主張

| 面向 | 已完成證據 | 可主張範圍 |
| --- | --- | --- |
| 參數收斂 | 0845 共 322 筆；270 通過、52 筆收斂期負向結果 | 原 X2 在 0845／792 MHz 的 lane 裕量不足；A1 中心候選通過該輪熱重設實驗 |
| 強測 | 64 MiB、M2、五輪 pattern `20/20`；480 MHz 安全恢復 `20/20` | 中心候選在 0845、同次上電後 watchdog 熱重設條件下零失敗 |
| 標準映像 | `P02e5`；10 套 IMG/XZ；共同 792 MHz A1 參數 | 建置、封裝、Build ID、bootloader 與雜湊鏈具可追溯性 |
| 標準啟動 | 0845、0438、1116 為 4 GiB／雙 Rank；0256 為 2 GiB／單 Rank | 同參數與 Noble 使用者空間在四板完成標準 Linux 啟動 G1 |
| 發行矩陣 | Bookworm、Jammy、Noble、Resolute、Trixie，各 CLI／XFCE | 10 個 XZ 與其 IMG 串流身分已驗證；不是十套逐一完成硬體壓力的聲明 |

## 目前決策與資源優先序

1. **可交付工程驗證，不可量產放行。** `P02e5` 與十套映像可作下一階段受控測試輸入；資格仍應維持 `A1_0845_M4ZLAB2_PASS_COLD_BOOT_PENDING`。
2. **先關閉三個主要缺口。** 受控完全斷電冷啟動、燒錄後映像／bootloader 回讀、Linux 長時間記憶體與 CPU／儲存並行壓力，均尚無 A1 合格證據。
3. **再擴大共同窗口。** 四板 G1 證明啟動覆蓋，但不能取代跨容量、Rank、弱板、溫度、供電與批次的共同 Gate。
4. **保留可回復路徑。** 480 MHz 為實驗器安全恢復錨點；在 Gate 全數關閉前，不應移除保險映像或把 A1 標示為穩定／量產版本。

<div class="summary-verdict"><strong>主管核准建議：</strong>核准 A1 進入受控 G2～G5 驗證；暫不核准量產、穩定版或對外相容性承諾。</div>

</section>

<div class="toc-wrapper" markdown="1">

# 目錄

[TOC]

</div>

# 1. 報告目的、範圍與判讀原則

本報告整合 Banana Pi M4Zero 從原 X2 DDR 設定到 A1 候選的移植修正、參數收斂、建置封裝、映像矩陣與標準啟動證據，目的是讓管理、韌體、硬體及合作夥伴在同一份文件中看見：**做了什麼、為何這樣選、證據支持到哪裡，以及尚不能宣稱什麼。**

## 1.1 證據截止點

| 項目 | 基準 |
| --- | --- |
| Git 分支 | `bpi-m4zero-opi-ddr-port-20260813` |
| 報告使用的既有證據截止提交 | `65864f36712b8fce0b56b86f725c8b0447816a22` |
| 分支於報告起草時的遠端狀態 | 本機 HEAD 與 `origin/bpi-m4zero-opi-ddr-port-20260813` 同為上述提交 |
| 實驗 SPL Build ID | `2026.01-S127a-P2cea-Hc6a9-V3946-Be6d8-R448a` |
| 標準 A1 bootloader Build ID | `2026.01-S127a-P02e5-Hc6a9-V3946-Be6d8-R448a` |
| 標準映像核心 | `6.18.32-current-sunxi64` |

> **身分區隔：** `P2cea` 是執行期 DDR 掃描用的 M4ZLAB2 SPL；`P02e5` 才是十套交付映像與四板 G1 紀錄中的標準 A1 bootloader。兩者用途不可混用。

## 1.2 判讀規則

- 「通過」只對應該筆紀錄的板號、測試層級、重設方式、映像或啟動階段。
- M4ZLAB2 的 322 筆資料均在同一次上電後以 watchdog 熱重設執行，不計為完全斷電冷啟動。
- 52 筆失敗是收斂期間保留的負向結果，涵蓋刻意搜尋窗口邊界與舊設定反證；不是 A1 中心候選的失敗率。
- 四板 UART 支持 Noble 標準啟動 G1；因紀錄沒有完整映像檔名與媒體回讀 SHA-256，不能把它們綁定到特定 CLI／XFCE 檔案。
- 本報告引用已納入 Git 的遮蔽證據，不列出任何網路識別、認證值或其他秘密。

# 2. 移植背景與原始問題

## 2.1 從板級可啟動到跨板可用

BPI-M4 Zero 使用 Allwinner H618／H616 DDR 初始化路徑。早期移植先引入板級定義，再以 Orange Pi Zero 3 參數作起點；後續 X2 透過 0438 與 1116 的執行期實驗，把 792 MHz 共同候選收斂到 `TPR6=0x3a808080`，並保留 geometry 自動探測。X2 在既有板群有可用證據，但 0845 的實際啟動與資料完整性表現顯示，既有 lane packed 參數並未涵蓋新板的裕量。

source 追溯入口：

- `patch/u-boot/v2026.01/board_bananapim4zero/013-bananapi-m4zero-use-orangepi-zero3-ddr-baseline.patch`
- `tools/bpi-m4zero-ddr-lab-profile-cross-board-candidate-792.json`
- `patch/u-boot/v2026.01/board_bananapim4zero/016-bananapi-m4zero-use-0845-validated-ddr-lanes.patch`
- `tools/bpi-m4zero-ddr-lab-profile-0845-candidate-792.json`

## 2.2 0845 的症狀與反證

原 X2 792 MHz 中心參數在 0845 的 M2 基準測試為 `4/5`。失敗不是只有逾時或啟動停滯，而是 `words` pattern 的明確資料差異：期望值末尾為 `...9d`，實際值為 `...9f`。M0 在 480～792 MHz 的十個點合計 `50/50`、M1 在 672～792 MHz 的七個點合計 `35/35`，都沒有抓到此問題；只有覆蓋 Rank 邊界與較大窗口的 M2 顯示 792 MHz 間歇錯誤。

原 X2 lane 在 M2 頻率掃描中，720～780 MHz 各為 `5/5`，792 MHz 為 `4/5`。這個頻率相關性支持「0845 在 792 MHz 的現有參數裕量不足」判斷；它不等於已證明特定晶粒、佈線、電源或溫度是唯一物理根因。

| 症狀 | 證據 | 工程判讀 |
| --- | --- | --- |
| X2／0845／792 MHz M2 為 4/5 | `M4ZLAB2-hardware-0845-20260819.md` | 可重現且不是全數通過 |
| 失敗含資料位元差異 | `words` pattern 的 expected／actual 不同 | 至少有 DDR 資料可靠性反證，不能只歸因於 rootfs |
| M0、M1 未揭露；M2 揭露 | M0 `50/50`、M1 `35/35` | 粗掃可快速排除明顯壞點，但不足以作 792 MHz 放行 |
| 720～780 通過，792 間歇失敗 | X2 lane M2 頻率掃描 | 指向高頻設定裕量，不足以定案物理根因 |

# 3. 根因判斷與 A1 修正策略

## 3.1 可由證據成立的根因層級

本輪可確認的根因層級是：**原 X2 的 `TPR11/TPR12` lane packed 配對在 0845、792 MHz、M2 條件下裕量不足。** 不能由單片板與熱重設實驗外推為所有 M4Zero、特定 DDR 顆粒或 PCB 批次的共同物理缺陷。

## 3.2 配對實驗

以 1116 原廠動態訓練輸出作候選來源，0845 上的短測比較如下：

| `TPR11` | `TPR12` | 與 X2 關係 | 0845／792 MHz M2 | 判讀 |
| --- | --- | --- | ---: | --- |
| `0x24242422` | `0x110f1111` | X2 原設定 | 4/5 | 有明確資料錯誤，不接受 |
| `0x24242422` | `0x110f0f10` | 只換 `TPR12` | 3/5 | 單換 `TPR12` 未改善 |
| `0x25252523` | `0x110f1111` | 只換 `TPR11` | 5/5 | 顯示 `TPR11` 是主要觀察差異 |
| `0x25252523` | `0x110f0f10` | 兩者成對更新 | 5/5；後續強測 20/20 | 作為 A1 lane pair，仍須完整 Gate |

雖然短測顯示 `TPR11` 的影響較大，`TPR11/TPR12` 是 packed lane 參數，且短測樣本有限，因此決策不是拆開採用，而是成對更新後重新掃描 `TPR6` 窗口與失敗邊界。

## 3.3 修正原則

1. 保留已經有跨板依據的時脈、驅動、ODT、`TPR6` 與 `TPR10`。
2. 只修改由 0845 反證指出的 `TPR11/TPR12` lane pair，縮小變因。
3. 以 M0／M1 粗掃、M2 揭露資料錯誤，再以 64 MiB／五輪 pattern 增加中心候選強度。
4. 每次候選結束後恢復 480 MHz 安全設定；壞候選由 watchdog 重啟同一實驗 SPL，保留失敗樣本。
5. 標準 U-Boot 與完整映像使用不同 Build ID，避免把實驗器結果誤認為正式啟動結果。

# 4. 測試策略與決策流程

## 4.1 可重現流程圖

| 階段 | 輸入／動作 | 通過條件 | 失敗處置／輸出 |
| ---: | --- | --- | --- |
| 1 | 480 MHz 安全 profile 啟動 M4ZLAB2 | `READY` 與 geometry 完整 | 停止掃描，保留 UART |
| 2 | M0 頻率與基本初始化粗掃 | 快速排除明顯壞點 | 降頻或縮小候選範圍 |
| 3 | M1 擴充地址／Rank 覆蓋 | 候選無錯誤 | 回到 lane／時脈單變因 |
| 4 | M2 對 X2、lane pair 與 `TPR6[31:24]` 掃描 | 找到兩側失敗邊界與中間零失敗區 | 將失敗保留為負向證據 |
| 5 | 中心候選 64 MiB、五輪 pattern | 測試 `20/20` 且安全恢復 `20/20` | 不建立標準候選 |
| 6 | 建置標準 U-Boot／TF-A／DEB／IMG/XZ | Build ID、設定、二進位與雜湊一致 | 退回 source／封裝修正 |
| 7 | 多板 Noble 標準 Linux 啟動 G1 | geometry、核心、登入、正常關機 | 保留完整 UART，分板分析 |
| 8 | 受控冷啟動、回讀、長時間壓力 | 依預先凍結 Gate 全數通過 | A1 維持工程候選 |

**決策鏈：** X2 資料錯誤反證 → lane pair 單變因比較 → TPR6 上下邊界 → 中心強測與安全恢復 → `P02e5` 標準映像 → 四板 G1 → 尚待 G2～G5。

## 4.2 測試層級的角色

| 層級 | 角色 | 本輪發現能力 | 不可替代項目 |
| --- | --- | --- | --- |
| M0 | 初始化與頻率粗掃 | 快速排除完全不可用候選 | M2 資料可靠性、Linux 壓力 |
| M1 | 擴充地址／Rank 覆蓋 | 比 M0 更早淘汰不穩定點 | 64 MiB 強測與全容量壓力 |
| M2 | 分散窗口、Rank 邊界、pattern／benchmark | 抓到 X2 在 0845 的間歇位元錯誤 | 受控冷啟動、長時間與環境角落 |
| Linux G1 | 標準 bootloader 到使用者空間 | 驗證標準啟動鏈與 geometry | 完整映像身分、冷啟動次數、長時間壓力 |

# 5. X2 與 A1 參數差異及選擇依據

## 5.1 完整參數比較

| 參數 | X2 792 MHz | A1 792 MHz | 變更 | 選擇依據 |
| --- | --- | --- | --- | --- |
| `DRAM_CLK` | `792` | `792` | 不變 | 目標效能點；問題以 lane 調適處理 |
| `DX_ODT` | `0x07070707` | `0x07070707` | 不變 | 未被本輪反證指向 |
| `DX_DRI` | `0x0e0e0e0e` | `0x0e0e0e0e` | 不變 | 未被本輪反證指向 |
| `CA_DRI` | `0x00000d0d` | `0x00000d0d` | 不變 | 延用 X2 跨板候選 |
| `ODT_EN` | `0xaaaaeeee` | `0xaaaaeeee` | 不變 | 延用 X2 跨板候選 |
| `TPR6` | `0x3a808080` | `0x3a808080` | 不變 | `0x3a` 位於 0845 已觀察窗口近中央，亦與 1116 原廠動態輸出一致 |
| `TPR10` | `0x402f6663` | `0x402f6663` | 不變 | 未被本輪反證指向 |
| `TPR11` | `0x24242422` | `0x25252523` | **更新** | 1116 原廠動態結果；0845 配對實驗顯示主要觀察差異 |
| `TPR12` | `0x110f1111` | `0x110f0f10` | **更新** | 與新 `TPR11` 成對驗證，避免拆分 packed lane 決策 |

## 5.2 source 落點

`016-bananapi-m4zero-use-0845-validated-ddr-lanes.patch` 對既有 defconfig 只改兩行：

```diff
-CONFIG_DRAM_SUNXI_TPR11=0x24242422
-CONFIG_DRAM_SUNXI_TPR12=0x110f1111
+CONFIG_DRAM_SUNXI_TPR11=0x25252523
+CONFIG_DRAM_SUNXI_TPR12=0x110f0f10
```

這個最小修正與機器可讀 profile 互相對照；標準建置腳本另外逐欄驗證九組 DDR 設定，避免 source、建置環境與封裝產物漂移。

# 6. 0845 掃描窗口、失敗邊界與收斂結果

## 6.1 TPR6 掃描結果

以下數值是 `TPR6[31:24]` 的離散實測點所形成的已觀察包絡；`..` 表示報告中的窗口範圍，不代表範圍內每一個未列值都做過連續掃描。

| 實測區段／點 | M2 結果 | 工程意義 |
| --- | ---: | --- |
| `0x20..0x2e` | 各實測點 0/3 | 下側失敗區；`0x2e` 是貼近窗口的刻意失敗邊界 |
| `0x30..0x36` | 各實測點 3/3 | 下半通過區 |
| `0x37` | 小窗口 20/20；64 MiB 強測 3/3 | 接近中心的額外覆蓋 |
| `0x38` | 3/3 | 通過 |
| `0x3a` | 小窗口 20/20；64 MiB 強測 20/20 | 最終中心候選 |
| `0x3c..0x42` | 各實測點 3/3 | 上半通過區 |
| `0x44..0x46` | 各實測點 0/3 | 上側失敗區；`0x44` 是貼近窗口的刻意失敗邊界 |

已觀察零失敗窗口為 `0x30..0x42`。選擇 `0x3a` 而非單板最佳化的新值，有三個理由：它接近窗口中心、與既有 X2 的跨板中心相同、也等於 1116 原廠動態輸出。因此 A1 只更新有反證的 lane pair，不增加第三個不必要變因。

## 6.2 322 筆 evidence 的正確解讀

| 統計 | 數量 | 解讀 |
| --- | ---: | --- |
| 全部紀錄 | 322 | 同一次上電後，由 watchdog 熱重設進行 |
| 通過 | 270 | 包含粗掃、配對、窗口與中心候選 |
| 失敗 | 52 | 收斂期負向結果：包含邊界搜尋與舊設定反證 |
| A1 中心 64 MiB／M2／五輪 pattern | 20/20 | `clk=792`、`TPR6=0x3a808080`、新 lane pair |
| 同批中心候選安全恢復 | 20/20 | 每筆完成後回到 480 MHz 安全設定 |

52 筆失敗被刻意保留，因為窗口兩側的失敗證據是判定中心距離的必要條件。把 `52/322` 當成 A1 候選失敗率會混入舊設定與刻意壞點，屬於錯誤統計口徑；A1 中心強測的直接口徑是 `20/20` 與安全恢復 `20/20`。

# 7. 建置、封裝與 Build ID

## 7.1 標準候選身分

| 項目 | 值 |
| --- | --- |
| U-Boot | `v2026.01` |
| U-Boot upstream | `127a42c7257a6ffbbd1575ed1cbaa8f5408a44b3` |
| TF-A | `lts-v2.12.9` |
| A1 建置來源提交 | `6e05b3313317936d8e6abbd32a49dbcd9f4e0109` |
| Build ID | `2026.01-S127a-P02e5-Hc6a9-V3946-Be6d8-R448a` |
| `SOURCE_DATE_EPOCH` | `1786579200` |
| bootloader 大小 | 873,977 bytes |
| 映像內偏移 | 8,192 bytes |
| bootloader SHA-256 | `0b9333deac4a63353eb18442c9ef2f7ef269be1d7ef015cae3eee65f1b92a0cf` |

## 7.2 建置與封裝守門

| 檢查 | 結果 |
| --- | --- |
| U-Boot 與 TF-A 編譯 | 通過 |
| 九組 DDR 設定逐欄比對 | 通過 |
| DEB 與工作樹組合 bootloader | 逐位元一致 |
| 唯讀 `M4ZDDR1` 診斷標記 | 10 組標記通過 |
| 執行期 `M4ZLAB2` 標記 | 標準映像中不存在 |
| 未封裝 SPL | 38,912 bytes，小於 40 KiB，餘 2,048 bytes |
| 工具單元測試 | 23 項通過（建置證據所載） |
| Jammy CLI IMG／XZ | bootloader、替換區、分割表、XZ 解壓串流皆經一致性驗證 |

標準 A1 只啟用唯讀診斷，不包含可在 UART 執行任意候選的 M4ZLAB2；這使交付映像與實驗工具的功能邊界清楚。

# 8. 四板標準 Linux 啟動 G1

四份 Noble UART 都顯示同一 `P02e5`、792 MHz 與 A1 DDR 參數，並完成 DDR geometry、TF-A、U-Boot、核心、使用者空間登入及正常關機。0845 額外完成一次暖重啟。這批資料支持標準啟動 G1，不支持受控完全斷電次數、特定完整 IMG 身分或 Linux 長時間穩定性。

| 板號 | 容量／Rank | geometry | 啟動觀察 | 證據範圍 |
| --- | --- | --- | --- | --- |
| `0845` | 4 GiB／雙 Rank | x32、16 Rows、10 Columns | 兩次核心與使用者空間；一次暖重啟、一次正常關機 | G1 通過；不是受控冷啟動 |
| `0438` | 4 GiB／雙 Rank | x32、16 Rows、10 Columns | 一次核心、登入與正常關機 | G1 通過 |
| `0256` | 2 GiB／單 Rank | x32、16 Rows、10 Columns | geometry 自動退回單 Rank後進入 Linux並正常關機 | 2 GiB／單 Rank及標準啟動覆蓋 |
| `1116` | 4 GiB／雙 Rank | x32、16 Rows、10 Columns | 一次核心、登入與正常關機 | G1 通過 |

UART 未記錄完整映像檔名或媒體回讀 SHA-256，只能確認 A1 bootloader 與 Noble 系統，不能只憑這批紀錄區分 CLI 或 XFCE，也不能宣稱十套映像均已完成實機啟動。

# 9. 十套 IMG/XZ 映像更新矩陣

## 9.1 共同條件

| 項目 | 值 |
| --- | --- |
| 發行版 | Bookworm、Jammy、Noble、Resolute、Trixie |
| 類型 | 各 CLI、XFCE，共 10 套 |
| 核心 | `6.18.32-current-sunxi64` |
| 共同 Build ID | `P02e5`（完整字串見第 7.1 節） |
| 共同 bootloader SHA-256 | `0b9333deac4a63353eb18442c9ef2f7ef269be1d7ef015cae3eee65f1b92a0cf` |
| 矩陣清單 SHA-256 | `7043b46d3bd5b3f4889384c3a13f8126c826b136f5582e29feffb54caf2af96f` |
| XZ 清單 SHA-256 | `a3d9a7bf413768d815cb846230cf6c3de27156125271b723a4b0fd868e09239a` |
| 資格檔 SHA-256 | `89aff7b3d12a5dd079cfa2328aad8e840b141a835ad6e180f59871a74194538a` |

## 9.2 完整雜湊矩陣

下表的 IMG SHA-256 來自 `MATRIX.tsv` 所保存的建置／解壓串流驗證；XZ SHA-256 已列入 `SHA256SUMS-XZ`。本報告產製時另對本機現存的 10 個 XZ 執行 `sha256sum -c SHA256SUMS-XZ`，並逐套解壓串流重算 IMG SHA-256、檢查 8 KiB 偏移的 Build ID 與 873,977 bytes bootloader SHA-256，結果 10/10 全數相符。

目前矩陣目錄保留 10 個 XZ 與 10 份 metadata，沒有保留未壓縮 `.img` 實體檔；因此下表的 IMG 重驗結果是「XZ 解壓串流的 raw IMG 身分」，不是對現存 `.img` 檔案重算。這不影響 XZ 交付雜湊，但現場使用仍須先解壓／燒錄並完成媒體回讀 Gate。

<div class="matrix-table" markdown="1">

| 發行版 | 類型 | IMG bytes | IMG SHA-256 | XZ bytes | XZ SHA-256 |
| --- | --- | ---: | --- | ---: | --- |
| Bookworm | CLI | 2,067,791,872 | `e060d9999f5d642acd0049ae397934c600ecb74bc04b083b9b63395d945e4450` | 432,902,872 | `264d2a2d4a10e2dcf816bef74e4029aaa3f08a3cd5cec02759feb20e8cdf7af4` |
| Bookworm | XFCE | 4,940,890,112 | `1d0a65650effe92dc51716529c99aaa250b360d040c9ea0b92faf9dac3687ccd` | 973,683,436 | `13b1b2cc72897a8a41a1efb9415cf5d6c869362e864458d133343a3f71a79de4` |
| Jammy | CLI | 2,034,237,440 | `bf15c4869090fc54a5d8c6af3d35c0a6a955ff36284fd42aa6712d48ace0db6f` | 454,842,924 | `fee9ea0f72c06e80e681cf2f5d43782a4b039755bd5e4ec1f94da18a37e7fdef` |
| Jammy | XFCE | 4,412,407,808 | `2beacf24352fb41811b7ab392f73a54b7b4c96f74066a23706f1326b0470f496` | 888,996,180 | `51436d74c6e65fad7ea80078c07feb4f8365b3a02bf09d90c1ec990948de3b64` |
| Noble | CLI | 2,164,260,864 | `354ec18afb22aea4d25a2b94279442f2a76984c38c3fdb365bfac933f18c38c6` | 448,867,768 | `f5ba10a4725713f13a4e149aef4e6f021750f0d7fce390509707e72788401e41` |
| Noble | XFCE | 4,907,335,680 | `4593e85d669d4368565d9fd1f0dfa9fe3a842055a85b2d83c9a20fd21bcbf24b` | 916,579,416 | `24bd40359ab1e158a2132c4e15f2b1b2b6f491fd83524bba97a984efccfe70a6` |
| Resolute | CLI | 2,126,512,128 | `055d94308a9bd15e652bbe74c079652bd63dd05928b93f304ef5d4f15d7af2a0` | 458,596,604 | `5116fd8515d88be7aa8915f67792cb3d15919b4c65130f51217dec7bb0fc9d17` |
| Resolute | XFCE | 5,360,320,512 | `4faeef73f6c070ac6bd56db4ea773d1bdbc33539a39d4a178971172e75d3272b` | 1,001,418,120 | `d03fabdca1693a54faf7e54efba8d924638905a2d4510d0b0cdfaa8d6864960a` |
| Trixie | CLI | 2,122,317,824 | `a016eaae1e98d3c1c6a24772bc51829ec9cc590bdb49f5c6af45c20adf69597f` | 459,723,132 | `d28d7228bd4cd001f058cee4dd5c2cbdab10b2f7313f7b360a545bf22403666a` |
| Trixie | XFCE | 5,582,618,624 | `df75837a6560e8c939f5c6a0bbf4c7d1eff1e61102d794219e5dfdc01be79a7e` | 1,084,666,640 | `807c4a5cfcd226d89d5c48ac55d4f17fad76b04a68dac2283eb724692dbecc07` |

</div>

## 9.3 映像更新與現場使用限制

標準封裝是在各來源映像的 8 KiB 偏移置換 873,977 bytes bootloader，並驗證分割區起點、替換區外內容、IMG SHA-256、XZ 解壓串流與 metadata。現場燒錄前必須核對 XZ SHA-256；燒錄後、首次啟動前應回讀完整有效映像範圍與 bootloader 區段。未取得回讀證據前，不得把檔案雜湊等同於媒體內容已正確寫入。

# 10. Git 歷史與用途追溯

六筆指定提交均已由 Git 核對完整 SHA，且都是證據截止提交與當時遠端分支的祖先。

| 時間（臺北） | 完整 commit | 用途 |
| --- | --- | --- |
| 2026-08-19 22:41:51 | `6e05b3313317936d8e6abbd32a49dbcd9f4e0109` | 收斂 0845／792 MHz 參數；加入原始 M4ZLAB2 證據、A1 profile、016 patch 與建置／封裝入口 |
| 2026-08-19 22:50:21 | `147b09f0c03874bad2a5f70c4eb891fb1ef5fbb1` | 記錄標準 U-Boot、TF-A、DEB 與 Jammy IMG/XZ 的建置與一致性證據 |
| 2026-08-19 23:56:47 | `de2ba4bd4af2aba8b654c5c3f13deae19d0b9486` | 參數化矩陣工具並新增 A1 wrapper，鎖定 Build ID、bootloader SHA 與資格 |
| 2026-08-20 00:15:14 | `bf8f8de3fd6d018917b0dc8a123c58054b282215` | 將中心 64 MiB 強測擴增至 20/20；原始資料更新為 322 筆 |
| 2026-08-20 01:38:23 | `4817724d5dee10aac7e5cb63ad4efc21ac00e861` | 完成五發行版 × CLI／XFCE 十套映像矩陣與逐列雜湊交付 |
| 2026-08-20 06:15:25 | `65864f36712b8fce0b56b86f725c8b0447816a22` | 提交四板已遮蔽 Noble UART、清單與 G1 判讀，形成報告證據截止點 |

# 11. 驗證結果總表

| 驗證項目 | 板／產物 | 結果 | 判定層級 |
| --- | --- | --- | --- |
| X2 反證 | 0845、792 MHz、M2 | 4/5；一筆資料位元錯誤 | 證明原設定在該條件不足 |
| lane pair 短測 | 0845 | A1 pair 5/5 | 進入窗口掃描 |
| TPR6 下邊界 | 0845、`0x2e` | 0/3 | 刻意失敗邊界成立 |
| TPR6 上邊界 | 0845、`0x44` | 0/3 | 刻意失敗邊界成立 |
| 已觀察窗口 | 0845、`0x30..0x42` | 實測點零失敗 | 單板熱重設窗口 |
| 中心強測 | 0845、64 MiB、M2、五輪 pattern | 20/20，安全恢復 20/20 | 單板中心候選通過 |
| 標準建置 | `P02e5` | 編譯、設定、二進位、封裝守門通過 | 工程候選可燒錄 |
| 四板標準啟動 | 0845／0438／0256／1116 | G1 全數通過 | 覆蓋 4 GiB雙 Rank及2 GiB單 Rank |
| 十套 XZ／IMG 串流本機重驗 | 10 個現存 XZ | XZ 雜湊、解壓 raw IMG 雜湊、Build ID、bootloader 區段皆 10/10 相符 | 壓縮交付與可解壓映像身分確認 |
| 受控完全斷電冷啟動 | A1 | 尚未完成 | **Gate 開啟** |
| 映像／媒體回讀 | A1 | 尚未完成 | **Gate 開啟** |
| Linux 長時間壓力 | A1 | 尚未完成 | **Gate 開啟** |

# 12. 風險邊界與後續 Gate

## 12.1 不得外推的範圍

- 0845 的 M4ZLAB2 結果是單板、同次上電後熱重設，不是冷啟動統計。
- `0x30..0x42` 是 0845 在本次離散掃描下的已觀察窗口，不是跨板、跨批次或溫度／供電角落的保證窗口。
- 四板 G1 證明相同參數可完成標準 Linux 啟動，但沒有媒體回讀或長時間壓力，不能取代資料完整性 Gate。
- 十套映像的建置與雜湊完整，不等於十套都已在實體板完成 CLI／桌面功能與壓力驗證。
- 現有證據不支持「量產完成」、「穩定版」、「所有板相容」或「A1 已可全面取代 X2」。

## 12.2 建議 Gate 與交付物

| Gate | 必要操作 | 必存證據 | 放行原則 |
| --- | --- | --- | --- |
| G2 受控冷啟動 | 0845、0438、0256、1116 各至少 10/10 完全斷電；固定斷電／上電時間 | 電源控制紀錄、板號、SD CID、完整 UART、成功與失敗逐次結果 | 不以暖重啟替代；任何失敗保留原始紀錄並停止外推 |
| G3 燒錄與回讀 | 燒錄前核對 XZ；首次啟動前回讀有效 IMG 範圍；另回讀 8 KiB 起 873,977 bytes | XZ、IMG 串流、媒體回讀與 bootloader SHA-256 | 四層身分一致，且記錄實際檔名與媒體識別 |
| G4 Linux 壓力 | 可用記憶體 pattern，接續記憶體／CPU／MMC 並行長時間測試 | 命令、時長、溫度、負載、核心／I/O 錯誤掃描與結束碼 | 時長與錯誤門檻須測前凍結；零資料錯誤與零非預期重啟 |
| G5 跨板與角落 | 2 GiB單 Rank、4 GiB雙 Rank、已知弱板、不同批次、溫度與供電角落 | 共用矩陣、板料資訊、環境條件與窗口交集 | 以共同通過交集決定參數，不以單板最佳值取代 |
| G6 發行候選 | 依風險選定 CLI／XFCE 與發行版代表組合，完成桌面／顯示與基本周邊驗證 | 每套 Build ID、檔案 SHA、回讀、啟動與功能清單 | 所有殘餘風險有 owner、期限與接受人 |

# 13. 結論與建議

## 13.1 結論

1. 0845 的 M2 資料錯誤已建立原 X2 lane 參數在 792 MHz 裕量不足的直接反證。
2. A1 以最小 source 變更更新 `TPR11/TPR12`，保留 `TPR6=0x3a808080`；0845 已觀察窗口 `0x30..0x42`，兩側 `0x2e`／`0x44` 均有刻意失敗證據。
3. A1 中心候選在 64 MiB、M2、五輪 pattern 完成 `20/20`，每次安全恢復亦為 `20/20`；整批 322 筆的 270／52 分布具可追溯原始資料。
4. `P02e5` 標準 bootloader 已封裝至 Bookworm、Jammy、Noble、Resolute、Trixie 的 CLI／XFCE 十套 IMG/XZ，Build ID、bootloader 與檔案雜湊具完整清單。
5. 相同參數在 0845、0438、0256、1116 完成 Noble 標準啟動 G1，涵蓋 4 GiB雙 Rank與2 GiB單 Rank；證據只支持 G1，不支持量產完成。

## 13.2 建議

- 以 Jammy CLI 作第一個受控 G2～G4 輸入，先在 0845 完成冷啟動、回讀與長時間壓力，再擴至其餘板。
- 第一個 CLI 路徑關閉後，以 Noble XFCE 驗證桌面／顯示路徑；其他映像按發行風險排序，不以檔案存在代替實機覆蓋。
- Gate 開啟期間保留 480 MHz 安全路徑、原始失敗 evidence 與 X2 回退選項。
- 對合作夥伴只使用「A1 792 MHz 工程候選、四板 G1 通過、G2～G5 待完成」的精確表述。

<div class="final-verdict"><strong>最終判定：</strong>A1 已完成可交付的工程候選與追溯鏈，適合進入下一階段硬體資格驗證；尚不具量產或穩定版放行條件。</div>

# 附錄 A：引用 evidence 與 SHA-256

## A.1 核心 source／文件

| repo 相對路徑 | SHA-256 | 用途 |
| --- | --- | --- |
| `patch/u-boot/v2026.01/board_bananapim4zero/013-bananapi-m4zero-use-orangepi-zero3-ddr-baseline.patch` | `338f79e300cc2f208db8f6ba53c5155b5036455de16a86a31cb32431c9b472ce` | X2 起點與完整參數 |
| `patch/u-boot/v2026.01/board_bananapim4zero/016-bananapi-m4zero-use-0845-validated-ddr-lanes.patch` | `6e58847805a8690e971e4f996acafff0f5a6afd5e380e5888a3dfc85459c1817` | A1 兩行 lane pair 修正 |
| `tools/bpi-m4zero-ddr-lab-profile-cross-board-candidate-792.json` | `d91a7a2710c7e9a7d6385501622e2a8e988a1236c8262469d16aa1e960d8f26e` | X2 機器可讀 profile |
| `tools/bpi-m4zero-ddr-lab-profile-0845-candidate-792.json` | `d848bed2a79d01129034ed32529a94e16efa69cc0cfb4146e32264b4ded44d1d` | A1 機器可讀 profile |
| `docs/evidence/bananapi-m4zero-opi-ddr/M4ZLAB2-hardware-0845-20260819.md` | `64d9fc491db965ffc7dfdf2c5e4aa9469cf555a9c6a4e2124e882ef8dd19b9f5` | 0845 掃描、邊界、統計與限制 |
| `docs/evidence/bananapi-m4zero-opi-ddr/A1-0845-792-build-image-20260819.md` | `b485cec771fe96b398988a765564b04a205233c295a2031b47be3977b0defbad` | `P02e5` 建置與 Jammy 封裝證據 |
| `docs/bananapi-m4zero-a1-792-image-matrix-delivery-20260820.md` | `907c80d9c49ff8d1126e555a03ba9b5a07c56890f69e27359996ed5843374a49` | 十套映像矩陣與共同限制 |
| `docs/evidence/bananapi-m4zero-opi-ddr/A1-Noble-four-board-G1-20260820.md` | `280cecd599190f38ec0ef13f6f2c7dc251299bcc15b0d9da30c588d7cbed2658` | 四板標準啟動 G1 判讀 |
| `docs/evidence/bananapi-m4zero-opi-ddr/hardware/A1-Noble-four-board-20260820/results.tsv` | `265dacf628e716854c2ed9a71d938e21f4a466160be7fc13d4d63917a827509b` | 四板機器可讀摘要 |

## A.2 0845 原始壓縮 evidence

| repo 相對路徑 | 壓縮檔 SHA-256 | 解壓內容 SHA-256 |
| --- | --- | --- |
| `docs/evidence/bananapi-m4zero-opi-ddr/hardware/M4ZLAB2-0845-20260819/results.jsonl.gz` | `613710659c2905d4cb29ad74afbb5687812ad6e95aee983d220f682ec623e574` | `968d7651164197ca76d3fac6f010f32f9f8825e222ab30b15c01b942c32adbed` |
| `docs/evidence/bananapi-m4zero-opi-ddr/hardware/M4ZLAB2-0845-20260819/uart.log.gz` | `a8c603df437f0dd6e0516e91bbb37ec1454a9c24fd7685c3316ee900921a8772` | `9f20109bc727d3beea1b947c3bc2a049e9baed89123152f2686a2a0f68f07fe1` |
| `docs/evidence/bananapi-m4zero-opi-ddr/hardware/M4ZLAB2-0845-20260819/ranking.json.gz` | `22fe8964553c1aabced2d7f0c73b96b8e924cf4e217eb43b4de5f14d51b22a39` | `6552cca4fde59fbf0d7951997744348276fe25da684553b4b538aa77670dee6a` |

# 附錄 B：重現與文件驗證命令

以下命令均從 repo 根目錄執行；硬體 Gate 需在受控測站另行完成。

```bash
# 核對提交與用途
git show -s --format='%H|%aI|%s' \
  6e05b3313 147b09f0c de2ba4bd4 bf8f8de3f 4817724d5 65864f367

# 重算 322 筆 pass／fail
gzip -cd docs/evidence/bananapi-m4zero-opi-ddr/hardware/\
M4ZLAB2-0845-20260819/results.jsonl.gz \
  | jq -r '.status' | sort | uniq -c

# 核對十個現存 XZ
cd output/images/2026.08/bpi-m4zero-a1-0845-792-matrix
sha256sum -c SHA256SUMS-XZ

# 產生正式 PDF
python3 tools/build-bananapi-m4zero-a1-report-20260820.py

# PDF metadata、文字、字型與全頁影像
pdfinfo docs/reports/bananapi-m4zero-a1-porting-ddr-tuning-report-20260820.pdf
pdffonts docs/reports/bananapi-m4zero-a1-porting-ddr-tuning-report-20260820.pdf
pdftotext -layout docs/reports/bananapi-m4zero-a1-porting-ddr-tuning-report-20260820.pdf -
pdftoppm -png -r 120 docs/reports/bananapi-m4zero-a1-porting-ddr-tuning-report-20260820.pdf /tmp/a1-report/page
montage /tmp/a1-report/page-*.png -thumbnail 260x -tile 4x -geometry +8+8 /tmp/a1-report/montage.png
```

---

**文件控管註記：**本報告的 Markdown 與 PDF 應成對交付；最終檔案 SHA-256、PDF 頁數、Git 交付 commit 與遠端分支狀態，以交付訊息及提交後核對結果為準。報告本體不自嵌自身雜湊，以避免自我參照。
