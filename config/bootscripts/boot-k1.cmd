# Common parameter
earlycon=sbi
console=ttyS0,115200
init=/init
bootdelay=0
baudrate=115200
loglevel=loglevel=8
#BPI
mmc_root=LABEL=BPI-ROOT

#partitions/mtdparts/mtdids would set while flashing env.bin
partitions=
mtdids=nor0=spi-dev
mtdparts=spi-dev:64K@0K(bootinfo),256K@64K(fsbl),128K@512K(env),384K@640K(opensbi),1M@2M(uboot)

# Nand flash rootfs device
nand_root=ubi0:rootfs
nand_rootfstype=ubifs

# Nor flash rootfs device
nor_root=/dev/mtdblock6
nor_rootfstype=squashfs

# eMMC/SDCard rootfs device
mmc_rootfstype=ext4

## Get "rootfs" partition number in decimal, and set var "mmc_root"
## Variable "boot_devnum" is set during board_lat_init()
set_mmc_root=if part number mmc ${boot_devnum} rootfs rootfs_part; then \
             setexpr rootfs_part ${rootfs_part} + 0; \
             else setenv rootfs_part 2; fi\
             setenv mmc_root "/dev/mmcblk${boot_devnum}p${rootfs_part}";

set_nvme_root=part number nvme ${boot_devnum} rootfs rootfs_part; \
             setexpr rootfs_part ${rootfs_part} + 0; \
             setenv nvme_root "/dev/nvme${boot_devnum}n1p${rootfs_part}";

# NFS device
#override here, otherwise gen random addr and save to eeprom by uboot 
#ethaddr=fe:fe:fe:22:22:01
#eth1addr=fe:fe:fe:22:22:02

ipaddr=192.168.1.15
netmask=255.255.255.0
serverip=192.168.1.2
gatewayip=192.168.1.1

preboot=
knl_addr=0x40000000
ramdisk_addr=0x50000000
knl_name=uImage.itb
ramdisk_name=initramfs-generic.img
#ramdisk_name=uInitrd
dtb_name=dtb/spacemit/k1-x_evb.dtb
dtb_addr=0x28000000
mdio_intf=
phyaddr0=1
phy_link_time=10000
netdev=eth0
nfsopts=
nfs_root=
nfs_bootfile=

# Common boot args
commonargs=setenv bootargs earlycon=${earlycon} earlyprintk console=tty1 console=${console} ${loglevel} clk_ignore_unused rdinit=${init}

#detect product_name from env and select dtb file to load
dtb_env=if test -n "${product_name}"; then \
                if test "${product_name}" = k1_evb; then \
                    setenv dtb_name dtb/spacemit/k1-x_evb.dtb; \
                elif test "${product_name}" = k1_deb1; then \
                    setenv dtb_name dtb/spacemit/k1-x_deb1.dtb; \
                elif test "${product_name}" = k1_deb2; then \
                    setenv dtb_name dtb/spacemit/k1-x_deb2.dtb; \
                else \
                    echo "Unknow product_name: ${product_name}, loads default dtb: ${dtb_name}"; \
                fi; \
            fi;

detect_dtb=echo "product_name: ${product_name}"; run dtb_env; echo "detect dtb_name: ${dtb_name}";

# spinand_ubifs boot
ubifs_list= mtd list; \
             ubifsls;
ubifs_loadimg=  echo " ubifsload uImage.itb..."; \
                ubifsload ${knl_addr} ${knl_name};

ubifs_boot=echo "Trying to boot from UBIFS..."; \
           run ubifs_loadimg; \
           bootm ${knl_addr};

# Nand boot
nand_boot=echo "Try to boot from nand flash..."; \
		  run ubifs_list; \
		  run ubifs_loadimg; \
		  bootm ${knl_addr};

# Nor boot
set_nor_args=setenv bootargs ${bootargs} mtdparts=${mtdparts} root=${nvme_root} rootfstype=ext4

nor_loadknl=echo "Loading kernel..."; \
            fatload ${bootfs_devname} ${boot_devnum}:${bootfs_part} ${knl_addr} ${knl_name}; \
            fatload ${bootfs_devname} ${boot_devnum}:${bootfs_part} ${ramdisk_addr} ${ramdisk_name}; \
            fatload ${bootfs_devname} ${boot_devnum}:${bootfs_part} ${dtb_addr} ${dtb_name};

nor_boot=echo "Try to boot from NVMe ..."; \
         run commonargs; \
         run set_nvme_root; \
         run set_nor_args; \
         run detect_dtb; \
         run nor_loadknl; \
         bootm ${knl_addr} ${ramdisk_addr} ${dtb_addr}; \
         echo "########### boot kernel failed by default config, check your boot config #############"

###############################################################################
# eMMC/SDCard boot
###############################################################################
set_mmc_args=setenv bootargs "${bootargs}" root=${mmc_root} rootwait rootfstype=${mmc_rootfstype} board=bpi-f3;

mmc_loadknl=echo "Loading kernel ..."; \
            fatload mmc ${boot_devnum}:${bootfs_part} ${knl_addr} ${knl_name}; \
            fatload mmc ${boot_devnum}:${bootfs_part} ${ramdisk_addr} ${ramdisk_name}; \
            fatload mmc ${boot_devnum}:${bootfs_part} ${dtb_addr} ${dtb_name};


mmc_boot=echo "Try to boot from MMC${boot_devnum} ..."; \
         run commonargs; \
         run set_mmc_root; \
         run set_mmc_args; \
         run detect_dtb; \
         run mmc_loadknl; \
         bootm ${knl_addr} ${ramdisk_addr} ${dtb_addr}; \
         echo "########### boot kernel failed by default config, check your boot config #############"

# NFS boot args
nfs_args=run commonargs; setenv bootargs ${bootargs} root=/dev/nfs nfsroot=${serverip}:${nfs_root},${nfsopts}
addip=setenv bootargs ${bootargs} ip=${ipaddr}:${serverip}:${gatewayip}:${netmask}:${hostname}:${netdev}:off
nfs_boot=echo Booting from NFS..; run nfs_args; run addip; tftp ${knl_addr} ${nfs_bootfile} && bootm ${knl_addr}

# U-boot autoboot script: try to boot from device, if failed then try nfs boot
# Variable "boot_device" is set during board_late_init()
autoboot=if test ${boot_device} = nand; then \
                run nand_boot; \
        elif test ${boot_device} = nor; then \
                run nor_boot; \
        elif test ${boot_device} = mmc; then \
                run mmc_boot; \
        fi; \
        run nfs_boot;
bootcmd=run autoboot; echo "run autoboot"

#
#
#


