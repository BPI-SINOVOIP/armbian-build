# Banana Pi F2P 元件編譯證據

## 範圍

本次只驗證固定 BSP 提交內的 F2P U-Boot、Linux 核心、DTB 與模組可以編譯。沒有建立完整 rootfs 映像，沒有寫入任何儲存裝置，也沒有進行實機測試。

## 環境

| 項目 | 值 |
| --- | --- |
| 主機 | Ubuntu 22.04.5 LTS，x86_64 |
| 主機核心 | Linux 6.8.0-136-generic |
| BSP 提交 | `3eee97bd8fb7582c2d9942a533647c3d78222bb5` |
| BSP 提交日期 | 2020-12-27T21:13:58+08:00 |
| 交叉編譯器 | `arm-linux-gnueabihf-gcc`，Linaro GCC 7.3-2018.05 |
| Armbian 基準 | `3f2cd8493b00be096c004278f8a67269e1b93867` |

原始碼在本工作樹的 gitignored `.tmp/bananapi-sunplus-f2p-component/source` 內獨立檢出；沒有修改主工作樹或共用快取下層。

## 執行流程

```bash
git apply patch/u-boot/u-boot-sunplus-sp7021-bpi-legacy/0001-scripts-dtc-remove-duplicate-yylloc-definition.patch
./configure bpi-f2p
make -C u-boot-sp sp7021_bpi_f2p_defconfig CROSS_COMPILE=<固定工具鏈前綴>
make -C u-boot-sp -j20 all CROSS_COMPILE=<固定工具鏈前綴>
make -C linux-sp ARCH=arm sp7021_chipC_bpi-f2p_defconfig CROSS_COMPILE=<固定工具鏈前綴>
make -C linux-sp ARCH=arm -j10 uImage dtbs modules CROSS_COMPILE=<固定工具鏈前綴>
```

## 結果

| 元件 | 產物 | 大小 | SHA-256 | 結果 |
| --- | --- | ---: | --- | --- |
| U-Boot | `u-boot-sp/u-boot.img` | 432,487 | `0936db799ddc212c40c2ac8f13239972637ed412beb161302ccb752e92bd4a09` | 通過 |
| U-Boot DTB | `u-boot-sp/arch/arm/dts/sp7021-bpi-f2p.dtb` | 20,639 | `f77611c2753bc3e1424f6e656d867e23383499a44d206fee8a57be3c5ca69c9f` | 通過 |
| Linux | `linux-sp/arch/arm/boot/uImage` | 5,078,848 | `56b555a066ad4dfb67ce56e6a168aba7c081b80abee5744f9f0aa47d0f4bd0a4` | 通過 |
| Linux DTB | `linux-sp/arch/arm/boot/dts/sp7021-bpi-f2p.dtb` | 20,851 | `85405ed87704f984aa72b4c83e56b14b0190909517352d89d4a22e3047b1ef24` | 通過 |
| Linux 模組 | 11 個 `.ko` | 不適用 | 本次未建立整包雜湊 | 通過 |

可重跑工具把建置時間固定為 BSP 提交時間。上表雜湊仍只作為本次執行證據；在完成第二主機重建比對前，不宣稱跨主機位元重現。兩個 DTB 與預建資產雜湊已納入機器可讀契約。

## 編譯警告與限制

- U-Boot 多次回報 `CONFIG_BOOTDELAY` 重複定義，最終仍完成連結與映像封裝。
- Linux defconfig 回報顯示模式選項重複指定，核心與模組仍完成建置。
- 外部主機使用較新 OpenSSL，核心主機工具出現已棄用 API 警告。
- `dtc` 對舊 DTS 回報多項結構警告；DTB 產生成功不代表其描述符合目前 schema。
- U-Boot 二進位可找到 `SP7021/CA7/BPI-F2P`、F2P DTB 身分、SD 與 eMMC 控制器字串。
- 沒有執行 `make pack`、`make linux`、Armbian `compile.sh` 或任何燒錄命令。

## 證據等級

本結果只支持「固定來源的 F2P 元件可編譯」。它不支持下列結論：

- `ISPBOOOT.BIN` 已獲授權或已在 F2P 驗證。
- SD 或 eMMC 可開機。
- Trixie rootfs 可正常運作。
- GPIO、I2C、SPI、TPM、網路、USB、顯示或攝影機介面已通過。
- 候選可以對外發布。
