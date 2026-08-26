# Banana Pi BPI-CM6 legacy 候選來源政策

更新日期：2026-08-27

## 階段結論

`bananapicm6` 已完成建置前的固定來源、SpacemiT K1 啟動鏈、板級 DT、核心功能、韌體追溯與 L2 驗證契約。板卡仍保留 `.wip`；本階段只證明政策與回歸測試，證據等級維持 L0，不得宣稱可開機或正式發布。

## 固定來源

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| Linux | `https://github.com/BPI-SINOVOIP/pi-linux.git` | `0d0af0d895251383baee939d44e523699e31889f` |
| U-Boot | `https://github.com/BPI-SINOVOIP/pi-u-boot.git` | `066cccd77f35e57d13363fea524a439759196dca` |
| OpenSBI | `https://github.com/pyavitz/spacemit-opensbi.git` | `05479f5228f3fab2a4221fe0745f3703171ace58` |

2026-08-27 重新查核時，兩個 BPI 官方分支 HEAD 仍分別等於上述 Linux 與 U-Boot 提交；OpenSBI `k1-bl-v2.2.9-release` 標籤解參照後亦等於上述提交。板檔改用 `commit:`，避免未來分支移動造成相同建置命令取得不同來源。

Linux 是 BPI CM6 的供應商 `6.6.36` 基線，並非目前上游長期支援分支的最新修正版。L2 只能建立可重現候選，後續仍須制定安全更新與向較新核心移植的維護政策。

## 啟動與分割政策

- U-Boot 固定 `k1_defconfig`，封裝 `bootinfo_emmc.bin`、`bootinfo_spinor.bin`、`FSBL.bin`、`fw_dynamic.itb`、`u-boot.itb` 與 `u-boot-env-default.bin`。
- SD 整碟映像把 `bootinfo_emmc.bin` 寫在 offset 0、`FSBL.bin` 寫在 512、`fw_dynamic.itb` 寫在 655360、`u-boot.itb` 寫在 1048576 bytes。
- 第一個根檔案系統分割區固定由 sector 8192 開始，分割表明確設為 MBR／`msdos`，避免 U-Boot 套件中繼資料誤記成家族預設 GPT。
- U-Boot 必須包含 `/extlinux/extlinux.conf`、`/boot/extlinux/extlinux.conf`、K1-X 自動開機環境及 `product_name=k1-x_deb1`。
- `extlinux.conf` 必須選取 CM6 專用的 `/boot/dtb/spacemit/k1-x_bpi_cm6.dtb`。

SpacemiT 家族的 eMMC 寫入函式會把前兩段載荷寫到 eMMC `boot0`，其行為與 SD 整碟映像的 user area 原始偏移不同。原廠參考映像另採多分割區 GPT。這三種佈局不得互相混稱；本候選只驗證 SD 映像內容，eMMC 安裝與原廠升級相容性必須以實機另行封閉。

## 板級與功能政策

專用核心補丁建立繼承供應商 `k1-x_deb1.dts` 的 `k1-x_bpi_cm6.dtb`，把板級身分改為 `BananaPi BPI-CM6`，加入 `bananapi,bpi-cm6` 相容字串，並移除繼承的 `debug loglevel=8` 與 `rdinit=/init`。實際核心命令列只由 Armbian extlinux 政策提供；共用 deb1 DTB 不會被候選修改。

核心契約涵蓋 SD、SDIO、8-bit HS400 eMMC、雙 GbE、PCIe／NVMe、USB host、USB gadget mass storage、HDMI、IMG GPU、Linlon 視訊、ES8326 音訊、RTL8852BS、Bluetooth、GPIO、I2C、SPI、PWM fan、熱感測、watchdog、遠端處理器與硬體加密。根檔案系統加入 GPIO、I2C、SPI、PCIe、NVMe、USB、音訊、無線網路與乙太網路的標準診斷工具。

`esos.elf` 是不可由本倉庫來源重新建置的預編譯遠端處理器韌體。候選固定其 SHA-256，並安裝 SpacemiT 授權原文及繁體中文來源追溯；雜湊一致不代表其內部行為可審計，對外發布仍須完成授權合規確認。

## 建置與升級門檻

完整建置命令：

```bash
./tools/run-bananapi-spacemit-cm6-candidate-isolated-cache.sh
```

唯讀映像驗證命令：

```bash
./tools/verify-bananapi-spacemit-cm6-candidate.sh
```

只有完整建置成功，且 IMG／XZ 同一性、MBR、六項 U-Boot 載荷、固定來源、extlinux、DTB、核心設定、韌體、授權文件、套件與唯讀掛載內容全部通過後，才可升為 L2。L3 仍需實體板 UART、多次冷啟動、SD、eMMC `boot0` 安裝、雙網路、USB、PCIe／NVMe、HDMI、音訊、Wi-Fi、Bluetooth、40-pin I/O、重新啟動、關機與長時間壓力證據。
