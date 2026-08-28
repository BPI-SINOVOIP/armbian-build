# BPI-SM10 SpacemiT K3 L2 內部候選計畫

更新日期：2026-08-28

## 目標

從已固定的 `k3-br-v1.0.y` manifest、20 個 SDK 專案提交及現有 L1 元件證據，建立 `bananapism10` 的 Debian Trixie current minimal CLI 完整映像。只有來源一致的啟動鏈、Linux、DTB、rootfs、IMG 與 XZ 全部通過唯讀物質守門及歷史重驗後，中央狀態才可由 L1 提升為 L2 內部軟體候選。

本計畫不核准公開發布、安全開機、載板等同性或任何實體功能聲明。

## 固定來源與輸出

- manifest 提交：`6d767b42fdbd759dc9511b8a13523c3de42aaa5a`。
- Linux 提交：`27275ec8240cc49af3a525b8bc325d9b5029fb81`。
- U-Boot 提交：`1b10c8119e1a9b5451a4236f6b384f7c91eed1e2`。
- OpenSBI 提交：`3e2f9efc9660b8d5fcae4e0b6495f306d5c64078`。
- ESOS 提交：`92a8baf250e42853a094a7af6f7ee849adb3de4a`。
- Armbian firmware：固定至提交 `f50a2a21bcdb77a562b3976930c5c6b521a1df08`。
- 固定建置時間：`SOURCE_DATE_EPOCH=1777390324`，取自固定 U-Boot 提交時間。
- 固定輸出：`output/images/2026.08/bananapi-spacemit-k3-sm10-trixie-current-cli`。
- 共用快取：`/media/pi/SMCI/armbian/bpi-v26.2.1/cache` 只作唯讀 lower；身分固定為 device `66306`、inode `96224797`。
- 專用上層：`.tmp/bananapi-spacemit-k3-sm10-cache-overlay`。

## 目前證據與差異

L1 元件輸出已保存 Linux `Image`、SM10 DTB、核心設定、`FSBL.bin`、`bootinfo_block.bin`、`u-boot.itb`、U-Boot 設定、`fw_dynamic.itb` 與 `fw_dynamic.elf`。其中三個可由固定來源重建的載荷與目前 Git 封裝內容不同：

| 載荷 | L1 固定來源重建 SHA-256 | 取代前封裝 SHA-256 |
| --- | --- | --- |
| `FSBL.bin` | `9a40d9d27ec8de79a38ece8ad00de96d29d45b507c43f46f3bf45589c50034d7` | `d18ceb20ae2433e441e9a5d935b1db34a7d35b5cf074979d8104ffc35c4971f2` |
| `fw_dynamic.itb` | `37dcca0ad696c88900c316a5bab289f1e3e55f09836cb22a4f09c1faa93be86d` | `6ba858dcbf79371cdf3cc4770e036ea448e7d81547bf880af5b2903e7296a044` |
| `u-boot.itb` | `f7560b4afd523b484b7f950f038485dea7c28cbf5f9c225290d940ca4461ae13` | `1f7752ad032e3b04e30ffce5e9e3a79b427c05efc7fc7ef4130fde23a7990982` |

原廠預建版本的 FIT 建立時間為 2026-05-26；固定來源重建版本採 U-Boot 提交時間 2026-04-28。`bootinfo_block.bin` 與環境載荷已相同。`esos.itb` 尚無受控重建產物，仍只能以 SHA-256 `11099edb92c9721ebb207de3a200905618dcfded32c19f5ef0525275c253bf28` 的預建載荷納入內部候選，並持續阻擋公開發布。

## 2026-08-28 執行進度

- 固定來源元件已完成第二次獨立重建；`FSBL.bin`、`bootinfo_block.bin`、`u-boot-env-default.bin`、`fw_dynamic.itb`、`u-boot.itb` 與 `uboot.config` 的兩次 SHA-256 全部一致，`env.bin` 由相同的 U-Boot 預設環境位元組建立。
- 第二次重建的 Linux `Image` 與未封裝的 `fw_dynamic.elf` 並非位元級一致；這兩項不得列入可重現位元組聲明。正式映像的 Linux 仍須由 Armbian 固定來源重建並以最終設定及來源中繼資料守門。
- 七項來源建置或可追溯衍生的啟動產物已納入封裝契約；只有 `esos.itb`、`env_k3.txt` 與 `bianbu.bmp` 保留為未確認再散布授權的受控預建資產。
- L1 校準契約已加入來源契約投影、固定 Armbian firmware、80 GiB 空間下限、固定 lower 身分、固定輸出及專用 OverlayFS 上層。
- 共用唯讀驗證器已加入 K3 `env_k3` 模式，核對 GPT 名稱、類型、起點、不同的 bootfs／rootfs `PARTUUID`、受控環境檔、boot 檔案、來源載荷及最終組態。
- L1 只會產生 `SM10_CALIBRATION.json`；只有使用精確 GPT 與最終組態的已推送 L2 過渡契約，才能產生 `SM10_MATERIAL_EVIDENCE.json` 並提升驗證狀態。
- SM10 專屬 16 項與 SpacemiT 相關 31 項回歸、ShellCheck、Python 語法與 48 板盤點已通過。兩次全倉 483 項執行皆有 482 項通過，唯一失敗是中央狀態更新後兩份衍生盤點報告過期；以正確輸出參數重建報告後，14 項盤點測試已全部通過。這是組合守門證據，不冒充一次不中斷的 483 項全綠。尚未執行 L1 完整映像建置，因此中央證據維持 L1。

### 第二次元件重建暫存回收

- 回收前先確認工作樹與遠端提交 `1ce91266e0b3b295ef0ed3b70a1ea61b74ecc991` 一致，目標沒有掛載點、執行程序、開啟檔案或 Docker 掛載引用。
- 只刪除 `.tmp/bananapi-sm10-components-rebuild-20260828`，邏輯大小為 9,261,621,248 bytes；執行環境拒絕 `rm -rf`，因此使用限定精確目錄及單一檔案系統的 `find -xdev -depth -delete`。
- `/media/pi/SMCI` 可用空間由 118,947,454,976 bytes 增加至 128,205,795,328 bytes，實際回收 9,258,340,352 bytes。
- `output/components/2026.08/bananapi-spacemit-k3-sm10-current` 的 31,461,376 bytes L1 元件證據仍保留；正式映像目錄尚未建立。唯讀 lower 身分仍為 device `66306`、inode `96224797`。

## 執行階段

1. **來源一致性**：以已保存且通過 L1 守門的固定來源重建產物取代三個可重建封裝載荷，加入 U-Boot 最終設定證據，更新逐檔雜湊、來源說明、板級封裝與負向測試。ESOS 必須明確保留為未重建預建資產。
2. **L2 狀態機**：擴充 validation 與政策檢查器，加入穩定來源契約投影、L1 校準／L2 正式狀態、完整映像證據形狀、來源提交與 tree、建置及驗證契約、IMG／XZ、清單、最終設定、映像 DTB 與歷史重驗；不得預填尚不存在的正式證據。
3. **固定環境守門**：SM10 專用入口固定唯一輸出、唯一專用上層、唯一 lower 身分、來源 SDK 與公開發布禁止；拒絕環境覆寫、髒工作樹、並行 Armbian 建置及低於 80 GiB 的可用空間。
4. **L1 校準建置**：由已推送提交執行 SDK 來源核對與隔離完整建置，量測 GPT、六個 raw offset 載荷、FAT／ext4、根標籤、`env_k3.txt`、initramfs、映像 DTB、最終核心與 U-Boot 設定、必要套件及模組。校準結果只可寫成 L1，不得提升中央狀態。
5. **正式契約推送**：把校準的精確值寫入受控契約、工具、文件及測試，先提交並推送；確認無掛載、程序、開啟檔案或容器引用後，只刪除校準輸出與 SM10 專用上層。
6. **L2 正式重建**：從空白專用上層及已推送契約提交重建完整 IMG／XZ，執行 XZ 串流同一性、GPT、唯讀 FAT／ext4、來源中繼資料、六個 raw offset 載荷、禁止私鑰、DTB、最終設定、套件與模組守門。
7. **證據閉合與回收**：回填正式來源 commit／tree、來源契約投影、建置與驗證 validation、IMG／XZ、候選矩陣、完成狀態、驗證清單、載荷清單及設定清單雜湊；全案回歸、歷史重驗與遠端推送完成後才升級中央 L2，並只回收正式專用上層。正式 IMG 與 XZ 必須保留。

## 拒絕條件

- 任一 SDK 專案、manifest、Linux、U-Boot、OpenSBI、ESOS 或 Armbian firmware 不是固定提交。
- 映像使用可重建元件的舊預建位元組，或把 `esos.itb` 誤標成已由來源重建。
- 來源提交、tree、validation、來源投影、候選矩陣或驗證器身分不一致。
- GPT、分割區起點、檔案系統、標籤、`env_k3.txt`、根裝置解析、DTB、initramfs、最終設定、套件或模組不符。
- 六個 raw 載荷的套件雜湊、映像 offset、大小或內容不一致。
- 映像含 SDK 私鑰、未登錄載荷，或宣稱 CRC32 FIT 等同安全開機。
- 將 `.wip`、donor DTS、完整映像或靜態 DT 節點誤宣稱為 BPI-SM10 載板、開機媒體或介面實測通過。

## L2 後續限制

L2 完成後仍須取得 BPI-SM10 與 K3-CoM260 載板拓撲比對資料，並以實體板完成 SD 冷啟動、UART、儲存、網路、Wi-Fi／Bluetooth、USB host／gadget、PCIe／NVMe、UFS、顯示、GPU、VPU、相機、音訊、CAN、GPIO、I2C、SPI、重新啟動、關機及長時間壓力測試。ESOS、PowerVR、VPU 韌體與開發私鑰流程未完成授權及量產安全審查前，組合映像不得公開發布。
