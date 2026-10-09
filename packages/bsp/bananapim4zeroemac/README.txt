Banana Pi H618 標準 I/O 工具

用途
  bpi-h618-hw-info 列出板型、核心、I/O 節點與覆蓋層設定。
  命令不需 root，不請求 GPIO 線路，不掃描匯流排，不修改裝置。
  裝置缺失、工具未安裝或權限不足時，命令顯示資訊限制。
  回傳 0 只代表資訊列印完成，不代表硬體通過驗證。
  參數錯誤回傳 2；--help 與 -h 顯示用法。

可沿用的標準工具
  libgpiod 的 gpiodetect 與 gpioinfo 可查詢控制器及線路資訊。
  本命令只在已有 gpiodetect 時呼叫它，不自動安裝任何套件。
  i2c-tools 的 i2cdetect -l 僅列出介面，可另行手動使用。
  不執行 i2cdetect 位址掃描、gpioget、gpioset、SPI 傳輸或 UART 收發。
  gpioget 也會請求線路，不能視為不干擾腳位的資訊查詢。
  GPIO 編號不是排針編號；不得假設 gpiochip 的編號固定。

GPIO 權限邊界
  99-bananapi-h618-io.rules 僅匹配 sun50i-h616-pinctrl 主控制器。
  H618 沿用此驅動名稱；未知驅動與外接 GPIO 控制器不匹配。
  匹配裝置使用 gpio 群組及 0660 權限；不授權 users 或所有使用者。
  不新增群組成員，不調整 R_PIO、I2C、SPI、UART 或 PWM 權限。
  gpio 群組可控制整顆主控制器，並非唯讀群組或逐腳位存取清單。
  僅由管理員明確加入可信任使用者，不得預設開放所有登入使用者。
  EMAC 使用 PA0-PA9，參考時脈使用 PA12；不得請求或重新設定。
  核心驅動與裝置樹須維持腳位占用；不得卸載驅動或更換覆蓋層來繞過。
  udev 權限無法保證逐腳位隔離。需要此保證時，不得授予 gpio 群組權限。

整合契約
  僅由 bananapim4zeroemac 板型設定選用本目錄，不作為全域規則。
  本目錄位於框架既有的板級 BSP 雜湊範圍；檔案變更會更新套件識別。
  將命令以 0755 安裝至 /usr/bin/bpi-h618-hw-info。
  將規則以 0644 安裝至 /etc/udev/rules.d/99-bananapi-h618-io.rules。
  BSP postinst 的 configure 階段確認 gpio 系統群組存在，適用新裝與升級。
  若群組不存在則建立；不指定固定 GID 或自動加入成員，失敗須中止套件設定。
  本目錄不提供安裝器、服務、排程或開機控制，也不修改板型設定。
  覆蓋層只讀取 overlays 與 user_overlays，不執行設定檔。

未移植內容
  bpi-h618-io-compat-install 會下載、編譯與安裝 WiringPi、RPi.GPIO。
  此流程超出標準 I/O 範圍，故不移植。
  舊規則對 users 開放 I/O，並遞迴調整 PWM 權限，故不沿用。
  舊資訊命令的圖形、影音、核心日誌及完整開機命令列輸出不保留。

驗證範圍
  本次只做本機語法、格式與靜態規則檢查。
  未執行板端命令、udev 重載、映像重建或硬體驗證。
  實際裝置匹配、群組生效與 EMAC 腳位占用仍需另行驗證。
