# Banana Pi 全系列主線整合結果

日期：2026-08-31

分支：`bpi-integration-20260829`

計畫提交：`585fd21a6e433f14537eff936ba05ff276b32f61`

歷史整合提交：`34b68511381034a3eb53565352604695e06f0318`

## 一、結論

`bananapi-family-optimization-20260826` 的完整歷史已合併至 `bpi-integration-20260829`，來源提交 `937e7e81ab81012d52febcbc800c1262821be12c` 已是整合分支祖先。現行分支可追蹤 48 個 Banana Pi 產品板卡與 1 個 BPI-M4 Zero EMAC 功能變體，並保留 BPI-M4 Zero、BPI-M4 Berry 的 H618 DDR、GPU、媒體、40-pin、無線及工具成果。

本結果代表來源、板型、工具、契約、歷史證據及本機回歸已整合，不代表 48 個板卡都由本次提交重新產生映像，也不代表未取得實機的板卡已通過硬體、量產、授權或對外發布門檻。

## 二、整合範圍

- 保留 369 個全系列歷史提交，不壓縮成單一快照。
- 依現行 Armbian `main` 的目錄與函式介面處理 26 個衝突檔案。
- 納入 Meson、Sunxi、Rockchip、Filogic、SpacemiT、Sunplus、Realtek、Renesas、VS680 與 Unisoc 相關板型、固定來源、工具、驗證器及證據文件。
- 維持 `.conf`、`.csc`、`.wip`、`.eos` 身分，不因合併擅自提升支援層級。
- 維持 3 個 L0、45 個歷史 L2、0 個 L3 至 L5 的證據分布；L2 仍是軟體證據，不是實機通過。
- 將 `bananapim4zeroemac` 建模為 `bananapim4zero` 的獨立功能變體，不混入 48 個產品板卡統計。

完整板卡矩陣、開放問題與下一門檻位於 `docs/bananapi-family-optimization-audit-20260826.md`。

## 三、主線相容修正

### 3.1 板卡與來源

- BPI-R2 的 U-Boot 改為 `v2026.07` 固定提交 `ece349ade2973e220f524ce59e59711cc919263f`，修補已對乾淨上游來源執行 `git apply --check`。
- BPI-R2 Pro 移除重複 U-Boot 定義，固定 `v2026.01` 提交 `127a42c7257a6ffbbd1575ed1cbaa8f5408a44b3`，並由 `current` 掛鉤覆寫可移動來源。
- BPI-M3 固定現行 U-Boot `v2026.07` 提交，移除會在 A83T SPL 引發 MMC 讀取錯誤的舊延遲校準修補，並以回歸測試禁止重新啟用。
- Sunxi `edge` 測試與修補路徑改用現行 `7.1`，不恢復失效的 `7.0` 副本。
- Meson 板卡測試可正確辨識 `.conf`、`.csc`、`.wip`、`.eos` 維護層級變更。

### 3.2 建置與封存

- W2、AI-M7 與 SM10 的歷史映像守門改以 `.img.xz` 為標準封存產物，串流驗證解壓大小與 SHA-256，不再要求數 GB 的重複 `.img` 常駐。
- W2 由 XZ 串流讀取 MBR、雙分割區與 U-Boot 載荷；AI-M7 由 XZ 串流閉合原始映像證據。
- SM10 只在 GPT CRC、分割區及載荷重驗期間建立自動清理的暫存映像，完成後不占用永久空間。
- RTL8852BS 共用驅動注入、快取鍵與核心產物身分統一使用提交 `58840d11af91d0b72bc830980b4aff740a37b5e3`；CM5 Pro 繼續使用其歷史 L2 已驗證的板級提交覆寫，不冒充已重新建置。
- R2 移除不會由現行家族安裝的舊板級 networkd 死檔，改驗證通用 `/etc/netplan/10-dhcp-all-interfaces.yaml`。

### 3.3 H618 正式與實驗邊界

- BPI-M4 Zero 正式 U-Boot 修補序列不再包含 DDR 實驗 SPL。
- DDR 實驗修補移至 `patch/lab/`；實驗建置工具加鎖後才暫時連結至 `userpatches`，結束或失敗時均自動清理。
- BPI-M4 Berry 測試會合併判讀板檔與 H618 共用 include，確認標準 I/O、桌面媒體工具及主線 RTL8821CU 設定。
- BPI-M4 Zero EMAC 維持 L2 功能變體；AC300 EPHY 實機仍是必要門檻。

## 四、本機驗證

已執行下列確定性守門，不使用 GitHub Actions：

```bash
python3 -m unittest discover -s tests -p 'test_bananapi*.py'
python3 -m unittest discover -s tests -p 'test_bpi*.py'
python3 tools/bananapi-board-audit.py --check
bash -n <本次修改的 Bash 與板卡設定>
shellcheck <本次修改的 Bash 與板卡設定>
python3 -m py_compile <本次修改的 Python 程式與測試>
python3 -m json.tool <本次修改的 JSON 契約>
git diff --check
git apply --check patch/u-boot/v2026.07/board_bananapir2/enable-boot-from-ext4.patch
```

結果：

- Banana Pi 全系列回歸：505 項通過，0 項失敗。
- H618／BPI 專項回歸：103 項通過，0 項失敗。
- 板卡盤點：48 個產品板卡、1 個功能變體，狀態與產生報告一致。
- W2、AI-M7、SM10 的真實 XZ 歷史重驗通過。
- Bash、ShellCheck、Python、JSON、修補套用與差異格式守門通過。

## 五、證據限制

- 上述測試數量是整合分支的程式回歸，不等同逐板完整映像建置數量。
- R2、R2 Pro 與 M3 的現行 U-Boot 固定來源已更新，舊 L2 文件只保留歷史證據；必須重新建置才可建立新版本 L2 紀錄。
- W2、AI-M7、CM5 Pro、SM10 等既有 L2 仍受預建載荷、韌體、工具鏈、載板等同性或再散布授權限制。
- BPI-M2C、CM2、M4 Super 維持 L0；缺少專屬硬體資料、可攜來源或受控簽署資料時不得提升。
- 沒有新的 L3 實機證據；冷啟動、儲存、網路、顯示、加速器、USB、無線與排針功能仍依每板下一門檻執行。
- 本分支不是 Armbian 官方接受證明；可上游內容仍須拆分、審查並由官方專案決定是否合併。

## 六、後續順序

1. 優先重新建置 R2、R2 Pro 與 M3 現行來源候選，產生新的 L2 證據，不覆寫舊映像。
2. 取得 BPI-M4 Zero EMAC 實體板後驗證 AC300 EPHY。
3. 依盤點報告的 `next_gates` 逐板完成 L3 實機矩陣，失敗證據同樣納入版本控制。
4. 先拆分通用且不含授權阻擋的修正提交，再評估送交 Armbian 官方。
