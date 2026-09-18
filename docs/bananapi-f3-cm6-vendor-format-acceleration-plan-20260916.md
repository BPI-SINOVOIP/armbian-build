# BPI-F3／BPI-CM6 官方格式相容 Armbian 與硬體加速計劃書

> 2026-09-18 更新：CM6 Titan eMMC 已回報 Code 20014 分區表匹配失敗；兩份舊 eMMC 候選撤回待查。先前離線通過不代表 Titan 可燒錄。兩份 SD 仍未驗證；原檔保留追溯。

日期：2026-09-16

狀態：舊 eMMC 候選因 Titan Code 20014 已撤回；新版 `20260918-rc2` 完成程式層重播，實際燒錄／開機／回讀仍待驗證。詳見 [修正候選紀錄](bananapi-f3-cm6-titan-20014-candidate-20260918.md)。

原 2026-09-17 建置紀錄：兩板 Noble 根系統與加速配套離線預檢已完成，四份官方格式候選已完成建置，108 項回歸及實際封裝離線驗證通過；完成紀錄見 [實作紀錄](bananapi-f3-cm6-vendor-implementation-20260917.md)。依使用者最新指示，先交付他人實測，再建立 AI 自動驗證環境；不等待本機實物接線。

查閱基線：`bananapi-release-finalize-20260906`，提交 `5f2788df426cc01773dcaf7e60c752ea513ebaf3`。

## 一、已確認的交付範圍

保留既有 BPI-F3、BPI-CM6 Armbian 映像與安裝流程，另建一條符合 Banana Pi／SpacemiT 官方格式的發行路線。每塊板分別交付 SD 與 eMMC 成品，開機後維持 Armbian 系統環境、套件管理與既有可相容的最佳化。

首批已由使用者選定 **Ubuntu 24.04 Noble 桌面**，共四個成品：F3 SD、F3 eMMC、CM6 SD、CM6 eMMC。其他發行版及精簡版列為後續擴展，不納入本次四成品的必要交付。

新增路線包含 GPU、AI 推論加速、VPU 多媒體、CPU／記憶體、儲存、網路、顯示、音訊與板上介面最佳化。以官方 BSP、官方參考映像和現有 Armbian 成果交叉核對，並以實機證據判定完成。

K1 是 `riscv64` 平台。使用者所稱的 NPU 需求，在本計劃落實為 K1 的 CPU 融合 AI、RVV／IME 與官方推論執行環境；官方標示的 2 TOPS 不作為應用效能保證，也不直接描述為獨立 NPU。[K1 官方規格](https://cdn-resource.spacemit.com/file/chip/K1/K1_brief_zh.pdf)、[官方 IME 規格](https://github.com/spacemit-com/riscv-ime-extension-spec)。

| 板卡 | 新增成品 | 封裝與安裝方式 | 必要結果 |
| --- | --- | --- | --- |
| BPI-F3 | 官方配置 SD 版 | `.img.zip`，解壓後為整碟映像，使用 Etcher 等整碟寫入工具 | SD 獨立開機，進入 Armbian，完成加速驗收 |
| BPI-F3 | Titan eMMC 版 | `.zip`，內含分區映像、開機元件與刷機設定，由 Titan Flasher 匯入 | USB 下載模式直接燒錄，移除 SD 後從 eMMC 開機 |
| BPI-CM6 | 官方配置 SD 版 | `.img.zip`，使用 CM6 與指定載板的配置 | 同上，另記錄載板版本及接線 |
| BPI-CM6 | Titan eMMC 版 | `.zip`，使用 CM6 與指定載板的配置 | 同上，另驗證 eMMC 開機區及容量相容性 |

官方文件明確區分 SD 的 `.img.zip` 與 eMMC 的 `.zip`；兩者具有不同內容契約，不能以重新壓縮或改副檔名替代格式轉換。[F3 燒錄文件](https://docs.banana-pi.org/en/BPI-F3/GettingStarted_BPI-F3)、[CM6 燒錄文件](https://docs.banana-pi.org/en/BPI-CM6/GettingStarted_BPI-CM6)。

「官方格式相容」表示符合指定官方工具與參考格式，成品名稱採 `vendor-sd`、`titan-emmc` 等用途識別，不宣稱本專案成品由原廠發布或認證。

## 二、既有成果與缺口

| 項目 | 已查得內容 | 本計劃處理 |
| --- | --- | --- |
| 發行成品 | 中央候選目錄的 F3、CM6 各有 8 個 `.img.xz`，涵蓋 `trixie`、`jammy`、`noble`、`resolute` 的精簡及 XFCE 版本 | 作為可追溯輸入與回歸基線；不覆寫 |
| CM6 | `legacy 6.6.36`、專用 DTB、固定 BPI Linux／U-Boot 來源 | 比對官方 BSP 的板級與加速套件配套 |
| F3 | `current 6.18.37`、專用 DTB、固定核心與開機元件 | 核對官方 DDK／VPU／IME 能否配合此核心 |
| 開機配置 | 既有 Armbian 使用 MBR、4 MiB 起始根分區及固定偏移載荷；eMMC 安裝另有 `boot0` 寫入行為 | 為官方格式建立獨立分區契約與媒體設定 |
| GPU／VPU | 核心已有 PowerVR、SpacemiT DRM、Linlon 設定 | 補齊執行環境、版本配對與實際工作負載 |
| AI | 本次受查 L2 證據沒有推論後端、模型與效能驗收 | 新增 RVV／IME、推論正確性、加速路徑與效能證據 |
| 證據等級 | 既有 CM6／F3 文件最高為 L2 建置與內容檢查 | 新格式重新驗證；不能繼承為已實機通過 |

本機依據：[CM6 來源政策](evidence/bananapi-family-optimization/F-spacemit-cm6-source-policy-20260827.md)、[CM6 建置證據](evidence/bananapi-family-optimization/F-spacemit-cm6-L2-build-20260827.md)、[F3 來源政策](evidence/bananapi-family-optimization/B-f3-source-policy-20260826.md)、[既有矩陣](../config/bananapi-latest-release-matrix.tsv)。

## 三、官方 BSP 與版本選擇

以 Banana Pi 的板級差異、SpacemiT 的完整加速軟體配套及指定官方鏡像組成參考基線。官網列出的早期 `linux-6.1.15-k1` 等入口只作來源線索；正式候選須選擇經比對的完整版本組合。[F3 BSP 入口](https://docs.banana-pi.org/en/BPI-F3/BananaPi_BPI-F3#_linux_bsp_source_code)、[CM6 BSP 入口](https://docs.banana-pi.org/en/BPI-CM6/BananaPi_BPI-CM6#_linux_bsp_source_code)。

本輪已重新核對的 CM6 官方分支：

| 元件 | 官方分支 | 查得提交 |
| --- | --- | --- |
| Linux | `BPI-SINOVOIP/pi-linux:linux-6.6.36-k1-cm6` | `0d0af0d895251383baee939d44e523699e31889f` |
| U-Boot | `BPI-SINOVOIP/pi-u-boot:v2022.10-k1-v2.1` | `066cccd77f35e57d13363fea524a439759196dca` |

兩者與既有 CM6 固定來源吻合，可直接做差異分析。[Linux 固定提交](https://github.com/BPI-SINOVOIP/pi-linux/tree/0d0af0d895251383baee939d44e523699e31889f)、[U-Boot 固定提交](https://github.com/BPI-SINOVOIP/pi-u-boot/tree/066cccd77f35e57d13363fea524a439759196dca)。分支符合不等於 GPU、AI 或官方燒錄已相容。

另已查得 SpacemiT `k1-bl-v2.2.y` 的完整 BSP manifest，包含 `linux-6.6`、`uboot-2022.10`、OpenSBI、Mesa3D、GPU、VPU 與 MPP；作為新版配套比較候選。manifest 引用可變分支，因此即使固定 manifest 本身，建置前仍要鎖定每個元件的實際提交。[官方 BSP 清單固定版本](https://github.com/spacemit-com/manifests/blob/6d767b42fdbd759dc9511b8a13523c3de42aaa5a/k1-bl-v2.2.y.xml)。

第一階段鎖定下列相依：官方鏡像及刷機工具版本／雜湊、BSP manifest、FSBL／DDR 初始化、OpenSBI、U-Boot、核心、DTB、遠端處理器韌體、GPU 核心驅動／韌體／DDK、Mesa／GBM／EGL、VPU 函式庫、AI 執行環境與工具鏈。每項記錄來源、固定提交或套件版本、SHA-256、授權及相容對象。

新路線優先選擇能完整支持硬體加速的配套。若 F3 現有 `current` 核心無法配合官方 GPU／VPU／IME，評估移植必要差異；若代價或穩定性不合理，新增路線使用獨立的 BSP 核心組合，並保留原有 `current` 發行。CM6 仍使用自身板級設定，不直接套用 F3 DTB。

GPU 核心驅動與使用者態 DDK 必須匹配，並核對 Mesa／GBM／EGL／GLVND 等圖形配套。[官方 GPU 配套要求](https://github.com/spacemit-com/docs-buildroot/blob/75b1c43ecef6b181bb48770c74afe2ece014a66c/en/k1_buildroot/graphics/graphics_driver_framework.md)。發行版的 `glibc`、`libstdc++`、Python、圖形介面與 RISC-V 指令要求也要核對。透過受控套件與限定來源安裝必要元件，避免將整個 Armbian 套件來源換成 Bianbu。預編譯元件須保存授權與來源，不能假定不同發行版的二進位可直接混用。

首批固定為 `noble` 桌面版本，核對官方套件相依後選定桌面配套。官方 GPU 套件的一份固定版本含有停用 Xorg `glamoregl`、設定 `Accelmethod "none"` 的配置；這不代表所有應用程式都不能使用 GPU，但表示 XFCE／Xorg 桌面加速不能直接假定成立。[官方 Xorg 設定](https://github.com/spacemit-com/img-gpu-powervr/blob/2e8686fcf6cad16f3d6b75d5eeeb36cfd3b04a22/target/usr/share/X11/xorg.conf.d/00-noglamoregl.conf)。桌面優先評估既有 XFCE；若 BSP 的完整加速路徑要求 Wayland，首批改用通過驗證的 Noble Wayland 桌面，清楚記錄原因及 XFCE／Xorg 的限制。

## 四、雙格式封裝契約

### 4.1 共用內容與媒體差異

同一板卡、發行版及軟體組合共用核心套件、模組、韌體、加速函式庫與根檔案系統來源；在最後階段分別產生 SD 與 eMMC 成品。分區識別、開機參數與媒體專屬元件各自產生，不要求兩份根分區逐位元相同。

根據指定官方套件實際核對 `factory/FSBL.bin`、`bootinfo_sd.bin`／`bootinfo_emmc.bin`、`fw_dynamic.itb`、`u-boot.itb`、環境映像、分區描述及刷機流程。`fastboot.yaml`、`partition_*.json`、`bootfs`、`rootfs` 等檔名與內容以鎖定版本為準，不先假定所有版本相同。

| 契約 | 必查項目 |
| --- | --- |
| SD | 官方配置的分割表、保留區與偏移、SD 專用開機資訊、分區界線及映像最小容量；完整斷電後可獨立開機 |
| eMMC | Titan 的套件辨識、USB 暫載 FSBL／U-Boot、儲存目標選擇、分區寫入、`boot0`／`boot1`／使用者區及開機設定的實際處理 |
| 板級 | DRAM 容量／初始化、板型識別、DTB、CM6 載板、電源與儲存配置 |
| 容量 | 以實際可用 LBA 與套件展開大小判定；不足時在寫入前拒絕，不能只看標稱 GB 數 |
| 完整性 | 每個內部元件及最終封裝都有雜湊；解壓後與 manifest 一致，分區不重疊、不越界 |

USB 暫載刷機用的 U-Boot 與最終安裝的 U-Boot 分別列入 manifest。先確認官方刷機協定支援，不能因某載荷可由 SD 開機，就認定能處理 Titan 的刷機命令。

### 4.2 Armbian 開機與更新

官方配置若使用分離的 `bootfs`、`rootfs`，須同步調整掛載點、`fstab`、extlinux／其他實際開機腳本、根分區識別及首次擴容。逐項追蹤核心、DTB、initramfs 與模組在套件升級後的落點。

驗收包含一次受控的核心／加速套件升級與重啟，以及已準備的回復方式。標準 `armbian-install` 或 bootloader 更新程式若會按舊 MBR 格式寫入，新增版本須提供相容路徑或在寫入前清楚拒絕不支援的操作；不能留給使用者在一般更新時破壞官方配置。

加速配套以版本相依、中繼套件與限定套件來源優先序管理，防止一般 `apt upgrade` 將核心、GPU DDK、韌體、Mesa 或 AI 執行環境拆成不相容組合。新組合通過升級與開機測試後才更新配套；保留前一組可安裝套件及回復步驟。版本控制不採永久凍結，核心安全修補與套件更新另有持續移植及回歸工作。

先檢查移除 SD 的 eMMC 開機，再檢查保留 eMMC 時的 SD 開機與媒體優先序。啟動紀錄須證明實際載入的 bootloader、核心、DTB 與根分區來源，避免偶然借用另一媒體而誤判成功。

### 4.3 發布隔離

建議另設 `output/vendor-format/<建置識別>/<板卡>/sd/` 與 `emmc/`；原有發行目錄及檔名不變。成品名稱至少包含板卡、發行版、桌面／精簡配置、核心組合、`vendor-sd` 或 `titan-emmc`、版本識別。

每份交付包含映像、SHA-256、來源與元件清單、繁體中文安裝／回復說明及功能驗證報告。Titan 套件內只放工具接受的必要內容；附加文件是否可放入套件須實測，否則放在旁邊。

## 五、硬體最佳化與完成條件

| 領域 | 工作內容 | 完成證據 |
| --- | --- | --- |
| GPU | 配對 PowerVR 核心驅動、韌體、DDK、Mesa／GBM／EGL／Vulkan；分別驗證桌面合成與應用程式 | 實際渲染器、API 版本、工作負載、GPU 統計、畫面正確性、持續負載；排除 `llvmpipe`、`softpipe`、`lavapipe` 回退 |
| 圖形相容 | 分開測試 Wayland、Xorg／Xwayland，以及交付桌面使用的介面 | `eglinfo`、`vulkaninfo` 及相應實際渲染測試；Xorg 有輸出不能代替加速證據 |
| AI／RVV／IME | 核對實機 ISA、官方工具鏈、核心支援與執行緒配置；整合官方推論環境 | 真正選用的執行後端與算子分配、正確性、回退比例、延遲、吞吐、記憶體及溫度 |
| VPU／多媒體 | 對照 BSP 的 Linlon、MPP、FFmpeg／GStreamer、顯示及音訊配套；逐格式測試 | 固定影片雜湊、格式／解析度、實際硬體編解碼路徑、CPU 使用率、掉幀、音畫同步；不以 GPU 成功代替 VPU 成功 |
| CPU／記憶體 | RVV 程式庫、排程、調速器、記憶體穩定與散熱策略 | 同供電／散熱下的延遲、吞吐、頻率、溫度與錯誤紀錄；保留過熱保護 |
| SD／eMMC／NVMe | 存取可靠性、合理時序、擴容與檔案系統設定 | 固定測試檔的正確性與吞吐、啟動及重啟；效能測試使用指定測試檔，避免原始裝置破壞寫入 |
| 網路／USB | 板上各埠、Wi-Fi／Bluetooth、USB 主機／裝置角色及 PCIe | 實際吞吐、重連、長時間傳輸及錯誤統計；介面數以板型與載板為準 |
| 顯示／音訊／I/O | HDMI、實際交付配置的 DSI／CSI、音訊、GPIO／I2C／SPI／UART／PWM | 依原理圖和具備的周邊建立板級矩陣；F3 與 CM6 載板分別記錄，未測項目明示 |

GPU、VPU、AI 分別建立狀態，任何一項缺少執行證據都不能標為「完整最佳化」。OpenCL、特定編碼格式等功能先核對指定 BSP 實際能力；晶片規格中的支援列表不能直接變成交付保證。

AI 首批選擇官方已支援的一個分類模型及一個偵測模型，固定模型／資料／量化設定及正確性門檻，對照一般 CPU 與 RVV／IME 最佳化執行。官方提供 `spacemit-onnxruntime`、`python3-spacemit-ort` 與 `SpaceMITExecutionProvider`，並支援子圖及執行剖析；不支援的算子會回退 `CPUExecutionProvider`。[官方 ONNX Runtime 文件](https://github.com/spacemit-com/docs-ai/blob/main/en/compute_stack/ai_compute_stack/onnxruntime.md)。以此作優先整合路徑，逐項揭露回退；若走其他官方執行環境，提供等效證據。AI 本來在 CPU 上執行，不能用「有 CPU 使用率」判定加速失效。

多媒體以官方 V4L2 VPU、`k1x-vpu-firmware`、`k1x-vpu-test` 及 MPP 與 FFmpeg／GStreamer 配套為參考，先驗證 H.264／HEVC 基本樣本，再擴大格式與解析度。[官方多媒體架構](https://github.com/spacemit-com/docs-buildroot/blob/75b1c43ecef6b181bb48770c74afe2ece014a66c/en/k1_buildroot/media/mpp/01-multimedia_framework.md)、[官方 VPU 驗證工具](https://github.com/spacemit-com/docs-buildroot/blob/75b1c43ecef6b181bb48770c74afe2ece014a66c/en/k1_buildroot/media/mpp/03-VPU.md)。

本機語言模型另作延伸工作負載，記錄量化、提示長度、生成長度、首字延遲與每秒輸出數，不作為首批分類／偵測驗收的替代品。核心數與親和性採實測和 BSP 支援決定，不直接將全系統固定到單一核心群。

## 六、實測方法與驗收門檻

以下數量是本計劃提出的驗收要求，不代表已完成測試。

1. 每板建立原廠參考映像、既有 Armbian 與新增候選三組比較；選擇同硬體、供電、散熱、儲存與相同測試負載。舊版無某加速功能時記錄缺項，不填入虛構效能值。
2. 階段 B 先做短循環檢查；最終四個板卡／媒體組合各完成至少 30 次完整斷電冷啟動及 10 次暖重啟，均可登入、正確掛載及進入預期系統；保留逐次 UART 紀錄。30 次門檻延續[既有 F3 實機要求](evidence/bananapi-family-optimization/B-f3-L2-build-20260826.md)。
3. 兩個 eMMC 套件各完成一次指定測試板的空白狀態安裝及一次重刷回復，使用固定版本 Titan 並記錄主機系統與 USB 驅動；SD 使用者流程也從發布封裝重新寫卡驗證。燒錄前識別板卡、實體媒體及需保留的板機資料。未測過的工具或主機版本不列入相容清單。
4. 每個交付桌面完成 GPU 渲染、影片播放及 AI 推論；相同元件雜湊可重用效能比較，但 SD、eMMC 均需確認驅動、套件、掛載與實際應用執行。
5. 四個成品各完成至少 2 小時 GPU、AI、影片及儲存／網路混合負載，每板另選一種代表性媒體配置做至少 8 小時穩定性測試；無核心崩潰、GPU 重置、資料損壞或未揭露加速回退。另一媒體的長時間結果不自動視為已通過。
6. 效能測試先暖機，再量測至少 5 組；報告中位數、延遲分布、CPU／記憶體、溫度與測試條件。功耗有量測設備才填實測值。
7. 以原廠可用功能為對照，逐項列出相容性；最佳化至少在一項約定主要指標呈現超出重複量測波動的改善。其餘關鍵指標若有明顯退步須分析並修正，不能只挑好看的數字。數值門檻在完成基線量測後、套用調整前鎖定。
8. 完成受控系統更新、重啟及回復測試。缺少實體板、指定載板或周邊時保留待測狀態，軟體候選最高維持 L2；不能把本機檢查當成實機通過。

首批實機結論限於記錄的板卡、RAM／eMMC 規格與載板。擴展其他容量或批次時增加樣本與差異測試，不用單片結果概括所有硬體。

## 七、執行階段

| 階段 | 具體工作與交付 | 進入下一階段的條件 |
| --- | --- | --- |
| A：來源與基線 | 鎖定官方 SD／eMMC 範例、Titan、BSP 配套；盤點既有成品；建立兩板元件與功能差異表 | 分區、刷機協定、核心／GPU／AI 相依及測試硬體已識別 |
| B：第一組實機 | 優先使用可連線測試的板，完成一個 Armbian 根系統的 SD／eMMC 包裝；立即做燒錄、UART 開機與 GPU／AI 短測 | 兩種媒體均能開機，GPU 與 AI 有實際執行證據；阻斷問題已定位 |
| C：四種成品 | 套用共用打包機制到另一板，補上板級 DTB／DRAM／載板差異 | 四種成品各有封裝檢查、燒錄與開機證據 |
| D：功能最佳化 | 完成圖形、AI、多媒體、儲存、網路與熱管理比較，逐項調整與回歸 | 核心功能與效能門檻通過，保留前後差異和限制 |
| E：更新與交付 | 更新／回復、長時間測試、中文說明、雜湊及來源清單 | 四種可重現候選、完整驗收表及可重播操作紀錄 |
| F：後續增量擴展 | 本次四份 Noble 桌面完成後，另依需求補其他發行版／精簡版 | 每個組合有自己的相容性結果與完成標記 |

原階段順序已依使用者後續指示調整：首批四份候選先完成建置與離線驗證，交付他人實測後，再導入 AI 自動驗證環境。遇到 GPU／AI 相容性阻斷，集中修復該元件組合，不繼續產生同樣缺陷的大量映像。工期在 A 階段取得實際建置時間、BSP 差異與測試設備後估算。

## 八、資源與防重策略

延用[既有增量防重計劃](bananapi-incremental-deduplicated-release-plan-20260905.md)及 `audit-bananapi-release-state.py`、`prepare-bananapi-incremental-state.py`、`run-bananapi-incremental-queue.sh` 的設計。新格式使用獨立矩陣與狀態目錄，並補上媒體及格式識別；既有五欄唯一鍵不足以區分本次成品。

新增用途唯一鍵至少為：

```text
board / release / profile / kernel_track / image_format / storage / layout_version
```

建置身分另含主倉提交、BSP manifest、各元件版本／雜湊、板級補丁、工具鏈、rootfs 套件清單、加速配套、打包器與設定雜湊。只有完整身分與驗證結果吻合才能直接沿用。

- 同一板卡與系統的共用 rootfs／套件只建一次，再分出 SD、eMMC；F3／CM6 只共用證實相同的快取與元件。
- 純分區或封裝變更只重打包；核心／驅動／runtime 變更才重建受影響元件與必要 rootfs。
- 沿用單一主建置與有界壓縮佇列，避免多個建置器爭用快取、迴圈裝置與磁碟；唯讀分析及非重疊審查可由子代理平行完成。
- 每項使用獨立日誌與可續跑標記，已完成、待打包、待壓縮者不可重新編譯。共享舊快取維持唯讀，新增變更使用隔離上層。
- 本計劃只處理 F3／CM6，不啟動全系列 444 映像重建。大型原廠映像先盤點本機；需要補件時只下載已選定版本。

## 九、預計實作位置與驗證

以下新增檔名為設計提案，目前尚未建立工具或改動程式。

| 位置 | 預計用途 |
| --- | --- |
| `config/validation/bananapi-spacemit-k1-vendor-*.json` | 兩板與媒體的來源、分區、載荷、套件及加速驗證契約 |
| `config/bananapi-spacemit-k1-vendor-matrix.tsv` | 獨立 SD／eMMC 發行矩陣及用途識別 |
| `extensions/spacemit-k1-vendor-format.sh` | 明確啟用的新格式與加速套件整合，不變更一般 Armbian 預設 |
| `tools/package_bpi_k1_vendor.py` | 共用元件輸出 SD 磁碟映像及 Titan 套件、來源清單與雜湊 |
| `tools/verify-bananapi-spacemit-vendor.py` | 分區界線、引用、容量、雜湊、板型、更新配置與格式交叉檢查 |
| `tools/collect-bananapi-spacemit-runtime.sh` | 在指定測試板蒐集開機、GPU、VPU、AI 與熱管理證據 |
| `tests/` 與 `docs/evidence/` | 新格式失敗案例回歸、實機結果、效能比較及來源證據 |

既有 `build-bananapi-spacemit-candidates.sh`、`verify-bananapi-spacemit-candidates.sh` 可提供來源與套件檢查；其 MBR 專用斷言不可直接拿來驗收新的官方配置。共用可獨立部分，為新配置新增明確驗證分支，不放寬舊規則。

Armbian 的 `post_build_image` 鉤子可提供 `FINAL_IMAGE_FILE`，適合導出額外成品；正式設計先判斷能否直接使用共同 rootfs 階段，避免反覆解壓整碟映像。獨立打包器不直接寫入實體裝置。

修改實作後，先執行相關本機回歸：

```bash
python3 -m unittest discover -s tests -p 'test_bananapi_spacemit_candidate_tools.py'
python3 -m unittest discover -s tests -p 'test_bananapi_spacemit_cm6_candidate.py'
python3 -m unittest discover -s tests -p 'test_bananapi_spacemit_source_policy.py'
```

新測試涵蓋跨板誤配、SD／eMMC 標記錯誤、分區重疊或超出容量、缺載荷、雜湊不符、GPU／核心版本不符、加速後端回退及中斷續跑。測試實際故障邊界，不以與程式相同的計算重做一次當作證明。所有驗證在本機與實機完成並保存命令、結果及提交碼。

另驗證未啟用新擴充時，F3／CM6 仍選擇原有來源、MBR 配置與一般輸出流程。若修改共用分割或套件程式，限定重建受影響的代表性舊格式候選做回歸，不擴成全矩陣。

## 十、本輪狀態與待核對項目

- 已完成：需求確認、四種交付格式界定、本機基線查閱、官方 SD／eMMC 流程及 BSP 入口核對、計劃書整理。
- 計劃初稿時尚未執行的項目，現已進展至 Noble 桌面／官方加速配套安裝與四份格式轉換；實體燒錄、效能及穩定性測試仍待外部驗證。
- 首批系統已確定：Ubuntu 24.04 Noble 桌面，兩板各 SD／eMMC，共四份。
- 已落定：GNOME Wayland、官方 v2.3 格式載荷、分板 GPU 配套與各 30 個固定加速套件。官網 Titan 入口為 1.0.35-beta，匯入／燒錄仍待實測；板卡、CM6 載板、RAM、媒體及工具實際版本由外測者回填。
- 本計劃不提升現有證據等級；新路線的完成狀態由各項新增證據決定。
