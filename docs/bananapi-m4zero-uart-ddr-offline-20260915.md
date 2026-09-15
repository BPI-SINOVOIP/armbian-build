# 0845：獨立 DDR SPL2 離線交付與升級界限

工作批次：2026-09-15；最終整理：2026-09-16。分支：`bpi-h618-recovery-network-20260915`。
本文件接續 [P4A 計劃](bananapi-m4zero-uart-ddr-plan-20260915.md)及[實板 UART 故障紀錄](bananapi-m4zero-uart-fault-results-20260915.md)。

## 結論

已完成新版載入契約、獨立 SRAM 負載、主機上載器與離線執行串接。**不是只有重新打包：實際交叉編譯的 AArch64 SPL1、DDR SPL2 均已在受控模型執行，並使用真實 `sx` 經偽終端完成上載與交接。**

但這不是 DDR 調校完成，也不是可發布的 Armbian 鏡像。電源及記憶體幾何前置流程尚未移植完成，`ddr_preflight()` 固定拒絕訓練；模型不映射 DDR 或相關控制器 MMIO。因此目前有效參數的終態為 `blocked`、`tested_bytes=0`，沒有 480／792 MHz 實板或穩定性結論。

卡上仍是舊版 `build-009`。本輪只做五種 UART 故障、正常重傳與交握，**未寫 SD／eMMC、未控制電源、未載入新的 DDR 負載到實板**。最後維持 SPL1 等待命令，UART 已釋放。

## 實作

| 項目 | 本次變更 |
| --- | --- |
| 固定救援 SPL1 | 預設仍為 ABI 1；明確指定 `--ddr-v2` 才開 ABI 2 |
| 封包分離 | 舊 `version=1, kind=1` 與新 `version=2, kind=2` 分開；交叉組合拒絕 |
| 主機守門 | 必須核對本地完整封包、同一 nonce、韌體回報的 kind 及 SHA-256 後，才允許一次交接 |
| 相容性 | 新控制 ABI 2 仍以 ABI 1 上下文執行舊 smoke；DDR 上下文則為 ABI 2 |
| 接收期限 | 整包 120 秒、無資料 10 秒；有效字元更新活動時間；內外層重試都檢查期限 |
| DDR 來源 | 固定既有 U-Boot v2026.01 與已套用的 DDRlab／A1 程式雜湊，保留原演算法及授權 |
| 獨立負載 | 自有 EL3 入口、向量、BSS、參數、一般與例外堆疊；沒有 `gd`、heap 或 DDR 重定位 |
| 參數 | 沿用 15 欄解析器，另綁 nonce；時脈、ODT、驅動、TPR、測試層級及窗口為執行期值 |
| 原型限制 | 只允許單輪 `passes=1`；不開放任意電壓、MMIO、幾何或媒體寫入 |
| 來源讀取 | 沿用既有一般檔案安全 API，拒絕裝置、符號連結與路徑替換競態 |

SRAM 全區間為 `[0x30000,0x48000)`，共 98,304 B。負載裸映像 20,176 B，BSS 結束於 `0x35260`；一般堆疊與例外堆疊均在 SRAM。逐函式靜態堆疊最大 448 B，不代表所有未執行 DDR 路徑的完整呼叫鏈均已驗證。

程式與精確前置缺項見 [DDR SPL2 說明](../patch/lab/u-boot/bananapim4zero/sram-ddr/README.md)。

## 驗證方法

1. **靜態稽核**：使用 `pyelftools` 解析 ELF、符號與 PT_LOAD，核對裸映像及零填補，檢查 SRAM 範圍、禁止相依、組態與建置意圖。
2. **真實 C 函式模型**：抽取接收迴圈及期限函式，驗證空等、部分傳輸、已逾時、正常封包、活動期限延長與總期限不可延長。
3. **AArch64 執行模型**：從 SPL1 命令入口及重設入口測試，使用真實映像完成 CRC／SHA／填補／長度檢查、交接及 DDR 就緒。UART、時基與部分最低初始化由替身提供，不是 H618 電氣模型。
4. **主機整合**：真實主機工具及 `sx` 經偽終端對接模型；錯誤 ABI、nonce、型別及雜湊不得發送交接命令。
5. **QEMU 純邏輯**：同一組 AArch64 解析物件測試完整參數、缺欄、重複、範圍、行界限、上下文及幾何公式；不在 QEMU 使用者模式執行 EL3／MMIO。
6. **變異反例**：在記憶體複本把前置檢查呼叫改指 DDR 初始化，重新計算封包雜湊，確認完整執行模型因禁止的 MMIO 存取而失敗。原 ELF、裸映像與來源不變。

只檢查 `ddr_preflight` 的固定返回機器碼，不能證明所有呼叫路徑都受它保護；靜態結果與第 3、6 項執行結果須分開解讀。有效 nonce 範圍為完整 u32，包含零，已補齊原型錯誤拒絕零值的反例。

最終 `validation-v1-002` 為 **171 項測試及 8 組稽核通過**；`validation-v2-002` 為 **312 項測試及 8 組稽核通過**。兩條路徑均無失敗、無跳過，來源雜湊與最終工作樹相符。兩套含共享測試，不應相加宣稱 483 個獨立案例。新版其中包含 26 項 AArch64 串接／反例及 28 項負載來源、解析和結構測試。

Ruff 通過，但明確排除既有 auditor 未修改的 `E731` lambda；不是未帶任何例外的全倉 lint。最終彙總為證據根目錄的 `offline-validation.json`，SHA-256：`90a1580be942c67956c754220f85e93dcf6271e87365e8482906fa9391517b7d`。

新增補丁檔以一般文字執行 `git diff --check` 時，其必要的上下文前綴空白會觸發警告，因此完整暫存區的此命令並未通過，不能省略限制。一般程式／文件使用 `git diff --check HEAD^ HEAD -- . ':!*.patch'` 通過；補丁本身另對固定 `pristine/` 來源執行 `git apply --check --whitespace=error-all` 通過。未刪除上下文空白或放寬補丁實際新增程式的空白檢查。

- V1 驗證報告 SHA-256：`b886421a17cffefa9f74cf9aaf85b279d9ed7fe2f12c320a9446b2daa51d03f0`。
- V2 驗證報告 SHA-256：`1ba4c9fefcda56e7674b7efece104681485a70f8bd00c4cd4ad62566e80f0d71`。

早期 `loader-v2-build-002` 僅補外層期限檢查，整合模型仍出現約 36 秒空等。最終使用 `loader-v2-build-003` 的活動期限修正，不把早期失敗候選當最終產物。初次測試工具匯入及上下文解析錯誤也已修正；最終回歸不能引用初次失敗的結果代替。

## 產物與重跑

本機整合證據根目錄：

```text
/media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-sram-supervisor-plan/output/evidence/bpi-h618-recovery-network/P4A-0845-20260915/
```

| 產物 | 長度 | SHA-256 |
| --- | --- | --- |
| `loader-v2-build-003/spl1-egon.bin` | 40,960 B | `80e67d7ebaacb58d8b2b64a6a01a94b4d4329c5f11f8f9cb35e917d060f7e33d` |
| `spl2-ddr-preflight-v2.sram` | 20,992 B | `bb8af8b4b53016c957189ccffeb1cbf642b24ecdffda415b480c0a966154b8d5` |
| DDR 裸映像 `spl2-ddr.bin` | 20,176 B | `e15f1140e3385c7851e1775cacb024d344f80ff555d0c56195efabf698f38bd9` |

獨立 DDR 建置使用 `tools/build_bpi_sram_ddr.py`，新版 SPL1 使用 `tools/build_bpi_sram_supervisor.py --ddr-v2`。DDR 封裝工具為 `tools/bpi_sram_ddr_package.py`；主機工具為 `tools/bpi_sram_ddr_uart.py`。它們不代替後續硬體前置或媒體部署授權。

最終獨立 DDR 建置目錄為同一工作樹下的 `output/evidence/bpi-sram-ddr-build-008/`，`build-report.json` SHA-256 為 `1f2a63ab19d1214ca3de0dc3288c91eaad1299d6e9e351c20a956639afaab251`。`007` 與 `008` 裸映像逐位元相同；`008` 更新的是建置器安全讀取及靜態稽核的表述範圍，沒有再次更動 DDR 參數或演算法。原先 `007` 封包的雜湊因此仍有效。

整套回歸使用 `tools/validate_bpi_sram.py`，明確指定新版 SPL1 的 `--build-dir`、獨立負載的 `--ddr-build-dir` 及不存在的 `--output-dir`。舊版不能套用 DDR 測試，新版缺負載目錄也不能用舊測試充數。來源／工具／產物／測試雜湊與命令輸出保存於各次 `validation-report.json`。

以下只執行離線回歸；`verification-local-001` 若已存在，須改成新的輸出目錄：

```bash
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B \
  tools/validate_bpi_sram.py \
  --build-dir output/evidence/bpi-h618-recovery-network/P4A-0845-20260915/loader-v2-build-003 \
  --ddr-build-dir output/evidence/bpi-sram-ddr-build-008 \
  --output-dir output/evidence/bpi-h618-recovery-network/P4A-0845-20260915/verification-local-001
```

## 尚未完成

- **PMIC 前置程式**：移植並審查不依賴舊開機的 R-I2C 時脈、重設、pinmux、PMIC 身分與電壓設定回讀；不能從舊 defconfig 推定本輪實際狀態。
- **DDR 前置與幾何**：補齊 PRCM 校準、可失敗返回的容量探測與重新初始化檢查；歷史 4 GiB／rank 記錄不是當輪幾何證據。
- **實板新契約**：驗證新版的空等期限、smoke 相容性、DDR 就緒、拒絕與電源復原，尚未部署。
- **真正 DDR 測試**：前置完成後才能解鎖單組訓練、對照／792 MHz 矩陣、容量覆蓋及長時間測試。
- **媒體與多系統**：UART 更新交易、eMMC、多槽 Linux、PXE 與多 OS 尚未因此完成。

P4A1 的載入契約可交付；P4A2 的 SRAM 框架與離線串接已建立，但電源／幾何接合仍未完成，不能把整個 P4A2 或 P4A3 標示完成。

## 下一次最小部署方案

以下只是待核准方案，**不是本輪已執行操作，也不是直接可執行的燒錄命令**。

1. SD 回插主機後重新辨識容量、CID、分割表及占用狀態；不得假設仍是先前裝置。保存新的前 4 MiB 備份，核對卡上仍為已知 `build-009` 與原槽 0。
2. 新增專用升級工具與離線反例測試。既有 `bpi_sram_sd_minimal.py` 固定針對舊產物，**不可放寬其雜湊守門或直接拿來寫新版**。
3. 只替換 SPL1 的 `[8192,49152)`，共 40,960 B，採上表唯一候選雜湊。槽 0 `[3145728,3147264)` 不變，分割表及 4 MiB 起的原系統不變；本次升級不配置其他槽。
4. 寫入前核對所有前置，寫入後同步並完整回讀前 4 MiB，確認只有指定範圍改變，保留讀回結果及新舊雜湊。
5. 首次上板只驗證新版救援、舊 smoke、故障期限及 DDR 負載就緒／拒絕，不立即開始 792 MHz 掃描。
6. 若 SPL1 無法啟動，必須由主機按新備份恢復該 40,960 B 並回讀。固定救援入口本身更新失敗時，不能承諾 UART 一定可救回；這仍可能需要人工插卡。

需要使用者配合的事項是確認這一次性升級、將正確 SD 插回主機，再插回 0845。PMIC 與幾何接合是後續程式工作，不轉嫁成需要使用者手動調寄存器；完成前不對實板訓練作通過承諾。
