BPI-SM10／SpacemiT K3-CoM260 啟動載荷
====================================

來源建置的 `FSBL.bin`、`bootinfo_block.bin`、`u-boot-env-default.bin`、
`fw_dynamic.itb`、`u-boot.itb` 與 `uboot.config` 由下列固定來源於原廠
K3 建置容器內重建兩次，兩次 SHA-256 完全一致。`env.bin` 是相同
`u-boot-env-default.bin` 位元組的原始位置副本。其餘必要預建載荷沿用
2026-05-26 原廠 K3 Buildroot SDK 產物；原始證據位於：

  /media/pi/SMCI/bpi/bpi-sm10/release/20260526-k3-buildroot-v1.0-vendor-bsp

固定來源：

  manifest：      6d767b42fdbd759dc9511b8a13523c3de42aaa5a
  linux-6.18：    27275ec8240cc49af3a525b8bc325d9b5029fb81
  uboot-2022.10： 1b10c8119e1a9b5451a4236f6b384f7c91eed1e2
  opensbi：       3e2f9efc9660b8d5fcae4e0b6495f306d5c64078
  esos：          92a8baf250e42853a094a7af6f7ee849adb3de4a

可重現建置時間基準：

  SOURCE_DATE_EPOCH：1777390324

受控預建資產：

  env_k3.txt、esos.itb 與 bianbu.bmp

Armbian 依原廠 `partition_universal.json` 使用下列固定位移：

  env.bin              640 KiB
  bootinfo_block.bin  1024 KiB
  FSBL.bin            1536 KiB
  esos.itb            4096 KiB
  fw_dynamic.itb      7168 KiB
  u-boot.itb          8192 KiB

開機分割區從 12 MiB 開始，大小為 256 MiB、格式為 FAT；根檔案系統從
268 MiB 開始。`env_k3.txt` 只負責讓 U-Boot 載入 Armbian 的 Image、initramfs
及 `k3-bananapi-sm10.dtb`。

授權與發布限制：

- Linux 與 U-Boot 主要採 GPL-2.0 系列授權，OpenSBI 採 BSD-2-Clause。
- ESOS 是 RT-Thread 與多家晶片廠元件的組合，原廠授權清單仍含未註明項目。
- K3 VPU 韌體逐檔標記為沒有可確認的授權；PowerVR 套件授權檔仍是樣板內容。
- SDK 內含測試或開發用途的私鑰材料，不能當作量產安全開機金鑰。
- 在完成逐檔再散布授權、量產金鑰流程及實機驗證前，本目錄只能作內部候選
  建置與比對，不代表核准公開發布或硬體支援聲明。
