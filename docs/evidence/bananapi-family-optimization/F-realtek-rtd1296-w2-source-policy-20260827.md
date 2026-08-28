# Banana Pi BPI-W2 legacy 候選來源政策

更新日期：2026-08-27

## 階段結論

`bananapiw2` 先完成固定來源、預建二進位資產盤點、板級介面契約、隔離元件建置及 L1 唯讀元件檢查；2026-08-28 再由已推送提交完整重建 rootfs、IMG 與 XZ，並通過 L2 唯讀內容及歷史重驗。板卡仍保留 `.wip`，不得宣稱可開機、介面可用、硬體通過或允許公開發布。

本候選只修改 W2 板級設定、W2 專用修補、驗證契約及工具，不修改共用 Realtek 家族建置邏輯。主工作樹與既有 Armbian 快取只作唯讀研究來源。

## 固定來源

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| Linux 4.9.119 | `https://github.com/BPI-SINOVOIP/BPI-W2-bsp.git` | `6e6aefc35dc50b1b8231cdb03a995d088f29eb21` |
| U-Boot 2015.07 | `https://github.com/BPI-SINOVOIP/BPI-W2-bsp.git` | `6e6aefc35dc50b1b8231cdb03a995d088f29eb21` |
| Armbian firmware | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |

Linux 與 U-Boot 位於同一個約 1.86 GiB 的供應商單體倉庫，板檔使用精確 `commit:`，避免分支移動後取得不同內容。固定提交時間為 Unix 時間 `1571768256`；隔離建置亦固定 `SOURCE_DATE_EPOCH`、建置使用者及主機名稱。舊版 U-Boot 原先每次直接讀取系統時間，本候選以 W2 家族專用修補讓時間戳採用固定 epoch 與 UTC，避免只有內嵌秒數不同的假性漂移。

Linux 授權入口為 `linux-rtk/COPYING`，SHA-256 為 `af8067302947c01fd9eee72befa54c7e3ef8a48fecde7fd71277f2290b2bf0f7`。U-Boot 授權入口為 `u-boot-rtk/Licenses/README`，SHA-256 為 `8ce570d60d1fbc0ab8ea22e31e06bd7c9773953ba58fd7075d4e5690de9a08d0`。這些入口只能旁證上游聲明，不能替代逐檔授權審查。

固定 BSP 內含 `gcc-linaro-7.3.1-2018.05` 工具鏈。元件入口會核對 GCC 大小與 SHA-256，但不把工具鏈封裝到執行期候選；其再散布授權尚未完成確認。

## 預建資產與發布阻擋

U-Boot 來源建置實際由連結映射載入下列預建靜態庫；本倉庫未找到對應原始碼建置路徑，也未確認再散布授權：

| 資產 | 大小 | SHA-256 |
| --- | ---: | --- |
| `u-boot-rtk/static_lib/libefuse.a` | 35152 | `cb634ff54f9518af59b1bcab784f068036d8af32974583281c85763bcff84b41` |
| `u-boot-rtk/static_lib/libsha1_util.a` | 15192 | `fc23654b0d2bfbff349d5385e1722c820fadf4bcb33c2429dbad9dbbd34d4348` |
| `u-boot-rtk/static_lib/libsecurity.a` | 39616 | `c02176718463ce1e5aca67a94ae2fd8e7a16aed88130fb6421a302b9ada68ed7` |
| `u-boot-rtk/static_lib/libkeyset.a` | 68960 | `224543a99174bf46f1bd7fb849f40dd2f21ff4026640ff32413d714149d73f9b` |

執行期 `bluecore.audio` 是 3969840 bytes 的 Realtek 音訊 DSP 預建載荷，SHA-256 為 `59252270f05cc55cba0ddeb246bc7c6b20dab9554fa18be4e9595ea549fd9b1c`；同樣缺少可重建來源與已確認的再散布授權。上述五項資產均為公開發布阻擋，雜湊固定只代表內容可辨識，不代表來源可審計或可合法再散布。

供應商 `spirom-bpi-w2.bin` 與舊 `uInitrd` 明確排除於本候選。Armbian 建置應產生自己的 initramfs；SPI ROM 更新流程未納入本階段契約。

## 啟動與儲存契約

- U-Boot 使用 `rtd1296_sd_bananapi_defconfig`，原始載荷由整碟 offset 40960 bytes 寫入。
- 分割表固定為 MBR／`msdos`，避免 GPT 項目區與 40 KiB 載荷位置重疊。
- FAT boot 分割區為第 1 區，根檔案系統為第 2 區；`uEnv.txt` 使用 `root=LABEL=BPI-ROOT`，不綁定 `/dev/mmcblk0p2` 的核心枚舉順序。
- boot 目錄固定為 `/boot/bananapi/bpi-w2/linux`，契約資產為 `uEnv.txt`、`bluecore.audio`、`uImage`、`uInitrd` 與 `rtd-1296-bananapi-w2-2GB.dtb`。
- DT 靜態節點涵蓋 SD、8-bit eMMC、SATA 及兩個 PCIe 控制器；這不代表媒體啟動、熱插拔、吞吐或資料完整性已通過實機測試。

## 板級介面契約

W2 專用 Linux 修補把 DT 根節點固定為 `Banana Pi BPI-W2`，相容字串依序為 `bananapi,bpi-w2` 與 `realtek,rtd1296`。機器契約另固定下列靜態範圍：

- 網路：GMAC 節點及 vendor `R8168` 核心設定；不宣稱實體埠數、連線速率或封包穩定性。
- USB：一個 `peripheral` DRD、USB 2.0 host 與 USB 3.0 host 節點；核心保留 ConfigFS ECM 與 mass storage 能力，但不宣稱 Type-C／OTG 角色切換已實測。
- 顯示：HDMI TX 與 DisplayPort TX 節點為 `okay`，HDMI RX 節點存在但為 `disabled`；不宣稱顯示輸出、擷取、音訊或硬體加速通過，也不把 disabled 節點列為候選功能。
- I/O：兩組 GPIO 控制器、六組 I2C、一組 SPI 與 PWM；`BPI-WiringPi2` 與 `RPi.GPIO` 腳位表只作本機比對證據，不宣稱 40-pin 腳位、電壓或多工功能已實測。
- 無線：官方資料未列板載 Wi-Fi；候選只保留選配擴充的可能性，未固定模組物料，因此不得宣稱 Wi-Fi 或 Bluetooth 支援。
- 診斷：根檔案系統套件契約涵蓋 GPIO、I2C、SPI、MMC、SATA、PCIe、USB、網路、顯示、音訊、熱感測、watchdog 及壓力測試工具。

官方產品頁快照、V1.1 原理圖、`BPI-WiringPi2` 與 `RPi.GPIO` W2 標頭均以本機路徑及 SHA-256 登錄於 JSON 契約，但不封裝進候選；其再散布授權也尚未確認。

## 可重現元件驗證

靜態政策檢查：

```bash
python3 tools/check-bananapi-realtek-w2-source-policy.py
python3 -m unittest tests.test_bananapi_realtek_w2_candidate
```

隔離元件建置：

```bash
./tools/build-bananapi-realtek-w2-components.sh
```

唯讀元件驗證：

```bash
./tools/verify-bananapi-realtek-w2-components.sh
```

元件、建置記錄及清單保存在 `output/components/2026.08/bananapi-realtek-rtd1296-w2-legacy`。此本機證據目錄含 `bluecore.audio` 與由四個無來源靜態庫連結出的 U-Boot，只能供內部核對；在取得再散布授權前不得上傳或對外打包。完整 7.8 GiB 隔離來源與建置樹不屬交付證據。

建置入口只有在可用空間至少 50 GiB 時執行，最多使用 8 個工作，且完成後整個隔離工作目錄不得超過 10 GiB。它以 `git clone --shared --no-checkout` 從既有固定 BSP 物件庫建立工作樹，只讀取下層快取；不清理、不覆寫快取，也不建立 rootfs 或整碟映像。驗證內容包括兩次 U-Boot 雜湊一致、Linux `Image`、DTB、modules、核心設定、來源資產、實際連結的預建庫、DT 節點、U-Boot 識別字串與穩定根標籤。

2026-08-27T04:32:00Z 的乾淨端到端建置完成，工作目錄為 8171092 KiB，未建立 rootfs 或整碟映像。U-Boot 連續兩次重建的 SHA-256 均為 `d4d425862ded2334d354b421ff2df8cdb965041b3b3b2c903fbeddd29ab23890`，內嵌版本時間固定為 `Oct 22 2019 - 18:17:36 +0000`。

| 元件 | 大小 | SHA-256 |
| --- | ---: | --- |
| `u-boot.bin` | 432240 | `d4d425862ded2334d354b421ff2df8cdb965041b3b3b2c903fbeddd29ab23890` |
| `uEnv.txt` | 1261 | `5f21667c90fdbd2f8a2011e6c4de3ae85acc79fd1f82b0e929b968c1c16a0594` |
| `bluecore.audio` | 3969840 | `59252270f05cc55cba0ddeb246bc7c6b20dab9554fa18be4e9595ea549fd9b1c` |
| `Image` | 22065664 | `7b8011d75b477b67ecd3f9bc3e6b9ee3a601b7c8e68b9c4d332e35a80bc3a590` |
| `rtd-1296-bananapi-w2-2GB.dtb` | 48273 | `e2f0d51977310ecd06a8b72088a3ee3fbcec439b850ceacd9887c9b557d1c420` |
| `linux.config` | 139555 | `b27ab2e9279a67f325a68926f822e3ca7a70da0d2527c9e801ce47ef92d5db1e` |
| `linux-modules.tar.xz` | 37076636 | `4e1be051371acb38fee02c4fdafb909f5c1a1feca9aaff051c3ae8b82fd4e124` |

U-Boot 兩次建置未出現警告；Linux 建置日誌計得 246 筆 `warning:` 或 DT `Warning (` 訊息，包含 vendor MMC 回傳型別、USB 未初始化值、格式字串、未使用變數及 DT `reg_format`。這些警告不阻擋元件產生，但屬後續程式碼審查與實機驗證風險，L1 結論不把它們視為已解決。

## 升級與發布門檻

2026-08-28 已由提交 `7882ba85da55ad5a8096321811a8c2ff531b4c01` 完成正式 L2 重建。IMG SHA-256 為 `37d28132a24e0944112097caf66ce714ee589e6b8317351e861a6ff0c85a34fe`，XZ SHA-256 為 `ae74b820d3b3e540d79bf8a60d2d92210f1e41090e7c3ef14b28d0504072b116`；MBR、FAT、ext4、根標籤、vendor boot、W2 DTB、U-Boot 載荷、最終核心設定、清單與 XZ 串流均通過唯讀守門及歷史重驗。完整證據記於 `M-realtek-rtd1296-w2-L2-build-20260828.md`。

L2 只證明固定來源完整映像符合本機軟體契約，不能升格為硬體或發布證據。公開發布至少仍須：

1. 釐清四個 U-Boot 靜態庫、`bluecore.audio`、內含工具鏈及外部文件的來源與再散布授權。
2. 以實體板及 UART 完成多次冷啟動、SD、eMMC、SATA、PCIe、網路、USB host／gadget、HDMI TX／RX、DisplayPort、音訊、GPIO、I2C、SPI、PWM、熱感測、watchdog、重新啟動、關機與長時間壓力測試。
3. 評估 Linux 4.9.119、U-Boot 2015.07、229 筆正式建置 vendor 警告與非逐位元重現輸入的風險，建立可持續更新或移植到受維護版本的方案。

在上述阻擋關閉以前，`public_release_allowed`、`hardware_validated` 與 `hardware_claims_allowed` 必須維持 `false`。
