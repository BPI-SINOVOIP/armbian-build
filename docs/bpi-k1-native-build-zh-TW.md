# CM6／F3 官方格式的 Armbian 標準建置

此配方保留一般 Armbian 板名，另以獨立BOARD產生官方SD及Titan eMMC格式。CM6代表配置為Ubuntu 24.04 Noble、GNOME與固定官方BSP配套；版本識別預設20260927-rc12。這是公開候選來源，建置成功與實機通過分開判定。

## 取得來源與建置

公開倉為 `https://github.com/BPI-SINOVOIP/armbian-build.git`，專用整合分支 `bpi-cm6-standard-20260927`。採用Ubuntu 24.04 amd64建置主機；準備足夠磁碟空間、記憶體及網路。先安裝Git與Python基本工具，其餘固定主機編譯相依由標準建置階段準備及核對。

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends git python3 python3-yaml
git clone --single-branch --branch bpi-cm6-standard-20260927 \
  https://github.com/BPI-SINOVOIP/armbian-build.git armbian-cm6
cd armbian-cm6
git rev-parse HEAD
sudo ./compile.sh build BOARD=bananapicm6-titan-emmc \
  EXPERT=yes PREFER_DOCKER=no CPUTHREADS=8 \
  SHARE_LOG=no UPLOAD_TO_OCI_ONLY=no ARTIFACT_IGNORE_CACHE=yes
```

上述命令在新來源目錄重建核心、框架U-Boot及Armbian根系統；沒有 `DONT_BUILD_ARTIFACTS`、既成rootfs或私人覆層。建置前記錄完整提交SHA；重現指定版本時先 `git checkout --detach <完整提交SHA>`。普通Ubuntu套件從當次已簽章的公開APT索引解析，實際版本由成品紀錄保存；不宣稱未固定APT快照時全鏡像位元一致。

SD採相同標準入口，只換BOARD：

```bash
sudo ./compile.sh build BOARD=bananapicm6-vendor-sd \
  EXPERT=yes PREFER_DOCKER=no CPUTHREADS=8 \
  SHARE_LOG=no UPLOAD_TO_OCI_ONLY=no ARTIFACT_IGNORE_CACHE=yes
```

| 板名 | 產物 | 與一般Armbian的關係 |
| --- | --- | --- |
| `bananapicm6-titan-emmc` | CM6 Titan分割載荷ZIP | 獨立官方eMMC格式 |
| `bananapicm6-vendor-sd` | CM6官方SD磁碟IMG／ZIP | 獨立官方SD格式 |
| `bananapif3-titan-emmc` | F3 Titan分割載荷ZIP | 保留來源支援，本輪CM6驗收不代替F3實測 |
| `bananapif3-vendor-sd` | F3官方SD磁碟IMG／ZIP | 同上 |
| `bananapicm6`／`bananapif3` | 一般Armbian映像 | 不因官方別名而強制改為Titan格式 |

官方別名固定Noble／GNOME／相應核心分支及媒體，錯誤參數會拒絕。此設定不能拿來生成原四發行版minimal／XFCE矩陣；標準BOARD矩陣另依自身發行版、ABI及配套支援處理。`CARD_DEVICE`不被官方格式入口接受，建置不會直接燒錄媒體。

## 來源與二進位邊界

CM6 Linux固定 `BPI-SINOVOIP/pi-linux` 提交 `0d0af0d895251383baee939d44e523699e31889f`，套用板級網路與相機必要補丁後編譯。框架U-Boot固定 `BPI-SINOVOIP/pi-u-boot` 的 `066cccd77f35e57d13363fea524a439759196dca`；OpenSBI固定 `05479f5228f3fab2a4221fe0745f3703171ace58`。

最終官方格式中的FSBL、DDR、OpenSBI／U-Boot等啟動載荷仍取自 `config/spacemit-k1-vendor/sources.lock.json` 指定的公開官方封裝，逐件核對SHA／CRC，不能把框架編譯結果冒稱最終全部啟動載荷的來源。官方U-Boot版本包含未提交修改標示，完整對應來源仍有限制。來源鎖未提供整包SHA的欄位不作整包驗證聲明。

GPU／VPU等使用者空間依 `config/spacemit-k1-acceleration/noble.lock.json` 的固定套件、索引簽章與SHA；閉源SDK與韌體保留原來源及授權，不能宣稱全部從原始碼重建。相機依固定來源重編libsdkcam，其他官方感測器庫與閉源配套保留；來源、補丁和已知停止限制見 `config/spacemit-k1-camera/README.md`。相機固定HTTPS+SHA與完整APT索引簽章鏈屬不同保證，尚未補齊者如來源鎖所示。

CM6 GPIO由固定來源建DEB並納入共同rootfs，沒有板上手裝或pip覆寫：WiringPi `da58b589a3ca3e44f569850f07ee17de2e294b5f`，RPi.GPIO `c04d27c86f65ed824921a457455a09d6820b9e1d`。builder、lock、archive、補丁順序與實際SHA及DEB內容必須一致，舊產物不能重標新來源。Noble版Python ABI為3.12；其他發行版不得直接套用此二進位。

## 產物與核對

官方成品位於 `output/vendor-format/<版本識別>/<BOARD>/<建置UUID>/`，分為 `sd/` 或 `emmc/`；各有ZIP、manifest、格式核對結果及 `evidence/` 的來源快照／套件與建置紀錄。對照manifest的檔名、大小、SHA、root／boot UUID及成品媒體，不能只憑副檔名判定可燒錄裝置。

Titan eMMC封裝由官方工具使用；SD IMG寫入相應SD。一般Armbian MBR映像與官方GPT／eMMC boot區不相同，不能直接互換寫入。任何燒錄前均確認實際目標與可恢復備份；此倉不包含實驗工作站控制系統。

首次開機完成正常帳號設定。GPIO最小只讀核對可用：

```bash
dpkg-query -W bpi-cm6-wiringpi python3-bpi-cm6-gpio
gpio -warranty
gpio -V
python3 -I -B -c 'import RPi.GPIO as G, RPi._GPIO as C; print(G.VERSION, G.RPI_INFO, G.__file__, C.__file__, G.getmode())'
```

`gpio -V`是板型代碼，CM6為102；`-warranty`提供編譯版本。上述命令不操作接腳，不能代替電氣測試。

## 驗證範圍

本輪公開代表需對自己的成品重新驗證正常開機／登入／媒體、原生畫面、單路相機30FPS、網路、BT掃描、雙聲道、擴容、GPU／桌面、VPU／影音、USB基本讀寫、GPIO軟體及APT基本更新。實際結果另記，不沿用先前版本的PASS。AI、雙相機、長測、HDMI擷取色偏與首登異常情境不在本輪門檻，GPIO不操作腳位。SD載入eMMC只證明該路徑，不能當無SD獨立啟動或Titan USB實燒通過。

建置追溯檔以 `${SOURCE_ROOT}`、`${BUILD_ROOT}` 等穩定標記表示來源與暫存根目錄，避免成品帶入工作站私有路徑。各標記意義隨 manifest 記錄；來源、builder、固定鎖、補丁與套件 SHA 仍保留，可核對實際建置內容。這些標記只用於紀錄，不會改變編譯命令的實際執行位置。
