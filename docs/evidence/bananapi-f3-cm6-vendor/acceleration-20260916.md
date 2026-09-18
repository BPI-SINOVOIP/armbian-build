# F3／CM6 Noble 加速配套鎖與離線準備證據

日期：2026-09-16。

本輪完成官方加速套件的來源驗證、實際下載、板級 DDK 配對、Noble 相依稽核及候選根系統預檢工具。GPU、AI 與 VPU 均保留「待實機驗證」；套件存在、可安裝或預檢通過不代表硬體加速已運作。

## 配套選擇

| 項目 | F3 | CM6 |
| --- | --- | --- |
| 既有固定核心 | `3e8e7fd730721aee3926a365cef6635221705b61` | `0d0af0d895251383baee939d44e523699e31889f` |
| 核心 PowerVR DDK | `24.2@6603887` | `23.2@6460340` |
| 官方 GPU 套件 | `img-gpu-powervr=24.2-6603887bb8` | `img-gpu-powervr=23.2-6460340bb2` |
| Mesa／GBM／EGL 配套 | `24.01-bb3` | `22.3.5-bb2` |
| GPU 來源快照 | `noble-porting/snapshots/v2.3` | `noble-porting/snapshots/v2.0` |

核心版本直接核對固定提交的 `pvrversion.h`：[F3](https://github.com/jasonmontleon/linux-spacemit/blob/3e8e7fd730721aee3926a365cef6635221705b61/drivers/gpu/drm/img-rogue/pvrversion.h)、[CM6](https://github.com/BPI-SINOVOIP/pi-linux/blob/0d0af0d895251383baee939d44e523699e31889f/drivers/gpu/drm/img-rogue/pvrversion.h)。兩板不能直接共用 GPU 使用者態套件。原廠 CM6 v2.3 映像使用較新的核心與 DDK 24.2；它的 GPU 二進位不能直接搬到本專案既有 CM6 6.6.36 核心。

以下套件兩板共用，均取自官方 Noble v2.3 固定快照：

| 用途 | 固定配套 | 相依或用途限制 |
| --- | --- | --- |
| AI | `onnxruntime`、`python3-spacemit-ort`、`k1-udev-rules`：`1.2.2` | 底層 ONNX Runtime 為 `1.18.1`，SpacemiT EP 為 `1.2.2`；Python `>=3.12~` 且 `<3.13`、`libstdc++6>=13.1`、`libc6>=2.38` |
| VPU | `k1x-vpu-firmware=0.1.0`、`k1x-vpu-test=0.1.0` | Linlon 韌體包含 H.264／HEVC；實際格式與效能待硬體測試 |
| 多媒體框架 | `mpp=0.1.6~bpo2+1`、`libv4l-stmpp=0.1.3` | MPP 安裝 `libspacemit_mpp.so.0.0.15` |
| FFmpeg | `7:6.1.1-3ubuntu5bb8` 與同版八個執行期函式庫 | 官方 `libavcodec60` 明確依賴 MPP；一般 Ubuntu FFmpeg 套件不能代替這項配套證據 |
| GPU 測試 | `k1x-gpu-test=0.1.4` | 需 EGL／GLES／GBM／Wayland／OpenCL 載入器等 Ubuntu 相依套件 |
| GNOME 合成器 | `libmutter-14-0`、`mutter-common`、`mutter-common-bin`、`gir1.2-mutter-14`：`46.0-1ubuntu9bb14` | 保留 SpacemiT KMS `RDMA_ID`、觸控映射及游標修補，其餘桌面套件使用 Ubuntu Noble |

截至本次核對，[Ubuntu Noble 更新庫的 GNOME Shell](https://packages.ubuntu.com/noble-updates/gnome-shell) 為 `46.0-0ubuntu6~24.04.14`，對 Mutter 函式庫與 GIR 的最低要求均為 `46.0`，上述官方 Mutter 滿足其版本條件。這是套件相依結果；實際登入、合成器渲染與多螢幕仍須驗證。

官方 Mutter 的板級修補可在[固定版本 Debian 原始碼補丁包](https://archive.spacemit.com/bianbu/pool/main/m/mutter/mutter_46.0-1ubuntu9bb14.debian.tar.xz) 核對。為保留這些修補，鎖定四個必要 Mutter 套件，另補 `libxau6=1:1.0.11-1`、`libxtst6=2:1.2.5-1` 兩個直接依賴。這兩個小型官方套件合計 `33,898` 位元組，沒有要求替換 Ubuntu libc 或其他系統底層，也沒有匯入完整 Bianbu 桌面。

## 已驗證的來源鏈

套件鎖位於 [`config/spacemit-k1-acceleration/noble.lock.json`](../../../config/spacemit-k1-acceleration/noble.lock.json)。它記錄每個套件的版本、架構、官方路徑、大小、SHA-256、相依、來源索引，以及可取得的套件授權檔 SHA-256。

1. 驗證公鑰來自本機官方 CM6 v2.3 參考映像內的 `bianbu-archive-keyring-noble.gpg`，公鑰檔 SHA-256 為 `b7724569967465b320af924a1399fa6e204f47dc055af70c4cf9d5f06a2363b5`。
2. `gpgv` 成功驗證官方 v2.0、v2.3 `InRelease`；簽署指紋為 `7E4C0796F8ACEF3DBA91315E14ABAB347F260659`。
3. 驗證已簽章的 Release 內容、壓縮及解壓後的套件索引雜湊。
4. 驗證鎖中套件欄位與簽章索引一致，再下載並比對 `.deb` SHA-256 與大小。
5. 使用 `dpkg-deb --field` 再核對真正 `.deb` 的套件名稱、版本、架構、相依及前置相依。

官方索引：[v2.0](https://archive.spacemit.com/bianbu/dists/noble-porting/snapshots/v2.0/InRelease)、[v2.3](https://archive.spacemit.com/bianbu/dists/noble-porting/snapshots/v2.3/InRelease)。未指定快照的舊 `noble-porting/Release` 入口已不可用，工具不依賴它。套件名中的 `+` 在下載網址必須編碼為 `%2B`；MPP 下載已涵蓋此情況。

兩板各 30 個鎖定套件；共同快取共 37 個唯一 `.deb`。F3 配套下載大小為 `61,722,540` 位元組，CM6 為 `57,911,246` 位元組。套件快取位於 `/media/pi/SMCI/bpi/f3-cm6-vendor-20260916/reference/debs/`。此目錄包含兩種 GPU 版本，安裝時必須使用每板的 `prepared.json`，不能對整個快取使用 `*.deb`。

## 已定位的相容邊界

- `img-gpu-powervr` 的 `Architecture: all` 不代表它是跨架構程式；套件包含 RISC-V ELF。本工具仍要求目標為 `riscv64`。
- 兩版 GPU 的 `libpvr_dri_support.so`、`libGLESv2_PVR_MESA.so` 已用 `readelf` 檢查，受查函式庫最高需 `GLIBC_2.34`；Mesa／Python 配套的套件相依提高整體需求至 `libc6>=2.38`，與 Noble 的 libc 世代相容。
- GPU 套件明確停用 Xorg `glamoregl`，並設定 `COGL_DRIVER=gles2`、`GDK_GL=gles`、`SDL_VIDEODRIVER=wayland`、`MESA_LOADER_DRIVER_OVERRIDE=pvr` 等環境。因此新桌面候選採 Wayland；原有 XFCE／Xorg 有畫面不能作為桌面加速證據。
- AI 配套需要核心 `CONFIG_SPACEMIT_TCM`。兩個固定核心的 `drivers/misc/Kconfig` 均提供此選項且預設為 `y`；預檢檢查成品的實際核心設定，並不只信任來源預設值。實機另需檢查 `/dev/tcm` 與 `SpaceMITExecutionProvider` 的算子分配、正確性、延遲及回退比例。
- 官方更新庫較新的 `spacemit-onnxruntime=2.0.3-bpo1` 另需 `libstdc++6>=14`、`spacemit-tcm>=3.0.0`。本候選使用固定 Noble 快照的 `1.2.2` 配套，沒有為追新版本替換整個工具鏈或基礎系統。
- GPU 套件的 `copyright` 檔含尚未填妥的模板欄位，官方 GPU 原始庫又說明僅提供二進位。已保留原始套件及授權檔雜湊供追溯；不能把模板中的標籤推定為全部二進位的完整授權結論。

## 工具操作與驗證

準備工具僅寫入指定快取、套件清單與優先序檔，不安裝套件、不新增套件來源，也不修改候選根系統。

```bash
python3 tools/bpi_k1_acceleration.py prepare \
  --board bpi-f3 \
  --cache /media/pi/SMCI/bpi/f3-cm6-vendor-20260916/reference/debs \
  --output /media/pi/SMCI/bpi/f3-cm6-vendor-20260916/reference/acceleration/bpi-f3

python3 tools/bpi_k1_acceleration.py prepare \
  --board bpi-cm6 \
  --cache /media/pi/SMCI/bpi/f3-cm6-vendor-20260916/reference/debs \
  --output /media/pi/SMCI/bpi/f3-cm6-vendor-20260916/reference/acceleration/bpi-cm6

python3 tools/bpi_k1_acceleration.py preflight \
  --board bpi-cm6 --rootfs /候選根系統掛載點 --stage installed

python3 -m unittest discover -s tests -p 'test_bpi_k1_acceleration.py'
```

兩板 `prepare` 均已成功執行。輸出 `prepared.json` 提供每板套件的絕對路徑、SHA-256、大小與鎖檔身分；`packages.txt` 提供固定版本清單；`bpi-k1-acceleration.pref` 固定這些套件，並禁止從官方 Bianbu 來源任意引入其他套件。基礎相依由候選的 Ubuntu 來源解析，安裝器仍須使用 `apt --no-remove` 檢查完整相依閉包。新配套通過回歸後一併更新核心與版本鎖。

`preflight --stage base` 檢查 Ubuntu／Armbian 身分、板型、RISC-V 架構、核心內實際 DDK 字串與核心功能，另列出待補齊相依。`--stage installed` 加驗所有固定套件版本、相依及反向相依、必要加速檔案與 Wayland 工作階段，任一必要項目不符即回傳狀態碼 `2`。它以候選根目錄解讀絕對符號連結，避免把主機的 `os-release` 誤認為候選內容。

本機 15 項回歸已通過，包含跨板 DDK 誤配、同時存在不相容核心、Bianbu 身分混入、錯誤架構、缺少 VPU 核心功能、缺套件或 Wayland 工作階段、Python ABI 上限、虛擬套件相依、損壞快取、未簽章索引、套件路徑越界及符號連結循環。候選根系統的最終安裝與驗證結果由主封裝流程另外記錄；本文件不將單元測試當成實機證據。

## CM6 真實候選的相依閉包檢查

已在私有掛載命名空間以 `ro,noload` 掛載 `work/cm6-prepared/rootfs.ext4`，原檔沒有寫入。核心實際包含 `CONFIG_SPACEMIT_TCM=y`、`CONFIG_RISCV_ISA_V=y`、`CONFIG_POWERVR_ROGUE=y`、`CONFIG_DRM_SPACEMIT=y`、`CONFIG_VIDEO_LINLON_K1X=y`，核心 DDK 字串為 `23.2@6460340`。以最終 30 包配套執行 `preflight --stage base` 通過，`pending_dependencies` 為空。

另外複製候選的 `/var/lib/dpkg/status`、APT 索引與設定到獨立暫存目錄，使用主機 APT 指定 `APT::Architecture=riscv64` 及該獨立狀態目錄，對全部 30 個已驗證本機 `.deb` 執行 `apt-get -s --no-remove --allow-downgrades install`。回傳碼為 `0`，解析結果為 2 包升版、4 包降版、0 新裝、0 移除，沒有剩餘遞迴缺包。這是從已安裝最初 24 包的候選補齊六包的實際解析結果，非空白根系統的估算。

原始 APT 解析日誌與預檢 JSON 已存於本次交付工作目錄的 `reference/acceleration/cm6-apt-simulate.log`、`reference/acceleration/cm6-base-preflight.json`。最終鎖 SHA-256 為 `5318fc9fd4fbb6073c1a5b631136e3442f1a35efb9243f2ba4d76339cf1a9df6`，兩板 `prepared.json` 已同步。

## F3 真實來源的相依與開機元件檢查

已在私有掛載命名空間以 `ro,noload,offset=4194304` 掛載 `work/f3-armbian-noble.img`，沒有修改來源映像或配套鎖。安裝前預檢通過；實際核心為 DDK `24.2@6603887`，`CONFIG_SPACEMIT_TCM`、`CONFIG_RISCV_ISA_V`、`CONFIG_POWERVR_ROGUE`、`CONFIG_DRM_SPACEMIT` 均為 `y`，`CONFIG_VIDEO_LINLON_K1X=m`。

使用獨立複製的 F3 套件狀態及 APT 索引，對 30 個固定套件與 `gnome-session gnome-shell gdm3` 執行 `apt-get -s --no-remove --no-install-recommends --allow-downgrades install`，回傳碼為 `0`；解析結果為 8 包升版、87 包新裝、4 包降版、0 移除。來源尚未具備的 NumPy、FFmpeg 與 GNOME 相依可由既有 Ubuntu 來源補齊，無須修改加速鎖。

此安裝必須保留 `--no-install-recommends`：若允許推薦套件，GNOME 引入的 `pipewire-audio` 會要求移除既有 `pulseaudio` 與 `pulseaudio-module-bluetooth`，使 `--no-remove` 正確阻斷。此次沒有切換音效服務，也沒有放寬禁止移除的檢查。

| F3 開機元件 | 已查得內容 |
| --- | --- |
| `/boot/Image` | 指向 `vmlinuz-6.18.37-current-spacemit`；內容為 gzip，大小 `18,601,542` 位元組 |
| 解壓後核心 | 原始 RISC-V 核心映像大小 `53,937,664` 位元組 |
| `/boot/uInitrd` | RISC-V U-Boot 傳統 RAMDisk 封裝，大小 `19,872,341` 位元組 |
| initramfs 資料 | `19,872,277` 位元組，與 `uInitrd` 的 64 位元組標頭差額一致 |
| F3 DTB | `k1-bananapi-f3.dtb`，大小 `114,442` 位元組 |

官方格式封裝若不設定 U-Boot 的壓縮核心載入與解壓位址，應在獨立 `bootfs` 產生解壓後的原始 `Image`；`uInitrd` 保留有效封裝，不可將它當成核心一起解壓。這是格式與內容檢查，實際 `sysboot`／`booti` 仍待外部板機驗證。

原始解析日誌與預檢 JSON 位於 `reference/acceleration/f3-apt-simulate.log`、`reference/acceleration/f3-base-preflight.json`。
