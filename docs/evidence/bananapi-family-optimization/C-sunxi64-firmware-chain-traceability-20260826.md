# Sunxi64 啟動韌體鏈可追溯性補強

日期：2026-08-26

## 問題

既有候選映像守門能證明 U-Boot 套件來源與映像中的 payload 相同，但 64 位元 Allwinner payload 另包含 ARM Trusted Firmware 與可選的 Crust SCP 韌體。若只登錄 U-Boot 提交碼，仍無法由已安裝套件反查這兩項韌體的實際來源提交。

## 實作

1. ATF 與 Crust 完成來源同步並切換工作樹後，直接以 `git rev-parse HEAD` 取得實際編譯提交，拒絕非 40 位十六進位 revision。
2. U-Boot 套件的 `u-boot-metadata.sh` 只在對應元件啟用時寫入來源、ref 與 revision；32 位元 Sunxi 板不會增加空欄位。
3. 候選建置器把驗證設定中的 ATF／Crust 固定來源寫入產物中繼資料。
4. 唯讀驗證器同時比對產物中繼資料與映像內已安裝 U-Boot 套件；任一來源欄位缺漏、revision 格式錯誤或實際值不一致都拒絕升級為 L2。

## 證據邊界

這項守門證明產物所宣告的 ATF／Crust Git revision 與編譯套件記錄一致，也由既有 payload 位元組比對證明該 U-Boot 套件載荷已寫入映像。它不等同於從複合 `u-boot-sunxi-with-spl.bin` 反向切出並獨立識別每個內嵌韌體，也不能替代實體板的啟動、待機、喚醒與電源管理驗證。
