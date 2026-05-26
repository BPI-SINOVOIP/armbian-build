BPI-SM10 / SpacemiT K3-CoM260 boot blobs
========================================

These files were produced by the verified vendor Buildroot BSP build staged at:

  /media/pi/SMCI/bpi/bpi-sm10/release/20260526-k3-buildroot-v1.0-vendor-bsp

Source manifest revisions from the official SpacemiT K3 Buildroot SDK sync:

  linux-6.18:   27275ec8240cc49af3a525b8bc325d9b5029fb81
  uboot-2022.10: 1b10c8119e1a9b5451a4236f6b384f7c91eed1e2
  opensbi:      3e2f9efc9660b8d5fcae4e0b6495f306d5c64078
  esos:         92a8baf250e42853a094a7af6f7ee849adb3de4a

The Armbian family config writes these files at the same raw offsets used by
the vendor genimage layout:

  env.bin             640 KiB
  bootinfo_block.bin 1024 KiB
  FSBL.bin           1536 KiB
  esos.itb           4096 KiB
  fw_dynamic.itb     7168 KiB
  u-boot.itb         8192 KiB

The image partition layout is:

  bootfs  starts at 12 MiB, size 256 MiB, FAT
  rootfs  starts at 268 MiB, ext4

env_k3.txt is Armbian-specific and points U-Boot at the Armbian boot files on
the FAT bootfs partition.
