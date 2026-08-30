# Banana Pi BPI-Forge1 vendor 候選映像 L2 建置證據

更新日期：2026-08-27

## 結論

`bananapiforge1` 已使用專用 OverlayFS 隔離快取，由固定來源完整建置 Debian Trixie vendor minimal CLI，並通過 Rockchip 專用 L2 唯讀守門。IMG／XZ 同一性、GPT、U-Boot 載荷、固定 Linux／U-Boot／RKBin 來源、Banana Pi 專用 DTB、開機腳本、核心設定、RKBin 授權與標準 I/O 工具均符合本次受控政策。

此結果不代表實體板已開機。核心是 Rockchip 6.1 vendor 基線，U-Boot 是 `2026.04-rc1`，DDR 與 TEE 使用預編譯 RKBin，板級 DTS 仍繼承 ArmSoM Forge1 硬體描述；在原理圖與實機證據封閉這些邊界前，板卡維持 `.wip` 且最高為 L2。

## 建置身分

| 項目 | 值 |
| --- | --- |
| 板卡 | `bananapiforge1` |
| 發行版 | Debian 13 `trixie` |
| 核心目標 | `vendor` |
| 映像型態 | minimal CLI |
| 映像來源提交 | `1befcacaf8d1c121f61b603ba57209d316181af7` |
| 驗證器提交 | `01e59fc2955687f17e4074fda4db7cc41ec70eda` |
| 建置時驗證政策 SHA-256 | `8f152dc9e00977ef3206a13957c967b76f46f5cbd9fc01cadee00683d76979d4` |
| 最終驗證政策 SHA-256 | `eb21444759c973e8dc2e66da5e215a06493070df3a45375aee2934e41cfaa8b4` |
| Linux | `6.1.115`，來源提交 `c6157104418d012823413c02f9222f3fe123dd25` |
| U-Boot | `2026.04-rc1`，來源提交 `a72ec1294fc6ba6b0bfd5ebc912a7bed2dc2513d` |
| RKBin | 來源提交 `1d3c61008fa823936ae7a59615393f8294b64456` |
| 完整建置時間 | 12 分 09 秒；Docker 執行 736 秒 |
| 輸出目錄 | `output/images/2026.08/bananapi-rockchip-rk3506-forge1-trixie-vendor-cli/` |

U-Boot、核心、DTB、套件、根檔案系統、IMG 與 XZ 均在本次完整建置重新產生；共用快取只作唯讀下層，不是將既有映像改名或只替換 bootloader。

## 映像與 GPT

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| IMG | 1337982976 | `66ee65f1be6752b16bb5397cc9f44108dcdd5e0cb5e89b4b1a3c03f4bf4b9cf2` |
| XZ | 302744280 | `54c1ae74fa3f0ca31b0f1672120102486ebe11fb54948262b4e1717ac5538751` |

XZ 通過完整串流解壓，解壓後大小與 SHA-256 均和 IMG 相同。GPT 通過 `sgdisk -v`；唯一 `rootfs` 分割區由 sector 32768 開始，到 sector 2611199 結束，前方保留 16 MiB 給啟動鏈。

## U-Boot 與 RKBin

| 載荷 | 位元組偏移 | 大小 | SHA-256 |
| --- | ---: | ---: | --- |
| `u-boot-rockchip.bin` | 32768 | 9072216 | `c5edc729b7ea9ca8719ef0179ec6f4cc474527863b3819bb2c540263e81b41d2` |

守門從映像內安裝的 U-Boot 套件取出載荷，驗證套件 MD5、大小與 SHA-256，再逐位元比對整碟映像 offset 32768。載荷沒有跨入 sector 32768 的根分割區。U-Boot 設定確認 RK3506J、固定 DDR／TEE blob、MMC、USB、USB mass storage、Btrfs、雙 Ethernet、1500000 baud、Banana Pi U-Boot DT 及 Linux DTB 路徑；載荷證據清單 SHA-256 為 `66687d067d303f41e5e140345221984746d364d0b17b0bc79b73a56f51a71589`。

| RKBin 檔案 | SHA-256 |
| --- | --- |
| `rk35/rk3506b_ddr_750MHz_v1.06.bin` | `14a607be903eff6c0984cdbeda77e7ce2963afad74aa900cad17149ec3fc65a7` |
| `rk35/rk3506_tee_v2.10.bin` | `93603ca22cdf22e47ac130e4ac386cdf9474443ab076039807dfc2d5d30b7ecd` |
| `LICENSE.TXT` | `0b37e1522c36cf4579c45dfb138798c3cb5665fcf6302b95377179fbed38e35c` |

RKBin 證據清單 SHA-256 為 `b9a60bfc079113c9ec4b2a3aa15709f0bdc0796baa8113a61a801f27bbc18ca8`。DDR 與 TEE 無法由本倉庫來源重建；固定雜湊只證明內容與來源提交一致。授權副本已安裝到映像，對外散布仍須遵守其 Rockchip 平台限制並完成合規審查。

## DTB、核心與工具

映像內 `rk3506b-bananapi-forge1.dtb` SHA-256 為 `bc6a4d9329a095dcbdc21f0f38912c0aa90f778f4c5286f598419533d10cb657`。守門解析並確認 Banana Pi model、三個 compatible、SD、雙 RMII Ethernet、USB OTG、USB host、SPI-NAND、I2C、RTC、RK730 音訊、CAN、RGA、RNG、溫度、DSI、觸控、UART、alias、bus width 與必要屬性。供應商硬編碼的 MMC root 及 `ttyS2` 啟動字串不在 DTB；開機腳本使用 `ttyFIQ0,1500000n8` 及 Banana Pi 專用 Linux DTB。

核心設定確認 GPIO character device、I2C、SPI、SD、雙 Ethernet、CAN、USB host、USB HID、USB gadget ConfigFS mass storage、DSI、Rockchip DRM、RGA、RK730 音訊、thermal、watchdog 與硬體 RNG 已納入。根檔案系統實際安裝 `gpiod`、`i2c-tools`、Python GPIO／SPI、`spi-tools`、USB、輸入裝置、CAN、Ethernet 與 `iperf3` 等診斷工具。

板上沒有 Wi-Fi 或 Bluetooth；本候選沒有加入板級無線套件、載入規則或韌體聲明。共用根檔案系統內存在其他平台韌體時，也不代表 Forge1 具備板載無線硬體。

## 建置記錄限制

建置器回報宿主 LoongArch binfmt 啟用失敗，但 `armhf` QEMU 測試、U-Boot、核心、Trixie 根檔案系統與最終映像仍完成。接受候選的建置記錄沒有命中 `error`、`failed`、`warning` 或 `missing firmware` 字串；這只描述本次軟體建置，不是實物品質證明。

## L3 實機門檻

- 用 UART 保存多次冷啟動、重新啟動與斷電重啟記錄，確認 DDR、SPL、TEE、U-Boot、boot script、initramfs 與 Linux 啟動鏈穩定。
- 確認 BPI-Forge1 原理圖與板卡版本相對於 ArmSoM DTS 的所有差異，修正後再重建候選。
- 驗證 SD、雙 RMII Ethernet、USB host、USB OTG gadget mass storage、GPIO、I2C、SPI、CAN、RTC 與 SPI-NAND；NAND 必須另建分割、燒錄、壞區與升級流程。
- 驗證 DSI 顯示、觸控、RK730 音訊、RGA、HID 與輸入事件。
- 執行 CPU、記憶體、網路、USB 與儲存混合壓力，保存溫度、節流、重置、I/O 錯誤與測試時間。
