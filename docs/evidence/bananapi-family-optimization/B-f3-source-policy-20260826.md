# Banana Pi F3 current 來源與候選守門政策

日期：2026-08-26

## 結論

`bananapif3` current 原先使用固定標籤的 U-Boot 與 OpenSBI，但 Linux 仍追蹤可移動分支，且板級設定未宣告既有 `k1-*.dtbo` 的 overlay 前綴。本階段將 U-Boot、OpenSBI 與 Linux 全部固定到不可變提交，補上標準 overlay 設定與開發工具，建立可重現的 F3 軟體候選基線。

此政策只支援 L2 唯讀軟體驗證，不能取代實體板的冷啟動、儲存、網路、無線、顯示、相機、音訊或 40-pin 迴路測試。

## 固定來源

| 項目 | 來源 | 提交 |
| --- | --- | --- |
| U-Boot | `https://github.com/pyavitz/spacemit-u-boot.git` | `d61c8c77e241314438dce31d9ff1b1cbd9d53688` |
| OpenSBI | `https://github.com/pyavitz/spacemit-opensbi.git` | `05479f5228f3fab2a4221fe0745f3703171ace58` |
| Linux | `https://github.com/jmontleon/linux-bianbu.git` | `3e8e7fd730721aee3926a365cef6635221705b61` |

U-Boot 與 OpenSBI 提交分別是 `k1-bl-v2.2.9-release` 標籤解析後的提交；Linux 提交是 2026-08-26 解析 `linux-6.18.y` 所得。固定只由 `post_family_config_branch_current` hook 套用，不改變 legacy 或 edge 的來源政策。

## 不透明韌體

`packages/blobs/riscv64/spacemit/esos.elf` 是 F3 核心與 BSP 套件使用的 RISC-V ELF 韌體，大小為 562152 bytes，SHA-256 為：

```text
3b3ef5ba9b404c6500bfc0f7f1efc0cb7fdde818450b7beddac1c00f29898537
```

此檔案納入逐位元組證據，但僅有雜湊不能證明其來源碼、授權、功能或實機行為。

現有檔案可追溯到 SpacemiT `buildroot-ext` 提交 `65994600db55ec0db7a70a138f63a10785a3e7a1` 的舊版韌體；上游目前檔案雜湊已不同。本階段不在缺少 `remoteproc`、休眠及壓力實機測試時升級它。

## 啟動映像布局

F3 的 SD／eMMC 主映像使用下列四段 payload：

| 套件 payload | 映像偏移 |
| --- | ---: |
| `bootinfo_emmc.bin` | 0 bytes |
| `FSBL.bin` | 512 bytes |
| `fw_dynamic.itb` | 655360 bytes |
| `u-boot.itb` | 1048576 bytes |

`bootinfo_spinor.bin` 與 `u-boot-env-default.bin` 仍由 U-Boot 套件提供，但不直接寫入本次 SD／eMMC 主映像，因此不能假裝成主映像內的連續區段。`bootinfo_emmc.bin` 的正常大小只有 80 bytes，驗證器必須依套件 MD5 與映像偏移逐位元組比對，不能用任意 32 KiB 下限拒絕。

SpacemiT 原始碼另有 `bootinfo_sd.bin` 與不同的 FSBL 位置，但現行 Armbian 及既有映像對 SD 使用上述 eMMC 格式。本階段維持已知布局並明確取證，不在沒有 BootROM 與實機證據時擅自切換格式。

## F3 L2 軟體門檻

- Linux 6.18 `spacemit` 核心、F3 DTB、initramfs 與板級套件完整封裝。
- SD 4-bit、SDIO 4-bit 與 eMMC 8-bit 裝置樹設定符合受控 DTB。
- 雙 GbE、GPU、HDMI、PCIe、USB host、UART、I2C、QSPI、PWM 與溫度節點處於啟用狀態。
- SpacemiT DRM、VPU、HDMI、NVMe、RTL8852BS、Bluetooth、USB gadget mass-storage、音訊、GPIO、I2C 與 SPI 核心能力已建置。
- `k1` overlay 前綴與 I2C、SPI、UART overlay 檔案已納入映像；F3 目前使用 `extlinux.conf`，未預設套用這些 overlay。
- GPIO、I2C、SPI、V4L2、PCIe、NVMe、USB、音訊、網路與無線診斷工具已安裝。
- IMG／XZ、來源提交、設定雜湊、四段 U-Boot payload、OpenSBI、Linux 與 `esos.elf` 證據通過守門。
- 六個 U-Boot 套件 payload 均產生大小與 SHA-256 清單；其中四個另與主映像偏移逐位元組比對。

## 不可由 L2 推論

- SD、eMMC、QSPI 或 USB 救援路徑已在實機成功啟動或燒錄。
- 2 GiB／4 GiB 記憶體均通過冷啟動、壓力與溫度邊界。
- 雙 GbE、Wi-Fi、Bluetooth、USB、HDMI、音訊、相機或 PCIe 已可用。
- GPU、VPU 或其他加速器已完成硬體加速；核心設定與節點存在不是執行證據。
- USB gadget 或 `g_mass_storage` 已在 Type-C 連線上通過主機辨識與資料完整性驗證。
- 40-pin overlay 已完成實體腳位、電壓域、衝突及外接迴路驗證。
- 此候選已具備量產 secure boot 信任鏈；FSBL 的 `secure=0` 設定不能支持此推論。
