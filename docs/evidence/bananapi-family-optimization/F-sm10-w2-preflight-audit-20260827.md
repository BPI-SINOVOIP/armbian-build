# Banana Pi SM10 與 W2 完整映像預檢稽核

## 結論

Banana Pi SM10 與 W2 目前均維持 L1 元件候選，不能直接提升為內部 L2。兩張板已有固定來源與可攜元件證據，但都缺少可由同一提交重建、再以唯讀方式驗證的完整映像契約。

本文件是建置前的唯讀稽核，不包含完整映像建置、燒錄或實機測試，也不授權任何公開發布或硬體功能聲明。

## SM10

### 已有證據

- manifest、Linux、U-Boot、OpenSBI、ESOS 與 20 個 SDK 專案 revision 已固定。
- 元件證據位於 `output/components/2026.08/bananapi-spacemit-k3-sm10-current/`。
- `COMPONENTS.tsv` SHA-256：`14e1f7f8c4ad59493a6a6bae3da9fd8bdeb9bf9e12c5c9156539c1d84ced4931`。
- 最終元件核心設定 SHA-256：`549dd138a5205d71cca5620c4d54f5905d6305dfa502ca80c4d7a319d20d34c5`。
- 最終元件 U-Boot 設定 SHA-256：`ffb244d91c6d9ce59f20eeabee15f0391e5d6417548856cacd4720d87cf69b9c`。

### 阻擋 L2 的問題

- 未固定 Armbian firmware 的來源與提交，也未啟用來源解析守門。
- 家族封裝流程使用 Git 內預建載荷，不是本次元件重建結果。
- 重建的 `FSBL.bin`、`fw_dynamic.itb`、`u-boot.itb` 與實際封裝載荷逐位元不一致；現階段只能證明固定預建載荷，不能宣稱來源重建一致。
- 現有驗證器未完整綁定來源 tree、`CANDIDATES.tsv`、建置契約、驗證提交、最終映像設定與套件載荷。
- 共用驗證器尚未支援 SM10 的 `env_k3` 開機設定模式。
- FIT 只能證明 CRC32，不能據此宣稱安全開機；SDK 內開發私鑰不得視為量產金鑰。
- ESOS、PowerVR 與 VPU 韌體的再散布授權尚未閉合。

### 下一次預檢必須取得

1. 映像實際採用的六個 raw payload 身分與雜湊。
2. 精確 GPT 分割區數量、起點、大小、標籤與檔案系統。
3. `BPI-ROOT`、initramfs 與核心命令列的根檔案系統解析關係。
4. 映像內最終核心設定、U-Boot 設定與 DTB 雜湊。
5. 核心套件、套件集合、package-only payload 與禁止資產清單。
6. 建置來源提交、來源 tree、validation SHA-256 與驗證器提交的完整綁定。

## W2

### 已有證據

- 單體 BSP 固定為提交 `6e6aef00092d2d2cd2042f15c25a020cf98feb21`；Linux 4.9.119 與 U-Boot 2015.07 來自同一提交。
- Armbian firmware 提交固定為 `f50a2a21bcdb77a562b3976930c5c6b521a1df08`。
- 元件證據位於 `output/components/2026.08/bananapi-realtek-rtd1296-w2-legacy/`。
- `COMPONENTS.tsv` SHA-256：`d73e819b70b3fcdef6a8c2b8a12de530160ede5d6024311543deebb1541afcb9`。
- 核心設定 SHA-256：`b27ab2e9279a67f325a68926f822e3ca7a70da0d2527c9e801ce47ef92d5db1e`。
- U-Boot 二進位 SHA-256：`d4d425862ded2334d354b421ff2df8cdb965041b3b3b2c903fbeddd29ab23890`。
- W2 DTB SHA-256：`e2f0d51977310ecd06a8b72088a3ee3fbcec439b850ceacd9887c9b557d1c420`。

### 阻擋 L2 的問題

- 尚未建立 W2 專用完整映像建置器與驗證器。
- 現有驗證資料沒有精確分割區契約，也沒有可交接的最終 U-Boot `.config`。
- W2 使用 vendor `uEnv.txt`、`root=LABEL=BPI-ROOT` 與 `/boot/bananapi/bpi-w2/linux/`，不能套用共用驗證器目前的 `armbian_env` 模式。
- 四個 U-Boot 靜態庫與 `bluecore.audio` 缺少可建置原始碼及已確認的再散布授權。
- BSP 內範例 RSA 私鑰不得被封裝，也不得據此宣稱安全開機。
- Linux 4.9.119、U-Boot 2015.07 與 vendor 警告仍是維護風險。

### 下一次預檢必須取得

1. 建立 `realtek_uenv` 驗證模式，核對 MBR、兩個分割區、FAT boot 與 ext4 root。
2. 固定 `BPI-BOOT`、`BPI-ROOT` 標籤及根檔案系統解析，禁止 `/dev/mmcblk*` 硬編碼。
3. 驗證 `uImage`、`uInitrd`、W2 DTB、`bluecore.audio` 與 `u-boot.bin@40960` 的套件及映像內容。
4. 固定第一分割區起點，證明不與 byte `40960` 至 `473199` 的 U-Boot 範圍重疊。
5. 保存映像內最終核心與 U-Boot 設定、來源 tree、validation SHA-256、候選矩陣與驗證提交。
6. 排除 BSP 範例私鑰，並保持公開發布與硬體聲明關閉。

## 決策

SM10 與 W2 都先完成一次受控預檢，再依實際產物收緊契約，最後由同一提交正式重建及唯讀驗證。未完成上述流程前，中央狀態維持 L1；即使完整映像可產生，也不得跳過內容守門直接升級。
