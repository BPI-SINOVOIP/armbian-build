#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0
# BSP packages are release-independent. Build against the oldest supported
# userspace (Bullseye), even when the build container supplies a newer GCC/glibc.
# This ABI also runs on Bookworm, Trixie, Jammy, Noble and Resolute.
set -euo pipefail

assets=$(cd -- "$(dirname -- "$0")" && pwd)
destination=${1:?BSP staging directory required}
cache=${2:?Toolchain cache directory required}/bpi-m8-uart-bullseye
compiler=aarch64-linux-gnu-gcc
elf_reader=aarch64-linux-gnu-readelf
archive_url=https://deb.debian.org/debian/pool/main/c/cross-toolchain-base
build_dir=$(mktemp -d -t bpi-m8-uart.XXXXXXXX)
download=
trap '[[ -z "$download" ]] || rm -f -- "$download"; rm -rf -- "$build_dir"' EXIT
sysroot=$build_dir/sysroot
mkdir -p "$cache" "$sysroot" "$build_dir/debian" "$build_dir/dpkg"
touch "$build_dir/dpkg/status"

# Cache only verified archives; extract a private sysroot on every build so
# concurrent builders cannot observe partial or modified shared headers/libs.
while read -r expected filename; do
	[[ "$expected" =~ ^[0-9a-f]{64}$ && "$filename" =~ ^[a-z0-9][a-z0-9.+_-]*\.deb$ ]]
	archive=$cache/$filename
	if [[ ! -s "$archive" ]]; then
		download=$(mktemp "$cache/.download.XXXXXXXX")
		curl --fail --location --retry 3 --connect-timeout 30 \
			-o "$download" "$archive_url/$filename"
		printf '%s  %s\n' "$expected" "$download" | sha256sum --check --status
		mv -- "$download" "$archive"
		download=
	fi
	printf '%s  %s\n' "$expected" "$archive" | sha256sum --check --status
	dpkg-deb -x "$archive" "$sysroot"
	if [[ "$filename" == libc6-arm64-cross_* ]]; then
		dpkg-deb -e "$archive" "$build_dir/libc-control"
	fi
done < "$assets/bullseye-sysroot.sha256"

target_lib=$sysroot/usr/aarch64-linux-gnu/lib
helper=$destination/usr/lib/armbian/bpi-m8-aic-hciattach
rtk_helper=$destination/usr/bin/rtk_hciattach
[[ -s "$build_dir/libc-control/shlibs" ]]

# --sysroot alone does not isolate Debian cross GCC's absolute include/CRT
# paths. Pin all three; keep GCC's own intrinsic headers and support archive.
# The sysroot's libc.so linker script resolves its absolute paths via sysroot.
compile=(env -u CPATH -u C_INCLUDE_PATH -u CPLUS_INCLUDE_PATH -u LIBRARY_PATH
	"$compiler" --sysroot="$sysroot" -nostdinc
	-isystem "$("$compiler" -print-file-name=include)"
	-isystem "$sysroot/usr/aarch64-linux-gnu/include"
	-B"$target_lib/" -L"$target_lib" -static-libgcc)
"${compile[@]}" -O2 -Wall -Wextra -Werror "$assets/bpi-m8-aic-hciattach.c" -o "$helper"

# Rebuild the same vendor release as the shared prebuilt (which needs 2.33).
# Keep the upstream warning policy for its unmodified sources; in particular
# retain H5 and the RTL8852BS firmware/config table. See rtk-hciattach/copyright.
"${compile[@]}" -O2 -Wall \
	"$assets/rtk-hciattach/hciattach.c" "$assets/rtk-hciattach/hciattach_rtk.c" \
	"$assets/rtk-hciattach/hciattach_h4.c" "$assets/rtk-hciattach/rtb_fwc.c" -o "$rtk_helper"

# Do not merely lower Depends: reject a binary that leaked newer symbols or
# unexpected runtime libraries before asking dpkg-shlibdeps for dependencies.
for binary in "$helper" "$rtk_helper"; do
	"$elf_reader" --dynamic --wide "$binary" > "$build_dir/dynamic"
	if grep -Eq '\((RPATH|RUNPATH)\)' "$build_dir/dynamic"; then
		echo "M8 UART helper contains an unexpected runtime library path: $binary" >&2
		exit 1
	fi
	while read -r library; do
		case "$library" in
			libc.so.6 | ld-linux-aarch64.so.1) ;;
			*) echo "Unexpected M8 UART runtime library: $library" >&2; exit 1 ;;
		esac
	done < <(sed -n 's/.*(NEEDED).*\[\(.*\)\].*/\1/p' "$build_dir/dynamic")
	"$elf_reader" --version-info --wide "$binary" > "$build_dir/versions"
	while read -r version; do
		if ! dpkg --compare-versions "$version" le 2.31; then
			echo "M8 UART helper requires GLIBC_$version, newer than Bullseye: $binary" >&2
			exit 1
		fi
	done < <(sed -n 's/.*Name: GLIBC_\([^ ]*\).*/\1/p' "$build_dir/versions")
done

# Use metadata from the very same verified libc package, not the host's
# /var/lib/dpkg database (Noble cross shlibs impose libc6 >= 2.39).
install -m 0644 "$assets/dependency-control" "$build_dir/debian/control"
shlib_output=$(
	cd "$build_dir"
	DEB_HOST_ARCH=arm64 dpkg-shlibdeps --admindir="$build_dir/dpkg" \
		-L"$build_dir/libc-control/shlibs" -l"$target_lib" \
		-O -e"$helper" -e"$rtk_helper"
)
runtime_depends=$(printf '%s\n' "$shlib_output" | sed -n 's/^shlibs:Depends=//p')
[[ -n "$runtime_depends" && "$runtime_depends" != *$'\n'* ]]
printf 'Armbian-BSP-Depends: %s, rfkill, kmod, bluez\n' "$runtime_depends" >> "$destination/DEBIAN/control"
