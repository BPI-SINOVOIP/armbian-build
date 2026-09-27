# CM6 標準板名配套

此配方只由 `BOARD=bananapicm6 BRANCH=legacy` 啟用，保留標準磁碟格式。官方 SD／eMMC 別名繼續使用原配方；本增量不更動該路徑。

## 建置與範圍

```sh
./compile.sh build BOARD=bananapicm6 BRANCH=legacy RELEASE=jammy BUILD_MINIMAL=yes BUILD_DESKTOP=no KERNEL_CONFIGURE=no
./compile.sh build BOARD=bananapicm6 BRANCH=legacy RELEASE=trixie BUILD_MINIMAL=no BUILD_DESKTOP=yes DESKTOP_ENVIRONMENT=xfce DESKTOP_TIER=mid KERNEL_CONFIGURE=no
```

Jammy／Noble／Trixie 各 minimal／XFCE 為原矩陣中的六個可建置候選；建置成功與實機驗收分開記錄。Noble GNOME 官方封裝代表另計。Resolute 不可由替換發行版湊數：K1 是 RVA22，而 Ubuntu 26.04 官方使用者空間要求 RVA23，配方會明確拒絕。

- [CM6 官方硬體規格](https://www.banana-pi.com/en/core-board-and-kit/197.html)
- [Ubuntu 26.04 指令集要求](https://documentation.ubuntu.com/release-notes/26.04/summary-for-lts-users/)
- [SpacemiT 對 K1 與 Ubuntu 26.04 的說明](https://forum.spacemit.com/t/topic/939)

標準板名套用既有 eth0 GPIO45 PHY 重設修正及藍牙首次偏好。首次偏好只在新映像沒有既有藍牙設定時預置，不於每次開機強制解除使用者封鎖。GPIO 沿固定 CM6 來源：WiringPi `da58b589a3ca3e44f569850f07ee17de2e294b5f`、RPi.GPIO `c04d27c86f65ed824921a457455a09d6820b9e1d`。

## 各發行版套件

Noble 沿既有已鎖交叉建置，使用 Python 3.12。標準 hook 直接以 `bananapicm6`、`camera=none` 呼叫共用 `bpi_k1_host_dependencies.py`：框架準備來源鎖列出的五個精確工具鏈套件及一般建置工具，資產建置前再核對版本與 BT 編譯器 SHA。此路徑目前要求 Ubuntu Noble amd64 主機或同版本容器，不需啟用官方格式 extension。主機相依收據為建置工作目錄中的 `host-dependencies.json`；共用 helper 及其 registry 亦納入快取指紋。Jammy／Trixie 在各自全新 rootfs 內，利用簽章 APT 的 `build-essential`、`python3-dev`、`libcrypt-dev` 重建三套 DEB。來源下載、SHA、解壓及補丁在主機完成；目標階段使用自身 `sysconfig`、Python 3.10／3.13、libc／libcrypt，明確指定 `-march=rv64gc -mabi=lp64d`。相依由目標 `dpkg-shlibdeps` 產生，不使用 Noble 二進位、不以降低 Depends 偽裝相容、不使用 pip。

RPi.GPIO 的新補丁只保留 Python 3.9 以前的舊執行緒初始化呼叫，避免使用 Python 3.13 已移除的 API；不改接腳方向、電平或 CM6 板型資料。建置不執行 GPIO、I2C、SPI、PWM 或板端探測。

APT 計畫拒絕既有套件升降級及移除，保存精確版本、DEB SHA、簽章索引 SHA 與命令收據。安裝成品後，僅移除本輪新增建置相依；移除模擬若牽涉原套件或成品執行期相依，會停止封裝。原套件版本及 manual／auto 標記保留。預設 APT 索引仍隨建置日期變動；收據提供可追溯性，尚不代表跨日期位元組相同。需要固定時點重現時，須再提供相同版本仍可取得的公開簽章快照，不能依賴私密快取。

APT 移除計畫可能省略架構，而 `dpkg-query` 的 `binary:Package` 可能包含 `:riscv64`。移除守門依本次已安裝套件的名稱與架構解析唯一身分，再核對是否屬於新增建置相依；未知、架構歧義、原有套件、成品套件或任何安裝／升降級均拒絕。真實計畫與名稱映射保存在 `builddeps_cleanup`；守門拒絕時會先將實際計畫留在建置日誌，不執行移除。此處只修正名稱比對，不放寬套件保護範圍。

GPIO／藍牙套件各保存來源、補丁、builder／lock SHA、實際工具鏈、ELF、DEB 與安裝檔 SHA。藍牙上游鎖中的交叉編譯器欄位保留為原配方追溯；Jammy／Trixie 的實際工具鏈以本次標準建置 manifest 為準。結果保存在映像的 `/usr/share/bpi-cm6-standard/integration.json`；完整命令與 DEB 收據保留於建置工作目錄。

## 圖形與驗收限制

官方 GPU SDK／七個 Mesa 配套目前僅鎖定 Noble。Jammy／Trixie 不安裝這組 Noble DEB，不宣稱已具備相同硬體加速；Noble XFCE 的配套安裝亦仍需實機桌面驗收。完整 GPU／影音門檻由獨立的 Noble GNOME 代表驗證承擔。

標準 Noble XFCE 在核對官方 Xorg 載荷 SHA 後，只將 `00-noglamoregl.conf` 的 `Accelmethod none` 改為 `glamor`、`Disable glamoregl` 改為 `Load glamoregl`。LightDM 會清除 Xserver 的一般繼承環境，因此新增 `20-bpi-cm6-pvr.conf`，透過 `xserver-command=/usr/bin/env MESA_LOADER_DRIVER_OVERRIDE=pvr /usr/lib/xorg/Xorg -core` 明確傳入 PVR；不使用 systemd 環境變數補丁。修改前核對完整 LightDM 設定，原指令須為唯一官方 `X -core`，其他啟動命令覆寫或非 XFCE 工作階段會拒絕。

傳遞方式依據 LightDM 固定來源中的[清空環境](https://github.com/ubuntu/lightdm/blob/170df31f7d9c00b049bdd158682472df915adeae/src/x-server-local.c#L453)與[保留啟動引數](https://github.com/ubuntu/lightdm/blob/170df31f7d9c00b049bdd158682472df915adeae/src/x-server-local.c#L325)；不依賴私密控制系統。

原官方 DEB／payload manifest 保留原 SHA，兩份設定改動另記 `source_adaptations` 的修改前後 SHA，新增檔明記原先不存在；此修正不更動核心、DEB 鎖或官方 GNOME／vendor／其他發行版。Xorg glamor、真實 X11 EGL、GLX 與畫面須分別驗證，不能以 GBM 成功代替 X11，也不宣稱 GLX 已硬體加速。新版成品仍待重建與實機核對。

PVR 配套提供的 OpenGL ES 不代表 GLX 桌面 OpenGL 支援；全域 PVR 選擇保留，以維持 EGL 硬體路徑。遇到 GLX 程式的 `BadValue`，可只對該程式使用 `env -u MESA_LOADER_DRIVER_OVERRIDE <程式>`，作為軟體相容啟動方式；同一環境下 EGL 亦可能改用軟體繪圖，不能將此方式記為 GPU 加速通過。

此來源增量仍須由標準 compile.sh 在實際 rootfs 階段建出成品，核對 dpkg／ABI／安裝清單，再進行逐份開機、正常登入、媒體及回救援驗證。離線守門、成功建 DEB 或 QEMU 執行均不可算實機通過。
