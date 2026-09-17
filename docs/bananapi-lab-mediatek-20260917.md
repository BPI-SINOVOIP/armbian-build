# C3 MediaTek 原配組件

API：`tools.bpi_lab_mediatek.prepare(read_file, *, board='bpi-r3', kernel_release, output)`，
以及 `validate_template(manifest, *, template, artifact_root)`。
共用行為見 [原配契約](bananapi-lab-extlinux-20260917.md)。

`bpi-r2` 實作 MT7623 專用 `boot-mt7623.cmd`，核對 ARM32 zImage／uInitrd／原配 DTB。
腳本預設為 `/boot/dtb/mt7623n-bananapi-bpi-r2.dtb`，但 `env import` 的 `fdtfile` 會覆寫它。
接受原配 R2 名稱及已知 `mediatek/` 位置，包括板級產生的無副檔名值；均依原值讀取並驗證實際 DTB 身分，
不自動補 `.dtb`、刪除子目錄或改載預設檔。`checks.dtb_selection` 記錄選擇來源與未改寫的路徑。
原腳本固定 `console=ttyS2,115200n1`、`ext4load`，並讀 `rootfs` 而不是 `rootfstype`；
若建置器產生後者，只接受與有效 `rootfs` 等值的重複資訊，不靜默忽略相異值。
原環境須明示 root UUID，不能用當下 MMC 分割號猜測。未知 extraargs／overlay 設定阻擋。

`bpi-r64`、`bpi-r3`、`bpi-r3mini`、`bpi-r4`、`bpi-r4lite`、`bpi-r4pro` 六板採 extlinux。
這些板明示 `SRC_EXTLINUX=yes`，因此不要求本機不存在的 `boot-filogic.cmd`。
逐板核對 DTB、版本、initrd、APPEND、label、overlay 與根 UUID；不互換 MT7622／MT7986／MT7987／MT7988。

本輪不寫 GPT、BL2、FIP、eMMC boot0 或前置載入器。
MT7623 來源已記錄的主線 U-Boot eMMC 早期卡住限制未修復；SD 原配解析成功不代表 eMMC 可執行。
Filogic 的 `bootflow` 功能宣告不等於實板 U-Boot、DRAM、簽章或救援入口已核定。

測試：`tests/test_bpi_lab_mediatek.py`，涵蓋七板與 MT7623 特有命令列、DTB 位置及不支援欄位。

## R2 原映像問題

`allboard-bpi-r2-003/preparation.json` 綁定的 extraction SHA-256 為
`8f09a8dd8e8e98154bfeee519665c36957d53fb41b0d9e7e20e043cce54b09c4`。
核心實際完整版本是 `6.6.153-current-mt7623`，原環境的
`fdtfile=mediatek/mt7623n-bananapi-bpi-r2` 來自板級宣告，原腳本不會自動修補此字串。

2026-09-18 [新目錄重播](../output/evidence/bpi-c3-r2-dtb-replay-20260918-001/preparation.json)
解除先前過嚴的名稱拒絕，但仍 `blocked`／`reader_failed`：快照沒有
`/boot/dtb/mediatek/mt7623n-bananapi-bpi-r2` 的內容，也沒有查詢紀錄證明其不存在。
不能從這份快照斷言原映像缺檔，更不能挪用另一份 DTB 證明成功。

主代理後續從同一原 XZ 完整擷取至
[R2-004](../output/evidence/bpi-multiboard-integrate-20260917-allboard-bpi-r2-004/preparation.json)，
已確認該精確路徑不存在，結果為 `blocked`／`missing_file`，不是 parser 尚未實作或快照缺漏。
來源 SHA-256 為 `38a736cbc41c21cb7969e18d0f7954a5ea217af70f347beadb0883ef6e2d58e5`，
新 extraction SHA-256 為 `aa5f8b065ee20e178bfed723f85e589b283ba984feb94bdb57b309fcae30c274`。
工具依原環境忠實讀取，因此這是原環境／打包不一致的軟體問題，不能用待實板核定代稱。
R2 原腳本與完整 DTB 測資的正例仍用於驗證工具，不修改原圖、不替換其 DTB，也不解除真來源阻擋。
本代理只讀取主代理的新證據，沒有另行解壓、修改映像、硬體操作、提交或推送。
