# 原映像組件準備與證據重播

## 使用範圍

`tools/bpi_lab_prepare.py` 串接完整映像核對、唯讀檔案擷取及家族組件準備。目前接入 Allwinner ARM32／A64 的 13 個板別候選，以及 Amlogic CM4IO／M2 Pro／M2S／M5。這不是 17 板已可實測的宣告：特殊格式、舊式 FEX、未支援參數／fixup 仍會阻擋，部署及救援後端也未全部完成。

映像容器目前只支援 MBR 單一 Linux 主分割的 ext 檔案系統。輸出必須是全新目錄；工具不掛載、不改原映像、不連 UART、不操作電源或媒體。`prepared` 只代表本次離線組件核對通過，`hardware_validated`、`whole_backend_ready` 及 `boot_executed` 均為 `false`。

## 首次擷取

主機需要 Linux、Python 3.10 以上、`e2fsprogs` 及 `device-tree-compiler`；使用固定的 `/usr/sbin/debugfs`、`/usr/bin/dtc`、`/usr/bin/fdtget`、`/usr/bin/fdtoverlay`。擷取不需要 root、loop 裝置或掛載權限，建議以一般使用者執行。

```sh
python3 tools/bpi_lab_prepare.py /絕對路徑/映像.img.xz \
  --sha256 完整XZ摘要 \
  --family amlogic --board bpi-m5 \
  --kernel-release 6.18.49-current-meson64 \
  --output /絕對路徑/全新證據目錄
```

Allwinner 改用 `--family allwinner` 及其 `bpi-*` 正式板名，例如 `bpi-m1`、`bpi-m64`。核心版本須使用映像內完整版本，不是只填 `6.18.49`；M1 範例為 `6.18.49-current-sunxi`。Amlogic 的正式板名在 CLI 明確對應 `bananapi*` 建置板名，不做模糊比對。家族模組及板別檢查在解壓前執行。

除家族模組自身核對外，此入口另將原環境 root UUID 與真正 ext 超級區塊 UUID 比對，並將完整來源、原始 IMG、分割、家族結果及擷取證據摘要共同記入 `preparation.json`。

## 不重解壓的續作

若前次已完整擷取，只是組件解析器需要修正，可使用固定摘要的 `extraction.json` 重播：

```sh
python3 tools/bpi_lab_prepare.py /前次目錄/extraction/extraction.json \
  --from-extraction --sha256 前次extraction.json的SHA256 \
  --family allwinner --board bpi-m1 \
  --kernel-release 6.18.49-current-sunxi \
  --output /另一個全新證據目錄
```

此時 `--sha256` 是證據 JSON 的摘要，不是 XZ 摘要。工具核對舊擷取成功狀態、每個實際重用的組件摘要及缺檔診斷摘要。前次沒有讀過的路徑不能被當成「不存在」；缺少證據即阻擋，須針對新增需求重新擷取。

重播結果明列 `source_reread=false`，保留 `replay_of` 路徑與摘要。它證明重用的組件符合先前固定證據，不證明磁碟上的原 XZ 此刻仍未變動，也不能替代部署前完整來源核對。新增結果不覆蓋舊失敗，亦不更改硬體佇列。

## 輸出

| 路徑 | 內容 |
| --- | --- |
| `preparation.json` | 整合結果、原始／壓縮摘要、分割、根 UUID、限制與阻擋原因 |
| `family-result.json` | 固定本次家族模組回傳結果，與摘要綁定 |
| `components/manifest.json` | 家族組件、來源、overlay／fixup 核對及個別限制 |
| `components/files/` | 原配組件及離線有效 DTB，不含整張原始 IMG |
| `extraction/extraction.json` | 完整擷取或證據重播索引 |
| `extraction/file-*.bin` | 擷取介面的普通檔案副本，以索引對應原路徑 |

成功退出碼為 `0`，阻擋為 `2`。沒有自動重試、沒有跳過錯誤繼續刷下一套。程序意外終止時也不得只因目錄存在就當成完成。

## 下一層條件

組件準備完成後，才能交給家族的離線 U-Boot 配置核對函式。RAM banks、固定保留區、核心工作區、載入位址、媒體身分、引導分割、U-Boot 版本與資格摘要都須由站點另外提供，工具不猜實板值。

Amlogic 動態 CMA 與固定 `reg` 韌體保留區分開保存。依 [DeviceTree 動態保留記憶體規範](https://github.com/devicetree-org/dt-schema/blob/main/dtschema/schemas/reserved-memory/reserved-memory.yaml)及 [共用 DMA 區規範](https://github.com/devicetree-org/dt-schema/blob/main/dtschema/schemas/reserved-memory/shared-dma-pool.yaml)，`size`／`alloc-ranges` 不等於已固定占用的韌體位址。限定 CMA 需求仍須由外部逐項核定，配置檢查會扣除固定保留區及載入組件，不能藉此略過 RAM 安全條件。

詳細家族限制見 [Allwinner](bananapi-lab-allwinner-20260917.md)、[Amlogic](bananapi-lab-amlogic-20260917.md)及[共用映像擷取](bananapi-lab-image-20260917.md)。這些流程不取代板上摘要、開機、周邊、壓力及故障返回驗證。
