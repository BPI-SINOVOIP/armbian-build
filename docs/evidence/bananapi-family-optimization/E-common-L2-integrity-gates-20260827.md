# Banana Pi 共用 L2 完整性守門

## 目的

本次修正封閉共用完整映像驗證器可能產生假陽性 L2 的路徑。L2 必須代表同一個乾淨來源提交建立的 IMG／XZ，並由同一提交的驗證器與 validation 完成唯讀內容檢查；舊狀態檔、後處理腳本或略過壓縮串流均不得替代這條證據鏈。

## 建置狀態契約

`COMPLETION_STATUS.json` 在建置完成時必須記錄：

- `source_commit` 與 `source_tree`。
- 建置時的 `validation_config_sha256`。
- 最終 `CANDIDATES.tsv` 的 `candidates_sha256`。

建置入口固定保留 40 GiB 不可下調的硬下限；板級或執行環境可以要求更高空間，但不能以環境變數繞過硬下限。

## L2 驗證契約

共用驗證器新增下列必要條件：

1. L2 固定要求 `VERIFY_ARCHIVES=yes`，逐檔執行 XZ 完整性與解壓資料 SHA-256 比對。
2. 候選來源提交必須等於驗證器目前提交；建置與驗證的 validation SHA-256 必須相同。
3. `COMPLETION_STATUS.json`、`CANDIDATES.tsv` 與板級 `artifact.metadata.txt` 必須指向同一來源提交、來源樹及 validation。
4. 驗證開始即把既有 `VERIFICATION_STATUS.json` 原子改為 `in_progress`，並移除舊的衍生證據；任一失敗只留下 `failed`，不得沿用舊 `complete`。
5. validation 列出的分割區數量必須精確相等，不能額外加入未登錄分割區。
6. 映像必須恰好包含一份最終核心設定；U-Boot target 設定及核心設定都會寫入 `FINAL_CONFIG_EVIDENCE.tsv`，若板級契約提供整檔 SHA-256 則必須逐位元相同。
7. `uboot_target_make_forbidden` 與 `forbidden_packaged_assets` 可分別拒絕 U-Boot target 片段及映像內禁止資產。
8. 根檔案系統與獨立 boot 分割區均以 `ro,nosuid,nodev,noexec` 掛載；ext4 另使用 `noload`。

## 家族附加證據

Rockchip 的 RKBin／無線驅動清單與 R3 Mini 的 eMMC boot0／載荷邊界，必須在共用驗證器正式寫出 `complete` 前加入暫存狀態。附加程序不能覆寫來源提交、validation、候選矩陣或核心證據等受保護欄位；附加程序失敗時，共用失敗 trap 會將正式狀態改為 `failed`。

## 既有候選影響

本守門不追溯修改既有映像。缺少新 `COMPLETION_STATUS.json` 欄位或來源提交不等於目前驗證器提交的舊候選，在重新完整建置前不能用新工具重新產生 L2。中央登錄保留既有歷史證據，但每個尚未完成的 L1 板卡都必須由套用本守門的乾淨提交重新建置及驗證後，才可提升為新的內部 L2。

## 證據限制

這些檢查只證明來源、封裝、壓縮串流、分割區及唯讀內容的一致性，不證明板卡能冷啟動，也不證明儲存、網路、顯示、無線、加速器或 40-pin 實體功能。專有載荷與韌體的再散布權仍須逐板審查；L2 不等於公開發布核准。
