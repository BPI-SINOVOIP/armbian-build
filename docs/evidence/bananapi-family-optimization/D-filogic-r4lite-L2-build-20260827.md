# Banana Pi BPI-R4 Lite 實驗性 current 候選映像 L2 建置證據

更新日期：2026-08-27

## 結論

`bananapir4lite` 已使用隔離快取，由固定來源完整建置 Debian Trixie current minimal CLI，並通過 MT7987 專用 L2 唯讀守門。候選映像的來源、IMG／XZ 同一性、五分割區 GPT、BL2／FIP 寫入內容、U-Boot 標準自動開機、板級 DTB、2.5GbE PHY 驅動與韌體、核心功能及標準網路與 I/O 工具均符合本次受控政策。

此候選仍保留實驗性與 `.wip` 狀態。固定 Linux 提交是 `6.17.0-rc1`，不是穩定版或長期支援核心；本次亦沒有實體板 UART、冷啟動或周邊測試。候選只涵蓋 SD 整碟映像，不得據此宣稱 eMMC、SPI NOR、SPI NAND、recovery 或安裝流程可用。取得實機證據前維持 L2。

## 建置身分

| 項目 | 值 |
| --- | --- |
| 板卡 | `bananapir4lite` |
| 發行版 | Debian 13 `trixie` |
| 核心目標 | `current` |
| 映像型態 | minimal CLI |
| 映像來源提交 | `32565f1d270c4493c6953c7741497becdcb9cb44` |
| 驗證器提交 | `9a9dc9eda5cff8e642c3eb326566713679c13db2` |
| 建置時驗證政策 SHA-256 | `768f7b68475c3135a1365f6f109d0210471c2ad01a035cc6c9312b7c9b960506` |
| 最終驗證政策 SHA-256 | `d1d7da1126efd6ccc1f8042c6668d9410414d9d888393295e0b9e70251f812f1` |
| Linux | `6.17.0-rc1`，來源提交 `0529574fee9fcaa75159f9edcedf35e8bc57400d` |
| U-Boot | `2025.04`，來源提交 `34820924edbc4ec7803eb89d9852f4b870fa760a` |
| ATF | 來源提交 `c34e37802efaea356991a0811c8fc50f8a810f5b` |
| Armbian firmware | 來源提交 `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| Linux firmware | 來源提交 `01205307636157a12c29e6a774bf83b218732050` |
| 完整建置時間 | 14 分 14 秒；Docker 執行 860 秒 |
| 輸出目錄 | `output/images/2026.08/bananapi-filogic-mt7987-r4lite-trixie-current-cli/` |

建置使用 R4 Lite 專用 OverlayFS 隔離快取，共用快取只作唯讀下層。U-Boot、ATF、核心、韌體、板級套件與根檔案系統均由固定提交重新產生，不是由舊映像改名或只替換 bootloader。

## 映像與 GPT

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| IMG | 1413480448 | `265821e47cf3ffd2115329d0aad8bf3d4c1fb33ff38583959e2a6f726c069ea5` |
| XZ | 330288968 | `cd95a87930ab7bfdda6d906e275caf578c6ecf421d797062ffaad304e6d8a63c` |

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
| `bl2.img` | 寫入映像 | 17408 | 250288 | `d1f18a1c97d38d59f4911de385f9e71715591ec0086c30c69411417cac037cca` |
| `u-boot.fip` | 寫入映像 | 6815744 | 1214264 | `89d71f5107017ba9bee4c45c8ef7af96429bfd2134bada3815512610dac5de9b` |
| `gpt` | U-Boot 套件保留 | 不直接重複寫入 | 17408 | `beb31c2284ec7b8e910faeea8d323f40532b26010e87d0bae851d823705efa1d` |

守門從 U-Boot 套件取出載荷，逐位元比對映像內指定偏移。`mt7987a_bananapi_bpi-r4-lite-sdmmc_defconfig`、BootSTD、extlinux、bootflow、MMC、EXT4、預設 DTB 與自動開機設定均已驗證。U-Boot 以 `ubootenv` 分割區名稱存放兩份冗餘環境；每份大小 `0x40000`，固定回退偏移為 `0x400000` 與 `0x440000`，不跨入 `factory`。啟動載荷證據清單 SHA-256 為 `a935cf9f20c9815f642f8086e243221dc7b19838a3d0b2a37f079e93f4c5df0f`。

## 核心、DTB 與韌體

映像內 `mediatek/mt7987a-bananapi-bpi-r4-lite-sd.dtb` SHA-256 為 `d01ef737139d97393b210c1bcd9cf3823dfe4e59e83a9aee9e25fdc2660a4ad9`。DTB 內已移除供應商 `/dev/fit0` 與 `ubi.block=0,firmware` 啟動參數；根檔案系統由 extlinux 的 PARTUUID 決定。核心與執行期契約確認：

- MediaTek Ethernet／WED、MT7530 DSA、2.5GbE PHY、SFP、PCIe、NVMe、MMC、USB、PWM fan、RTC、I2C mux、PCA9555、EEPROM、按鍵與 LED 設定存在。
- `ethtool`、`iperf3`、`nftables`、`tcpdump`、`smartmontools`、`pciutils`、`nvme-cli`、`usbutils`、`iw`、`rfkill`、`gpiod`、I2C、SPI 與感測工具已安裝。
- 固定 6.17 核心的正確符號是 `CONFIG_MEDIATEK_2P5GE_PHY=y`，映像核心含 `mtk-2p5ge.o`、`MediaTek MT7988 2.5GbE PHY` 與驅動宣告的 `mediatek/mt7988/i2p5ge-phy-pmb.bin`。
- 兩個 MT7987 參考韌體、上述必要 MT7988 PHY 韌體及五個共享 Filogic 韌體均以固定 Linux firmware 提交與精確 SHA-256 驗證。
- MediaTek 授權原文與繁體中文來源追溯文件已安裝於板級套件文件目錄。
- 最終建置記錄沒有 `Possible missing firmware` 或 `missing firmware` 警告。

MT7987 參考檔案與共享 Filogic 韌體不表示 R4 Lite 具有其他 SoC 的硬體。ATF 的 MT7987 流程仍會連結供應商提供的預編譯 DRAM／eFuse 物件；在逐檔來源與授權旁證補齊前，這是再散布合規風險，本地 L2 不構成對外授權核准。

## 被拒絕的初版候選

來源提交 `eee6582c3` 的第一次完整建置雖然成功，但共用核心設定使用不存在於固定 6.17 核心的舊符號 `CONFIG_MEDIATEK_2P5G_PHY`，實際映像未包含 2.5GbE PHY 驅動，因此被明確拒絕。原始產物保留於：

`output/images/2026.08/bananapi-filogic-mt7987-r4lite-trixie-current-cli-rejected-missing-2p5ge-20260827/`

修正版同時保留 6.12 BPI 分支採用的舊符號，並新增 6.17 所需的 `CONFIG_MEDIATEK_2P5GE_PHY=y`；未被目標核心定義的符號由 Kconfig 忽略。最終接受映像只來自來源提交 `32565f1d2`。

## 建置記錄限制

建置記錄包含宿主 LoongArch binfmt 啟用失敗、補丁摘要解析器無法解析 R4 Pro 補丁、U-Boot `foresee.c` 未使用變數、Linux fortify 自我測試警告，以及 GPT 重寫後 loop 分割區重讀忙碌訊息。這些訊息均未阻止 R4 Lite 的 U-Boot、ATF、核心、套件、映像、XZ 與唯讀驗證完成；也不得把非致命建置訊息解讀為硬體通過或失敗。

## L3 門檻

- 以 UART 保存多次冷啟動、重新啟動與斷電重啟記錄，確認 BL2、ATF、U-Boot、extlinux 與 Linux 啟動鏈穩定。
- 驗證 SD 開機、GPT 保留區及 `factory` 未被破壞；其他啟動媒體必須另走各自受控流程。
- 驗證四個 LAN、2.5GbE PHY、SFP、VLAN、橋接、NAT 與長時間 `iperf3` 雙向流量。
- 驗證 PCIe、NVMe、USB host、MMC 與儲存壓力，保存錯誤計數與測試時間。
- 驗證 I2C mux、RTC、EEPROM、PCA9555、按鍵、LED、PWM fan、GPIO 與排針電氣對照。
- 執行 CPU、記憶體、網路與儲存混合壓力，保存溫度、節流、重置與錯誤證據。
