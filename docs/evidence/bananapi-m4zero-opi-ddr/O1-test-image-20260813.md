# O1 可燒錄測試映像證據

## 產物身分

| 項目 | 值 |
| --- | --- |
| 封裝提交 | `cd69c06c797bda76b166abfc5df104a525629c62` |
| O1 建置提交 | `238e3e24433a85613d58fff7cd5ed69e1f2b1008` |
| 建置識別碼 | `2026.01-S127a-P4301-Hc6a9-V3946-Bd0d2-R448a` |
| DDR 設定檔 | Orange Pi Zero 3、LPDDR4、792 MHz |
| 映像大小 | 2,034,237,440 bytes |
| 實機驗證 | 尚未執行 |

原始映像：

```text
/media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-opi-ddr/output/images/2026.08/bpi-m4zero-o1-opi-ddr-diag/Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_o1-opi-ddr-diag-P4301-792mhz.img
```

壓縮映像：

```text
/media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-opi-ddr/output/images/2026.08/bpi-m4zero-o1-opi-ddr-diag/Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_o1-opi-ddr-diag-P4301-792mhz.img.xz
```

## 封裝方式

本次沒有重新建置 rootfs。封裝腳本以已知可用的 U0 Jammy 映像作為既有
系統內容，只替換位元組區間 `[8192, 882169)` 的 bootloader：

```bash
./tools/package-bpi-m4zero-o1-test-image.sh \
  output/evidence/bpi-m4zero-opi-ddr/O1-20260813-131210-238e3e244
```

| 元件 | 值 |
| --- | --- |
| 來源 U0 映像 | `Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_u0-safe-480mhz.img.xz` |
| 來源 SHA-256 | `80f9b188d6315b9a7d189a3e08b3b174ffbeb6b6173c74c98007a4ff1dbb6348` |
| bootloader 偏移 | 8,192 bytes |
| bootloader 大小 | 873,977 bytes |
| bootloader SHA-256 | `fe94a100f3ed688e16f986cbdd05d3056b66e73bb56f8581e9a15c89d5a9efb3` |

因此此映像適合比較 U0 與 O1 的早期啟動差異，不代表 O1 已重新驗證或
更新 U0 既有系統內容裡的 kernel、rootfs 與使用者空間。

## 封裝腳本驗證

| 檢查 | 結果 |
| --- | --- |
| 寫入前來源 `.xz` 完整性 | 通過 |
| `[0, 8192)` 前綴不變 | 通過 |
| bootloader 回讀逐位元比較 | 通過 |
| `[882169, EOF)` 後綴不變 | 通過 |
| 寫入前後映像大小 | 相同 |
| 分割表解析 | DOS，第一分割區從磁區 8192 開始 |
| 新 `.xz` 完整性 | 通過 |

## 獨立複驗

封裝程序結束後另行執行，不沿用腳本內部判定：

```bash
sha256sum -c Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_o1-opi-ddr-diag-P4301-792mhz.img.sha256
sha256sum -c Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_o1-opi-ddr-diag-P4301-792mhz.img.xz.sha256
xz -t Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_o1-opi-ddr-diag-P4301-792mhz.img.xz
cmp -n 873977 -i 8192:0 \
  Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_o1-opi-ddr-diag-P4301-792mhz.img \
  ../../../evidence/bpi-m4zero-opi-ddr/O1-20260813-131210-238e3e244/u-boot-sunxi-with-spl.bin
```

四項皆通過。映像中的 bootloader 另可讀到 `P4301`、
`M4ZDDR1_PROFILE0`、`M4ZDDR1_BEGIN` 與 `M4ZDDR1_FINAL`。

## 產物 SHA-256

| 產物 | SHA-256 |
| --- | --- |
| `.img` | `316e0d24dc02c9bbfd9579d2b190cbb1aea37516acd2ddcefa85842546897e23` |
| `.img.xz` | `20d70f507c3a7e81e2aafc4f6ebf0f36d4249ecd59ad9f46eb301a7642704847` |

同目錄另保存 `manifest.tsv`、`sfdisk.json` 與兩份 SHA-256 隨附檔。

## 證據邊界

- 已證明指定 O1 bootloader 被正確放入可燒錄映像。
- 已證明替換區間以外的既有系統內容位元組不變。
- 尚未證明 SPL 能在任何 M4 Zero 上完成 DDR 初始化。
- 尚未證明 2 GiB／4 GiB 容量偵測正確。
- 尚未證明 792 MHz 穩定；O1 的 UART 輸出也會改變啟動時序。
