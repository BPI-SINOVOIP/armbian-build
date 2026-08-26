# Banana Pi BPI-R3 current 候選映像 L2 建置證據

更新日期：2026-08-27

## 結論

`bananapir3` 已使用隔離快取，由固定來源完整建置 Debian Trixie current minimal CLI，並通過 Filogic MT7986 專用 L2 唯讀守門。候選映像的來源、IMG／XZ 同一性、五分割區 GPT、BL2／FIP 寫入內容、U-Boot 自動開機設定、板級 DTB、核心設定、MT7986 韌體、網路與標準 I/O 工具均符合本次受控政策。

此結果不代表實體板已完成 UART、冷啟動、SD、NOR、NAND、eMMC、SATA、PCIe、NVMe、DSA、SFP、無線網路、USB 或排針驗證。候選只涵蓋 SD 映像；NOR、NAND 與 eMMC 不得直接沿用本映像的寫入程序。取得實機證據前維持 L2。

## 建置身分

| 項目 | 值 |
| --- | --- |
| 板卡 | `bananapir3` |
| 發行版 | Debian 13 `trixie` |
| 核心目標 | `current` |
| 映像型態 | minimal CLI |
| 映像來源提交 | `e48860750260d31f2cb96964f4523d7b70734971` |
| 驗證器提交 | `a0542dc797901ec744c0cd0283688e6933be1be5` |
| 建置時驗證政策 SHA-256 | `fb305116700da217ab9e58e18bd95dfd728289d093f6bb87eb40b429c9a60f1d` |
| 校準後驗證政策 SHA-256 | `649e2f51651e1b708784c75fadd90e9f909f9118650e98c953aad1740a8336c3` |
| Linux | `6.12.82`，來源提交 `4a4506842b77b597f11e7fc53be1dcdbdc97eea9` |
| U-Boot | `2025.04`，來源提交 `34820924edbc4ec7803eb89d9852f4b870fa760a` |
| ATF | 來源提交 `c34e37802efaea356991a0811c8fc50f8a810f5b` |
| Armbian firmware | 來源提交 `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| `mt76` 韌體 | 來源提交 `c5a3bd91aa735b669618610d5f0ebfa5786845a6` |
| 完整建置時間 | 15 分 42 秒；Docker 執行 949 秒 |
| 輸出目錄 | `output/images/2026.08/bananapi-filogic-mt7986-r3-trixie-current-cli/` |

建置使用 OverlayFS 隔離快取，共用快取只作唯讀下層。U-Boot、ATF、核心、韌體、板級套件與根檔案系統均由固定提交重新產生，不是由舊映像改名或只替換 bootloader。

## 映像與 GPT

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| IMG | 1421869056 | `6677a1df027b727eb1253eaf0a7763f19e652be901e21ab70cd44149b020def1` |
| XZ | 330546436 | `72070e805eac403bd2389469314e43fcc9b253499e359b1dcbe38fcff4f4f6ca` |

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
| `bl2.img` | 寫入映像 | 17408 | 204889 | `16be0b8dbe42d73cecd8a02742b3f7ba16d924638b828255af97a20aa86a1d3d` |
| `u-boot.fip` | 寫入映像 | 6815744 | 511881 | `aa6c3e4b196601cbdb570a74bd3a26132fb476fc2473857caf6fd6e8c9178785` |
| `gpt` | U-Boot 套件保留 | 不直接重複寫入 | 17408 | `beb31c2284ec7b8e910faeea8d323f40532b26010e87d0bae851d823705efa1d` |

守門從 U-Boot 套件取出載荷，逐位元組比對映像內指定偏移。`mt7986a_bpir3_sd_defconfig`、`CONFIG_AUTOBOOT=y`、BootSTD、extlinux、bootflow、MMC 與 EXT4 設定均已驗證。啟動載荷證據清單 SHA-256 為 `31a4f5b963beb8421e2d74776fd2cd1aedd7ed07852319a0d737defc6d97fb9c`。

## 核心、DTB 與韌體

映像內 `mediatek/mt7986a-bananapi-bpi-r3-sd-nor.dtb` SHA-256 為 `c44d3a020aa7de19e821e4f1031493fa678a81d90f9ba654e999bd7e66babebd`。核心與執行期契約確認：

- MT7986 Ethernet、DSA、SFP、PHY、PPE 與 WED 資料路徑設定存在。
- MT7986 WMAC、`mt7915e`、`mt798x_wmac` 與相依 `mt76` 模組存在。
- SATA AHCI、PCIe、NVMe、MMC、MTD、USB xHCI、I2C、SPI、PWM、GPIO 與硬體亂數設定存在。
- `ethtool`、`iperf3`、`nftables`、`tcpdump`、`smartmontools`、`hdparm`、`pciutils`、`nvme-cli`、`usbutils`、`iw`、`rfkill`、`gpiod`、I2C、SPI 與感測工具已安裝。
- 十二個 MT7986 主韌體、EEPROM 與兩組 WED offload 韌體均以固定 `mt76` 提交及精確 SHA-256 驗證。

本段只證明來源、組態、檔案與映像結構一致，不代表無線射頻、交換器、SFP、SATA 或 PCIe 已在實機運作。

## L3 實機門檻

- 以 UART 保存多次冷啟動、重新啟動與斷電重啟記錄，確認 BL2、ATF、U-Boot、extlinux 與 Linux 啟動鏈穩定。
- 驗證 SD 開機、GPT 保留區及 `factory` 分割區未被破壞；NOR、NAND 與 eMMC 另走各自受控安裝程序。
- 驗證 DSA 各網路埠、SFP、VLAN、橋接、NAT、PPE／WED 與長時間 `iperf3` 雙向流量。
- 驗證 2.4 GHz／5 GHz 無線網路、射頻校準、PCIe／NVMe、SATA、USB host 與儲存壓力。
- 驗證 I2C、SPI、UART、PWM、GPIO 與排針電氣對照；不得只以裝置節點存在判定通過。
- 執行 CPU、記憶體、網路與儲存混合壓力，保存溫度、節流、錯誤計數與測試時間。
