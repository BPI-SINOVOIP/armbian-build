# Banana Pi SM10 固定來源、授權與候選限制

## 結論

本候選以 SpacemiT K3 Buildroot `k3-br-v1.0.y` SDK 為唯一基準，固定
manifest 與 20 個子專案提交。Linux、U-Boot、OpenSBI 可由現有原始碼重建；
TF-A 不適用，因為 K3 是 RISC-V 平台，對應的 M-mode 執行階段是 OpenSBI。

候選維持 `.wip`，且 `public_release_allowed=false`、
`hardware_claims_allowed=false`。這代表可追溯與可編譯，不代表已完成實機支援、
量產安全開機或公開再散布授權。

## SDK 基準

| 項目 | 固定值 |
| --- | --- |
| SDK 目錄 | `/media/pi/SMCI/bpi/bpi-sm10/sdk/k3-buildroot-sdk-1.0` |
| manifest 來源 | `git@github.com:spacemit-com/manifests.git` |
| manifest 分支 | `main` |
| manifest 檔 | `k3-br-v1.0.y.xml` |
| manifest 提交 | `6d767b42fdbd759dc9511b8a13523c3de42aaa5a` |
| 固定 revision manifest SHA-256 | `6aa7ec0fe51fae1359552efb46ba92007b432e1b8530cf8a8872f663fc2b2b39` |
| SDK 工作分支 | `k3-br-v1.0.y` |
| 稽核結果 | 20 個專案皆乾淨且與固定提交一致 |

完整專案清單由
`config/validation/bananapi-spacemit-k3-sm10-current.json` 管理，
`tools/verify-bananapi-spacemit-k3-sm10-sources.sh` 會重新產生 revision manifest、
核對其雜湊，並逐一比對所有專案 HEAD 與髒污狀態。

## 核心元件

| 元件 | 固定提交 | 授權判定 | 候選處理 |
| --- | --- | --- | --- |
| Linux 6.18 | `27275ec8240cc49af3a525b8bc325d9b5029fb81` | GPL-2.0-only | 從原始碼重建 Image 與 DTB |
| U-Boot 2022.10 | `1b10c8119e1a9b5451a4236f6b384f7c91eed1e2` | GPL-2.0-or-later | 從原始碼重建 SPL、FSBL、bootinfo 與 FIT |
| OpenSBI | `3e2f9efc9660b8d5fcae4e0b6495f306d5c64078` | BSD-2-Clause | 從原始碼重建 `fw_dynamic.itb` |
| ESOS | `92a8baf250e42853a094a7af6f7ee849adb3de4a` | 混合且未閉合 | 僅核對既有產物，不核准公開散布 |

## 啟動鏈

1. K3 Boot ROM 讀取固定位置的 `bootinfo_block.bin`。
2. `FSBL.bin` 由 U-Boot SPL 原始碼產生，負責 DDR 初始化與下一階段載入。
3. SPL 載入 `esos.itb`、`fw_dynamic.itb` 與 `u-boot.itb`。
4. OpenSBI 提供 RISC-V M-mode 執行階段，不使用 ARM TF-A。
5. U-Boot 依 EEPROM `product_name` 選擇其控制 DT，並由 `env_k3.txt` 載入
   Armbian Image、initramfs 與 Linux DTB。
6. 原廠 GPT 佈局保留 0 至 12 MiB 給啟動載荷，FAT 開機分割區從 12 MiB
   開始，根檔案系統從 268 MiB 開始。

## DTS 身分與拓撲限制

Linux 候選新增 `k3-bananapi-sm10.dtb`，只在原廠 `k3_com260.dts` 上覆寫
`model` 與 `compatible`：

- `model = "BananaPi BPI-SM10"`
- `compatible = "bananapi,bpi-sm10", "spacemit,k3-com260"`

電氣節點仍完整繼承 `k3_com260.dts`。目前沒有足夠的 BPI-SM10 載板線路圖、
EEPROM 內容或實機節點對照，可證明 SM10 與 K3-CoM260 參考載板完全等同。
因此 U-Boot 控制 DT 仍保留 `k3_com260.dtb`；強行改名會破壞 SPL 依
`product_name` 選擇 FIT 組態的既有流程。

## 授權與安全阻擋

- ESOS 以 RT-Thread 為主體，但原廠版權清單含多家晶片廠元件及未註明授權。
- `k3x-vpu-firmware` 的逐檔版權資料明列沒有可確認的授權。
- PowerVR 套件的 Debian 版權檔仍含待填樣板欄位，不能作為再散布證明。
- SDK U-Boot 來源樹含多個私鑰與憑證檔；即使目前 `k3_defconfig` 未開啟
  `CONFIG_RSA_VERIFY`，這些材料也不能當作量產金鑰。
- 本候選不把 SDK 私鑰複製到 Armbian、元件證據或映像輸出。
- 公開發布前必須完成逐檔法務盤點、正式金鑰隔離／輪替／撤銷流程與實機驗證。

## 驗證層級

### 工具責任

| 工具 | 用途 | 本次狀態 |
| --- | --- | --- |
| `tools/check-bananapi-spacemit-k3-sm10-policy.py` | 強制 `.wip`、固定來源與禁止發布／硬體聲明 | 已執行 |
| `tools/verify-bananapi-spacemit-k3-sm10-sources.sh` | 重建固定 revision manifest 並核對 20 個專案 | 已執行 |
| `tools/build-bananapi-spacemit-k3-sm10-components.sh` | 在官方 SDK 容器內只編譯 Linux、U-Boot、OpenSBI | 已執行 |
| `tools/build-bananapi-spacemit-k3-sm10-candidate.sh` | 日後建立受控的單一 Trixie CLI 候選映像 | 未執行，避免建立 rootfs |
| `tools/run-bananapi-spacemit-k3-sm10-candidate-isolated-cache.sh` | 以 OverlayFS 隔離 Armbian 共用快取後呼叫候選建置器 | 未執行，避免建立 rootfs |
| `tools/verify-bananapi-spacemit-k3-sm10-candidate.sh` | 只讀 loop 掛載 IMG，核對 GPT、檔案、DTB、來源與原始位移載荷 | 未執行，因本次沒有 IMG |

機器可讀邊界集中在
`config/validation/bananapi-spacemit-k3-sm10-current.json`。其中
`candidate_boot_media=["sd"]` 只表示設計目標，`supported_boot_media=[]` 表示
尚無任何實機核准媒體；政策檢查器會拒絕把這兩者混用。

可在沒有 SM10 實機時成立的證據：

- manifest、20 個專案提交與工作樹乾淨狀態。
- Linux Image、專屬 DTB、U-Boot SPL／FIT、OpenSBI 的元件編譯。
- 啟動載荷雜湊、固定 offset 與映像唯讀檢查工具。

沒有實機時不得成立的聲明：

- SD、eMMC、UFS、SPI、NVMe 或 USB 確實可啟動。
- GPIO、I2C、SPI、CAN、PCIe、網路、GPU、VPU、顯示、相機或 USB 已可用。
- 安全開機、量產金鑰、休眠、溫度或長時間壓力測試已通過。
