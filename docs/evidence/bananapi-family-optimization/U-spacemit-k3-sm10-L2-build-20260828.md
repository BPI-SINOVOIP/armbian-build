# BPI-SM10 SpacemiT K3 L2 正式建置證據

更新日期：2026-08-28

## 結論

`bananapism10` 已從已推送提交 `fd360f4e10f812c363eee370734dff28c250c1e9`，使用 SM10 專用 OverlayFS 與固定來源完整建立 Debian Trixie current minimal CLI。IMG、XZ、GPT 雙分割區、唯讀 FAT／ext4、六項映像內啟動載荷、SM10 DTB、最終核心設定與最終 U-Boot 設定均通過守門，因此證據層級提升為 L2 內部軟體候選。

本結果不包含實體板開機、介面、效能、穩定性或量產驗證。`esos.itb`、`env_k3.txt`、`bianbu.bmp`、PowerVR 與 VPU 韌體的公開再散布授權尚未閉合，SDK 也含私鑰材料，因此不得公開發布組合映像。

## 固定來源

| 項目 | 固定身分 |
| --- | --- |
| Armbian 來源提交 | `fd360f4e10f812c363eee370734dff28c250c1e9` |
| Armbian 來源 tree | `aa9ab635005c76d469e8eadcc49e25969609ebc8` |
| SpacemiT manifest 提交 | `6d767b42fdbd759dc9511b8a13523c3de42aaa5a` |
| Linux 6.18 | `27275ec8240cc49af3a525b8bc325d9b5029fb81` |
| U-Boot 2022.10 | `1b10c8119e1a9b5451a4236f6b384f7c91eed1e2` |
| OpenSBI | `3e2f9efc9660b8d5fcae4e0b6495f306d5c64078` |
| Armbian firmware | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| `SOURCE_DATE_EPOCH` | `1777390324` |
| 建置／驗證契約 SHA-256 | `3800ade7b68eba9474ea86138f7d5d4c94f2578e2260a819cc74d6f4c58b2ea9` |
| 來源契約投影 SHA-256 | `3b182f48aac7a05f9baa21801c127d8e6f383ef1cf7933ff3cd7c8089d8353f6` |

## 正式產物

固定目錄：`output/images/2026.08/bananapi-spacemit-k3-sm10-trixie-current-cli`

| 產物 | 大小 | SHA-256 |
| --- | ---: | --- |
| `Armbian-unofficial_26.05.0-trunk_Bananapism10_trixie_current_6.18.3_minimal.img` | 1,916,796,928 | `0de1f1bab7f5b01e56768b6a70cdf55381157430ccaf48dc78b4861ddf8f533e` |
| `Armbian-unofficial_26.05.0-trunk_Bananapism10_trixie_current_6.18.3_minimal.img.xz` | 479,047,032 | `26b6322d46230688701d1ab08747dd15da1ee1a0fef95dca8d10e12055e3b595` |

XZ 結構、校驗碼與完整解壓串流同一性均通過。正式 IMG 與 XZ 保留，不列入空間回收。

## 內容守門

- GPT 與 CRC 有效；`bootfs` 自 LBA 24,576 起、長度 524,288 sectors，`rootfs` 自 LBA 548,864 起、長度 3,192,832 sectors。
- FAT 標籤為 `BPI-BOOT`，ext4 標籤為 `BPI-ROOT`；兩個檔案系統均以唯讀方式檢查。
- 映像內 `env.bin`、`bootinfo_block.bin`、`FSBL.bin`、`esos.itb`、`fw_dynamic.itb` 與 `u-boot.itb` 的位移、大小及 SHA-256 均符合固定契約。
- SM10 DTB SHA-256 為 `a74520d979cc62fcdb12dfddd97c7968900109df6a33ae34c1489d87a34695ba`。
- 最終核心設定 SHA-256 為 `2ea6c3b62bd8118b685a10d6c4c22a1718df7a9e533c3e929282fcee90c82445`。
- 最終 U-Boot 設定 SHA-256 為 `ffb244d91c6d9ce59f20eeabee15f0391e5d6417548856cacd4720d87cf69b9c`。
- 候選矩陣、完成狀態、驗證清單、啟動載荷清單與最終設定清單均已寫入機器契約；完成驗證時間為 `2026-08-28T05:29:33Z`。

## 證據限制

- SM10 板級設定維持 `.wip`；L2 只證明固定來源完整映像符合本機軟體契約。
- SD 只是候選開機媒體，尚未驗證冷啟動、重新啟動、儲存、乙太網路、Wi-Fi、Bluetooth、USB、顯示、GPU、VPU、NPU 或排針。
- Linux DTS 保守繼承 `k3_com260.dts`，U-Boot 控制 DT 仍是 `k3_com260.dtb`；尚未取得 SM10 載板逐網路拓撲等同性證據。
- 三個執行期預建資產沒有可重建來源或已確認的再散布授權，PowerVR 與 VPU 內容也未完成逐檔授權閉合。
- SDK 內含私鑰檔；本流程沒有封裝這些私鑰，也不構成安全啟動或量產簽署核准。
- APT 與 initramfs 輸入未宣稱整體映像逐位元可重現。

## 重驗命令

```bash
python3 tools/check-bananapi-spacemit-k3-sm10-policy.py
python3 tools/check-bananapi-spacemit-k3-sm10-policy.py --verify-historical-image
python3 -m unittest tests.test_bananapi_spacemit_k3_sm10_candidate
python3 tools/bananapi-board-audit.py --check
```

正式 IMG 與 XZ 應持續保留。完成歷史重驗後，只能移除 SM10 專用 OverlayFS；共用 `/media/pi/SMCI/armbian/bpi-v26.2.1/cache` 必須維持唯讀 lower，且不得刪除或改寫。
