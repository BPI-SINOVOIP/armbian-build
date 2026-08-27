# Banana Pi M1 Super 內部 L2 映像建置證據

## 結論

Banana Pi M1 Super 的 Debian Trixie minimal CLI 映像已由固定來源完整建置，並通過 L1 與 L2 唯讀內容守門。證據範圍只支持「來源、建置契約與映像內容一致的內部軟體候選」；未進行實機燒錄、UART、冷啟動或周邊測試，也尚未閉合無線料號及所有預建內容的再散布授權，因此不得公開發布或宣稱硬體功能通過。

板檔維持 `.wip`，`public_release_allowed=false`、`hardware_claims_allowed=false`。

## 建置身分

| 項目 | 值 |
| --- | --- |
| Armbian 來源提交 | `8c6533a10c3ec97e0565c46ef34ab857fca7d4d4` |
| 來源 tree | `efb01adef19c41fab45d44d8f3e01f943eb84feb` |
| 驗證器提交 | `8c6533a10c3ec97e0565c46ef34ab857fca7d4d4` |
| 建置與驗證 validation SHA-256 | `2026b2786f523bcb158f6eb70674535d8e134df690b31a17e76b26d878412f1c` |
| 候選矩陣 SHA-256 | `933ecbb6a32922b7688e3ef9ed2c59c15f0189c0da950803f70ae632e16c65c3` |
| Linux | `6.1.115-vendor-rk35xx`，來源提交 `c6157104418d012823413c02f9222f3fe123dd25` |
| U-Boot | `2017.09`，來源提交 `39cd993e5d6296635438e84f4576b3a9bf76f86e` |
| RKBin | `1d3c61008fa823936ae7a59615393f8294b64456` |
| Armbian firmware | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| 固定建置時間戳 | `1787082913` |
| 規範投影 SHA-256 | `5c5d6570f8a9e72f6c150dab4314de9d2bca7afdb89e796f36d9e41247e22d3d` |

建置使用 M1 Super 專用 OverlayFS 上層，共用快取只作唯讀下層。完成後已確認建置程序與掛載均結束，再精確清除專用上層；共用下層未修改。

## 產物

產物目錄為 `output/images/2026.08/bananapi-rockchip-rk3528-m1super-trixie-vendor-cli/`。

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| IMG | 2,420,113,408 | `bc30fcb7016b3f4fb2b0888ca130646465857fe38c8041c75b4d05ea27f43324` |
| XZ | 467,394,048 | `480e845023f838208f6099d29fb291a337fbd2c54aaa8a70df6a8e6252ebd9f4` |

## 唯讀守門結果

- XZ 結構與解壓串流 SHA-256 對應 IMG。
- GPT 根分割區從 sector `32768` 開始，大小為 `4691968` sectors，類型 GUID 為 Linux root ARM64。
- 根檔案系統為標籤 `armbi_root` 的 `ext4`，以唯讀限制掛載檢查。
- 映像內 `rockchip/rk3528-bananapi-m1-super.dtb` SHA-256 為 `68c0d6c27d2802abee0b7ab4b0569581048b14fc651d55c103392d81e00f2eb6`，身分、儲存、網路、顯示、USB OTG、SPI、I2C 與媒體節點符合受控契約。
- `idbloader.img` 位於 byte offset `32768`，大小 `311296`，SHA-256 為 `ecd35b1d69c4b87e2ba170017f58c2f67f44c178dbb7df3488d9b88c26847355`。
- `u-boot.itb` 位於 byte offset `8388608`，大小 `1320960`，SHA-256 為 `ee2067f149cfc6c74f84c5c09880673dcda9133d4593ec20e9fc6e328f6bd59a`。
- U-Boot 載荷清單 SHA-256 為 `c26193529828daf0c80cb0980dd20b1c06dc802992708a340b4b63bfa622479b`。
- 最終核心設定 SHA-256 為 `24edbbaabf1bd7960e7c2647ec7e96c25e2e9bf4de5a440c30827eb15b162e9e`。
- 最終 U-Boot 設定 SHA-256 為 `c56f7986bc9d636d51439509c4ad43b8adc247b97783717de61553bba8c7bf60`。
- 最終設定清單 SHA-256 為 `e40d737d10a0494a58eedfb5831bf28113ce13a1e618fe78d2c70329ee70e67c`。
- RKBin 證據清單 SHA-256 為 `79a10a440ef02ceb9353ec8f5f8914d9981a47a83e0f291b700ac168be64e458`，映像亦包含規定的授權檔副本。
- 映像內套件與核心設定涵蓋 GPIO、I2C、SPI、USB gadget、網路、無線、藍牙、DRM、VPU 及診斷工具；這只證明軟體內容存在，不代表裝置實際可用。

## 審查補強

- 正式建置入口先執行 `source-contract` 階段，只檢查固定來源、來源契約、狀態形狀與規範投影，不依賴既有 `output/`；因此 L2 狀態可在乾淨輸出目錄重新建置。
- 提升與稽核使用 `material-evidence` 階段，除核對檔案雜湊外，也會解壓 XZ 並與 IMG 比對、解析三份 TSV 清單、核對 IMG 內的 U-Boot 偏移內容，並以唯讀 loop 與唯讀根檔案系統重查 DTB、設定、套件載荷、韌體及 RKBin 授權檔。
- 規範投影排除候選層級、建置證據與影像衍生欄位，避免自我雜湊循環；來源提交的投影、現行 validation 投影與 L2 證據必須一致。新增套件、核心選項、載荷或其他受控要求後，舊 L2 證據會失效，必須重新建置與驗證。
- ATF／BL31 的來源敘述已收斂為固定 RKBin 預建載荷；沒有宣稱 ATF 由原始碼建置。
- 正式驗證成功現在必須依序完成共用唯讀驗證、即時物質證據重建、專用完成狀態原子寫入與讀回驗證；舊 validation 內的歷史映像證據不會被套用到新候選。
- 專用物質證據會重新記錄並核對 GPT CRC／結構、根分割區大小與類型、ext4 標籤、必要套件、核心模組、來源中繼資料與開機設定，並綁定共用驗證清單及狀態雜湊。
- XZ 除解壓 SHA-256 外另執行嚴格結構與結尾檢查；產物與矩陣路徑限於固定 M1 Super 目錄，DTB 路徑及固定時間戳均須跨 validation、metadata 與狀態一致。

## 未完成項目

- 量產 Wi-Fi／Bluetooth BOM 在 SYN43752、AP6275S 與 RTL8852BS 證據間仍不一致。
- Armbian firmware 逐檔授權尚未閉合；RKBin 只能依授權隨採用 Rockchip 積體電路的平台散布，不得獨立散布或修改二進位內容。
- RK3528 BL31、DDR 與 loader 使用預建 RKBin 載荷，尚無固定來源的完整重建鏈。
- 尚未在可追溯板號與板修上完成 SD／eMMC 冷啟動、UART、乙太網路、Wi-Fi、Bluetooth、HDMI、GPU、VPU、USB、GPIO、I2C、SPI、音訊及長時間穩定性測試。
- 本次 L2 不構成正式板級支援、量產核准、安全維護承諾或對外發布核准。
