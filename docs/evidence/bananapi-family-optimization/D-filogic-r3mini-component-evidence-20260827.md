# Banana Pi BPI-R3 Mini current 候選元件驗證證據

更新日期：2026-08-27

## 結論

本次從指定基線 `87099e8c1fa0c82ae06368ed9c1188fe1d365e21` 建立獨立工作樹，只進行靜態、補丁套用與元件建置。Linux DTB、U-Boot、ATF BL2／BL31 與 FIP 均成功產生；未建置完整映像、未寫入儲存裝置、未執行實體板測試，也未解除發布阻擋。

## 核心 DTB

固定 Linux 提交 `4a4506842b77b597f11e7fc53be1dcdbdc97eea9` 已通過 eMMC 補丁 `git apply --check` 與實際套用，並以 `ARCH=arm64` 只建置板級 DTB。

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| `mediatek/mt7986a-bananapi-bpi-r3-mini.dtb` | 23104 | `5457155de554539c902a22507cbd69ad249fd70a24cf6e24a5753c2b5e8b66ab` |

反編譯檢查確認型號與相容字串正確，`/soc/mmc@11230000` 為 `okay`、8-bit、200 MHz、HS200 1.8V、`non-removable`，並明確停用 HS400。

## U-Boot

固定 U-Boot 提交 `34820924edbc4ec7803eb89d9852f4b870fa760a` 已依 `u-boot-filogic` 系列完整套用補丁，`mt7986a_bpir3mini_emmc_defconfig` 元件建置成功。

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| `u-boot.bin` | 470264 | `cee67b1d090f9d1f3066cd40f11b5d6f122a49bf610e84bf5967ee6e9207afe3` |
| `u-boot.dtb` | 7272 | `f5e02788cb3e6ba87195b3f0f8f7d98762cd4f40d6a3c218ad577154abb0f55c` |

`.config` 已確認 BootSTD、bootflow、extlinux、EXT4、MMC、預設核心 DTB、1 秒自動開機，以及 `ubootenv` 的兩份冗餘環境設定。二進位含 `bootflow`、`extlinux/extlinux.conf`、`fdtfile` 與 `ubootenv`，不要求將完整 DTB 路徑編入二進位字串。U-Boot 自身 DTB 的 `/mmc@11230000` 已確認為 `okay`、8-bit、200 MHz、HS200 1.8V、`non-removable` 與 `no-mmc-hs400`。

## ATF 與 FIP

固定 ATF 提交 `c34e37802efaea356991a0811c8fc50f8a810f5b` 使用 `PLAT=mt7986 BOOT_DEVICE=emmc USE_MKIMAGE=1 DRAM_USE_DDR4=1 HAVE_DRAM_OBJ_FILE=yes` 完成乾淨建置，並由本次 BL31 與 U-Boot 建立 FIP。

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| `bl2.img` | 200793 | `44d4d6b1bdbfdc1f4d2b302047448788f0256b4d68568e9c9dd809005bccedfd` |
| `bl31.bin` | 37553 | `328eedb9b89a8fdf4a137a12a18619cc394429f8f977215807505195ac76ad94` |
| `r3mini-u-boot.fip` | 507953 | `8f56c689f10b3aa2367f4290f940451e8d5b766cd3c0120e6aa2cc398db3ff67` |

`fiptool info` 確認 FIP 含 BL31 與非受信任韌體 BL33。這些雜湊記錄本次稽核輸出，不取代未來完整 Armbian 建置產物的逐檔驗證。

## 證據限制

- 元件建置成功不能證明 eMMC `boot0` 寫入、boot partition enable 或整機冷啟動成功。
- 核心與 U-Boot 均保守停用未實測的 HS400；200 MHz HS200 仍須實體板壓力測試。
- EN8811H 與 MT76 韌體只完成來源、雜湊、驅動需求與安裝規則檢查，沒有網路硬體證據。
- ATF 的 MT7986 預編譯 DRAM 物件再散布範圍未釐清，公開發布維持阻擋。
- 本文件不是 L2 完整映像證據，也不是 L3 實機證據。

## 後續校準關係

2026-08-28 首次固定時間戳完整映像校準產生不同的 BL2、FIP 與最終 U-Boot 位元組身分。差異來自完整 Armbian 封裝流程與固定建置時間，不表示本文件的元件建置紀錄失效。本文件保留為提交 `717cdc7e91231a16d80b189f43dc6819a80fd739` 的歷史 L1 元件證據；正式 L2 契約改以 `H-filogic-r3mini-L2-calibration-20260828.md` 的校準值為準，仍須從新契約提交乾淨重建後才能成立。
