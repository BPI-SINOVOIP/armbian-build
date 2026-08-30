# BPI-R4 Pro 8X 內部 SD 候選 L2 建置證據

## 結論

`bananapir4pro` 已在固定來源提交 `d7b966c9fa615de09d341eb8a8e59a5721ece299` 完成 Trixie minimal CLI 全映像建置，並在雜湊鎖定提交 `bbd8f44099fe712ab04690451b4e23f5bd14cc2b` 通過第二次唯讀 L2 守門。候選僅涵蓋 SD 啟動，不代表 eMMC、SPI-NAND、SPI-NOR、NVMe 或 USB 啟動已支援。

本證據只核准為內部 L2 軟體候選。Linux `6.19.0-rc1` 尚非穩定核心，ATF MT7988 DRAM／eFuse 預編譯物件的逐檔來源與再散布授權未釐清，且尚無 BPI-R4 Pro 8X 實體板證據，因此不得核准公開散布或宣稱硬體功能已驗證。

## 建置輸入

| 項目 | 固定值 |
|---|---|
| Armbian 來源提交 | `d7b966c9fa615de09d341eb8a8e59a5721ece299` |
| Linux 提交 | `20fb2a966dcea69df6987463ae1fe1c67cff36b6` |
| U-Boot 提交 | `34820924edbc4ec7803eb89d9852f4b870fa760a` |
| ATF 提交 | `c34e37802efaea356991a0811c8fc50f8a810f5b` |
| Armbian 韌體提交 | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| mt76 韌體提交 | `c5a3bd91aa735b669618610d5f0ebfa5786845a6` |
| Linux 韌體提交 | `01205307636157a12c29e6a774bf83b218732050` |
| 發行版／分支／設定 | `trixie`／`current`／minimal CLI |

正式 U-Boot 使用 `u-boot-filogic-r4pro` 專用修補目錄，摘要為 2 個修補、2 個套用、0 個問題。Linux 修補摘要為 1 個修補、1 個套用、0 個問題。正式建置記錄位於 `output/images/2026.08/bananapi-filogic-mt7988-r4pro-trixie-current-cli/logs/bananapir4pro.log`，大小 1220546 bytes，SHA-256 為 `8424c31327df0008c1ea6e9a9663b260ab6f9f38d54c5b330bb2001681633fb3`。

## 映像與開機載荷

| 成品 | 大小 | SHA-256 |
|---|---:|---|
| IMG | 1426063360 | `137e7288d940041159242b14c00fac4df834a669d66bd4001a3053d68233ebb9` |
| IMG.XZ | 336759724 | `ab93bb852d48e288b86982a3ce88a9ac580d84ba66b07ac21cf72a9c69e0b80a` |
| DTB | - | `a35e5c81d74d0dcce2174058e87c58287744b273ae895fbb0b9d0eeccb9fac34` |
| `bl2.img` | 250190 | `1ebbdb9380e048e1e736dc9f5e735be906eb7ab13ecc5495226c6d417d60d1de` |
| `u-boot.fip` | 913969 | `96267b3ad65315dabed7543783b5562bfe9911ba98a8d90fcb085682c12e6c51` |
| `gpt` | 17408 | `beb31c2284ec7b8e910faeea8d323f40532b26010e87d0bae851d823705efa1d` |

BL2 位於映像 byte `17408`，FIP 位於 byte `6815744`。GPT 已通過 CRC 檢查，分割區依序為 `bl2`、`ubootenv`、`factory`、`fip` 與根檔案系統；根檔案系統從 sector `32768` 開始。U-Boot 最終設定已停用供應商預設環境、eMMC boot、SPI-NAND、UBI、`BOARD_LATE_INIT` 與 `OF_SYSTEM_SETUP`，並以 `mmc0`、extlinux 及命名冗餘環境啟動。

## 唯讀驗證

第二次驗證狀態如下：

- 來源提交：`d7b966c9fa615de09d341eb8a8e59a5721ece299`
- 驗證器提交：`bbd8f44099fe712ab04690451b4e23f5bd14cc2b`
- 建置時契約 SHA-256：`e4d37e0f02db6dda717354ee86200d8c2c37080a1fb65adeb9b36830b0e8fd43`
- 雜湊鎖定契約 SHA-256：`2ad876f375d8659af913c0e293d443da422cfa7138cbfdf97a84945a04fed3c6`
- U-Boot 載荷證據表 SHA-256：`97aa319d732ac5ccb9343393885b4b5824e99eb14ee3008504ef6d02c0f0ef3e`

驗證器實際檢查 IMG 與 XZ 大小及雜湊、XZ 串流解壓一致性、建置中繼資料、來源提交、GPT、payload 位元與偏移、第五分割區唯讀掛載、extlinux、Linux 來源中繼資料、DTB 身分與板級節點、U-Boot 最終設定、必要套件及固定韌體。結果為 `identity=pass`、`read_only_content=pass`、`evidence_level=L2`。

## 已排除失敗候選

提交 `12d304707` 的預檢映像雖通過內容檢查，但共用 Filogic U-Boot 佇列有 17 個修補問題，因此只保留為拒絕證據。提交 `18d768b9a` 的第一次專用佇列建置確認 2/2 修補零問題，但在 U-Boot 連結階段暴露兩個未實作鉤子，沒有產生候選映像。正式候選停用這兩個鉤子後從乾淨專用 Overlay 上層重建，不沿用失敗產物。

## 後續實機門檻

實體板驗證至少必須涵蓋三次冷開機、三次軟重開機、SD 讀寫壓力、四個 Ethernet MAC／PHY 對應、MT7996 Wi-Fi、PCIe／NVMe、USB、I2C、GPIO、PWM 風扇、SFP、看門狗與長時間網路壓力。完成前不得把 L2 解讀為硬體通過或發布核准。
