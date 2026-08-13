# BPI-M4 Zero X2 792 MHz 完整映像矩陣交付說明

## 交付結論

X2 792 MHz 完整映像矩陣已封裝完成，包含五個發行版的 CLI 與 XFCE，
合計十套未壓縮 IMG 與十套 XZ。OS、kernel、initramfs、DTB、分割表及
rootfs 來自雜湊鎖定的 U0 作業系統矩陣；封裝工具只把 `8,192` bytes 偏移、
大小 `873,977` bytes 的 U0 bootloader 替換成四板 G1 驗證使用的 X2
`P1f88` bootloader，範圍外資料逐位元不變。因此這是使用既有正式 Armbian
作業系統產物的可驗證 bootloader 替換，不是重新編譯十次相同 kernel，映像
內也不再執行 U0、V2、O1 或原廠 `boot0` 的 DDR 初始化。

交付目錄：

```text
output/images/2026.08/bpi-m4zero-x2-cross-board-792-matrix
```

建議對外傳輸下列內容：

- 十個 `.img.xz` 與十份 `.metadata.txt`。
- `SHA256SUMS-XZ`、完整的 `SHA256SUMS`、`MATRIX.tsv` 與
  `COMPLETION_STATUS.txt`。
- `QUALIFICATION_STATUS.txt`、`README.md` 與 `TEST_RECORD_TEMPLATE.tsv`。
- `bootloader/` 內的鎖定套件與 `P1f88` 二進位，供需要重現封裝的人員使用。

未壓縮 `.img` 保留在本機，供直接燒錄與回讀比對。十套 IMG 合計
`35,718,692,864` bytes，十套 XZ 合計 `7,120,276,228` bytes。

## 鎖定軟體組合

| 項目 | 鎖定值 |
| --- | --- |
| 板型 | Banana Pi BPI-M4 Zero |
| Armbian 板名 | `bananapim4zero` |
| Kernel | Linux `6.18.32-current-sunxi64` |
| U-Boot | `v2026.01` |
| TF-A | `lts-v2.12.9` |
| X2 Build ID | `P1f88` |
| DDR 時脈 | `792 MHz` |
| Bootloader 大小 | `873,977` bytes |
| Bootloader 寫入偏移 | `8,192` bytes |
| Bootloader SHA-256 | `a23cb287ac503a63bb505c4fe538447aec91a18fb5aadb6e5e87126b3c47e0ad` |

X2 DDR 參數：

```text
DX_ODT=0x07070707
DX_DRI=0x0e0e0e0e
CA_DRI=0x00000d0d
ODT_EN=0xaaaaeeee
TPR6=0x3a808080
TPR10=0x402f6663
TPR11=0x24242422
TPR12=0x110f1111
```

## 映像清單

| 發行版 | 介面 | XZ 大小 | XZ SHA-256 |
| --- | --- | ---: | --- |
| Bookworm | CLI | 432,902,600 | `739ce88b9b9111e82a60286fccf864e0ef47e4e910e5cc089d872717a3345cd2` |
| Bookworm | XFCE | 973,683,312 | `f0531f0c143c232eef231c67f31936d9c6da7d0d26d2ae8715fdfe87e30e0bd8` |
| Jammy | CLI | 454,842,916 | `3bff7ae94ffdc6e38fb5241646204dbe6ede9b6556028924bd54626ecc670fbd` |
| Jammy | XFCE | 888,995,752 | `636da77908bc4c3f5e0622496442b554b6d3bf10787eb2a218039289812832b2` |
| Noble | CLI | 448,867,252 | `867861ec0e691ef2ca1901fb0a40ffea63c1c8a83a12c2e881f7a153bac99fa7` |
| Noble | XFCE | 916,579,836 | `e148b33abc2ca4384bb40f8269d9cd99ae1d863f59f94ee05b89b663d5f97443` |
| Resolute | CLI | 458,596,500 | `4e3a1819a7b06c5293336898845227849887c49a4c79561a2f4b6fb440e74d5a` |
| Resolute | XFCE | 1,001,418,528 | `15b336d8a94288dc0f480addb18fae16e1492fa159db2d65f1a2a74a14f02b1c` |
| Trixie | CLI | 459,723,388 | `6cbcaed043cdd9c0480a767217082b6b165918d79a6f5411afb4446bc217c689` |
| Trixie | XFCE | 1,084,666,144 | `67097c7487d23f4a5d2aa56b9a29f83b76a72bb214edd43decfe307b6e38f0db` |

完整檔名、IMG 大小、IMG 雜湊與 XZ 雜湊以同目錄的 `MATRIX.tsv` 為準。

## 接收與燒錄

先驗證收到的 XZ：

```bash
grep -qx 'status=complete' COMPLETION_STATUS.txt
sha256sum -c SHA256SUMS-XZ
for image in ./*.img.xz; do xz -t "$image"; done
```

確認 SD 卡裝置名稱後再燒錄。下列 `/dev/sdX` 必須替換為實際 SD 卡，
選錯裝置會破壞其他磁碟資料：

```bash
lsblk -o NAME,SIZE,MODEL,TRAN,MOUNTPOINTS
xz -dc Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_x2-cross-board-792mhz.img.xz \
  | sudo dd of=/dev/sdX bs=16M conv=fsync status=progress
sync
```

也可以直接燒錄未壓縮 IMG：

```bash
sudo dd if=Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_x2-cross-board-792mhz.img \
  of=/dev/sdX bs=16M conv=fsync status=progress
sync
```

## 大量驗證順序

1. 先使用 Jammy CLI；它與四板 G1 使用的映像逐位元相同，可建立測試基準。
2. 每種 DDR 料號與批次至少選三片，每片執行十次完全斷電冷啟動；已知弱板
   建議執行二十次。軟體重新啟動不能取代 DDR 冷啟動。
3. 每次保留從上電前開始的完整 UART，記錄板號、DDR 料號、eMMC 料號、
   SD 卡型號、電源、環境溫度與結果。
4. Jammy CLI 冷啟動通過後，使用 Noble XFCE 驗證 HDMI、桌面、USB、網路
   與無線周邊。
5. 每種 DDR 容量至少選一片，依序啟動其餘八套映像，確認能進入登入或桌面。
6. 在 2 GiB 板執行約 `1.4 GiB` 記憶體壓力，在 4 GiB 板執行約 `3.0 GiB`
   記憶體壓力，同時執行四核心 CPU 壓力；短測至少三分鐘，長測至少八小時。

若映像已具備工具，可執行：

```bash
sudo memtester 1400M 3
stress-ng --cpu 4 --vm 1 --vm-bytes 1400M --verify --timeout 180s
```

4 GiB 板把 `1400M` 改為 `3000M`。若映像沒有這些套件，先透過套件管理器
安裝，或記錄無法執行的原因，不得把「未測」填成「通過」。

## 判定標準

一次完整通過至少要同時符合：

- SPL 顯示 `DRAM: 2048 MiB` 或 `DRAM: 4096 MiB`，且容量符合實物。
- 2 GiB 樣本辨識為 `x32 / 1 Rank`；目前的 4 GiB Rayson 樣本辨識為
  `x32 / 2 Rank`。
- TF-A、U-Boot、kernel 與 initramfs 均正常載入。
- 無 `Bad Data CRC`、initramfs 損壞、同步例外、Oops、panic 或無故重設。
- CLI 能進入登入；XFCE 能顯示桌面並完成基本輸入與網路檢查。
- 記憶體壓力沒有資料比對錯誤，核心日誌沒有新增硬體或記憶體錯誤。

任一項不符合即記為失敗，並保留第一次失敗的完整 UART 與測試條件。

## 已完成守門與限制

已完成：

- 十個 IMG 與十個 XZ 封裝。
- 十個 XZ 全串流 `xz -t` 檢查。
- `SHA256SUMS` 全檔案重新計算檢查。
- 十個 IMG 的分割表、Build ID 與內嵌 bootloader 雜湊回讀。
- bootloader 範圍外資料與各自來源映像一致。
- 同一份 Jammy CLI 已在 `0438`、`1116`、`S337`、`S322` 完成一次完全
  斷電 G1 啟動，其中三片已完成三分鐘短壓力測試。

目前狀態是「可供大量驗證的候選」，不是量產認證。尚未完成每片十次以上
冷啟動、所有舊 V2 弱板回歸、八小時以上壓力、溫度與電壓角落測試。Samsung
樣本上的 SDIO／Bluetooth 問題也應獨立記錄，不能與 DDR 穩定性混為一談。

## 可重現建置

```bash
cd /media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-opi-ddr
./tools/build-bpi-m4zero-x2-792-matrix.sh
```

工具支援中斷續跑，既有 IMG/XZ 必須先通過雜湊、分割表與 Build ID 驗證才會
沿用。新產物先寫入 `.partial`，通過檢查後才升格為正式檔案。
