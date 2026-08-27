# Banana Pi M4 Super 元件產物聲明撤回記錄

日期：2026-08-27

## 修正結論

先前文件列出的 Linux DTB、DTBO、U-Boot 與啟動載荷沒有連同可攜檔案、完整建置記錄、工具鏈資訊及重建清單保存在本工作樹，也無法從本工作樹獨立核對。因此全部大小、雜湊、可重現建置及元件通過聲明已撤回，不得引用為 M4 Super 證據。

目前機器契約固定為：

- `evidence_level=L0`
- `donor_hardware_equivalence_verified=false`
- `component_build_completed=false`
- `full_image_built=false`
- `hardware_validated=false`
- `public_release_allowed=false`

## 現存可核對內容

本工作樹只保留固定來源提交、ArmSoM Sige3 donor 的 DTB 與 U-Boot defconfig 參考身分、官方硬體頁面，以及已知硬體矛盾清單。這些內容只能證明研究輸入受到版本控制，不能證明任何 M4 Super 專屬元件存在。

下列項目不存在於目前證據集合：

1. M4 Super 專屬 Linux DTB 或 DTBO。
2. M4 Super 專屬 U-Boot DTS、defconfig、`u-boot.itb` 或 `idbloader.img`。
3. M4 Super 完整 Armbian 映像或壓縮檔。
4. 可攜建置日誌、最終設定、工具鏈鎖定資料與雙次重建結果。
5. SD、eMMC、PCIe、USB、乙太網路、無線、GPU、VPU、NPU、顯示或 40-pin 實物測試記錄。

## 後續證據規則

未來若重新建立元件證據，產物必須放在可追溯位置，並同時保存來源提交、補丁集合、最終設定、工具鏈版本、建置命令、完整日誌、產物清單與由檔案當場計算的雜湊。文件不得先填入無法由現存檔案驗證的大小或雜湊。

只有完成 M4 Super 與 Sige3 原理圖及 PCB 差異比對，並建立真正的 M4 Super 板級描述後，才可開始評估較高證據層級。donor 直接建置結果不得改名後當成 M4 Super 元件或映像。
