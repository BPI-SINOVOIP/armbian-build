# C3 Rockchip 原配組件

API：`tools.bpi_lab_rockchip.prepare(read_file, *, board='bpi-m5pro', kernel_release, output)`，
以及 `validate_template(manifest, *, template, artifact_root)`。
介面、來源責任及範本欄位見 [共用契約](bananapi-lab-extlinux-20260917.md)。

| 板型 | 已實作原入口 |
| --- | --- |
| `bpi-forge1` | RK3506 專用 `bootz` 腳本，根目錄 DTB、`ttyFIQ0` 與固定 ramdisk 位址 |
| `bpi-p2pro` | `boot-rockchip64.cmd`，只有帶前綴的核心 overlay 路徑 |
| `bpi-r2pro` | 板型明示的 extlinux，不套用家族一般腳本 |
| `bpi-m1super` | RK35xx 腳本，`ttyS2` 與無前綴 overlay 後備路徑 |
| `bpi-m5pro`, `bpi-cm5pro` | RK3576 腳本，`ttyS0` 與無前綴 overlay 後備路徑 |
| `bpi-m7`, `bpi-aim7`, `bpi-w3` | 依實際受控腳本區分共同 `current`／`edge` 與 RK35xx `vendor`／`legacy` |

逐板 DTB 身分由各自 Rockchip 驗證設定取得，並核對平台矩陣列出的 boards／families 摘要。
不借用 Allwinner overlay、rootfs、console 或板級政策。
腳本須符合已審閱的固定摘要，`boot.scr` 須有正確 CRC 並等同 `boot.cmd`。

保留 `earlycon`、console 選擇、bootlogo、cgroup、extraargs、PARTUUID 與 `kaslrseed` 等語意。
M7 真實擷取的 `6.18.49-current-rockchip64` 使用 `boot-rockchip64.cmd`，不能只按矩陣候選選 RK35xx。
`extraargs=cma=256M` 原樣保留；有效 DTB 的保留節點也必須進入範本。
這是原入口契約核對，不聲稱已推算或核准 CMA／RAM 佈局。

核心 overlay 先於使用者 overlay；RK35xx／RK3576 才有無前綴後備路徑。
未知 `param_*`、任意 fixup、額外引導入口及修改過的腳本均明確阻擋。
DDR、BL31、SPL、FIT、MaskROM、SPI／eMMC 入口及板修訂資格不會由 Linux 組件通過而取得。

測試：`tests/test_bpi_lab_rockchip.py`，含九板準備及原入口範本、console 差異、M7 分支、overlay 搜尋次序與拒絕案例。

2026-09-17 真 M7 [第二次準備](../output/evidence/bpi-multiboard-integrate-20260917-m7-002/preparation.json) 已為
`prepared`、`blockers=[]`、`root_uuid_verified=true`，完整版本為 `6.18.49-current-rockchip64`，
原入口為 `rockchip64` 的 `boot.scr`，不是早期失敗擷取的延續判定。
目前 [原入口執行器](bananapi-lab-original-entry-20260917.md) 已接入共用後端；
此抽樣仍未操作硬體，不能代替板級核定。跨家族抽樣總表見 [組件介面文件](bananapi-lab-extlinux-20260917.md#真來源抽樣)。

## Forge1 重播

2026-09-18 使用 `allboard-bpi-forge1-003` 的既有成功擷取，SHA-256
`cd73438a9d26b0a066cd9b0af3b208281a3b6502778f81fa2661207522107c46`，
未重解壓、未修改原檔。完整版本 `6.1.115-vendor-rockchip` 的
[重播準備](../output/evidence/bpi-c3-forge1-lz4-replay-20260918-001/preparation.json) 已為
`prepared`、`blockers=[]`、`root_uuid_verified=true`、`source_reread=false`。
新 extraction SHA-256 為 `78d7d88884c6e8ece9b43cbf57b070a3edfd6b9f8dd8d66362cf732457f41d73`。

核心是 LZ4 zImage，展開 11931032 位元組；原配 config 與內嵌 config 摘要一致。
`user_debug=31` 由核心在原腳本命令列後附加，不是覆寫 root UUID。
沒有 overlay；仍須獨立完成 ARM32 執行器、救援 U-Boot、RAM 與前置安全鏈資格，
此結果不是實板引導或後端完整通過。
