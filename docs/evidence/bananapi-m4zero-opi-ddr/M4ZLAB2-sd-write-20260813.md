# M4ZLAB2 SD 卡寫入證據

日期：2026-08-13

## 寫入目標

| 項目 | 值 |
| --- | --- |
| Linux 裝置 | `/dev/mmcblk0` |
| 裝置識別碼 | `0x97bc8c07` |
| 裝置路徑 | `pci-0000:01:00.0-platform-rtsx_pci_sdmmc.0` |
| 容量 | `63864569856` bytes |
| 根磁碟 | `/dev/nvme0n1p5`，不是寫入目標 |
| 寫入偏移 | `8192` bytes |
| 寫入大小 | `32768` bytes |

寫入前確認 `/dev/mmcblk0` 是整顆 SD 卡，所有子分割區均未掛載。工具只覆寫
8 KiB 偏移的 SPL 區段，未修改分割表與檔案系統。

## 來源

正式程式提交：`9db9f9549380f2657040d3462ba5f840f475dbfa`

來源 SPL：

```text
output/evidence/bpi-m4zero-ddr-lab/build-20260813-pushed-final-9db9f9549/sunxi-spl-ddr-lab.bin
```

Build ID：`2026.01-S127a-P2cea-Hc6a9-V3946-Be6d8-R448a`

## 寫入命令

```bash
sudo -n ./tools/write-bpi-m4zero-ddr-lab.sh \
  --device /dev/mmcblk0 \
  --spl output/evidence/bpi-m4zero-ddr-lab/build-20260813-pushed-final-9db9f9549/sunxi-spl-ddr-lab.bin \
  --evidence-dir output/evidence/bpi-m4zero-ddr-lab/write-mmcblk0-97bc8c07-20260813-180643 \
  --confirm-write
```

命令結束碼為 `0`。

## 雜湊與回讀

| 內容 | SHA-256 |
| --- | --- |
| 正式 SPL | `4cf6e982dfff69485e4c1251f7a8b16d74dfe9b881bede907a8a32b412171a8f` |
| 寫入前原區段備份 | `4aff4a4bb4a6ea86b78ca5308e5c0dfc1fbf16a139d41012d7cb3857e1f16a12` |
| 寫入後 SD 卡回讀 | `4cf6e982dfff69485e4c1251f7a8b16d74dfe9b881bede907a8a32b412171a8f` |

來源與回讀逐位元相同，證明本機寫入成功。原區段備份、回讀檔、資訊與雜湊表
保存在上述本機證據目錄。

## 證據邊界

本次只證明 SD 卡媒體寫入與回讀正確。尚未把卡放入 BPI-M4 Zero，也尚未取得
`M4ZLAB2_READY` UART 記錄，因此不代表 SPL 已在 2 GiB 或 4 GiB 板完成啟動，
亦不代表已找到保險值、最佳效能值或最大容錯值。
