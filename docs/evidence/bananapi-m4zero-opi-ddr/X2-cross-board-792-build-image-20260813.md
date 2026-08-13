# X2 跨板 792 MHz 候選建置與映像證據

## 結論

X2 已完成標準 U-Boot、TF-A、套件與完整 Armbian 測試映像建置。建置產物、
套件內 bootloader 與 U-Boot 工作樹產物逐位元一致；完整映像也已回讀確認
8 KiB 偏移內容相同。這只證明建置與封裝正確，尚未證明冷開機、Linux 或
跨批次 DDR 穩定性。

## 候選設定

```text
DRAM_CLK=792
DX_ODT=0x07070707
DX_DRI=0x0e0e0e0e
CA_DRI=0x0d0d
ODT_EN=0xaaaaeeee
TPR6=0x3a808080
TPR10=0x402f6663
TPR11=0x24242422
TPR12=0x110f1111
```

`M4ZDDR1` 唯讀診斷已啟用，`M4ZLAB2` 執行期實驗器已停用。標準 SPL 的
MMC、FIT、block load 與 raw image 載入功能均已啟用。

## 建置來源

| 項目 | 值 |
| --- | --- |
| Armbian 分支 | `bpi-m4zero-opi-ddr-port-20260813` |
| 建置提交 | `918c0e93a89d2ceec2e059ef742467f8dc546be4` |
| U-Boot upstream | `127a42c7257a6ffbbd1575ed1cbaa8f5408a44b3` |
| U-Boot 版本 | `v2026.01` |
| TF-A 版本 | `lts-v2.12.9` |
| `SOURCE_DATE_EPOCH` | `1786579200` |
| 建置入口 | `tools/build-bpi-m4zero-cross-board-792.sh` |

正式命令：

```bash
BUILD_STAMP=20260813-cross-board-pushed-v5 \
  ./tools/build-bpi-m4zero-cross-board-792.sh
```

正式證據目錄：

```text
output/evidence/bpi-m4zero-opi-ddr/X2-20260813-cross-board-pushed-v5-918c0e93a
```

## 建置守門

| 檢查 | 結果 |
| --- | --- |
| 18 份 U-Boot 補丁套用 | 通過 |
| U-Boot 與 TF-A 編譯 | 通過 |
| 套件與工作樹 bootloader 比對 | 通過 |
| 候選設定逐欄比對 | 通過 |
| 標準 SPL 載入功能 | 通過 |
| `M4ZDDR1` 標記 | 通過 |
| `M4ZLAB2` 停用 | 通過 |
| 未封裝 SPL 小於 40 KiB | 通過，38,912 bytes |
| Python 單元測試 | 通過，23 項 |
| ShellCheck 與 Git 空白檢查 | 通過 |

主要 bootloader 雜湊：

```text
u-boot-spl-nodtb.bin
469213670ab40c4bfdef9c464b8867dff86faa2edda7d71106ee72a464c88755

sunxi-spl.bin
88a5d1117da2d9563a0657f8e2ec63ca0a7ac3388592271dc998bc52d08949a3

u-boot-sunxi-with-spl.bin
a23cb287ac503a63bb505c4fe538447aec91a18fb5aadb6e5e87126b3c47e0ad
```

## 映像封裝

來源為已知可開機的 U0 480 MHz Jammy 映像，只替換 8 KiB 偏移的完整
bootloader。來源映像 SHA-256：

```text
80f9b188d6315b9a7d189a3e08b3b174ffbeb6b6173c74c98007a4ff1dbb6348
```

封裝入口：

```bash
./tools/package-bpi-m4zero-cross-board-792-image.sh \
  output/evidence/bpi-m4zero-opi-ddr/X2-20260813-cross-board-pushed-v5-918c0e93a
```

輸出目錄：

```text
output/images/2026.08/bpi-m4zero-cross-board-792
```

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| `Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_x2-cross-board-792mhz.img` | 2,034,237,440 | `fb665992d6a5becfe2694cade5f2e1367f0eeb18582fdcda8e8d3d446042610b` |
| `Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_x2-cross-board-792mhz.img.xz` | 454,842,916 | `3bff7ae94ffdc6e38fb5241646204dbe6ede9b6556028924bd54626ecc670fbd` |

前綴不變、bootloader 回讀、後綴不變、映像大小不變、分割表解析與 xz
完整性均通過。

## 失敗與修正紀錄

1. 第一輪一般建置因 LAB 執行期時脈變數只在條件宣告內可見而編譯失敗；
   改為宣告始終可見，實體定義及物件仍只在 LAB 模式連結。
2. 第二輪 U-Boot 編譯成功，但證據腳本只搜尋舊套件目錄，誤拿先前的
   480 MHz LAB 套件；改為同時搜尋現行與 hashed 輸出，且只接受建置開始
   後產生的套件。
3. 第三輪套件與工作樹差 11 bytes，確認只有兩處建置時間及其 eGON／FIT
   校驗值；加入固定 `SOURCE_DATE_EPOCH`，不放寬二進位比對。
4. 第四輪仍由 Armbian 套件快取回用固定時間戳之前的同 Build ID 套件；
   將兩份舊套件移入
   `output/evidence/bpi-m4zero-opi-ddr/pre-deterministic-P1f88-20260813`
   後重建。
5. 第五輪建置、套件、工作樹與映像回讀全部一致，才列為正式 X2 產物。

## 證據邊界

- 0438 與 1116 的 `M2 20/20` 是同次上電後熱重設結果。
- 本文件的 X2 映像尚未在任何板上冷開機。
- 新增兩片現場確認採用三星 DDR 的 M4 Zero 後，必須先取得完整料號，並
  完成四片板的冷啟動與 Linux 壓力矩陣，才能判定跨供應商與批次候選。
- BPI-M4B 雖同為 H618，仍須使用獨立板級設定與證據，不得直接燒錄本映像。
