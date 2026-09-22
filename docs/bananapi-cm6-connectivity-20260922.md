# CM6 rc2 燒錄啟動回報與藍牙／網路追蹤

2026-09-23 已接入實機，後續 PHY 重設修正、冷開機與藍牙開關測試見[實機驗證紀錄](bananapi-cm6-hardware-20260923.md)。以下保留 2026-09-22 的交付與判斷範圍。

2026-09-22，使用者回報 `Armbian_Noble_bpi-cm6_gnome_titan-emmc_20260918-rc2.zip` 燒錄、啟動通過，`eth1` 可以使用；板載藍牙查不到介面，`eth0` 插線無法使用。原始文字與證據分類保存在 [回報紀錄](evidence/bananapi-f3-cm6-vendor/user-report-20260922/report.json)。

這是 CM6 的使用者實機回報。F3 的實機狀態不因此變更；CM6 的冷開機條件、媒體回讀、Titan 版本與完整日誌仍未取得。原 rc2 ZIP、校驗值及建置時驗證紀錄保留，避免把後續問題混入原始成品。

## 已確認的映像內容

- 藍牙：BlueZ 與相關工具、核心 HCI UART／Realtek 模組及 RTL8852BS 韌體已存在，但缺少支援 `rtk_h5` 的 Realtek attach 工具與 CM6 板級啟動程序。`spacemit-bt` rfkill 的預設狀態會維持電源重置；只重啟 BlueZ 並不補上這條啟動鏈。
- 網路：兩個 EMAC 與 Realtek PHY 驅動均已啟用。`/etc/netplan/00-default-use-network-manager.yaml` 只指定 NetworkManager，沒有介面或 MAC 綁定；來源根系統沒有已存的 NM 連線、`no-auto-default.state`、networkd 介面設定或首登入自動改網路參數。

## 截圖提供的判斷界線

使用者補充 `/media/pi/SMCI/bpi/bpi-cm6/log` 下三張截圖，原始位元組另外保存在本機證據目錄，倉內僅保存 SHA-256 與必要狀態摘要。

- `49.png`：`hciconfig -a` 沒有列出控制器，與缺少 UART attach 啟動鏈的離線發現一致。
- `50.png`：`eth0` 存在，替代名稱 `end0`，旗標只有 `BROADCAST,MULTICAST`，沒有 `UP`；`eth1` 有 `UP,LOWER_UP` 並取得動態 IPv4 位址。
- `51.png`：`eth0` 仍沒有 `UP`；`eth1` 保持管理上的 `UP`，但沒有 carrier。

這能確認 eth0 的介面狀態，尚不能區分未啟用與 `ndo_open`／PHY 初始化失敗。需取得 `nmcli device connect eth0` 的結果與同時段核心日誌；本輪不直接修改 DTB。

## 官方來源與待驗假說

藍牙採用 [SpacemiT 官方工具固定提交](https://github.com/spacemit-com/rtk_hciattach/tree/9ce1210f63cce2da2ee7a347fa7f71cfb764aa20)，該版本包含 RTL8852BS 韌體選擇。電源與 attach 順序參考 [官方 S40hci](https://github.com/spacemit-com/buildroot-ext/blob/855fba3e9458d69d08f71bad66e42e87979e6885/board/spacemit/k1/plt_overlay/etc/init.d/S40hci)，CM6 實際 UART 則依本板裝置樹解析，不照抄其中的固定 `ttyS2`／`rfkill0`。

目前 CM6 的 UART 節點是 `/soc/uart@d4017100`，對應 `serial1`；[固定核心裝置樹](https://github.com/BPI-SINOVOIP/pi-linux/blob/0d0af0d895251383baee939d44e523699e31889f/arch/riscv/boot/dts/spacemit/k1-x.dtsi) 與 [板級 DTS](https://github.com/BPI-SINOVOIP/pi-linux/blob/0d0af0d895251383baee939d44e523699e31889f/arch/riscv/boot/dts/spacemit/k1-x_deb1.dts) 是選址依據。

官方 CM6 Bianbu 參考 DTB 多了 GPIO45／46 的 PHY reset pinctrl 與對應 GPIO range；這是可查核差異，但尚未由現場啟用錯誤證明與本次 eth0 問題有因果關係，保留為待驗假說。不可把其他核心版本的整份 DTB 或 reset ID 直接搬進 rc2。

## 待補的板上資料

CM6 載板型號／版本、`eth0` 插線後指示燈、NM 的狀態與原因、`rfkill list`，以及對應啟動日誌。診斷操作應保留目前能使用的 `eth1` 連線。

## 本輪交付

已產生 [藍牙修正候選 ZIP](https://drive.google.com/file/d/1Ma7wKF_TCrGZlUz87CCZ2cav9D-SpKIA/view) 與 [整包 SHA-256](https://drive.google.com/file/d/1ajG9B1LO3cmXV3AsnehS14aqgg5UVKVj/view)。本機絕對路徑：

```text
/media/pi/SMCI/bpi/f3-cm6-connectivity-20260922/BPI-CM6_rc2_藍牙修正候選_20260922.zip
```

ZIP 內包含 `bpi-cm6-bluetooth_0.1.0~20260922rc2_riscv64.deb`、單檔診斷工具、安裝／回退說明及校驗與驗證紀錄。這是現有 CM6 系統的增量修正候選；原 rc2 Titan 映像仍是原始位元組。套件補上依 OF 節點選址的 UART attach 服務，並移除官方工具在錯誤恢復時固定重設 `rfkill0` 的路徑。官方原始碼、最小補丁及修改後完整來源隨套件保存。

套件版本內的 `rc2` 指本次修正套件候選序號，與原映像的 `20260918-rc2` 分開追溯。套件 SHA-256：`49531220e72a508aa015e8b99f2c82d99418df93a5beb421e7a132a7d1b519d5`。ZIP SHA-256：`60b0172cd4c703e9d8117e937b2a89ffa815d7c9c70ca8e511f5fbf2e421f6e7`。

解壓並校驗後，在 CM6 執行：

```sh
sha256sum -c SHA256SUMS
sudo python3 collect_bpi_cm6_connectivity.py --output ./cm6-before
sudo apt install ./bpi-cm6-bluetooth_0.1.0~20260922rc2_riscv64.deb
bluetoothctl list
sudo python3 collect_bpi_cm6_connectivity.py --output ./cm6-after
```

套件安裝會啟用並嘗試啟動板載藍牙服務。若要回退，使用 `sudo apt purge bpi-cm6-bluetooth`。診斷器僅收集狀態，不啟用網卡；每次需指定新的輸出目錄。eth0 插線後另執行 `sudo nmcli device connect eth0`，保留輸出與同時段核心日誌。

## 驗證與未完成項目

[驗證清單](evidence/bananapi-f3-cm6-vendor/connectivity-20260922/validation.json) 記錄 92 項本機測試通過，涵蓋錯板／錯節點拒絕、來源與補丁篡改、實際 C 程式的 UART 錯誤退出、套件生命週期、同來源套件刷新及診斷器輸出限制。

[精確套件的隔離安裝紀錄](evidence/bananapi-f3-cm6-vendor/connectivity-20260922/installation-validation.json) 使用原 rc2 根系統的一般檔案副本與真實 RISC-V `dpkg`，通過安裝、所有 payload 檔案核對、unit 靜態檢查、helper `-l`、移除重裝、重複安裝、手動停用狀態保留與 purge。隔離環境沒有掛入主機 sysfs／裝置，也沒有啟動硬體服務。生命週期的執行中服務狀態另由真實 Debian helper／invoke 與 `systemctl` 替身驗證，不能視為板上服務實測。

藍牙 HCI、掃描、配對、資料傳輸與冷開機恢復仍待實機驗證。eth0 根因未定，詳見 [網路分析](evidence/bananapi-f3-cm6-vendor/connectivity-20260922/ethernet-analysis.json)；本包沒有網路修正。Google Drive 已回讀名稱、容量與父目錄，雲端完整內容雜湊未由連接器提供；下載端應使用隨附 SHA-256 核對。

## 後續映像建置接入

`tools/build_bpi_cm6_bluetooth.py` 依固定來源鎖交叉編譯；`tools/package_bpi_cm6_bluetooth.py` 校驗來源、補丁與架構後封裝。新建 CM6 根系統必須傳入 `--cm6-bluetooth-package`，並在同目錄提供 `package-manifest.json`；F3 不接受這個 CM6 套件。

同來源既有工作目錄只有明確使用 `--resume --refresh-packages` 才能新增或更新藍牙套件雜湊；原準備紀錄會先存入 `history/`。板型、來源映像與結構版本的身分核對仍有效。此接入已完成本機回歸，尚未據此產生新整份映像。
