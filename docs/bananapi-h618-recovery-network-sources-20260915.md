# H618 識別與救援架構來源查核

日期：2026-09-15。搭配[整合計劃](bananapi-h618-recovery-network-plan-20260915.md)使用。

## 原廠共用 BSP 與分板方式

| 來源 | 固定版本 | 查核結論 |
| --- | --- | --- |
| [BPI-M4B-bsp](https://github.com/BPI-SINOVOIP/BPI-M4B-bsp/tree/2009cae8022a407fbcae0c0d170b7b0f1bd9214b) | `2009cae8022a407fbcae0c0d170b7b0f1bd9214b` | 同倉包含 Zero／Berry，但 `configure`、建置命令、DTB 及封裝按板型分開 |
| [pi-u-boot](https://github.com/BPI-SINOVOIP/pi-u-boot/tree/533a8487f14e4d20a37b94b5b52ee8180738d7a5) | `533a8487f14e4d20a37b94b5b52ee8180738d7a5` | 通用 LRADC 讀值可轉成環境變數；未找到已啟用的 BPI 自動選板對照 |
| [BPI-H618-Android12](https://github.com/BPI-SINOVOIP/BPI-H618-Android12/tree/316cd80ca43fa17b0385eacd7f6f3652bbd66b2a) | `316cd80ca43fa17b0385eacd7f6f3652bbd66b2a` | 共用來源，按產品選擇配置；不能因共用 `sun50iw9-common` 就宣稱自動識別 |
| [pi-linux](https://github.com/BPI-SINOVOIP/pi-linux/tree/164a39245ef8b5cd4b16fd58f59b852d92afc963) | `164a39245ef8b5cd4b16fd58f59b852d92afc963` | 兩板 DTS 的 LRADC 鍵盤節點停用；所查 `sunxi-sysinfo` 是晶片資訊，不是 PCB 分壓選板表 |

### Linux BSP

- `sunxi-pack/sun50iw9/{bpi-m4zero,bpi-m4berry}/linux/boot.cmd` 直接設定各自 `board`。
- 兩板 `sys_config.fex` 的 `[dram_select_para]` 為 `select_mode=0`。這表示該配置未啟用 GPIO／GPADC 多組參數選擇，**不表示關閉 DRAM 容量偵測**。
- `.gitmodules` 指向 Dangku 的 `sun50iw9-v2018.05`，釘選 `4bf1aa789b94f65da995674763d3bf6598e0d36d`；本次公開存取該來源未成功。補查的 `pi-u-boot` 提交不是該子模組提交，不能冒稱完全等同。

### 通用 LRADC 實作

在所查 `pi-u-boot` 中：

- `board/sunxi/Kconfig` 的 `SUNXI_LRADC_VOL` 預設停用且依賴 `SUNXI_LRADC`，兩板 defconfig 未開啟。
- `board/sunxi/board_common.c` 的讀取位於 `board_late_init()`，不是 DDR 初始化前的 SRAM 流程。
- `board/sunxi/sunxi_lradc_vol.c` 使用 `/soc/keyboard` 的範圍設定，產生 `lradc_vol` 環境變數；不是 Zero／Berry 的實測硬體識別表。

因此可參考其暫存器流程，不能直接把晚期初始化搬進 SPL，亦不能把 GPADC 參數選擇等同原理圖的 LRADC HW_ID。

### Android

查核以原廠固定提交為準，不使用本機疊加實驗修正後的 `HEAD` 代表原廠。

- `longan/brandy/brandy-2.0/u-boot-2018/configs/sun50iw9p1_defconfig` 使用共用 DT 與 `CONFIG_OF_BOARD=y`，未啟用 `SUNXI_LRADC_VOL`。
- `longan/device/config/chips/h618/configs/{m4zero,m4berry}/` 各有 `sys_config.fex` 及板級 DTS；兩者 `select_mode=0`。
- 通用 LRADC 程式仍在晚期板級初始化且受設定控制，未發現啟用中的三板識別／選擇實作。

本查核不涵蓋閉源 boot0 內部、未公開廠內版本，也未逐一核實所有 Android 核心檔案。部分遠端遞迴樹回應有截斷，不能用來證明全倉完全不存在其他識別機制。

## 原理圖與尚未確認的電氣條件

| 圖檔 | 頁面與線索 | 待釐清 |
| --- | --- | --- |
| `BPI_M4-ZERO_V20_SCH_Release_20240821.pdf` | 第 1 頁，HW_ID／LRADC，R14 標 10 kΩ、R15 標 1.5 kΩ | 同頁表格卻出現 10 kΩ／10 kΩ，不得直接採用其中一組推導門檻 |
| `BPI_M4B_V10_SCH-20240605_Release.pdf` | 第 1 頁，HW_ID／LRADC，R14 標 82 kΩ、R15 標 16 kΩ | 須確認目前實物 PCB 版本與實裝 BOM |

本機參考庫位於 `bpi/doc/banana-pi-doc-benchmark-20260621/downloads/` 下的 `m4zero-dxf-schematic-p220-20260622/` 與 `m4berry-dxf-schematic-p221-20260622/`。查核包含圖像目視，並非只依賴文字擷取。

[公開 Berry V00 圖](https://linux-sunxi.org/images/1/12/BPI_M4B_V00_SCH-20231103_Release.pdf)也有 HW_ID 線索，但不能拿 V00 取代 V10 或目前實物證明。電阻值不直接等於可用 ADC 判別範圍；尚缺供電容差、量測及跨批次證據。

## 本倉配置與實機歷史

- `config/boards/bananapim4berry.conf` 使用 U-Boot `v2025.04`；`0008-u-boot-configs-Add-sun50i-h618-bananapi-m4berry-defconfig.patch` 開啟 `CONFIG_SUN8I_EMAC`、`CONFIG_PHY_REALTEK`。這是建置設定證據，不是實板 PXE 結果。
- Zero／Zero EMAC 目前配置使用 U-Boot `v2026.01`；原 SRAM 原型另固定 `v2024.04`。整合前須明確管理三者來源與 ABI，不以目錄名稱推定相同版本。
- [Berry 2026-08-25 實機報告](bananapi-m4berry-public-image-hardware-validation-20260825.md)記錄單片 2 GiB、SD 啟動、eMMC 唯讀、Linux 有線網路可用；不能代替目前在線狀態，也不能證明 SRAM／PXE 或多片 DDR 長測。
- 歷史候選端點只留本機證據，不在公開計劃放入實際 IP、帳號、序號或憑據。首次連線仍須檢查 SSH 主機金鑰與軟體宣告板型；實體身分另列未驗證。

## 上游設計依據

- [U-Boot 多 DTB 控制](https://docs.u-boot.org/en/latest/develop/devicetree/control.html)：可依板級程式選取 DTB；不是 H618 現成 HW_ID 實作。
- [U-Boot PXE](https://docs.u-boot.org/en/latest/usage/pxe.html)：在 U-Boot 及 RAM 中處理網路配置；指定標籤不存在可能退回預設，實驗工具須避免誤測另一版本。
- [Linux NFS 根檔案系統](https://docs.kernel.org/admin-guide/nfs/nfsroot.html)：核心、早期網路及根檔案系統條件需配套。
- [U-Boot MMC 命令](https://docs.u-boot.org/en/latest/usage/cmd/mmc.html)：區分媒體、分割區與可能持久的硬體設定；讀取能力不代表可以安全寫入。
- [H616 SD 開機位置修正討論](https://lists.u-boot-project.org/pipermail/u-boot/2024-May/553283.html)：H616 的替代位置為 256 KiB，布局設計不能照泛 sunxi 的 128 KiB 規則套用。
- [FIT 驗證設計](https://docs.u-boot.org/en/latest/usage/fit/verified-boot.html)：後續用於受信任產物驗證；本階段沒有完成簽章部署或硬體安全開機。

線上文件可能更新；實作時還須對所固定版本的來源碼及編譯配置確認。來源查核與規劃推論均不能取代實板驗收。
