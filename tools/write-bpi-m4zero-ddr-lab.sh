#!/usr/bin/env bash
set -euo pipefail

usage() {
	cat <<'EOF'
用法：
  sudo ./tools/write-bpi-m4zero-ddr-lab.sh \
    --device /dev/sdX \
    --spl output/.../sunxi-spl-ddr-lab.bin \
    --evidence-dir output/evidence/write-名稱 \
    --confirm-write

只寫入整顆 SD 卡 8 KiB 偏移的 SPL，不會建立或修改分割區。
EOF
}

device=
spl=
evidence_dir=
confirmed=no

while (( $# )); do
	case "$1" in
	--device)
		device="${2:-}"
		shift 2
		;;
	--spl)
		spl="${2:-}"
		shift 2
		;;
	--evidence-dir)
		evidence_dir="${2:-}"
		shift 2
		;;
	--confirm-write)
		confirmed=yes
		shift
		;;
	-h|--help)
		usage
		exit 0
		;;
	*)
		echo "未知參數：$1" >&2
		usage >&2
		exit 2
		;;
	esac
done

[[ "$confirmed" == yes && -n "$device" && -n "$spl" && -n "$evidence_dir" ]] || {
	usage >&2
	exit 2
}
(( EUID == 0 )) || {
	echo "燒錄與回讀需要 root，請使用 sudo" >&2
	exit 1
}
[[ -b "$device" ]] || {
	echo "目標不是 block device：$device" >&2
	exit 1
}
[[ -f "$spl" ]] || {
	echo "找不到 SPL：$spl" >&2
	exit 1
}

device="$(readlink -f "$device")"
device_type="$(lsblk -dnro TYPE "$device")"
[[ "$device_type" == disk ]] || {
	echo "只允許整顆磁碟，拒絕 $device_type：$device" >&2
	exit 1
}

root_source="$(readlink -f "$(findmnt -nro SOURCE /)")"
[[ -b "$root_source" ]] || {
	echo "無法確認系統根磁碟，拒絕寫入：$root_source" >&2
	exit 1
}
if lsblk -snrpo NAME "$root_source" | rg -Fxq "$device"; then
	echo "拒絕寫入目前系統根磁碟：$device" >&2
	exit 1
fi

if lsblk -nrpo MOUNTPOINT "$device" | rg -q '\S'; then
	echo "目標或其分割區仍在掛載：$device" >&2
	lsblk -o NAME,TYPE,SIZE,MOUNTPOINTS "$device" >&2
	exit 1
fi

device_size="$(blockdev --getsize64 "$device")"
spl_size="$(stat -c %s "$spl")"
(( device_size >= 64 * 1024 * 1024 )) || {
	echo "目標容量小於 64 MiB：$device_size" >&2
	exit 1
}
(( spl_size > 0 && spl_size <= 49056 )) || {
	echo "SPL 大小不合理：$spl_size" >&2
	exit 1
}
(( spl_size % 512 == 0 )) || {
	echo "SPL 大小不是 512 bytes 對齊：$spl_size" >&2
	exit 1
}

egon_magic="$(dd if="$spl" bs=1 skip=4 count=8 status=none)"
[[ "$egon_magic" == 'eGON.BT0' ]] || {
	echo "輸入不是有效的 eGON SPL" >&2
	exit 1
}

mkdir -p "$evidence_dir"
backup="$evidence_dir/pre-write-offset-8k.bin"
readback="$evidence_dir/readback-offset-8k.bin"
dd if="$device" of="$backup" iflag=skip_bytes,count_bytes \
	skip=8192 count="$spl_size" status=none

{
	printf '時間：%s\n' "$(date --iso-8601=seconds)"
	printf '目標：%s\n' "$device"
	printf '目標容量：%s\n' "$device_size"
	printf 'SPL：%s\n' "$(readlink -f "$spl")"
	printf 'SPL 大小：%s\n' "$spl_size"
	printf 'SPL SHA-256：%s\n' "$(sha256sum "$spl" | cut -d' ' -f1)"
	printf '寫入偏移：8192\n'
} >"$evidence_dir/write-info.txt"

dd if="$spl" of="$device" oflag=seek_bytes seek=8192 conv=fsync,notrunc \
	status=progress
blockdev --flushbufs "$device"
dd if="$device" of="$readback" iflag=skip_bytes,count_bytes \
	skip=8192 count="$spl_size" status=none
cmp "$spl" "$readback"

sha256sum "$spl" "$backup" "$readback" >"$evidence_dir/sha256sums.txt"
sync

echo "SPL 寫入與逐位元回讀通過：$device"
echo "證據目錄：$evidence_dir"
