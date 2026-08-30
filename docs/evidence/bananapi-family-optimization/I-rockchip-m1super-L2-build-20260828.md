# Banana Pi M1 Super 現行 L2 正式建置證據

日期：2026-08-28

## 結論

Banana Pi M1 Super 的 Debian Trixie vendor 精簡 CLI 映像已從已推送提交 `d2cc8559662e69a4b083cedc2efc85c80e26144c` 及專用 OverlayFS 上層乾淨重建。共用唯讀守門、L2 即時物質重查、原子完成狀態與二次回讀均通過，因此可列為內部 L2 軟體候選。

此結論只涵蓋來源、建置契約與映像位元內容。未進行實機燒錄、UART、冷啟動或周邊測試，量產無線 BOM 與韌體逐檔再散布授權也尚未閉合；不得據此宣稱硬體功能、量產或公開發布通過。

## 建置身分

| 項目 | 值 |
| --- | --- |
| 板卡 | `bananapim1super` |
| 分支／發行版／型態 | `vendor`／`trixie`／精簡 CLI |
| 建置與驗證提交 | `d2cc8559662e69a4b083cedc2efc85c80e26144c` |
| 來源樹 | `c53ce0af035f95b9565481be0fc5345b501a8d15` |
| 建置與驗證 validation SHA-256 | `8a426455536755783ce45ea8804ab0fd5e577838e252630867dac44bc8b5a074` |
| 規範投影 SHA-256 | `5c5d6570f8a9e72f6c150dab4314de9d2bca7afdb89e796f36d9e41247e22d3d` |
| 固定建置時間戳 | `1787082913` |
| 輸出目錄 | `output/images/2026.08/bananapi-rockchip-rk3528-m1super-trixie-vendor-cli` |

## 正式產物

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| IMG | 2,420,113,408 | `192269a97910729304d635e80921b3fef647a2036d4013958c4cd81cbd4752f8` |
| XZ | 468,870,520 | `b3b640fc04116f0193832354bda899aadcb8f894a22e8b6fed4b1d463fa06b63` |

IMG 與 XZ 均保留。XZ 已通過嚴格結構檢查，完整解壓串流的大小與 SHA-256 對應 IMG，不接受尾端垃圾資料。

## 證據鏈

| 證據 | SHA-256 |
| --- | --- |
| 候選矩陣 `CANDIDATES.tsv` | `1b2eb459d8b7e90734b09428024b478348bcb874393baa42e3a000f684652dfe` |
| 建置完成狀態 | `a24ce2edb1ec41696aad471e75829e7d90670bd270b010baedcdf452d1a28c36` |
| 共用驗證清單 | `edf3d706096d8072610ac452f06d2fb198bf2b63d1af9d9e09b1f3ef9e005cc4` |
| 共用驗證狀態 | `7bd0e00370ebe5fc391e6bfd763835bdc3539223c1c4a6e8dc0dd89f32e97d1e` |
| M1 Super 物質證據 | `02e194c3c925b72238a65c9af1755aa4a594a84e5c6b2ecaecc0622d70062b40` |
| M1 Super 物質完成狀態 | `6b8a5c136d60a215ce8f8c8283d24b30f2f9694244949943dff88ebef54d5c70` |
| U-Boot 載荷清單 | `c26193529828daf0c80cb0980dd20b1c06dc802992708a340b4b63bfa622479b` |
| 最終設定清單 | `e40d737d10a0494a58eedfb5831bf28113ce13a1e618fe78d2c70329ee70e67c` |
| RKBin 清單 | `79a10a440ef02ceb9353ec8f5f8914d9981a47a83e0f291b700ac168be64e458` |

即時驗證完成時間為 `2026-08-27T19:50:21Z`，物質狀態原子完成時間為 `2026-08-27T19:51:05Z`。驗證後再次讀回 IMG、XZ、矩陣、完成狀態、共用驗證、U-Boot 載荷、最終設定、RKBin 與物質完成狀態，沒有沿用舊候選完成狀態。

`artifact.metadata.txt` 的 `evidence_level=L1` 與 `COMPLETION_STATUS.json` 的 L1 說明是刻意保留的建置階段語意：建置只能產生尚未促進的候選，不能自行宣稱 L2。只有後續共用驗證與專用即時物質重查全部通過，才會在 `VERIFICATION_STATUS.json`、`VERIFICATION.tsv` 及兩份 `M1SUPER_MATERIAL_*` 寫入 L2；任一步驟失敗都會撤銷專用完成狀態。

## 內容守門

- GPT 主表與備份表的 CRC 及結構通過；唯一根分割區從 sector `32768` 開始，大小為 `4691968` sectors，類型 GUID 為 `b921b045-1df0-41c3-af44-4c6f280d3fae`。
- 根檔案系統以唯讀方式掛載，型態為 `ext4`、標籤為 `armbi_root`。
- 映像內 DTB 為 `rockchip/rk3528-bananapi-m1-super.dtb`，SHA-256 為 `68c0d6c27d2802abee0b7ab4b0569581048b14fc651d55c103392d81e00f2eb6`。
- `idbloader.img` 位於 byte offset `32768`，大小 `311296`，SHA-256 為 `ecd35b1d69c4b87e2ba170017f58c2f67f44c178dbb7df3488d9b88c26847355`。
- `u-boot.itb` 位於 byte offset `8388608`，大小 `1320960`，SHA-256 為 `ee2067f149cfc6c74f84c5c09880673dcda9133d4593ec20e9fc6e328f6bd59a`。
- 最終核心設定 SHA-256 為 `24edbbaabf1bd7960e7c2647ec7e96c25e2e9bf4de5a440c30827eb15b162e9e`；最終 U-Boot 設定 SHA-256 為 `c56f7986bc9d636d51439509c4ad43b8adc247b97783717de61553bba8c7bf60`。
- 必要套件、`brcmfmac.ko`、`hci_uart.ko`、固定來源中繼資料、受控韌體及 RKBin 授權副本均存在且雜湊相符。

## 重現與重查

```bash
./tools/run-bananapi-rockchip-m1super-candidate-isolated-cache.sh
./tools/verify-bananapi-rockchip-m1super-candidate.sh
PUBLIC_RELEASE=no HARDWARE_CLAIMS=no \
  ./tools/check-bananapi-rockchip-m1super-policy.py \
  --phase material-evidence --evidence-source historical
```

正式建置只允許固定輸出目錄，並以共用 `/media/pi/SMCI/armbian/bpi-v26.2.1/cache` 作唯讀下層。本文件、validation、中央盤點與回歸測試已由提交 `eac5ec7f7` 推送；確認無掛載、程序、開啟檔案或容器引用後，專用 OverlayFS 上層與已被取代的歷史大型輸出已精確移除，正式 IMG 與 XZ 保留且刪除前後雜湊一致。

## 尚未完成

- 尚未取得 SD／eMMC 冷啟動、重新啟動、斷電與長時間穩定性證據。
- 尚未驗證雙乙太網路、Wi-Fi、Bluetooth、HDMI、GPU、VPU、USB host／OTG、音訊、RTC、風扇與 40-pin。
- Wi-Fi 量產 BOM 在 `SYN43752`、`AP6275S` 與 `RTL8852BS` 證據間仍不一致。
- Armbian firmware 逐檔再散布授權尚未閉合；RKBin 只能依授權隨 Rockchip 平台以未修改二進位形式散布。
- `public_release_allowed=false`、`hardware_validation_complete=false` 與 `hardware_claims_allowed=false` 維持不變。
