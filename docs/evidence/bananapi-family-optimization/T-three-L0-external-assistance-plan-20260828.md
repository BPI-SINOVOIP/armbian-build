# Banana Pi 三個 L0 板卡外部協助計畫

更新日期：2026-08-28

## 結論

48 個板卡目前有 45 個達到 L2 內部軟體候選，剩餘 L0 僅有 `bananapicm2`、`bananapim4super`、`bananapim2c`。三者都已完成現有本機資料可支持的來源稽核、拒絕守門與 donor 邊界；缺口是外部硬體設計資料、可攜來源、授權或簽署服務，不是再執行一次既有建置就能解決。

收到本文件定義的最小資料包以前，不得把 donor 改名為目標板，不得建立虛假的專屬 DTS／U-Boot，不得把歷史映像或 PAC 當成目前可重放證據，也不得提高中央證據層級。

## 共通交付規則

每塊板使用獨立目錄，建議名稱為 `bananapicm2`、`bananapim4super`、`bananapim2c`。每個目錄必須包含 `MANIFEST.tsv`，欄位固定為：

```text
path	sha256	origin	purpose	license_status	confidentiality
```

交付要求如下：

1. 每個實體檔案都要列出大小與 SHA-256；網址、Git 提交、產品 SKU、PCB 版次及量產料號不可只寫在郵件中。
2. 原理圖、網表與 BOM 若受保密協議限制，可放在受控私有位置，但清單仍須記錄版本、持有人與可供誰審查。
3. 不接受任何私鑰、密碼、存取 token 或可直接簽署量產映像的秘密材料。簽署需求只能提供服務介面、公開憑證鏈、key ID 與不可偽造的稽核收據。
4. 原廠映像、韌體、工具與二進位必須附使用及再散布範圍；不確定時標成「未確認」，不得推定可公開發布。
5. UART 與系統紀錄要保留未加工原檔、測試板序號、PCB 版次、電源、儲存媒體、映像雜湊及時間。
6. 硬體資料至少涵蓋兩片目標板；單片成功只能作初步證據，不能支撐穩定性或量產聲明。

## BPI-CM2

### 現有邊界

目前只有 RK3568 BPI-R2 Pro 的固定 Linux、U-Boot、RKBin 與 DTB 參考。R2 Pro 不是已確認的 CM2 載板，不能用其 model、compatible、網路交換器、PCIe、SATA 或供電節點代表 CM2。

### 必需資料

- CM2 模組正式 SKU、PCB 版次、RAM／eMMC 容量及量產料號矩陣。
- 實際載板型號、PCB 版次、原理圖、網表、生產 BOM 與模組連接器逐腳映射。
- 電源樹、上電／斷電時序、時鐘、reset、I/O 電壓域、PHY、pinmux、啟動 strap 與儲存接線。
- CM2 專屬或原廠 BSP 的 DTS、U-Boot defconfig／patch、工具鏈、factory image、來源版本及授權。
- 兩片板的完整冷啟動 UART、執行中 DTB、`dmesg`、`lsblk`、`lspci`、`lsusb`、`gpioinfo` 與介面實測紀錄。
- RKBin 與其他預建內容可隨 CM2 組合映像散布的書面範圍。

### 接受順序

1. 驗證產品與載板身分，逐網路比對 R2 Pro donor。
2. 建立 CM2 專屬 Linux DTS 與 U-Boot 身分，不沿用 donor model／compatible。
3. 由固定來源建立並驗證元件，達成 L1。
4. 建立完整 IMG／XZ、唯讀內容與來源機器證據，達成 L2。
5. 兩片實機完成冷啟動、儲存、網路、USB、PCIe、顯示及排針測試後，才評估 L3。

## BPI-M4 Super

### 現有邊界

ArmSoM Sige3 只能作 RK3568 軟體 donor。官方資料記錄 SYN43752，但 donor DTS 使用 AP6275S；官方頁面也同時出現 PCIe 3.0 x1 與 x2。這兩項矛盾未由硬體資料解決前，不得恢復先前撤回的 M4 Super DTS、U-Boot 或 overlay。

### 必需資料

- M4 Super 正式 SKU、PCB 版次、RAM／eMMC 料號矩陣、原理圖、網表及生產 BOM。
- M4 Super 相對 Sige3 的逐網路差異，包含 PMIC、Type-C PD、SD／eMMC、Ethernet、HDMI、MIPI DSI、USB、UART 與 40-pin。
- SYN43752 實際匯流排、供電、reset、時鐘、天線、Wi-Fi 韌體、NVRAM、Bluetooth patchram 與逐檔授權。
- PCIe lane routing、實際 lane 數、REFCLK、PERST、CLKREQ、電源控制與連接器腳位。
- M4 Super 專屬 BSP、DTS、U-Boot、原廠映像、完整 UART 紀錄與所有交付物的來源版本。
- 兩片板的冷啟動、儲存、網路、無線、顯示、USB、PCIe、音訊及排針實測紀錄。
- RKBin、無線韌體與其他預建內容的組合散布及公開發布範圍。

### 接受順序

1. 先以原理圖與 BOM 解決 SYN43752／AP6275S、PCIe x1／x2 衝突。
2. 從 donor 做可審查的逐節點差異，建立目標板 DTS 與 U-Boot defconfig。
3. 固定所有來源並完成元件建置，達成 L1。
4. 建立完整映像與唯讀內容證據，達成 L2。
5. 兩片實機通過全介面與壓力測試後，才評估 L3。

## BPI-M2C

### 現有邊界

本機 `UNC_LINUX_RLS_25C_W26.07.2` 快照含 95 個 repo 專案、41 組未攜帶的追蹤差異，以及 55 個專案內共 6,751 個未分類未追蹤檔。歷史 secure PAC 只能證明舊髒來源樹曾打包，不能證明目前來源可在另一台機器重放，也不能證明一般 SD boot、Armbian 支援或公開發布權。

### 必需資料

- 95 個固定提交的 Git bundle、受控鏡像或等效可攜來源，以及全新機器可解析的固定 manifest。
- 41 組差異的有序 patch series；每個修補須記錄來源、用途、適用提交與授權。
- 6,751 個未追蹤檔的逐檔分類、SHA-256、用途、來源與授權，只允許不可替代的必要輸入。
- 固定 Yocto tarballs、工具鏈、容器映像、machine／distro 設定、建置命令與套件清單。
- chipram、PAC／簽署工具、modem、Trusty、GPU、NPU、VPU、GNSS 及其他預建內容的使用與再散布權。
- 受控簽署服務的呼叫規格、公開憑證鏈、key ID、演算法、輸入／輸出雜湊與稽核收據；禁止交付私鑰。
- PAC 分割配置、已知良好 PAC 的 SHA-256、目標板 fuse／安全狀態、SD／eMMC 啟動政策與失敗復原流程。
- 兩片板與固定治具的 UART、冷啟動、SD、eMMC、網路、無線、顯示、音訊、USB、GPIO 及壓力測試紀錄。

### 接受順序

1. 在隔離的新主機只靠交付資料重建 95 個專案來源，來源樹必須無未分類輸入。
2. 逐項重放 41 組修補並固定 Yocto 環境；來源驗證器需從拒絕轉為通過。
3. 透過受控服務完成簽署與 PAC，保存公開可驗的輸入／輸出收據，不處理私鑰。
4. 先建立可重放元件與 PAC 證據，再評估是否達 L1；不得直接跳到 L2。
5. 建立 Armbian rootfs 混合候選、完整分割及唯讀內容守門後，才評估 L2。
6. 實機完成一般／安全啟動邊界及全介面測試後，才評估 L3。

## 執行優先順序

CM2 與 M4 Super 的硬體資料可平行收集；資料完整者先做逐網路 donor 差異。M2C 應先處理可攜來源、修補集與未追蹤檔分類，因為在這三項完成前，重新編譯只會重現不可審查的本機狀態。

任何資料包到達後，先更新對應 `config/validation` 契約與本文件的接收紀錄，執行既有 L0 守門，再開始板級實作。每次證據提升必須分開提交並推送；沒有實機或授權證據時，硬體與公開發布旗標維持 `false`。
