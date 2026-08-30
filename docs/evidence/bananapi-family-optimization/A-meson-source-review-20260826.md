# Banana Pi Meson 第一批來源審查

日期：2026-08-26

對象：\`bananapim5\`、\`bananapim2pro\`、\`bananapicm4io\`、\`bananapim2s\`

## 結論

本階段只納入能由來源、既有故障紀錄或保守系統政策支持的修改。建置成功最多提升為 L1，映像唯讀內容守門通過最多提升為 L2；下列修改均不能取代實機驗證。

| 項目 | 決策 | 理由 |
| --- | --- | --- |
| FIP | 四板固定為 \`e11ae32f65219e9cba903e9744f216239b41386a\` | 原本追蹤可變動的 \`master\`，無法由 Armbian 提交碼重現 |
| 標準 I/O 工具 | 四板加入 \`gpiod\`、\`i2c-tools\`、\`python3-libgpiod\`、\`python3-spidev\`、\`v4l-utils\` | 提供 GPIO、I2C、SPI 與影像節點的標準診斷介面，不代表實體接腳已通過 |
| 無線工具 | 四板加入 \`rfkill\`、\`bluetooth\`、\`bluez\`、\`bluez-tools\` | 四板均有板載或選配無線路徑；仍須分板驗證實際驅動、韌體與天線 |
| M5 eMMC | U-Boot 使用 25 MHz 8-bit legacy；Linux 使用 100 MHz HS200 並禁止 HS400 | 既有 Hynix eMMC 故障紀錄支持保守設定，且開機載入與 Linux 執行期分層處理 |
| M2 Pro eMMC | 保持原始設定 | 與 M5 共用部分 DTS 不代表顆粒與故障相同；沒有足夠實機證據時不得共用降速 |
| CM4IO eMMC | 保留既有 25 MHz U-Boot／100 MHz Linux 修正 | 修補已在 current 路徑，但仍缺修正後多板冷啟證據 |
| M2S eMMC | 保持原始設定 | 現有 CM4 修補沒有對 M2S DTS 啟用保守屬性，且尚無同型故障證據 |
| CM4IO／M2S CPU | 調速器由 \`performance\` 改為 \`ondemand\`，保留既有頻率上下限 | 避免無負載時持續最高效能造成額外功耗與溫度；實機仍須檢查兩個 CPU policy |
| M5 CVBS | 不預設停用 | 停用 Composite 只能避開一部分 DRM 關機逾時，不能修正 BL31／BL30 的 CPU 關電失敗，且會犧牲實體輸出 |

## 已知關機問題

M5 的歷史紀錄同時包含 Meson DRM commit 逾時，以及進入 BL31／BL30 後的 CPU power-off timeout。M2 Pro 也曾出現後者，因此目前判斷至少有核心顯示與封閉韌體兩個層次。

第一批候選映像不宣稱已修正實體斷電。後續實機測試必須分別記錄：

1. 無顯示器、HDMI 與 CVBS 三種狀態的 \`poweroff\` 耗時。
2. DRM 是否仍出現 \`flip_done timed out\` 或 commit timeout。
3. BL31／BL30 是否仍出現 CPU power-off timeout。
4. 板上電源軌是否實際關閉，而不只記錄核心已進入 power down。

## 實機最低門檻

- M5：Hynix 與其他廠牌 eMMC 各自執行冷啟、重啟、關機、\`fio\`、校驗與非正常斷電恢復。
- M2 Pro：先以原始 200 MHz Linux 設定建立基準；只有重現相同 eMMC 錯誤後才建立專用保守候選。
- CM4IO：至少三片 Hynix 與一片其他 eMMC，每片執行 30 次冷啟，UART 不得再出現 CMD12、stop command 或 \`fs_devread\` 錯誤。
- M2S：先保留原始 eMMC 設定做相同回歸，不由 CM4IO 結果推論通過。
- 四板：逐板驗證 Ethernet、USB host／gadget、HDMI、Panfrost、Meson VDEC、Wi-Fi、Bluetooth 與 40-pin。

## 證據限制

- 既有 M5 與 CM4IO 映像由其他髒工作樹產生，來源提交沒有涵蓋未追蹤修補，不能當成本分支可重現證據。
- 既有 CM4IO UART 紀錄早於 eMMC 修補，能證明原始故障，不能證明修正已通過。
- M5 的 \`g_mass_storage\` 曾有實機成功紀錄，但需要在本分支候選映像重新驗證。
- FIP 含封閉韌體；固定提交與雜湊只能建立來源同一性，不能證明其內部行為正確。
