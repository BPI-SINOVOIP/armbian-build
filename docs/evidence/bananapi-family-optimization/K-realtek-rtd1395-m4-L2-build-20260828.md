# BPI-M4 RTD1395 L2 正式映像建置證據

## 結論

BPI-M4 已從已推送且乾淨的提交 `19b21c370b5ac0f9253b58da5b2c989b9235c9c9` 完成 Debian Trixie legacy minimal CLI 正式重建，並通過共用唯讀內容守門及版本控制內歷史重驗。這是 L2 內部軟體候選，不是實機通過或公開發布核准。

## 正式成品

輸出目錄：

`output/images/2026.08/bananapi-realtek-rtd1395-m4-trixie-legacy-cli`

| 項目 | 數值 |
| --- | --- |
| 來源提交 | `19b21c370b5ac0f9253b58da5b2c989b9235c9c9` |
| 來源 tree | `a7e902738aec5bca17a6ec5ed9298cac81e2c7e8` |
| 固定時間戳 | `1711071187` |
| IMG 大小 | `2126512128` 位元組 |
| IMG SHA-256 | `263a3efaba697a4b5035712b4773447a8efc5e1d1fa17907cbde296741b6b323` |
| XZ 大小 | `402258144` 位元組 |
| XZ SHA-256 | `76772c14f6e4c57820263312a78ac6d53b24ebc076fa45ea8b06806bda25dafb` |
| 2 GiB 映像 DTB SHA-256 | `8886772d273898612bd60dd4ae9ecba7ab73663ab3b27b8a996e6f9b9567a461` |
| 候選矩陣 SHA-256 | `675397dc38c9249e85caef431133a9f42ce77dde2c65f59fc6ffd6f88206f23d` |
| 建置狀態 SHA-256 | `edd2036e7760b378e5a2bfc10392f0e2089593bfc80742cac3665e30bee443a1` |
| 共用驗證清單 SHA-256 | `8dc6b8a3a37d459deb3133fed2a0bd87e271a2fa36c7569212869c437d0785a6` |
| U-Boot 載荷清單 SHA-256 | `6cb68ed44dc0bfc21d5f48563d76ade16ba523f5b2cef7bf730995ca33eaab86` |
| 最終設定清單 SHA-256 | `de6cbd78a98ccd936bb2c3fe216f7f17d4aad036919a3e1a90ddfd3c9d596eaf` |

## 已驗證內容

1. IMG 與 XZ 的檔案大小及 SHA-256 符合候選矩陣，XZ 結構、校驗碼及完整解壓串流等同 IMG。
2. MBR 分割區 1 為 `ea`，從 sector `8192` 開始，大小為 `524288` sectors；分割區 2 為 `83`，從 sector `532480` 開始，大小為 `3620864` sectors。
3. FAT `BPI-BOOT` 與 ext4 `BPI-ROOT` 均以唯讀方式掛載檢查；核心、initramfs、Realtek vendor boot 目錄及必要開機資產存在。
4. 1 GiB 與 2 GiB DTB 均封裝於 vendor boot 目錄，預設 2 GiB DTB 的身分、相容字串、必要與停用節點均符合契約。
5. 最終核心設定 SHA-256 為 `926ff6a7b7d22f32b85bdffd335e84b6c972c25626b8a493960622a056eb0a54`，必要儲存、網路、USB gadget、GPIO、I2C、SPI、顯示、熱管理與看門狗選項均通過。
6. RTL8821CU 以模組方式建立，映像中存在 `kernel/drivers/net/wireless/realtek/rtl8821cu/8821cu.ko`。
7. offset `40960` 的 `u-boot.bin` 大小為 `521968` 位元組，SHA-256 為 `5e91ddf0140820c1f091ac40d8af0daa180bf1e45b851231269e4df7be3e7003`；內含固定字串 `U-Boot 2015.07 (Mar 22 2024 - 01:33:07 +0000)`。
8. 建置提交、來源 tree、當時的 validation、來源契約投影、候選矩陣、完成狀態、驗證狀態與兩份元件清單均已互相綁定；歷史重驗會重新核對實檔、XZ 串流及 IMG 內 U-Boot 載荷。

## 除錯與拒絕紀錄

1. 第一次正式建置在 U-Boot 前因二次 shell 解析遺失 `sed` 引號而停止；修正後以行為測試覆蓋含空白與單引號的路徑。
2. 第一次完整成品因最終 Kconfig 雜湊與來源輸入設定不同而被守門拒絕；契約已分離輸入設定與映像最終設定。
3. 第二次完整成品因 U-Boot 使用當日建置時間而被守門拒絕；共用 Realtek legacy 函式已在函式範圍匯出固定時間，並同時驗證 M4 與 W2 路徑。
4. 第三次正式重建使用上述修正，完整建置耗時約 13 分鐘，所有軟體物質守門通過。

## 狀態邊界

- `candidate_level=L2 內部軟體候選`
- `public_release_allowed=false`
- `hardware_claims_allowed=false`
- `opaque_payload_redistribution_verified=false`
- `toolchain_redistribution_verified=false`
- 板檔維持 `.wip`。
- 尚未執行 1 GiB 與 2 GiB BPI-M4 的冷啟動、SD、eMMC、UART、網路、USB host／gadget、HDMI、GPU、VPU、音訊、Wi-Fi、Bluetooth、PCIe、40-pin、重啟、關機及壓力測試。
- `bluecore.audio`、六個未以固定 MIPS 工具鏈重建的輔助啟動段及內含工具鏈缺少完整逐項再散布授權，因此正式映像只保留為內部驗證用途。
