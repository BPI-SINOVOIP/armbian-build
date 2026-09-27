# CM6 的 RPi.GPIO 套件

此配方使用固定的 BPI-SINOVOIP/RPi.GPIO 提交，交叉編譯 Noble Python 3.12 的 RISC-V 64 位元擴充套件。來源及官方標頭套件的雜湊記於 `source-lock.json`；不使用原倉現成 ELF，也不呼叫 pip。

```sh
python3 tools/build_bpi_cm6_rpi_gpio.py build --cache /path/to/cache --output /path/to/output
```

主機須已有 `riscv64-linux-gnu-gcc`、對應 libc 開發檔、`readelf`、`dpkg-deb` 與 Python 3。交叉工具鏈套件版本亦在來源鎖固定；不同版本會停止，須另經驗證後更新鎖。建置不安裝主機套件。每次輸出包含 dpkg 套件、完整命令與 `package-manifest.json`，記錄來源、架構、編譯器、相依及各安裝檔雜湊。

`RPi.GPIO` 的 `VERSION` 保留上游 API 版本 `0.7.1`；套件版本由 dpkg 與套件清單識別。板型來自既有系統板型檔或實際裝置樹，不注入假板型。

本輪允許的板端檢查僅為 `import RPi.GPIO`、`GPIO.VERSION` 與 `GPIO.RPI_INFO` 的 `TYPE`、`PROCESSOR`。純匯入只讀識別檔，硬體映射延後到 GPIO 操作。不可把匯入通過宣稱為腳位電氣驗證；不執行 `setup`、`output`、`gpio_function`、PWM 或事件操作。`RPI_INFO` 的其他舊欄位沿用上游靜態資料，不能當作實機容量或板卡修訂量測。
