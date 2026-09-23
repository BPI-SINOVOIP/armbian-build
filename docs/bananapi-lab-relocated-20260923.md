# 多板測試載台已移至內部倉（2026-09-23）

本分支先前包含 Banana Pi 多板無人測試載台（`tools/bpi_lab*`、`tools/bpi_h618*`、`tools/bpi_sram_*`、`patch/lab/`、`config/bpi-lab/`、對應 `tests/`、`docs/bananapi-lab-*`、`docs/bananapi-multiboard-*`、`docs/evidence/bpi-*` 等，共 379 個路徑）。

這些屬於內部站點工具與實驗證據，已於本提交自公開分支移除，改在內部私有倉維護。本分支保留的內容為板卡支援：`config/boards`、`patch/u-boot`、`packages/bsp`、映像矩陣腳本與交付文件。

歷史提交未重寫；若需載台內容請聯絡維護者。

補記（同日）：`tests/test_bpi_h618_io.py` 驗的是本分支 `config/boards` 與 `packages/bsp/bananapi-h618`，屬板卡支援而非載台，誤隨載台移除，已自 `7a015eb` 原樣補回。
