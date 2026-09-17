# 原映像組件準備與證據重播

## 使用範圍

`tools/bpi_lab_prepare.py` 串接完整映像核對、唯讀檔案擷取及家族組件準備。目前有 45 個板別候選路由：Allwinner 16、Amlogic 4、Rockchip 9、MediaTek 7、SpacemiT 3、Sunplus 2、Renesas 1、Realtek 2、Synaptics 1。這不是 45 板已可實測的宣告：每個映像都必須通過原配核對，特殊格式、舊式 FEX、未支援參數／fixup 仍會阻擋。

預設 `--layout single-ext` 僅支援 MBR 單一 Linux 主分割；`--layout disk` 支援完整 MBR／GPT 核對及以 fstab 指定的 ext 根、獨立 FAT／ext 開機區。混合 MBR、延伸分割、不明確掛載與跨掛載符號連結保持阻擋。輸出必須是全新目錄；工具不掛載、不改原映像、不連 UART、不操作電源或媒體。`prepared` 只代表本次離線組件核對通過，`hardware_validated`、`whole_backend_ready` 及 `boot_executed` 均為 `false`。

## 首次擷取

主機需要 Linux、Python 3.10 以上、`e2fsprogs` 及 `device-tree-compiler`；使用固定的 `/usr/sbin/debugfs`、`/usr/bin/dtc`、`/usr/bin/fdtget`、`/usr/bin/fdtoverlay`。擷取不需要 root、loop 裝置或掛載權限，建議以一般使用者執行。

多分割模式另需 `util-linux`、`gdisk`、`mtools`，使用 `/usr/sbin/sfdisk`、`/usr/sbin/sgdisk`、`/usr/sbin/blkid` 與 `/usr/bin/mtype`。不執行分割修復；工具版本不同而無法確認診斷時停止，不忽略錯誤。

```sh
python3 tools/bpi_lab_prepare.py /絕對路徑/映像.img.xz \
  --sha256 完整XZ摘要 \
  --family amlogic --board bpi-m5 \
  --kernel-release 6.18.49-current-meson64 \
  --output /絕對路徑/全新證據目錄
```

Allwinner 改用 `--family allwinner` 及其 `bpi-*` 正式板名，例如 `bpi-m1`、`bpi-m64`。核心版本須使用映像內完整版本，不是只填 `6.18.49`；M1 範例為 `6.18.49-current-sunxi`。Amlogic 的正式板名在 CLI 明確對應 `bananapi*` 建置板名，不做模糊比對。家族模組及板別檢查在解壓前執行。

除家族模組自身核對外，此入口另將原環境 root UUID 與真正 ext 超級區塊 UUID 比對，並將完整來源、原始 IMG、分割、家族結果及擷取證據摘要共同記入 `preparation.json`。

Sunplus 可指定 `--kernel-release 0` 要求從核心內容取得完整版本，與 initramfs modules 及存在的 headers 交叉核對；成功結果改列真正版本，不信檔名中的 `0`。不會因此修改舊佇列的 20 筆阻擋紀錄。

Realtek 使用 `LABEL=BPI-ROOT` 時，額外核對原配 fstab、超級區塊標籤及其在映像內的唯一性。`root_binding` 保留方法、真實 UUID 及 `unique_on_hardware=false`；`root_identity_verified=true` 不冒充 `root_uuid_verified=true`。上板仍須排除其他 SD／USB 上的同名標籤，不能從單張映像推論全機唯一。

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

詳細家族限制見 [Allwinner](bananapi-lab-allwinner-20260917.md)、[Amlogic](bananapi-lab-amlogic-20260917.md)、[Rockchip](bananapi-lab-rockchip-20260917.md)、[MediaTek](bananapi-lab-mediatek-20260917.md)、[SpacemiT](bananapi-lab-spacemit-20260917.md)、[六板特殊入口](bananapi-lab-special-20260917.md)及[共用映像擷取](bananapi-lab-image-20260917.md)。這些流程不取代板上摘要、開機、周邊、壓力及故障返回驗證。
