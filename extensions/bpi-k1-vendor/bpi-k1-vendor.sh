#!/usr/bin/env bash
# @description 以本次 Armbian 根系統建立 K1 官方 SD 與 Titan eMMC 格式，保留標準流程。

enable_extension "bpi-k1-desktop"

function bpi_k1_native_config() {
	python3 "${SRC}/tools/bpi_k1_native.py" config \
		--board "${BOARD}" --branch "${BRANCH}" --release "${RELEASE}" \
		--desktop "${DESKTOP_ENVIRONMENT:-gnome}" --tier "${DESKTOP_TIER:-minimal}" \
		--outputs "${BPI_K1_OUTPUTS:-sd,emmc}" --release-id "${BPI_K1_RELEASE_ID:-}" \
		--camera "${BPI_CM6_CAMERA_PROFILE:-none}" --card-device "${CARD_DEVICE:-}" \
		--build-desktop "${BUILD_DESKTOP:-}" --build-minimal "${BUILD_MINIMAL:-}" "$@"
}

function post_family_config__900_bpi_k1_validate_early() {
	# 在桌面來源下載前拒絕錯誤板型、媒體寫入參數與不相容組合。
	bpi_k1_native_config > /dev/null
	declare -g DESKTOP_ENVIRONMENT="${DESKTOP_ENVIRONMENT:-gnome}"
	declare -g DESKTOP_TIER="${DESKTOP_TIER:-minimal}"
	declare -g BPI_K1_OUTPUTS="${BPI_K1_OUTPUTS:-sd,emmc}"
	declare -g BPI_CM6_CAMERA_PROFILE="${BPI_CM6_CAMERA_PROFILE:-none}"
}

function extension_prepare_config__bpi_k1_vendor() {
	local config
	config="$(bpi_k1_native_config)"
	local expected_source expected_commit
	expected_source="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["profile"]["kernel_repository"])' <<< "${config}")"
	expected_commit="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["profile"]["kernel_commit"])' <<< "${config}")"
	[[ "${KERNELSOURCE}" == "${expected_source}" && "${KERNELBRANCH}" == "commit:${expected_commit}" ]] || \
		exit_with_error "核心來源與 K1 固定配套不符" "${BOARD}"
	declare -g BPI_K1_BOARD BPI_K1_PROFILE_SHA256 BPI_K1_ASSET_SHA256
	BPI_K1_BOARD="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["board"])' <<< "${config}")"
	BPI_K1_PROFILE_SHA256="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["profile_sha256"])' <<< "${config}")"
	BPI_K1_ASSET_SHA256="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["asset_sha256"])' <<< "${config}")"
	if [[ "${BPI_K1_BOARD}" == "bpi-cm6" ]]; then
		# 額外目錄在核心產物身分計算前加入，並由框架統一排序及套用補丁。
		declare -g KERNELPATCHDIR="archive/bananapicm6-legacy archive/bananapicm6-native-eth0"
		[[ "${BPI_CM6_CAMERA_PROFILE}" != "dual-imx415" ]] || KERNELPATCHDIR+=" archive/bananapicm6-native-dual-imx415"
	fi
	local cache_hash
	cache_hash="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["cache_sha256"])' <<< "${config}")"
	local suffix="-k1native-${cache_hash:0:16}"
	[[ "${EXTRA_ROOTFS_NAME:-}" == *"${suffix}"* ]] || EXTRA_ROOTFS_NAME="${EXTRA_ROOTFS_NAME:-}${suffix}"
	add_packages_to_image gjs gnome-control-center ibus python3-numpy python3-pil python3-opencv \
		mesa-utils vulkan-tools cloud-guest-utils gdisk e2fsprogs
}

function add_host_dependencies__bpi_k1_vendor() {
	local dependencies
	local -a selected=()
	# 套件與精確版本取自既有來源鎖；讓框架統一處理 APT 與 Docker 相依。
	dependencies="$(python3 "${SRC}/tools/bpi_k1_host_dependencies.py" packages \
		--board "${BOARD}" --camera "${BPI_CM6_CAMERA_PROFILE:-none}" \
		--host-release "${host_release:-${HOSTRELEASE:-}}" --host-arch "${host_arch:-${HOSTARCH:-}}")" || {
		exit_with_error "K1 主機配套規劃失敗；目前支援 Ubuntu Noble amd64" "${BOARD}"
		return 1
	}
	mapfile -t selected <<< "${dependencies}"
	EXTRA_BUILD_DEPS+=("${selected[@]}")
}

function host_dependencies_ready__bpi_k1_vendor() {
	declare -g BPI_K1_WORK_DIR="${SRC}/.tmp/bpi-k1-native-${ARMBIAN_BUILD_UUID}"
	declare -g BPI_K1_ASSET_DIR="${BPI_K1_ASSET_DIR:-${SRC}/cache/bpi-k1-native/${BPI_K1_ASSET_SHA256}}"
	mkdir -p "${BPI_K1_WORK_DIR}"
	# 先確認框架已準備固定 GPIO 工具鏈、相機主機相依與藍牙編譯器，再下載／建置資產。
	python3 "${SRC}/tools/bpi_k1_host_dependencies.py" verify \
		--board "${BOARD}" --camera "${BPI_CM6_CAMERA_PROFILE:-none}" \
		--host-release "${HOSTRELEASE}" --host-arch "${HOSTARCH}" \
		> "${BPI_K1_WORK_DIR}/host-dependencies.json" || {
		exit_with_error "K1 主機配套與來源鎖不符" "${BOARD}"
		return 1
	}
	bpi_k1_native_config --output "${BPI_K1_WORK_DIR}/config.json" > "${BPI_K1_WORK_DIR}/config-preflight.json"
	python3 "${SRC}/tools/bpi_k1_native.py" assets --config "${BPI_K1_WORK_DIR}/config.json" \
		--output "${BPI_K1_ASSET_DIR}" > "${BPI_K1_WORK_DIR}/assets.json"
}

function post_repo_customize_image__bpi_k1_native_rootfs() {
	local -a extra=() install_args=()
	if [[ "${BPI_K1_BOARD}" == "bpi-cm6" ]]; then
		local package
		package="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["artifact"])' \
			"${BPI_K1_ASSET_DIR}/bluetooth-package/package-manifest.json")"
		extra+=(--cm6-bluetooth-package "${BPI_K1_ASSET_DIR}/bluetooth-package/${package}")
		extra+=(--cm6-gpio-cache "${BPI_K1_ASSET_DIR}/gpio-cache")
		[[ "${BPI_CM6_CAMERA_PROFILE}" != "dual-imx415" ]] || extra+=(--cm6-camera-cache "${BPI_K1_ASSET_DIR}/camera-cache")
	fi
	python3 "${SRC}/tools/bpi_k1_native_rootfs.py" stage \
		--board "${BPI_K1_BOARD}" --armbian-board "${BOARD}" \
		--rootfs "${SDCARD}" --work-dir "${BPI_K1_WORK_DIR}/integration" \
		--build-id "${ARMBIAN_BUILD_UUID}" --deb-cache "${BPI_K1_ASSET_DIR}/deb-cache" \
		"${extra[@]}" > "${BPI_K1_WORK_DIR}/stage-result.json"
	python3 -c 'import json,sys; values=json.load(open(sys.argv[1]))["install_args"]; assert all(isinstance(v,str) and v and "\n" not in v for v in values); print("\n".join(values))' \
		"${BPI_K1_WORK_DIR}/stage-result.json" > "${BPI_K1_WORK_DIR}/install-args"
	mapfile -t install_args < "${BPI_K1_WORK_DIR}/install-args"
	[[ "${#install_args[@]}" -gt 0 ]] || exit_with_error "原生配套安裝清單為空"
	chroot_sdcard_apt_get --no-remove --allow-downgrades --no-install-recommends install "${install_args[@]@Q}"
	chroot_sdcard dpkg --audit
}

function post_post_debootstrap_tweaks__900_bpi_k1_native_finish() {
	# 框架已完成 autoremove，此時固定套件清單才可綁定最終封存。
	local python_import_probe='import cv2, numpy; from PIL import Image; print("AI 前處理依賴匯入通過：cv2=" + cv2.__version__ + "，NumPy=" + numpy.__version__ + "，Pillow=" + Image.__version__)'
	chroot_sdcard timeout 30s /usr/bin/python3 -I -B -c "${python_import_probe@Q}" || \
		exit_with_error "AI 前處理依賴匯入失敗或逾時" "cv2／NumPy／Pillow"
	python3 "${SRC}/tools/bpi_k1_native_rootfs.py" finish --rootfs "${SDCARD}" \
		--work-dir "${BPI_K1_WORK_DIR}/integration" --build-id "${ARMBIAN_BUILD_UUID}" \
		> "${BPI_K1_WORK_DIR}/integration-result.json"
}

function pre_umount_final_image__900_bpi_k1_native_export() {
	# 使用最終 initramfs 更新後的 MOUNT；不重新讀取任何歷史 IMG。
	python3 "${SRC}/tools/bpi_k1_native.py" export --config "${BPI_K1_WORK_DIR}/config.json" \
		--rootfs "${MOUNT}" --integration-dir "${BPI_K1_WORK_DIR}/integration" \
		--output "${BPI_K1_WORK_DIR}/prepared" > "${BPI_K1_WORK_DIR}/export-result.json"
}

function post_build_image__850_bpi_k1_vendor_outputs() {
	local storage
	local -a selected=()
	IFS=',' read -r -a selected <<< "${BPI_K1_OUTPUTS}"
	local output_root="${SRC}/output/vendor-format/${BPI_K1_RELEASE_ID}/${BPI_K1_BOARD}"
	if [[ -n "${BPI_K1_BOARD_TARGET:-}" ]]; then
		# 完整板名與本次建置識別分開保存，容許重建而不覆寫既有成品。
		output_root="${SRC}/output/vendor-format/${BPI_K1_RELEASE_ID}/${BOARD}/${ARMBIAN_BUILD_UUID}"
	fi
	for storage in "${selected[@]}"; do
		python3 "${SRC}/tools/package_bpi_k1_vendor.py" build \
			--board "${BPI_K1_BOARD}" --storage "${storage}" \
			--prepared "${BPI_K1_WORK_DIR}/prepared" --reference "${BPI_K1_ASSET_DIR}/vendor-reference" \
			--release-id "${BPI_K1_RELEASE_ID}" --output "${output_root}/${storage}"
	done
	python3 "${SRC}/tools/bpi_k1_native_evidence.py" --prepared "${BPI_K1_WORK_DIR}/prepared" \
		--output-root "${output_root}" --config "${BPI_K1_WORK_DIR}/prepared/native-config.json"
	display_alert "官方格式成品與各自驗證紀錄已產生" "${output_root}" "info"
}
