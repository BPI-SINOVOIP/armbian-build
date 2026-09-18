# Titan 分割表匹配錯誤的官方程式重現

日期：2026-09-18。結論：舊包把 `multi_flash.relate_partition` 改成固定的 `partition_universal.json`，使 Titan 一般流程無法匹配。已直接執行官方後端重現相同的 `Code 20014`；改用 `partition_{size1}.json` 後，新版兩個實包均通過官方匹配與燒錄操作建立。這次驗證沒有操作 USB、eMMC、UART，也沒有執行任何燒錄操作。

## 使用者證據與判定範圍

- [失敗畫面](user-report-20260918/titan-flash-error-code-20014.jpg) 顯示固定檔名、未匹配分割表與 `Code 20014`。
- [選檔參考](user-report-20260918/titan-selected-package.jpg) 只證明使用者提供的正確操作／選檔方式，不能記為另一次失敗或成功燒錄。
- 使用者當時的 Titan 版本未知。本次確認的是已取得的官方 `2.2.0-Rc` 與本機既有後端；不能把這兩者直接認定為使用者當時執行的版本。

## 固定來源

官方入口為 [SpacemiT 燒錄工具使用手冊](https://www.spacemit.com/community/document/info?lang=zh&nodepath=tools/user_guide/flasher_user_guide.md)，下載使用[官方 Linux AppImage 連結](https://cloud.spacemit.com/prod-api/release/download/tools?token=titantools_for_linux_64BIT_APPIMAGE)。此連結會更新，重現時必須核對雜湊。完整下載資訊保存於 [titan-source.json](../../../evidence/titan-matcher-20260918/titan-source.json)。

| 元件 | 本次識別 | SHA-256 |
| --- | --- | --- |
| 官方 AppImage | `titantools_for_linux-2.2.0-Rc.AppImage`；95,987,984 位元組；套件版本 `2.2.0-Rc`／建置 `0001` | `0bcd63c138813fbeb3234358efd01eb50c3b91c93cc47a52addee53b76eba5e6` |
| 官方內附後端 | `resources/app/flashserver`；8,790,592 位元組；Python `3.8.10` | `adde4756c863c9575170c36555bd501c694e241d3d385d9e8c98e9adcd417716` |
| 本機既有後端 | `/home/pi/.flash_temp/flashserver`；版本未知 | `01b510fc2601d064ea1653a7620639363609d1d2c90208cb4474cd6f5f77833a` |

原廠二進位、解包內容與完整反組譯輸出保留於 `/media/pi/SMCI/bpi/f3-cm6-vendor-20260916/reference/titan-20260918/`，不納入 Git。倉庫只保留自行撰寫的抽取／執行工具、雜湊、最小結果與本說明。

## 原因與對照結果

官方 `flashboot.source.actions.MultiFlash._check_partition()` 有兩條不同路徑：

1. 有明確 `part_config` 時，直接使用指定清單。官方入口把分割表參數傳入此欄位，因此命令列 `-part` 的成功案例不能證明一般自動匹配也接受固定檔名。
2. 一般流程逐項解析 `relate_partition` 的 `{...}` 變數。若檔名沒有變數，函式立即返回 `False`，尚未檢查該檔案是否存在。`build_multi_flash()` 隨後看到空清單，拋出官方 `FlashNotMatchError`，錯誤碼為 `20014`。

以下是直接執行官方函式所得，沒有重寫匹配演算法：

| 輸入 | 結果 |
| --- | --- |
| 固定 `partition_universal.json`，檔案確實存在 | 重現 `Code 20014` 與 `partition table is null` |
| `partition_{size1}.json`，`size1=universal` | 選到 `partition_universal.json` |
| 原廠雙變數清單，`size0=NULL`、`size1=universal` | 選到 `partition_universal.json` |
| 固定檔名加明確 `part_config` | 選到指定分割表 |
| 僅保留 `{size1}`，另有 `size0=2M` | 仍只選區塊裝置分割表 |
| 僅保留 `{size1}`，`size1=NULL` | 仍以 `Code 20014` 阻擋 |

官方新版與本機既有後端均通過上述六個對照，結果分別為 [官方新版結果](../../../evidence/titan-matcher-20260918/official-2.2.0-rc-results.json) 與 [本機後端結果](../../../evidence/titan-matcher-20260918/local-backend-results.json)。其中 `official_logs`、`error_text` 是必要的不可變程式輸出，保留原文供逐字比對使用者畫面，周邊說明以繁體中文撰寫。

這能分離本次匹配失敗的直接原因：即使檔案存在且媒體變數有效，固定檔名仍會失敗；不需要把缺少 `genimage.cfg` 或 SPI 分割表當作這個錯誤的必要條件。

## 新版實包的官方操作建立

對 `20260918-rc2` 的 F3、CM6 實際 `payload/fastboot.yaml` 與 `partition_universal.json`，另外匯入完整官方 `MultiFlash` 類別，呼叫 `build_multi_flash()`。這條路徑實際執行 `convert_2_flash_action()`、讀取分割表及各項 `validate()`，沒有替換轉換函式。六個最小匹配對照只停在轉換交接點，與這個完整實包檢查明確區分。

兩包均建立 `gpt`、`bootinfo`、`fsbl`、`env`、`opensbi`、`uboot`、`bootfs`、`rootfs` 共八項，引用檔案全部存在；`bootfs`、`rootfs` 的 `gzip_level` 都是 `5`。同時保留 `blk-size` 的 `size1` 變數流程，候選清單只使用 `partition_{size1}.json`。

| 成品 | ZIP SHA-256 | 官方重播證據 |
| --- | --- | --- |
| `Armbian_Noble_bpi-f3_gnome_titan-emmc_20260918-rc2.zip` | `865fc0cd05b0ee87257c3e637246e127b95b383d47e1befa61dc588699c99b9b` | [F3 結果](../../../evidence/titan-matcher-20260918/f3-rc2-official-results.json) |
| `Armbian_Noble_bpi-cm6_gnome_titan-emmc_20260918-rc2.zip` | `f62d471ef88bf042af84bbd7da6aa4e031159488a4447d49a4b3e0cbc4595277` | [CM6 結果](../../../evidence/titan-matcher-20260918/cm6-rc2-official-results.json) |

兩份 ZIP 均重新計算完整雜湊；控制檔與六個小載荷從 ZIP 讀出後，逐位元核對實際重播目錄，各操作檔案大小亦核對 ZIP 成員。兩包的 `fastboot.yaml` SHA-256 是 `f85c8017934e887d700c7e963e2acc1667c38270925b7c52b35371209666a335`，分割表是 `9e3fc864903994ce0d45208d8edbb081de060386ffccc7ec7ae37f38b3b26f89`。CM6 新包另以本機既有後端完成相同檢查，見 [交叉重播結果](../../../evidence/titan-matcher-20260918/cm6-rc2-local-results.json)。

實際 `bootinfo` 操作引用原廠 `factory/bootinfo_sd.bin`，80 位元組，SHA-256 為 `f339e3e576ce94d7812c2887622c5882ae12ac3c9a98059aef37741850a43cb6`。eMMC 寫入語意來自[固定提交的官方 U-Boot 程式](https://github.com/spacemit-com/uboot-2022.10/blob/db67f9dc4d26a45a29e3be085eb5b783aae8e965/drivers/fastboot/fb_mmc.c)：`flash bootinfo` 進入特別處理，重建 eMMC header 並寫入硬體分區 1 的偏移 0，即 `boot0`。因此不能只因目標是 eMMC 就替換 universal 表的來源 header；更多媒體選擇限制見[格式配套說明](../../../config/spacemit-k1-vendor/README.md)。

## 重現方式

從倉庫根目錄執行。需要本機 `gcc`、`unsquashfs`、`bwrap`、Python 3；不需要安裝另一套 Python，執行器載入官方包內的 Python 3.8 函式庫。以下命令只抽取官方包並執行離線診斷，沒有啟動 AppImage 或 `flashserver` 入口。

```bash
titan_ref=/media/pi/SMCI/bpi/f3-cm6-vendor-20260916/reference/titan-20260918
titan_scripts=evidence/titan-matcher-20260918
mkdir -p "$titan_ref"
curl -fL 'https://cloud.spacemit.com/prod-api/release/download/tools?token=titantools_for_linux_64BIT_APPIMAGE' \
  -o "$titan_ref/titantools_for_linux-2.2.0-Rc.AppImage"
printf '%s  %s\n' \
  0bcd63c138813fbeb3234358efd01eb50c3b91c93cc47a52addee53b76eba5e6 \
  "$titan_ref/titantools_for_linux-2.2.0-Rc.AppImage" | sha256sum -c -
unsquashfs -offset 188392 -d "$titan_ref/titan-2.2.0" \
  "$titan_ref/titantools_for_linux-2.2.0-Rc.AppImage" \
  resources/app/flashserver resources/app/package.json
python3 "$titan_scripts/extract_pyinstaller.py" \
  "$titan_ref/titan-2.2.0/resources/app/flashserver" "$titan_ref/official-flashserver"
gcc -O2 "$titan_scripts/python_embed.c" -ldl -o "$titan_ref/python_embed"
bash "$titan_scripts/run_matcher.sh" \
  "$titan_ref/official-flashserver" "$titan_ref/python_embed" \
  config/spacemit-k1-vendor \
  /media/pi/SMCI/bpi/f3-cm6-vendor-20260918-rc2/output/bpi-cm6-emmc/payload
```

最後一個參數可換成 F3 的 `payload` 目錄；省略時只跑六個最小對照。若下載連結已更新而雜湊不符，應使用既存相同雜湊的參考包，或另立新版證據，不能沿用本次版本識別。`run_matcher.sh` 以 `bwrap --unshare-all` 隔離網路，使用新的 `/dev`，主機檔案系統唯讀；所有結果透過標準輸出回傳。嵌入式 Python 可能在標準錯誤輸出提示預設函式庫位置，但腳本已明確設定官方封裝路徑，通過與否以結束碼及 JSON 的斷言結果判定。

本次驗證未涵蓋 GUI 匯入互動、BootROM／USB 傳輸、eMMC 寫後回讀、冷開機、桌面或加速器實測。新版仍為待實機驗證的候選；只排除這次已重現的分割表匹配問題與操作建立階段的缺檔問題。
