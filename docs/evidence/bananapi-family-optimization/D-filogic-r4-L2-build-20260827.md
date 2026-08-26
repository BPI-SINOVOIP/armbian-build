# Banana Pi BPI-R4 current 候選映像 L2 建置證據

更新日期：2026-08-27

## 結論

`bananapir4` 已使用隔離快取，由固定來源完整建置 Debian Trixie current minimal CLI，並通過 Filogic MT7988 專用 L2 唯讀守門。候選映像的來源、IMG／XZ 同一性、五分割區 GPT、BL2／FIP 寫入內容、U-Boot 標準自動開機、板級 DTB、核心設定、MT7988 2.5G PHY／WED 韌體、MT7996 韌體、網路與標準 I/O 工具均符合本次受控政策。

此結果不代表實體板已完成 UART、冷啟動、SD、NOR、NAND、eMMC、PCIe、NVMe、DSA、SFP、無線網路、USB 或排針驗證。候選只涵蓋 SD 映像；NOR、NAND 與 eMMC 不得直接沿用本映像的寫入程序。取得實機證據前維持 L2。

## 建置身分

| 項目 | 值 |
| --- | --- |
| 板卡 | `bananapir4` |
| 發行版 | Debian 13 `trixie` |
| 核心目標 | `current` |
| 映像型態 | minimal CLI |
| 映像來源提交 | `a059b934d9de5c864b3b910f2fc43ad7c8689af5` |
| 驗證器提交 | `557b79bf3cd982c8c9c3330e3f53e3abcd1bb9e3` |
| 建置時驗證政策 SHA-256 | `75db1b26a91736481785c929022e6ff2b984a5281cbbec082050de1339ac386d` |
| 固定核心契約後驗證政策 SHA-256 | `8d0bc75d83e4671d03a39bebf33059d91608e2d26ea737d28f5aab0ef3361519` |
| Linux | `6.12.82`，來源提交 `4a4506842b77b597f11e7fc53be1dcdbdc97eea9` |
| U-Boot | `2025.04`，來源提交 `34820924edbc4ec7803eb89d9852f4b870fa760a` |
| ATF | 來源提交 `c34e37802efaea356991a0811c8fc50f8a810f5b` |
| Armbian firmware | 來源提交 `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| `mt76` 韌體 | 來源提交 `c5a3bd91aa735b669618610d5f0ebfa5786845a6` |
| Linux firmware | 來源提交 `01205307636157a12c29e6a774bf83b218732050` |
| 完整建置時間 | 13 分 31 秒；Docker 執行 817 秒 |
| 輸出目錄 | `output/images/2026.08/bananapi-filogic-mt7988-r4-trixie-current-cli/` |

建置使用 OverlayFS 隔離快取，共用快取只作唯讀下層。U-Boot、ATF、核心、韌體、板級套件與根檔案系統均由固定提交重新產生，不是由舊映像改名或只替換 bootloader。

映像來源提交實際產生的核心設定已是 `CONFIG_MEDIATEK_2P5G_PHY=y`。驗證器提交再把此值明列於追蹤中的核心契約，防止後續核心預設值改變；這項政策固定沒有事後修改本次映像。

## 映像與 GPT

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| IMG | 1421869056 | `39602eeca574f1ab5427558aa2baf95181fa5ca66bd72f9a35dee395699b0e9b` |
| XZ | 337320316 | `a4070f9f57c414a89644a9d1b96b865cffba362c0ec877a559ae3e8c56b5291d` |

XZ 通過串流解壓檢查，解壓後 SHA-256 與 IMG 完全相同。GPT 結構如下：

| 分割區 | 名稱 | 起始 sector | sector 數 |
| ---: | --- | ---: | ---: |
| 1 | `bl2` | 34 | 8158 |
| 2 | `ubootenv` | 8192 | 1024 |
| 3 | `factory` | 9216 | 4096 |
| 4 | `fip` | 13312 | 8192 |
| 5 | 根檔案系統 | 32768 | 延伸至映像尾端 |

`sgdisk -v`、分割區名稱、起點與大小均通過守門。`factory` 分割區屬板卡校準資料保留區，測試與安裝流程不得任意覆寫。

## 啟動鏈證據

| 載荷 | 放置方式 | 位元組偏移 | 大小 | SHA-256 |
| --- | --- | ---: | ---: | --- |
| `bl2.img` | 寫入映像 | 17408 | 250190 | `f588a5398dad957a97cc6926cb8c60333f386ae309c0db55429db15113e8eefb` |
| `u-boot.fip` | 寫入映像 | 6815744 | 1186785 | `a72353e3683981175482ff59989df0e48402db8a0544b33ed8c3c5811d0d54ad` |
| `gpt` | U-Boot 套件保留 | 不直接重複寫入 | 17408 | `beb31c2284ec7b8e910faeea8d323f40532b26010e87d0bae851d823705efa1d` |

守門從 U-Boot 套件取出載荷，逐位元組比對映像內指定偏移。`mt7988a_bananapi_bpi-r4-sdmmc_defconfig`、`CONFIG_AUTOBOOT=y`、BootSTD、extlinux、bootflow、MMC 與 EXT4 設定均已驗證。啟動載荷證據清單 SHA-256 為 `d571c2da830089cbf18aae43d083466129a7df1f8be7097e0e8366a06ece198e`。

## 核心、DTB 與韌體

映像內 `mediatek/mt7988a-bananapi-bpi-r4-sd.dtb` SHA-256 為 `61e8b004222395dc8a3ca1dbd2c4f3957bd03e1fa54501d252d1d5eae52d6552`。核心與執行期契約確認：

- MT7988 Ethernet、DSA、WED、2.5G PHY、SFP 與 MT7996 無線網路設定存在。
- PCIe、NVMe、MMC、SPI NOR／NAND、USB xHCI、I2C、SPI、PWM、GPIO 與 RTC 設定存在。
- `ethtool`、`iperf3`、`nftables`、`tcpdump`、`smartmontools`、`pciutils`、`nvme-cli`、`usbutils`、`iw`、`rfkill`、`gpiod`、I2C、SPI 與感測工具已安裝。
- `i2p5ge-phy-pmb.bin`、兩個 MT7988 WED 韌體及十一個 MT7996 韌體均以固定來源與精確 SHA-256 驗證。
- MediaTek 韌體授權與中文來源追溯文件已安裝於板級套件文件目錄；建置記錄沒有缺少韌體警告。

Linux firmware 的固定來源是官方儲存庫 `https://gitlab.com/kernel-firmware/linux-firmware.git`。授權原文因法律與可追溯要求保留，周邊來源、提交與檔案雜湊說明均使用繁體中文。

## 被拒絕的初版候選

來源提交 `4f72b7827` 雖能完成建置及當時的舊版驗證器，但 initramfs 曾回報缺少 `mediatek/mt7988/i2p5ge-phy-pmb.bin`。這證明當時驗證契約不完整，因此該產物已被拒絕，不是發布候選，也不得用來宣稱 R4 達到 L2。

本次接受的映像只來自 `a059b934d`，並由 `557b79bf3` 驗證；修正版的建置記錄已確認上述警告消失。

## L3 實機門檻

- 以 UART 保存多次冷啟動、重新啟動與斷電重啟記錄，確認 BL2、ATF、U-Boot、extlinux 與 Linux 啟動鏈穩定。
- 驗證 SD 開機、GPT 保留區及 `factory` 分割區未被破壞；NOR、NAND 與 eMMC 另走各自受控安裝程序。
- 驗證 DSA 各網路埠、2.5G PHY、SFP、VLAN、橋接、NAT、PPE／WED 與長時間 `iperf3` 雙向流量。
- 驗證無線網路、射頻校準、PCIe／NVMe、USB host 與儲存壓力。
- 驗證 I2C、SPI、UART、PWM、GPIO 與排針電氣對照；不得只以裝置節點存在判定通過。
- 執行 CPU、記憶體、網路與儲存混合壓力，保存溫度、節流、錯誤計數與測試時間。
