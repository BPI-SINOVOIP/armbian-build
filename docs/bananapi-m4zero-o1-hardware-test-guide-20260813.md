# BPI-M4 Zero O1 實機測試手冊

## 1. 測試目的

O1 是 Orange Pi Zero 3 靜態 DDR 設定檔加上唯讀 UART 診斷的測試版。
本輪只回答下列問題：

1. DDR 初始化停在哪個階段。
2. 2 GiB 與 4 GiB 幾何參數是否辨識正確。
3. 通過與失敗板的控制器／PHY 狀態有何差異。

O1 不是發布候選，單次進入 Linux 也不代表 792 MHz 已穩定。

## 2. 測試前紀錄

每次測試先記錄：

| 欄位 | 必填內容 |
| --- | --- |
| 板號 | 例如 `450600826` 或 `1116` |
| DDR 料號 | `RS512M32LO4D1BDS-53BT`、`RS1G32LO4D2BDS-53BT` 或實際料號 |
| 容量 | 2 GiB 或 4 GiB |
| eMMC 料號 | 板上實際絲印 |
| SD 卡 | 品牌、容量、識別資訊 |
| 電源 | 供應器額定值與線材 |
| 測試類型 | 冷開機或暖重啟 |
| 映像 SHA-256 | 必須與本手冊相同 |

## 3. 映像與雜湊

```text
/media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-opi-ddr/output/images/2026.08/bpi-m4zero-o1-opi-ddr-diag/Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_o1-opi-ddr-diag-P4301-792mhz.img.xz
```

```text
20d70f507c3a7e81e2aafc4f6ebf0f36d4249ecd59ad9f46eb301a7642704847
```

燒錄前驗證：

```bash
sha256sum Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_o1-opi-ddr-diag-P4301-792mhz.img.xz
xz -t Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_o1-opi-ddr-diag-P4301-792mhz.img.xz
```

## 4. 燒錄

先用 `lsblk -o NAME,PATH,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS` 找出 SD 卡。
以下命令的 `/dev/sdX` 必須由操作者換成已核對的整顆 SD 裝置；不可填入
系統碟或分割區名稱。

```bash
xzcat Armbian-unofficial_26.05.0-trunk_Bananapim4zero_jammy_current_6.18.32_o1-opi-ddr-diag-P4301-792mhz.img.xz \
  | sudo dd of=/dev/sdX bs=16M status=progress conv=fsync
sync
```

燒錄後至少回讀 bootloader 區間並比對正式產物：

```bash
sudo cmp -n 873977 -i 8192:0 /dev/sdX \
  /media/pi/SMCI/armbian/bpi-v26.2.1-m4zero-opi-ddr/output/evidence/bpi-m4zero-opi-ddr/O1-20260813-131210-238e3e244/u-boot-sunxi-with-spl.bin
```

`cmp` 沒有輸出且結束碼為 `0` 才可進行測試。

## 5. UART 收集

設定為 115200 baud、8 個資料位元、無同位檢查、1 個停止位元，並停用
流量控制。純接收方式可使用：

```bash
sudo stty -F /dev/ttyUSB0 115200 cs8 -cstopb -parenb -ixon -ixoff -crtscts raw -echo
sudo timeout 180s stdbuf -oL cat /dev/ttyUSB0 \
  | tee uart-板號-o1-冷開機-01-$(date +%Y%m%d-%H%M%S).log
```

先啟動收集，再讓板子完全斷電至少 10 秒後上電。每份日誌必須包含從
SPL 第一個字元開始的完整邊界，不可只截取成功登入後的 `dmesg`。

預期建置識別碼：

```text
2026.01-S127a-P4301-Hc6a9-V3946-Bd0d2-R448a
```

預期至少出現：

```text
M4ZDDR1_PROFILE0
M4ZDDR1_BEGIN
M4ZDDR1_RUN
M4ZDDR1_STAGE
```

若完成初始化，還應出現 `M4ZDDR1_END`、`M4ZDDR1_REG` 與
`M4ZDDR1_FINAL`。缺少後段標記本身就是定位失敗階段的證據，不可因此丟棄
日誌。

## 6. 日誌解析

```bash
python3 tools/parse-bpi-m4zero-o1-uart.py uart-板號-o1-冷開機-01-時間.log
```

保存原始 UART、解析輸出與兩者 SHA-256。不得手工刪除亂碼或重排內容；
需要註記時另建說明文件。

## 7. 測試順序

1. 先在目前可取得的 `1116` 板做一次冷開機，確認診斷格式可讀。
2. 對 V2 曾失敗的 `450600146`、`450600826`、`450601075` 各做一次冷開機。
3. 依 O1 結果決定是否需要 O2 幾何參數控制組；未見相關問題時不做。
4. 診斷完成後，候選版每片至少做三次完整斷電冷開機，必須 `9/9`。
5. 通過啟動門檻後才執行記憶體壓力、重啟與溫度條件測試。

## 8. 判定規則

| 結果 | 分類 |
| --- | --- |
| 建置、雜湊、燒錄回讀通過 | 離線／媒體驗證通過 |
| 只看到 SPL 或 DDR 標記 | 診斷證據，不算開機通過 |
| 單次進入 Linux | 僅單次啟動通過，不算穩定 |
| 三片弱板各三次冷開機全過 | 進入壓力測試的必要條件 |
| 任一片失敗 | 保留日誌，回到單變因分析 |

目前狀態只有離線驗證通過，所有硬體欄位仍是「尚未實機驗證」。
