# Banana Pi 跨平台來源候選與唯讀守門

日期：2026-09-17。限定現有 45 板；本階段不操作硬體、電源、UART、網路設備或巨型映像。

## 來源與資格

[平台 JSON](../config/bpi-lab/platforms.json)綁定[registry](evidence/bpi-multiboard-lab-20260917/board-registry.json)的 SHA-256，另記錄 98 份來源：76 份原始 board／family／include、3 份架構預設、10 份 bootscript、2 份 extlinux 建置邏輯、2 份 K1 修補、2 份 K3 文字設定、3 個共用／階段工具。registry 不計入 98 份。

每板保留來源路徑、SoC、候選入口、檔案格式、早期引導組件、SPI／eMMC 宣告與軟體缺口；技術片段綁定來源行號。共用工具以 `line: null` 的穩定片段錨點核對，但同樣必須先通過整檔 SHA。

這只證明目前列出的本機來源一致，不證明既有 444 套映像使用相同來源、內容格式正確或建置完成，也不宣稱完整外部來源／修補／產物閉包。所有板的 `hardware_qualified`、`execution_ready`、`backend_complete` 均為 `false`；安全啟動狀態保持未知。

## 全部 45 板

命令與格式僅為來源候選，核心、initrd、DTB 仍須核對原配組件；共 14 群組、17 種入口資料。

| 群組 | 板數 | 板型 | 候選入口與差異 |
| --- | ---: | --- | --- |
| Allwinner32 | 12 | `bpi-6204`、`bpi-m1`、`bpi-m1p`、`bpi-m2`、`bpi-m2b`、`bpi-m2m`、`bpi-m2p`、`bpi-m2u`、`bpi-m2z`、`bpi-m3`、`bpi-p2z`、`bpi-pro` | `boot-sunxi.cmd`：`bootz`／`zImage`；舊式 FEX 分支另核定 |
| Allwinner64／A64 | 1 | `bpi-m64` | `boot-sun50i-next.cmd`：`booti`／`Image` |
| Allwinner H618 | 3 | `bpi-m4b`、`bpi-m4z`、`bpi-m4z-emac` | 使用 `boot-sun50i-next.cmd`，不是 `boot-sun50iw9.cmd`；不得共享 DDR 或救援資格 |
| Amlogic | 4 | `bpi-cm4io`、`bpi-m2pro`、`bpi-m2s`、`bpi-m5` | `boot-meson64.cmd` 的 `booti`／`Image` 分支；原廠 FIP 另核定 |
| Rockchip32 | 1 | `bpi-forge1` | 板型 hook 指定 `boot-rk3506-forge1.cmd`：`bootz`／`zImage` |
| Rockchip64 | 8 | `bpi-aim7`、`bpi-cm5pro`、`bpi-m1super`、`bpi-m5pro`、`bpi-m7`、`bpi-p2pro`、`bpi-r2pro`、`bpi-w3` | 按 SoC 分別引用腳本；R2 Pro 啟用 extlinux、命令待核定，其餘所列腳本為 `booti`／`Image` |
| MediaTek32 | 1 | `bpi-r2` | `boot-mt7623.cmd`：`bootz`／`zImage`；前置載入器與 eMMC boot0 另核定 |
| MediaTek64 | 6 | `bpi-r3`、`bpi-r3mini`、`bpi-r4`、`bpi-r4lite`、`bpi-r4pro`、`bpi-r64` | 板型啟用 `bootflow`／extlinux；架構預設 `Image`／`uInitrd`；BL2／FIP 另核定 |
| SpacemiT K1 | 2 | `bpi-cm6`、`bpi-f3` | 各自修補提供 `sysboot`／extlinux；FSBL、OpenSBI 與 U-Boot 配套 |
| SpacemiT K3 | 1 | `bpi-sm10` | 設定啟用 `booti`，環境列出 `Image`／`initramfs-generic.img`；完整原廠入口待核定 |
| Sunplus | 2 | `bpi-f2p`、`bpi-f2s` | family 產生 `uEnv.txt`：`bootm`／`uImage`；xboot／`ISPBOOOT.BIN` 另核定 |
| Renesas | 1 | `bpi-ai2n` | `boot-renesas-rzv2n-bpi.cmd`：`booti`／`Image`；BL2 參數區塊與 FIP 另核定 |
| Realtek | 2 | `bpi-m4`、`bpi-w2` | 原廠 BSP 把核心改名為 `uImage`，不足以證明格式或命令，兩者保持未知 |
| Synaptics | 1 | `bpi-m6` | `boot-vs680.cmd`：`booti`／`Image`；OEM／TZK 安全鏈另核定 |

ARM32 16 板、ARM64 26 板、RISC-V 3 板。六片 MediaTek64 與 R2 Pro 的 extlinux 宣告會令一般 bootscript 被忽略；因此不偽造本機不存在的 `boot-filogic.cmd` 摘要，也不誤用 R2 Pro 的家族腳本。

F2P 明示 SD-only、eMMC xboot 為空，F2S 才指定專用 eMMC xboot；R3 Mini 明示 eMMC 候選，R4 Pro 8X 本次限 SD。SPI 介面不等於 SPI 引導：M7 繼承設定與 W3 明示引導支援，其餘依來源標示家族條件路徑或未宣告。`not_declared` 不等同硬體不存在，`candidate_excluded` 不等同沒有該媒體。

K3 的 `CONFIG_BOOTCOMMAND` 為空；FIT、FIP、SBI、TZK 等名稱不代表共用工具支援容器或安全鏈。F2P／F2S 共 20 套核心版本 `0` 問題，以及 CM6／F3／SM10 各兩項映像缺項均保留。

## 工具實作與板級缺口

`shared_tools` 明列工具已實作、來源已檢視；板級 `uboot_tool`／`linux_tool=integration_pending` 另行保留，兩者不混用。

| 工具 | 已實作 | 限制與未完成項目 |
| --- | --- | --- |
| `bpi_lab_uboot.py` | 配置驗證、離線展開與一次性引導；ARM32 `zImage`／`bootz`，ARM64／RISC-V `Image`／`booti`，三架構未壓縮 legacy `uImage`／`bootm` | 需原配組件、RAM／保留區及已配對 U-Boot；非任意 FIT、原廠容器或 extlinux 執行器，不含完整部署與救援 |
| `bpi_lab_linux.py` | 跨架構唯讀收集／核對，根掛載追到直接 MMC／SD、CID 與控制器 | overlay、NFS、Btrfs、LVM、dm-crypt、NVMe／USB 根媒體阻擋；不是完整短測或壓力測試 |
| `bpi_lab_h618.py` | 僅 `bpi-m4zero-0845` 的 EMAC 映像格式接入 `preflight`／`deploy`／`smoke` | `boot`／`recovery`／`resume` 阻擋，`whole_adapter_ready=false`；映像名稱不證明 EMAC 擴充硬體通過，0845 授權不可借用於其他板 |

仍缺各板配置與原配組件驗證、平台部署／救援契約、階段接線及離線回歸，再加實體配對與授權。板級 `deployment`／`recovery`／`end_to_end=not_implemented` 不抹除 0845 的局部接線，也不能概括為「只待板子」。

## 使用、驗證與交接

```bash
PY=output/evidence/bpi-sram-supervisor/model-venv/bin/python
$PY -B tools/bpi_lab_platforms.py validate
$PY -B tools/bpi_lab_platforms.py list --group rockchip64 --json
$PY -B tests/test_bpi_lab_platforms.py PlatformTests -v
$PY -B -m unittest discover -s tests -p test_bpi_lab_platforms.py -v
```

`validate`／`list` 均先核對全部來源，即使篩選一板亦然；成功回傳 `0`，拒絕回傳 `2` 及中文 JSON。拒絕漏板、誤配、SHA 漂移、引用不符、資格升格、越界路徑、符號連結、非一般檔案與超限內容；讀取前後及結束時再核對檔案身分。來源只讀文字，不 `source`、不執行 shell，也不匯入三個階段工具；不是原子快照或簽章信任系統。

資料由 registry 固定對應及人工來源核對建立，再以結構化 JSON 序列化；沒有盲目重建 pins 的生成命令。最終主代理只可對已複審、語意仍相符的三個工具執行 `sha256sum tools/bpi_lab_uboot.py tools/bpi_lab_linux.py tools/bpi_lab_h618.py`，限定更新 `sources` 對應 SHA；片段錨點不受行號位移影響。registry 與其餘 95 份建置來源摘要不可盲刷，工具語意改變則需重新審核。

最終整合已核對三個工具的修改範圍並限定更新其摘要；39 項隔離測試與第 40 項 `LiveSourceTests` 全部通過，正式 `validate --json` 及 Rockchip 篩選 `list` 亦通過。臨時工具片段只測資料／拒絕邏輯，不代替正式來源核對；未修改 registry 或其餘建置來源摘要。本輪沒有硬體結果。
