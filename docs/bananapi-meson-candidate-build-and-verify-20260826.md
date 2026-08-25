# Banana Pi Meson 候選映像建置與驗證

日期：2026-08-26

適用板卡：`bananapim5`、`bananapim2pro`、`bananapicm4io`、`bananapim2s`

## 建置

由獨立工作樹根目錄執行：

```bash
CACHE_LOWER=/media/pi/SMCI/armbian/bpi-v26.2.1/cache \
  tools/run-bananapi-meson-candidates-isolated-cache.sh
```

執行器會把既有快取掛成 OverlayFS 唯讀下層，所有新增或變更寫入本工作樹 `.tmp/bananapi-meson-cache-overlay/upper`。建置結束後自動卸載 `cache`，不會把已套用補丁的舊核心或 U-Boot 工作樹當成新分支來源。

預設建置參數：

```text
BOARD=<四板之一>
BRANCH=current
RELEASE=trixie
BUILD_DESKTOP=no
BUILD_MINIMAL=yes
KERNEL_CONFIGURE=no
EXPERT=yes
ARTIFACT_IGNORE_CACHE=yes
COMPRESS_OUTPUTIMAGE=sha,img
CLEAN_LEVEL=make-kernel,make-uboot
```

預設輸出：

```text
output/images/2026.08/bananapi-meson-trixie-current-cli/
```

每板保存 IMG、XZ、SHA-256、中繼資料、FIP blob 雜湊與建置日誌。建置前會拒絕有追蹤、未追蹤或 `userpatches` 覆寫的來源，FIP 工作樹也必須乾淨；各板的 FIP 清單雜湊必須符合受控設定。既有產物只有在來源提交、Git tree、建置參數、驗證設定、FIP 提交與全部雜湊一致時才可續用。

如需只重跑部分板卡，可明確指定：

```bash
BOARDS="bananapim5 bananapim2pro" \
  tools/run-bananapi-meson-candidates-isolated-cache.sh
```

部分板卡輸出是除錯候選，不代表四板批次完成。正式四板結果必須以預設板卡集合重新執行。

## 唯讀驗證

```bash
tools/verify-bananapi-meson-candidates.sh
```

驗證器會執行：

1. CANDIDATES 矩陣、中繼資料、來源提交與 FIP 提交一致性。
2. IMG／XZ 大小、SHA-256、`xz -t` 與串流解壓同一性。
3. MBR 簽章、Amlogic 開機區非空，以及映像內 U-Boot 套件 payload 與實際寫入區逐位元組比對；只排除 MBR 分割表與簽章的 442 至 511 位元組。
4. 以 `losetup --read-only --partscan` 和 `mount -o ro,noload` 掛載，不執行映像內程式。
5. 核心、initrd、板級 DTB、必要 overlay、Armbian 套件及標準 I/O／無線工具檢查。
6. Meson GPU、VPU、Crypto、USB gadget、MMC、Ethernet、I2C、SPI、PWM 與 Bluetooth 核心設定檢查。
7. 逐板從 `/aliases/mmc1` 定位 eMMC，檢查 8-bit、HS200、最高頻率與 `no-mmc-hs400` 邊界。
8. 檢查 `ondemand` CPU 調速器。

完整通過後寫入 `VERIFICATION.tsv` 與 `VERIFICATION_STATUS.json`，證據等級為 L2。

## 限制

- L2 只證明來源可重現與映像內容符合政策，不證明板卡可開機。
- FIP 含封閉韌體；固定提交與 blob 雜湊不等於驗證其內部行為。
- M5 的 Hynix eMMC、CM4IO 的 Hynix eMMC、關機、USB gadget、GPU、VPU、無線與 40-pin 仍須依計畫完成實機測試。
- M2 Pro 與 M2S 保留原始 200 MHz Linux eMMC 設定，不得由其他板卡的 Hynix 結果推論其需要降速。
- 驗證器需要免互動 `sudo` 進行唯讀 loop 與掛載操作。
