# Banana Pi CM5 Pro vendor 候選映像 L2 建置證據

更新日期：2026-08-27

## 結論

`bananapicm5pro` 已使用專用 OverlayFS 隔離快取，由固定來源完整建置 Debian Trixie vendor minimal CLI。IMG、XZ、GPT、U-Boot 載荷、固定來源、RKBin、RTL8852BS 外部驅動、韌體、授權副本、Banana Pi 身分、核心設定、DTB 與診斷套件均通過 L2 唯讀守門。

此結果不代表實體板已開機，也不代表 ArmSoM CM5 IO donor 與 Banana Pi 載板已完成逐網路等同性審查。RTL8852BS 韌體仍缺少逐檔再散布授權；因此候選維持 `.wip`、禁止硬體通過聲明，也不能據此核准公開或商業散布。

## 建置資料

| 項目 | 值 |
| --- | --- |
| 板卡 | `bananapicm5pro` |
| 分支 | `vendor` |
| 發行版 | Debian Trixie |
| 設定 | minimal CLI |
| Linux | `6.1.115-vendor-rk35xx` |
| U-Boot | `2017.09` |
| 建置來源提交 | `eaf942d9f72a6bfe8a32fc2d497b62f1c97b7f99` |
| 驗證器提交 | `6c90fdef300e48354c5bee62f33df5939ae7c041` |
| 建置時間 | 29 分 2 秒 |
| 輸出目錄 | `output/images/2026.08/bananapi-rockchip-rk3576-cm5pro-trixie-vendor-cli` |

建置入口：

```bash
./tools/run-bananapi-rockchip-cm5pro-candidate-isolated-cache.sh
```

驗證入口：

```bash
./tools/verify-bananapi-rockchip-cm5pro-candidate.sh
```

## 產物同一性

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| `.img` | 2403336192 bytes | `0d0c2f69c81b75f9d70a08f40807d26821e1cdbe513e9bf72b47a447cba91767` |
| `.img.xz` | 473269684 bytes | `29f00fd69a3ef1ad72c08c32a4068ee11ebd8e3393ba2420bfbc0679a0e18e74` |

`xz -t` 已通過，XZ 解壓串流的 SHA-256 與未壓縮 IMG 相同。映像為 GPT，第一分割區從 sector 32768 開始，根檔案系統為 ext4。

## 啟動載荷

| 載荷 | 映像偏移 | 大小 | SHA-256 |
| --- | ---: | ---: | --- |
| `idbloader.img` | 32768 | 327680 bytes | `bc83761d6939fa04cc694f2eaa9dfcb4fd70cc834c6af10b9171ea2da11314e7` |
| `u-boot.itb` | 8388608 | 1441280 bytes | `9ebeaea9548d19f52830e8d669e75b0da8dbe82d503fb5cf404f5065f4399e3b` |

啟動載荷證據清單 SHA-256 為 `8f46f200caf571b1ef3f810edf4b33ec3d4f9d1955ea111446162d7a49dca56c`。

## 唯讀守門

守門已核對下列項目：

- 候選提交、來源樹、建置設定與驗證設定可追溯。
- Linux、U-Boot、RKBin、Armbian firmware 與 RTL8852BS 驅動使用固定提交。
- RKBin 與外部 Wi-Fi 驅動檔案清單及授權副本雜湊一致。
- DTB model、compatible、SD、SDIO、eMMC、Ethernet、PCIe、USB、Type-C、I2C、SPI、音訊、RTC、風扇、顯示及加速器靜態節點符合契約。
- 核心設定包含 Rockchip GPIO、I2C、SPI、MMC、PCIe、DRM、Mali、MPP、RGA、RKNPU、USB gadget 與 RTL8852BS。
- 映像內標準 I/O、網路、儲存、顯示及加速器診斷套件已安裝；Debian 的 `glmark2-es2-x11` 透過 `Provides: glmark2-es2` 滿足虛擬套件契約。
- 映像以唯讀方式掛載檢查，未執行或修改目標架構檔案系統。

## 證據限制

L2 只證明固定來源可建置成內容符合契約的完整映像。下列項目仍未完成：

1. Banana Pi 模組與載板原理圖相對 donor 的逐網路等同性審查。
2. RTL8852BS 韌體逐檔再散布授權與外部驅動安全稽核。
3. UART、DDR、SD、eMMC、PCIe、NVMe、Ethernet、Wi-Fi、Bluetooth、USB、Type-C、40-pin、音訊及顯示實測。
4. GPU、VPU、RGA 與 NPU 的實際硬體後端、正確性、效能、溫度及長時間穩定性驗證。
5. 多次冷啟動、暖重啟、斷電恢復與組合壓力測試。

完成以上工作前不得把 L2 描述為可量產、可公開散布或所有功能可用。
