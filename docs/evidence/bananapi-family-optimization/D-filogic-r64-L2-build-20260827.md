# Banana Pi BPI-R64 current 候選映像 L2 建置證據

更新日期：2026-08-27

## 結論

`bananapir64` 已使用隔離快取，由固定來源完整建置 Debian Trixie current minimal CLI，並通過 Filogic MT7622 專用 L2 唯讀守門。候選映像的來源、IMG／XZ 同一性、五分割區 GPT、BL2／FIP 寫入內容、U-Boot 標準自動開機、板級 DTB、核心設定、MT7622 網路與藍牙韌體、共享 Filogic 韌體及標準網路與 I/O 工具均符合本次受控政策。

此結果不代表實體板已完成 UART、冷啟動、SD、eMMC、NOR、NAND、網路、SATA、PCIe、USB、無線網路、藍牙或排針驗證。候選只涵蓋 SD 整碟映像；其他啟動媒體不得直接沿用本映像的寫入程序。取得實機證據前維持 L2。

## 建置身分

| 項目 | 值 |
| --- | --- |
| 板卡 | `bananapir64` |
| 發行版 | Debian 13 `trixie` |
| 核心目標 | `current` |
| 映像型態 | minimal CLI |
| 映像來源提交 | `1943ab2182ca969c1122f2d9f27db99d6c983dfc` |
| 驗證器提交 | `c2c2e3187806b02d7d264aa4a0da70a64f0b2752` |
| 建置時驗證政策 SHA-256 | `f3ae037a4a5365e03f8114c28a20f5aa43d1d1ec13dbdabff442325074f1d271` |
| 最終驗證政策 SHA-256 | `4b87ddf930d2aebfd82117c2495cbe438085dde8d8433cb3b56c27d28f4d4166` |
| Linux | `6.12.82`，來源提交 `4a4506842b77b597f11e7fc53be1dcdbdc97eea9` |
| U-Boot | `2025.04`，來源提交 `34820924edbc4ec7803eb89d9852f4b870fa760a` |
| ATF | 來源提交 `c34e37802efaea356991a0811c8fc50f8a810f5b` |
| Armbian firmware | 來源提交 `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| Linux firmware | 來源提交 `01205307636157a12c29e6a774bf83b218732050` |
| 完整建置時間 | 13 分 22 秒；Docker 執行 808 秒 |
| 輸出目錄 | `output/images/2026.08/bananapi-filogic-mt7622-r64-trixie-current-cli/` |

建置使用 R64 專用 OverlayFS 隔離快取，共用快取只作唯讀下層。U-Boot、ATF、核心、韌體、板級套件與根檔案系統均由固定提交重新產生，不是由舊映像改名或只替換 bootloader。

映像來源提交產生的 MT7622 `bl2.img` 為 69,718 bytes。初版驗證政策誤沿用 MT7986／MT7988 的 180,000-byte 下限；驗證器提交把 R64 的健全性下限校準為 65,000 bytes，並以回歸測試防止再次混用。套件載荷與映像指定偏移仍由驗證器逐位元比對，因此這項修正沒有降低內容同一性要求，也沒有事後修改映像。

## 映像與 GPT

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| IMG | 1421869056 | `11c7246fbdb8b3bf768e95f5dda3496bec05effa1427e98c31bfc0c58ff3347a` |
| XZ | 331165120 | `d274015c9b6c60330fc3371277ba7f41da51ac26ce334b5d8bd84cba679805cf` |

XZ 通過 `xz -t` 與串流解壓檢查，解壓後 SHA-256 與 IMG 完全相同。GPT 結構如下：

| 分割區 | 名稱 | 起始 sector | sector 數 |
| ---: | --- | ---: | ---: |
| 1 | `bl2` | 34 | 8158 |
| 2 | `ubootenv` | 8192 | 1024 |
| 3 | `factory` | 9216 | 4096 |
| 4 | `fip` | 13312 | 8192 |
| 5 | 根檔案系統 | 32768 | 延伸至映像尾端 |

`sgdisk -v`、分割區名稱、起點與大小均通過守門。`factory` 是校準資料保留區，建置、安裝及測試流程不得任意覆寫。

## 啟動鏈證據

| 載荷 | 放置方式 | 位元組偏移 | 大小 | SHA-256 |
| --- | --- | ---: | ---: | --- |
| `bl2.img` | 寫入映像 | 17408 | 69718 | `f06bd41b08f0ea422e3984ac27badbd40d2e3be09d7cee027099ee43fa896ea0` |
| `u-boot.fip` | 寫入映像 | 6815744 | 594468 | `c680f9217148d3661367dcf51147f03e15fd672ce60955c73d74668497a12b1b` |
| `gpt` | U-Boot 套件保留 | 不直接重複寫入 | 17408 | `beb31c2284ec7b8e910faeea8d323f40532b26010e87d0bae851d823705efa1d` |

守門從 U-Boot 套件取出載荷，逐位元比對映像內指定偏移。`mt7622_bananapi_bpi-r64-sdmmc_defconfig`、BootSTD、extlinux、bootflow、MMC、EXT4、預設 DTB 與自動開機設定均已驗證。U-Boot 以 `ubootenv` 分割區名稱存放兩份冗餘環境；每份大小 `0x40000`，固定回退偏移為 `0x400000` 與 `0x440000`，不跨入 `factory`。啟動載荷證據清單 SHA-256 為 `59a34bdeb9539652e0ee65237ce7de3a63c6ea5e9ff279f3331920540399e8f1`。

## 核心、DTB 與韌體

映像內 `mediatek/mt7622-bananapi-bpi-r64.dtb` SHA-256 為 `4ec43868bd7ff3965b60410631ba6b8fd8c87a23647110fd7b647201953353ab`。核心與執行期契約確認：

- MT7622 Ethernet、MT7530 DSA、內建無線網路、MediaTek 藍牙 UART、SATA、PCIe、MMC、SPI NAND、USB、I2C、SPI、PWM、GPIO 與 RTC 設定存在。
- `ethtool`、`iperf3`、`nftables`、`tcpdump`、`smartmontools`、`pciutils`、`nvme-cli`、`usbutils`、`iw`、`rfkill`、`gpiod`、I2C、SPI 與感測工具已安裝。
- 三個 MT7622 韌體及六個共享 Filogic 韌體均以固定 Linux firmware 提交與精確 SHA-256 驗證。
- MediaTek 授權原文與繁體中文來源追溯文件已安裝於板級套件文件目錄。
- 最終建置記錄沒有 `Possible missing firmware` 或 `missing firmware` 警告。

共享的 MT7981、MT7986 與 MT7988 韌體是為了滿足同一 Filogic 核心中內建驅動的 initramfs 契約，不表示 R64 具有這些 SoC 的硬體。建置記錄另有宿主 LoongArch binfmt 啟用失敗及補丁摘要解析器無法解析 R4 Pro 補丁的非致命訊息；實際 R64 patch、ATF、U-Boot、核心、套件與映像均完整成功，不得把這兩項非目標訊息解讀為硬體通過或失敗。

## 被拒絕的初版候選

來源提交 `8ac78258b0fbf80339299bbe0c77fa86851d88ef` 的第一次完整建置曾在 initramfs 回報缺少六個共享 Filogic 韌體。該產物只作預驗證並已刪除，不是發布候選，也不得用來宣稱 R64 達到 L2。

本次接受的映像只來自修正版來源提交 `1943ab218`，建置記錄已確認六項警告全部消失，再由驗證器提交 `c2c2e3187` 通過 L2 守門。

## 板級限制與 L3 門檻

- GPIO90 在第二組 PCIe 與 SATA 間切換；兩者不得宣稱可同時使用，實機必須分別選擇模式並保存結果。
- 以 UART 保存多次冷啟動、重新啟動與斷電重啟記錄，確認 BL2、ATF、U-Boot、extlinux 與 Linux 啟動鏈穩定。
- 驗證 SD 開機、GPT 保留區及 `factory` 未被破壞；eMMC、NOR 與 NAND 必須另走各自受控安裝程序。
- 驗證 DSA 各網路埠、VLAN、橋接、NAT、內建無線網路、藍牙與長時間 `iperf3` 雙向流量。
- 分別驗證 SATA、兩組 PCIe、NVMe、USB host、MMC 與儲存壓力，並保存 GPIO90 模式。
- 驗證 I2C、SPI、UART、PWM、GPIO 與排針電氣對照；不得只以裝置節點存在判定通過。
- 執行 CPU、記憶體、網路與儲存混合壓力，保存溫度、節流、錯誤計數與測試時間。
