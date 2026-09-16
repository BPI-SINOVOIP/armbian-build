# 0845 十套映像第一輪實測總結

日期：2026-09-17。分支：`bpi-h618-recovery-network-20260915`。

## 結論與範圍

十套均完成原映像部署、全範圍回讀、客戶核心開機、基本測試收集及回救援，全程未拔 SD。這證明單站免拔卡流程可用，**不代表十套完整功能或 A1 792 MHz 長期穩定性已通過**。

來源為 `google-drive-upload/2026/2026.08/bpi-m4z-emac/` 的五發行版各 minimal／XFCE，核心均為 `6.18.49-current-sunxi64`。實板為使用者確認的 M4 Zero 0845；不依映像 DT 名稱推定實體板型或擴充板版本。

使用共用 A1 引導，載入各映像原配套核心、initramfs、模組與 eMMC 根系統；原 DTB 在 RAM 套用已記錄的 overlay 並停用 SD host。自動擴容及自動更新被遮罩。原客戶 SPL、TF-A、U-Boot 與原生 SD／eMMC 冷啟動鏈未驗證，不是逐位元組原樣執行整條客戶開機流程。

## 十套結果

下列十套的部署回讀、客戶核心／根身分、CPU、嚴格 SSH 與返回救援皆曾通過。

| 發行版 | 版本 | 檔案讀寫 | 服務 | 基本短測與例外 |
| --- | --- | --- | --- | --- |
| Bookworm | minimal | 通過 | `console-setup` 失敗 | 未全通過 |
| Bookworm | XFCE | 通過 | `console-setup` 失敗 | 未全通過；初次 XZ 部署失敗，限定重試成功 |
| Jammy | minimal | 空間不足，未執行 | 無失敗 | 條件不足 |
| Jammy | XFCE | 通過 | 無失敗 | 基本短測通過 |
| Noble | minimal | 空間不足，未執行 | 無失敗 | 條件不足 |
| Noble | XFCE | 通過 | 無失敗 | 首次 RCU 停滯，未重刷的冷啟動重試後通過 |
| Resolute | minimal | 空間不足，未執行 | 無失敗 | 條件不足 |
| Resolute | XFCE | 通過 | 無失敗 | 基本短測通過 |
| Trixie | minimal | 空間不足，未執行 | 無失敗 | 條件不足 |
| Trixie | XFCE | 通過 | 無失敗 | 基本短測通過 |

基本短測全通過 4 套，服務失敗 2 套，條件不足 4 套；檔案測試是 16 MiB 暫存檔寫入、同步、快取丟棄提示及摘要回讀，不是全檔案系統壓測。四套 minimal 因停用擴容，無法同時容納測試檔並保留 32 MiB；不能據此宣稱正常擴容後的客戶系統故障。

Noble XFCE 首次有 RCU stall 且未到登入畫面。主機自行斷電回救援，約 43 秒唯讀核對完整映像仍相同，再冷啟動成功。這是間歇失敗證據，不是已修復證據；Bookworm XFCE 初次 LZMA 錯誤亦尚未定位。不可用本輪結果對外宣稱 0845 已解決長時間當機。

## 效率與救援

除一筆失敗的部分部署外，十套各有成功部署收據；SSH 首次金鑰產生、網卡命名及 Noble 啟動異常均從相應階段續作，沒有重刷已核對的映像。原始壓縮檔、備份及失敗紀錄不刪除。

已去重核對 11 次救援返回，其中 T4 的 10 次有明示 `board_serial=0845`，另 1 次 T3 報告本身未明示板號，保持此證據限制。T4 的 10 次包含 9 次正常關機及 1 次異常強制斷電；每次斷電至少 10 秒，RAM 根、嚴格 SSH、載入核心／DTB／overlay／initrd 的長度與 CRC32，以及 SD 前 4 MiB 摘要一致。這些冷返回證據可沿用，不必重新跑十次來建立同一項紀錄。

沒有逐次完整 bridge／FIT SHA-256 證據，不能把上述結果升格為完整開機鏈逐位元一致。獨立 RAM 救援一小時記憶體壓測仍未完成；原 SD 系統先前的一小時負載不能代替它。

目前板子保持上電、停在 RAM 救援 `6.6.75`，UART 已釋放，沒有殘留部署或壓測程序；保留必要的 `bpi-rescue net-events` 網路服務。未改寫 SD 固定入口、eMMC boot0／boot1 或 RPMB；SD 核對範圍明確限前 4 MiB。

## 證據與續作

本機證據根目錄：`output/evidence/bpi-h618-no-mux/`。

- `T6-first-pass-001/summary.json`：`d5dab342aec0bce1fae3bc7f22b53b9bdf6d6a823f1daaa7e84c8714a8f9577a`。
- `T6-first-pass-001/audit.json`：`59bb03e6654a7924b1576d77e6768359321c071c8977d231a9081ab2339d326f`。
- `T6-first-pass-001/state.json`：`1477ab3e65c47846a1ac98ef14e082ad4950b64d41d6de242fbaf75cefbc2f9c`。

[完整執行紀錄](bananapi-m4zero-ten-image-no-mux-worklog-20260916.md)、[進度工具](bananapi-m4zero-lab-progress-20260916.md)、[控制腳本快照](evidence/bpi-h618-no-mux-20260917/README.md)及[計畫](bananapi-m4zero-ten-image-no-mux-plan-20260916.md)已分開保存。389 項工具回歸通過，不計為 389 項硬體通過。

## 下一階段

1. 先追查間歇 RCU／LZMA 問題及 Bookworm 服務失敗；受控擴容後補四套 minimal 檔案測試，修改測試版本並保留既有結果。
2. 補每套普通使用者與桌面流程，以及適用的 GPU、USB、GPIO／I2C／SPI、Wi-Fi／藍牙、完整記憶體與長測。只在組件、測試條件及硬體確實相同時沿用結果。
3. 將 0845 原型的硬編參數分離為站點設定，再按 SoC／架構建立適配；先同系 H618，其次其他已具備硬體的板型。同站循序、多站獨立並行，不共用 UART／電源或猜測 MMC 編號。
4. 45 板別、444 個壓縮映像只是目錄盤點；其他板的實機適配與映像測試尚未完成。無 eMMC 板型須另選已核定的 USB 或網路測試區，不能直接套用本板 eMMC 方案。
