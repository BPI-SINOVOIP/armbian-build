# CM6 雙 IMX415 模式與停止候選

此來源建置只用於 `bpi-cm6` 的 `dual-imx415` 配置。它依序套用兩個固定補丁：修正官方 `k1x-cam 0.2.34` 的兩處感測器模式參數，以及讓雙路自動／手動停止共用先關閉兩路 VI 的 helper。從固定來源重編 `libsdkcam.so`，產生 `k1x-cam 0.2.34+cm6.2`。不修改感測器寄存器表、`libcam_sensors.so`、閉源 `k1x-cam-lib 0.1.8` 或核心。

衍生 DEB 只改動 `usr/lib/libsdkcam.so`、新增 `usr/share/camera_json/cm6-dual-imx415-mode2.json` 與 `usr/share/doc/k1x-cam/cm6-source.json`；封裝控制欄位另外更新版本及檔案校驗表。既有原生 rootfs 整合仍會停用相機服務的自動啟動及維持受控裝置權限。

mode2 配置使用 sensor0／sensor2，輸入 RAW 3840×2160、10 位元，輸出兩路 1920×1080 NV12；單次目標 300 幀，第 150 幀存檔。沒有新增開機負載或桌面自動啟動。

## 可攜輸入

`source-lock.json` 固定官方來源、兩個上游相機 DEB、Ubuntu 交叉工具鏈／標準 sysroot、目標圖形標頭及鏈結函式庫的網址、容量與 SHA-256。標頭和函式庫由 DEB 解出，不讀取主機 `/usr/include`，也不引用既有私人 rootfs。

目前建置主機範圍為 Armbian Noble 的 amd64 環境。工具鏈解到每次建置的私有目錄，以相對工具根及明確 `--sysroot` 使用；主機正常執行依賴的最低版本列在 `toolchain.host_requirements`，實際版本保存至建置紀錄。其他主機架構尚未加入對應的固定交叉工具鏈資產。

來源鎖不冒稱具備新增的 APT 簽章驗證；Ubuntu 套件欄位取自既有官方 APT 索引，SpacemiT 套件沿用既有固定官方來源與內容校驗。每次下載均核對容量與 SHA-256；快取缺件、多件、符號連結或內容不符均拒絕。

原生 `compile.sh` 的 CM6 相機資產階段自動取得同一組輸入並建包，快取位於倉庫下的 `cache/bpi-cm6-camera-debs`。不需要填寫這台建置伺服器的絕對路徑。獨立檢查可使用：

```sh
python3 tools/build_bpi_cm6_camera.py fetch --inputs cache/bpi-cm6-camera-debs
python3 tools/build_bpi_cm6_camera.py check --inputs cache/bpi-cm6-camera-debs
```

明確建置命令如下；輸出目錄必須不存在：

```sh
python3 tools/build_bpi_cm6_camera.py build --inputs cache/bpi-cm6-camera-debs --output output/cm6-camera-source-build
```

建置過程核對補丁前後來源、編譯器實際讀取的標頭範圍、ELF 相依及匯出介面、最小 DEB 載荷差異，並保存命令、來源鎖、補丁、輸入與產物雜湊。建置成功僅代表來源與封裝契約成立，不能代替串流、停止或跨主機重建驗證。

## 尚未解除的限制

停止順序修補屬於必要的串流收尾候選，不保證所有感測器或串流組合都能消除停止逾時及延遲IRQ警告。新成品須依自己的核心、相機套件與實機結果判定，不能以其他版本的測試代替。

共用 helper 同時覆蓋自動與手動 `c/C` 停止，保持單路原碼、核心500 ms等待與原回傳語意。套件記錄 `hardware_validation=pending`、`streamoff_fixed=false`，另以 `stop_order_patch_applied=true` 表示已套修補。`cam-test`退出碼零不能代替核心日誌、關閉事件與重啟串流核對。
