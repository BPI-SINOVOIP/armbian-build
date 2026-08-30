# Banana Pi W3 RK3588 vendor 候選映像 L2 建置證據

更新日期：2026-08-27

## 結論

`bananapiw3` 已使用專用 OverlayFS 隔離快取，由固定來源完整建置 Debian Trixie vendor minimal CLI。正式映像已通過共用唯讀驗證器；GPT 開機配置、Banana Pi 專屬 DTB 與 U-Boot 身分、RKBin 授權副本、核心設定及診斷工具契約均符合 L2 守門。

本結果不代表實體板已開機。核心是 Rockchip 6.1 vendor 基線，DDR、BL31 與 RockUSB loader 使用固定 RKBin 二進位；尚未有冷啟動、儲存、網路、無線、顯示或硬體加速實測，因此板檔維持 `.wip`，最高只可標示為 L2 軟體證據。

## 兩次映像邊界

| 項目 | 被拒絕的預檢 | 正式候選 |
| --- | --- | --- |
| 來源提交 | `84d36840acc0177a145f99c12f264ecf70362c68` | `3f2cd8493b00be096c004278f8a67269e1b93867` |
| 用途 | 只證明完整建置鏈可執行 | L2 唯讀守門候選 |
| Armbian firmware | 建置時由可移動的 `master` 解析 | 固定 `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| U-Boot 修補摘要 | 兩個套用，其中一個 `needs_rebase` | 兩個套用、零問題 |
| 核心修補摘要 | 兩個套用、零問題 | 兩個套用、零問題 |

預檢映像保存在 `output/images/2026.08/bananapi-rockchip-rk3588-w3-trixie-vendor-cli-preflight-moving-firmware-84d36840a/`，不得改名或當成正式候選。正式映像由全新 OverlayFS 重建，不是替換預檢映像的 bootloader。

## 正式建置身分

| 項目 | 值 |
| --- | --- |
| 板卡 | `bananapiw3` |
| 發行版 | Debian 13 `trixie` |
| 核心目標 | `vendor` |
| 映像型態 | minimal CLI |
| 映像來源提交 | `3f2cd8493b00be096c004278f8a67269e1b93867` |
| Linux | `6.1.115`，提交 `c6157104418d012823413c02f9222f3fe123dd25` |
| U-Boot | `2017.09`，提交 `39cd993e5d6296635438e84f4576b3a9bf76f86e` |
| RKBin | 提交 `1d3c61008fa823936ae7a59615393f8294b64456` |
| Armbian firmware | 提交 `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| Docker 執行時間 | 1566 秒；整體約 26 分鐘 |
| 輸出目錄 | `output/images/2026.08/bananapi-rockchip-rk3588-w3-trixie-vendor-cli/` |

U-Boot、核心、DTB、套件、根檔案系統、IMG 與 XZ 均在正式建置重新產生。建置紀錄明確顯示 firmware 以 `commit:f50a2a21bcdb77a562b3976930c5c6b521a1df08` 解析與封裝。

## 正式驗證結果

| 項目 | 值 |
| --- | --- |
| 驗證器提交 | `87206fe7ad9545850325b521aee3da60d5dc481c` |
| 建置時驗證契約 SHA-256 | `f4a0876e719b086d738c80fbee62cc6ace3196f228dae7f8365f0a24f8e08e06` |
| 正式驗證契約 SHA-256 | `d4fbb20fb135b1aa12e60f8dd4716038488662bbe05bb2bfcdb96ddea0a97690` |
| U-Boot 載荷清單 SHA-256 | `354b3d8047b9e561774687289371b1aa4b51c06c1e5c7e403a36f6f5350ba038` |
| RKBin 證據清單 SHA-256 | `c8b5c7d2c9ab264e11da23216ec888b4c04af15b1ea7e7d922aceee8548be101` |
| 驗證層級 | L2 |

驗證器已完成 XZ 串流檢查與解壓後 IMG 同一性、GPT、分割區起點、映像內 U-Boot 位元組配置、套件中繼資料、固定來源提交、DTB 結構、核心設定、必要套件、RKBin 授權副本及唯讀掛載檢查。機器可讀結果位於正式輸出目錄的 `VERIFICATION_STATUS.json`、`VERIFICATION.tsv` 與 `UBOOT_PAYLOAD_EVIDENCE.tsv`。

## 映像產物

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| IMG | 1874853888 bytes | `5988a850ac47b57d529ce5f476f9d4a4718e4a53e5f740990cad05b4ebc184c9` |
| XZ | 353573700 bytes | `1181b45f908dc157e7990f0bd8647c4bb5cd216ea30d8e635942bac1ca67e16b` |

XZ 的串流完整性與解壓後 IMG 同一性由正式驗證器負責確認。映像使用 GPT，根分割區由 sector 32768 開始；開機載荷配置為 `idbloader.img` 位於 32768 bytes、`u-boot.itb` 位於 8388608 bytes。

## DTB 與 U-Boot

映像內 `rockchip/rk3588-bananapi-w3.dtb` 的 SHA-256 為 `61b32e043b4da6265cefc6dc6e457968aa525aee3d69ba17b96189efcc3140d2`。唯讀解析結果如下：

- model：`Banana Pi W3`
- compatible：`bananapi,bpi-w3`、`armsom,w3`、`armsom,lm7`、`rockchip,rk3588`
- `armbianEnv.txt`：`fdtfile=rockchip/rk3588-bananapi-w3.dtb`
- overlay prefix：`rockchip-rk3588`

U-Boot 套件載荷如下：

| 載荷 | 大小 | SHA-256 |
| --- | ---: | --- |
| `idbloader.img` | 317440 bytes | `3d79ac31ca7f7c1fd0ffe91c59cc831693dd1ea6dfa0b076e942759491b60ef7` |
| `u-boot.itb` | 1443328 bytes | `a9777968f263dffb2a64a726540020a13fe395529e4498542c8b81fe95ec4404` |

`u-boot.itb` 包含 `Banana Pi W3` 與 `bananapi,bpi-w3`，沒有被禁止的 ArmSoM model 字串。正式驗證器還必須比對套件 MD5、實際映像偏移、大小、SHA-256 與逐位元內容，不能只檢查字串。

## RKBin 與核心能力

映像安裝的 `rkbin.LICENSE.TXT` SHA-256 為 `0b37e1522c36cf4579c45dfb138798c3cb5665fcf6302b95377179fbed38e35c`，與固定 RKBin 提交的授權檔一致。DDR、BL31 與 RockUSB loader 的固定雜湊由 `RKBIN_EVIDENCE.tsv` 保存；對外散布必須遵守僅隨 Rockchip 平台、不得修改二進位、不得獨立散布並附帶授權副本的條件。

映像內核心設定已包含 `CONFIG_MALI_BIFROST=y`、`CONFIG_ROCKCHIP_MPP_RKVDEC2=y`、`CONFIG_ROCKCHIP_RKNPU=y`、`CONFIG_SPI_SPIDEV=y`、`CONFIG_USB_CONFIGFS_MASS_STORAGE=y` 與 `CONFIG_BRCMFMAC=m`。這只能證明軟體功能被納入，不能證明 GPU、VPU、NPU、SPI、USB gadget 或 Wi-Fi 已在 W3 實機正常運作。

## L3 實機門檻

- 以 UART 保存多次冷啟動、重新啟動與斷電重啟記錄，確認 DDR、SPL、BL31、U-Boot、initramfs 與 Linux 啟動鏈。
- 分別驗證 SD、eMMC、NVMe、SPI-NOR、雙 2.5GbE、USB host 與 Type-C OTG。
- 驗證 AP6256 Wi-Fi、Bluetooth、HDMI 輸出、DisplayPort、HDMI 輸入、音訊、RTC 與風扇控制。
- 依正式 40-pin pin map 驗證 GPIO、I2C、SPI、PWM、電壓域與功能衝突。
- 執行 CPU、記憶體、儲存、網路及 GPU／VPU 混合壓力，保存溫度、節流、重置與 I/O 錯誤。
