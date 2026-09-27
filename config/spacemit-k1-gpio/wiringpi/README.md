# CM6 WiringPi 套件

固定來源為 `BPI-SINOVOIP/BPI-WiringPi2` 的 `da58b589a3ca3e44f569850f07ee17de2e294b5f`；來源封存與交叉連結相依由 `source-lock.json` 的網址、容量與 SHA-256 鎖定。必要小修只讓 BPI 的 `gpio -v` 提前結束記憶體能力探測，避免版本查詢使用 Raspberry Pi 的 GPIO 位址。另補上 `libwiringPiDev` 明確動態連結相依，避免依賴呼叫端的載入順序。原 CM6 腳位對應未修改。

使用 `python3 tools/build_bpi_cm6_wiringpi.py build --cache PATH --output PATH` 建置。主機須有 `riscv64-linux-gnu-gcc`、`riscv64-linux-gnu-strip`、`riscv64-linux-gnu-readelf`、`make`、`patch` 與 `dpkg-deb`。`libcrypt` 固定相依只解包到私有建置目錄；不安裝主機、不使用來源庫內預編譯 ELF。

套件提供 `gpio`、`libwiringPi.so.3`、`libwiringPiDev.so.3`、開發標頭、手冊及原授權。CLI 權限為 `0755`，不設 SUID。`package-manifest.json` 記錄來源、補丁、工具鏈、相依、成品 SHA 與每個安裝檔案；原始編譯輸出保存在 `build.log`。二進位連結最低相依為 Noble 可提供的 `libc6` 與 `libcrypt1`，不捆入另一份系統函式庫。

本輪代表板驗證命令僅 `gpio -v` 與 `gpio -R`。`readall` 會先初始化 GPIO，因此排除。本輪不設定方向、電平、上下拉、I2C、SPI 或 PWM，通過此驗證不能宣稱接腳電氣功能已實測。
