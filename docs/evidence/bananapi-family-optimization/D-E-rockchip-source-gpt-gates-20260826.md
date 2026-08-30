# Rockchip 來源、GPT 與啟動載荷守門

日期：2026-08-26

## 目的

R2 Pro、M7、M5 Pro、P2 Pro 與後續 RK3568 板卡的啟動鏈會同時依賴 Linux、U-Boot 與 RKBin 二進位。只保存 blob 雜湊仍不足以證明 U-Boot 套件使用哪一個 RKBin 提交，也無法在 RKBin ref 改變時保證 U-Boot 快取失效。本階段補齊來源、套件、映像與分割表之間的可追溯鏈。

## 已建立的證據鏈

1. Linux 映像套件新增 `armbian-kernel-metadata.sh`，保存 `KERNELSOURCE`、`KERNELBRANCH`、實際 checkout revision 與 `KERNELPATCHDIR`。
2. RKBin 擷取完成後保存實際來源、有效 ref 與 checkout revision，不以板檔文字代替實際工作樹提交。
3. U-Boot 套件新增 `UBOOT_RKBIN_GIT_SOURCE`、`UBOOT_RKBIN_GIT_BRANCH` 與 `UBOOT_RKBIN_GIT_REVISION`。
4. U-Boot artifact 快取輸入新增有效 RKBin URL 與 ref；更換 RKBin 來源或提交時會產生不同快取鍵。
5. 候選映像中繼資料保存 Linux 與 RKBin 的來源、ref、revision，驗證器同時比對映像中繼資料及已安裝套件中繼資料。

## 新增離線守門

- GPT：以 `sgdisk -v` 檢查主表、備份表與 CRC，並以 `sfdisk --json` 檢查表類型及第一分割區名稱。
- 啟動保留區：逐一計算 U-Boot payload 的結束位元組，不允許超過第一分割區起點。
- DTB：可選擇固定完整 SHA-256，並驗證數值屬性、必須停用的節點及 `/aliases` 路徑。
- U-Boot：可驗證封裝的原始 defconfig、最終 target `.config` 選項，以及 target make 中必須出現的 DDR／BL31 路徑。
- 分支：共用候選工具新增受驗證設定控制的 `legacy`，供 BPI-6204 等無 current 板級 DTB 的候選使用。

## 證據限制

上述守門可證明固定來源、套件內容、DTB 結構、分割表與映像載荷彼此一致，但不能證明 DDR 訓練、儲存、交換器、SATA、PCIe、USB、顯示、音訊、GPU、VPU、NPU、無線或排針已在實體板工作。未取得 UART 與實機功能證據的候選最高維持 L2。

既有映像的 `verifier_commit` 仍指向建置當時的舊守門；新來源中繼資料只會出現在本次變更後重新建置的套件，不回寫或改造既有證據。
