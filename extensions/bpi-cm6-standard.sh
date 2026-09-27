#!/usr/bin/env bash
# CM6 標準板名的連線與 GPIO 配套；保留原磁碟格式與桌面選擇。

function extension_prepare_config__bpi_cm6_standard() {
	[[ "${BOARD}" == "bananapicm6" && "${BRANCH}" == "legacy" ]] || \
		exit_with_error "CM6 標準配套只接受 bananapicm6 legacy"
	case "${RELEASE}" in
		jammy|noble|trixie) ;;
		resolute) exit_with_error "CM6 K1 為 RVA22；Ubuntu Resolute 要求 RVA23，拒絕建立不相容成品" ;;
		*) exit_with_error "CM6 標準配套未核定此發行版" "${RELEASE}" ;;
	esac
	[[ "${KERNELBRANCH}" == "commit:0d0af0d895251383baee939d44e523699e31889f" ]] || \
		exit_with_error "CM6 核心不在固定配套內"
	declare -g BPI_CM6_STANDARD_GPU="no"
	if [[ "${RELEASE}" == "noble" && "${BUILD_DESKTOP}" == "yes" && "${DESKTOP_ENVIRONMENT}" == "xfce" ]]; then
		BPI_CM6_STANDARD_GPU="yes"
		add_packages_to_image mesa-utils
	fi
	local fingerprint
	fingerprint="$(python3 - "${SRC}" <<'PY'
import hashlib,sys
from pathlib import Path
root=Path(sys.argv[1]); h=hashlib.sha256()
patterns=['extensions/bpi-cm6-standard.sh','tools/bpi_cm6_standard*.py','tools/bpi_cm6_gpio.py',
          'tools/build_bpi_cm6_*.py','tools/package_bpi_cm6_bluetooth.py','tools/bpi_k1_native_rootfs.py',
          'tools/bpi_k1_acceleration.py','tools/prepare_bpi_k1_vendor_rootfs.py',
          'tools/bpi_k1_host_dependencies.py','config/spacemit-k1-profiles/board-targets.json',
          'config/spacemit-k1-standard/*','config/spacemit-k1-gpio/*/*',
          'config/spacemit-k1-connectivity/*','config/spacemit-k1-acceleration/noble.lock.json']
for p in sorted({p for pattern in patterns for p in root.glob(pattern) if p.is_file()}):
    h.update(p.relative_to(root).as_posix().encode());h.update(b'\0');h.update(p.read_bytes())
print(h.hexdigest())
PY
)"
	declare -g BPI_CM6_STANDARD_FINGERPRINT="${fingerprint}"
	EXTRA_ROOTFS_NAME="${EXTRA_ROOTFS_NAME:-}-cm6standard-${RELEASE}-${fingerprint:0:16}"
}

function add_host_dependencies__bpi_cm6_standard() {
	if [[ "${RELEASE}" == "noble" ]]; then
		local dependencies
		local -a selected=()
		# 共用工具原生接受標準板名；不用啟用官方格式 extension，也不假借別名。
		dependencies="$(python3 "${SRC}/tools/bpi_k1_host_dependencies.py" packages \
			--board "bananapicm6" --camera "none" \
			--host-release "${host_release:-${HOSTRELEASE:-}}" --host-arch "${host_arch:-${HOSTARCH:-}}")" || {
			exit_with_error "CM6 標準 Noble 主機相依規劃失敗；需要 Ubuntu Noble amd64" "${BOARD}"
			return 1
		}
		mapfile -t selected <<< "${dependencies}"
		[[ "${#selected[@]}" -gt 0 ]] || { exit_with_error "CM6 標準 Noble 主機相依清單為空"; return 1; }
		EXTRA_BUILD_DEPS+=("${selected[@]}")
	fi
}

function host_dependencies_ready__bpi_cm6_standard() {
	declare -g BPI_CM6_STANDARD_WORK="${SRC}/.tmp/cm6-standard-${ARMBIAN_BUILD_UUID}"
	declare -g BPI_CM6_STANDARD_CACHE="${BPI_CM6_STANDARD_CACHE:-${SRC}/cache/bpi-cm6-standard}/${RELEASE}/${BPI_CM6_STANDARD_FINGERPRINT}"
	mkdir -p "${BPI_CM6_STANDARD_WORK}"
	if [[ "${RELEASE}" == "noble" ]]; then
		# 由框架完成 APT 後，核對相同五包精確版本及 BT 編譯器 SHA，再建資產。
		python3 "${SRC}/tools/bpi_k1_host_dependencies.py" verify \
			--board "bananapicm6" --camera "none" \
			--host-release "${HOSTRELEASE}" --host-arch "${HOSTARCH}" \
			> "${BPI_CM6_STANDARD_WORK}/host-dependencies.json" || {
			exit_with_error "CM6 標準 Noble 主機相依未通過來源鎖核對" "${BOARD}"
			return 1
		}
		local -a gpu_args=()
		[[ "${BPI_CM6_STANDARD_GPU}" != "yes" ]] || gpu_args+=(--gpu)
		python3 "${SRC}/tools/bpi_cm6_standard_assets.py" --cache "${BPI_CM6_STANDARD_CACHE}" \
			"${gpu_args[@]}" > "${BPI_CM6_STANDARD_WORK}/assets.json"
	else
		python3 "${SRC}/tools/bpi_cm6_standard_native.py" prepare --release "${RELEASE}" \
			--cache "${BPI_CM6_STANDARD_CACHE}" --output "${BPI_CM6_STANDARD_WORK}/prepared" \
			> "${BPI_CM6_STANDARD_WORK}/sources.json"
	fi
}

function post_repo_customize_image__bpi_cm6_standard() {
	if [[ "${RELEASE}" != "noble" ]]; then
		python3 "${SRC}/tools/bpi_cm6_standard_native.py" stage --release "${RELEASE}" --rootfs "${SDCARD}" \
			--prepared "${BPI_CM6_STANDARD_WORK}/prepared" --work "${BPI_CM6_STANDARD_WORK}/native" \
			> "${BPI_CM6_STANDARD_WORK}/native-stage.json"
		local -a target_argv=()
		mapfile -t target_argv < <(python3 -c 'import json,sys; print("\n".join(json.load(open(sys.argv[1]))["argv"]))' \
			"${BPI_CM6_STANDARD_WORK}/native-stage.json")
		[[ "${#target_argv[@]}" == 3 ]] || exit_with_error "CM6 目標建置命令不符"
		chroot_sdcard "${target_argv[@]@Q}"
		return
	fi
	local package
	package="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["artifact"])' \
		"${BPI_CM6_STANDARD_CACHE}/bluetooth-package/package-manifest.json")"
	python3 "${SRC}/tools/bpi_cm6_standard_connectivity.py" stage --rootfs "${SDCARD}" \
		--work-dir "${BPI_CM6_STANDARD_WORK}/connectivity" --gpio-cache "${BPI_CM6_STANDARD_CACHE}/gpio" \
		--cm6-bluetooth-package "${BPI_CM6_STANDARD_CACHE}/bluetooth-package/${package}" \
		> "${BPI_CM6_STANDARD_WORK}/connectivity-stage.json"
	local -a stages=("${BPI_CM6_STANDARD_WORK}/connectivity-stage.json") install_args=()
	if [[ "${BPI_CM6_STANDARD_GPU}" == "yes" ]]; then
		python3 "${SRC}/tools/bpi_cm6_standard_gpu.py" stage --rootfs "${SDCARD}" \
			--work-dir "${BPI_CM6_STANDARD_WORK}/gpu" --deb-cache "${BPI_CM6_STANDARD_CACHE}/deb-cache" \
			> "${BPI_CM6_STANDARD_WORK}/gpu-stage.json"
		stages+=("${BPI_CM6_STANDARD_WORK}/gpu-stage.json")
	fi
	mapfile -t install_args < <(python3 -c 'import json,sys; print("\n".join(v for p in sys.argv[1:] for v in json.load(open(p))["install_args"]))' "${stages[@]}")
	[[ "${#install_args[@]}" -ge 3 ]] || exit_with_error "CM6 標準配套安裝清單不足"
	chroot_sdcard_apt_get --no-remove --allow-downgrades --no-install-recommends install "${install_args[@]@Q}"
	chroot_sdcard dpkg --audit
}

function post_post_debootstrap_tweaks__900_bpi_cm6_standard_finish() {
	if [[ "${RELEASE}" != "noble" ]]; then
		python3 "${SRC}/tools/bpi_cm6_standard_native.py" finish --rootfs "${SDCARD}" \
			--work "${BPI_CM6_STANDARD_WORK}/native" > "${BPI_CM6_STANDARD_WORK}/native-result.json"
		return
	fi
	python3 "${SRC}/tools/bpi_cm6_standard_connectivity.py" finish --rootfs "${SDCARD}" \
		--work-dir "${BPI_CM6_STANDARD_WORK}/connectivity" > "${BPI_CM6_STANDARD_WORK}/connectivity-result.json"
	if [[ "${BPI_CM6_STANDARD_GPU}" == "yes" ]]; then
		python3 "${SRC}/tools/bpi_cm6_standard_gpu.py" finish --rootfs "${SDCARD}" \
			--work-dir "${BPI_CM6_STANDARD_WORK}/gpu" > "${BPI_CM6_STANDARD_WORK}/gpu-result.json"
	fi
}
