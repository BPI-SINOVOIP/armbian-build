# Banana Pi SM10 固定元件建置證據

## 結論

2026 年 8 月 27 日已在隔離 worktree 內完成 Linux、U-Boot 與 OpenSBI
元件建置。來源先通過固定 manifest 與 20 個專案提交核對，再以 SpacemiT
官方 SDK 容器和 SDK GCC 15.2 編譯。過程沒有執行 Armbian 完整映像建置，
也沒有建立 Buildroot rootfs。

依全系列稽核定義，本次結果為 `L1 元件候選`；只有完整映像通過唯讀內容守門
後才能升級為 `L2`。元件證據另由
`tools/verify-bananapi-spacemit-k3-sm10-components.sh` 對照契約重新核對。

這項結果只證明固定來源可編譯及專屬 Linux DTB 可產生，不代表 SM10 已通過
實機啟動、周邊功能、安全開機或公開散布核准。板檔仍維持 `.wip`，
`public_release_allowed=false`、`hardware_claims_allowed=false`。

## 建置基準

| 項目 | 固定值 |
| --- | --- |
| Armbian 基準 | `12d304707afebff59ee0a9f4d2d08c2b894b6c9c` |
| Linux 提交 | `27275ec8240cc49af3a525b8bc325d9b5029fb81` |
| U-Boot 提交 | `1b10c8119e1a9b5451a4236f6b384f7c91eed1e2` |
| OpenSBI 提交 | `3e2f9efc9660b8d5fcae4e0b6495f306d5c64078` |
| 容器 | `harbor.spacemit.com/bianbu/k3-bsp-builder:latest` |
| 容器映像 ID | `sha256:9531810450c5953c0675515ab7deee5d9634966e8abccfcd5ea60f8aee94e335` |
| 編譯器 | `riscv64-unknown-linux-gnu-gcc (gd094d3a8c4f) 15.2.0` |
| GCC 本體 SHA-256 | `7357a5d6e1197ca48da9db6e8a2f7a09784f3b6bb9163acfe213d191ee30bb2d` |
| 平行工作數 | `16` |
| 本機證據目錄 | `.tmp/bananapi-sm10-components-official-sdk-v4` |

建置命令：

```bash
COMPONENT_ROOT="$PWD/.tmp/bananapi-sm10-components-official-sdk-v4" \
JOBS=16 ./tools/build-bananapi-spacemit-k3-sm10-components.sh
```

工具先在主機執行來源稽核，再以唯讀方式把 SDK 掛入官方容器。Linux 使用
out-of-tree 輸出；U-Boot 使用隔離來源複本內建置，原因是 SpacemiT 的板級
`config.mk` 只有此模式會產生 FSBL、bootinfo、FIT 與預設環境。原始 SDK
工作樹沒有被元件建置修改。

可保存或交接的證據只包含 `artifacts/`、`source-evidence/`、
`COMPONENTS.tsv` 與 `COMPONENT_STATUS.json`。建置暫存的 `src/` 可能繼承 SDK
私鑰，不得複製到證據輸出或對外散布。

## 元件產物

| 元件 | 產物 | 大小 | SHA-256 |
| --- | --- | ---: | --- |
| Linux | `Image` | 33821184 | `7fe1aee56d5b9d49c494e03855d1245262bd13a20c89d5f31af2a8f733c4665d` |
| Linux | `k3-bananapi-sm10.dtb` | 142075 | `a74520d979cc62fcdb12dfddd97c7968900109df6a33ae34c1489d87a34695ba` |
| Linux | `linux.config` | 305343 | `549dd138a5205d71cca5620c4d54f5905d6305dfa502ca80c4d7a319d20d34c5` |
| U-Boot | `FSBL.bin` | 449984 | `9a40d9d27ec8de79a38ece8ad00de96d29d45b507c43f46f3bf45589c50034d7` |
| U-Boot | `bootinfo_block.bin` | 80 | `8cde21b4e18f0b72f2c98b5ae686dfe8ef75d8dc641393e97b52cf318b294e4b` |
| U-Boot | `u-boot.itb` | 2134494 | `f7560b4afd523b484b7f950f038485dea7c28cbf5f9c225290d940ca4461ae13` |
| U-Boot | `u-boot-env-default.bin` | 16384 | `e73d2c0c44c6b00019a4c6190e8e7d03a37be710a20fa76b887bdd098fa1ff51` |
| OpenSBI | `fw_dynamic.itb` | 272223 | `37dcca0ad696c88900c316a5bab289f1e3e55f09836cb22a4f09c1faa93be86d` |
| OpenSBI | `fw_dynamic.elf` | 1531736 | `2820c5181c7925ec5df26dc26d52acbad9ce64393162186f83bb5b9af15fc482` |

專屬 DTB 已由 `fdtget` 核對：

- `model`：`BananaPi BPI-SM10`
- `compatible`：`bananapi,bpi-sm10`、`spacemit,k3-com260`

## 與既有啟動檔比較

既有 Armbian blob 取自 2026 年 5 月 26 日的 SDK 完整建置。本次固定元件
建置採用固定 `SOURCE_DATE_EPOCH`，所以含編譯時間的 FSBL、U-Boot FIT 與
OpenSBI FIT 雜湊不同；FIT 中的資料大小、組態與控制 DT 清單一致。下列兩項
不含該時間差，逐位元相同：

- `bootinfo_block.bin`：`8cde21b4e18f0b72f2c98b5ae686dfe8ef75d8dc641393e97b52cf318b294e4b`
- `u-boot-env-default.bin`：`e73d2c0c44c6b00019a4c6190e8e7d03a37be710a20fa76b887bdd098fa1ff51`

本次建置產物沒有取代候選內既有 blob。既有 blob 仍由驗證契約以固定雜湊
管理；公開散布與硬體啟動仍受相同阻擋事項限制。

## 過程問題與處置

1. 主機 Ubuntu 22.04 無法執行 SDK 的 GCC 15.2 包裝器，因為它要求
   `GLIBC_2.38`。工具改用 SDK 指定的官方容器，沒有改用不同版本編譯器。
2. 唯讀 SDK 掛載會阻止 `repo` 寫入 `.repo/TRACE_FILE`。工具因此在主機完成
   來源稽核，再把固定來源唯讀掛入容器，沒有放寬 SDK 寫入權限。
3. Linux DTB 目標必須使用 `spacemit/k3-bananapi-sm10.dtb`，由核心建置系統
   自動補上架構路徑。
4. SpacemiT U-Boot 的板級 `config.mk` 不支援本工具原先使用的 `O=` 模式；
   改在隔離來源複本內建置後，FSBL、bootinfo、FIT 與預設環境均成功產生。

## 尚未解除的限制

- 沒有 BPI-SM10 載板線路圖、EEPROM `product_name` 證據與實機拓撲核對。
- U-Boot 控制 DT 仍由原廠 EEPROM 流程選擇 `k3_com260.dtb`；只有 Linux 使用
  專屬 `k3-bananapi-sm10.dtb`。
- ESOS、PowerVR 與 VPU 韌體沒有在本次重建，且授權盤點尚未閉合。
- SDK 來源含測試／私用簽章金鑰，不得作為量產金鑰或散布內容。
- 未執行 SD、eMMC、UFS、SPI、NVMe、USB、網路、顯示、GPU、VPU、GPIO、
  I2C、SPI、CAN、休眠、溫度或壓力實機驗證。
- 沒有建立或驗證完整 rootfs 映像；唯讀映像驗證工具只完成靜態與聚焦回歸。
