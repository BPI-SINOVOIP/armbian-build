#!/usr/bin/env bash
# K1 桌面來源與安裝器共用固定來源；由 bpi-k1-vendor 明確啟用。

function pre_desktop_sources__bpi_k1_desktop() {
	# 桌面解析早於完整主機依賴安裝；缺件時提供可操作的前置命令。
	python3 -c 'import yaml' >/dev/null 2>&1 || \
		exit_with_error "請先安裝桌面解析依賴" "sudo apt-get install python3 python3-yaml"
	declare -g BPI_K1_DESKTOP_PROFILE="${SRC}/config/spacemit-k1-profiles/desktop-noble-gnome.json"
	declare -g DESKTOP_ENVIRONMENT="${DESKTOP_ENVIRONMENT:-gnome}"
	declare -g DESKTOP_TIER="${DESKTOP_TIER:-minimal}"
	python3 "${SRC}/tools/bpi_k1_desktop.py" validate \
		--profile "${BPI_K1_DESKTOP_PROFILE}" --board "${BOARD}" --release "${RELEASE}" \
		--arch "${ARCH}" --desktop "${DESKTOP_ENVIRONMENT}" --tier "${DESKTOP_TIER}"
	declare -g CONFIGNG_REPOSITORY="https://github.com/armbian/configng"
	declare -g CONFIGNG_REF="commit:93d12895028410789962b2e6a1b8b2a25e69d1fc"
	declare -g CONFIGNG_CACHE_NAME="armbian-configng-k1-93d128950284"
}

function post_desktop_sources__bpi_k1_desktop() {
	declare -g BPI_K1_DESKTOP_FINGERPRINT
	BPI_K1_DESKTOP_FINGERPRINT="$(python3 "${SRC}/tools/bpi_k1_desktop.py" fingerprint --profile "${BPI_K1_DESKTOP_PROFILE}")"
	declare -g BPI_K1_DESKTOP_PREPARED="${SRC}/cache/bpi-k1-desktop/${BPI_K1_DESKTOP_FINGERPRINT}"
	python3 "${SRC}/tools/bpi_k1_desktop.py" prepare \
		--profile "${BPI_K1_DESKTOP_PROFILE}" --source "${CONFIGNG_DIRECTORY}" --output "${BPI_K1_DESKTOP_PREPARED}"
	declare -g DESKTOP_SOURCE_DIRECTORY="${BPI_K1_DESKTOP_PREPARED}/runtime"
	# 不覆蓋其他擴充已加入的快取後綴；重入時亦不重複加入。
	local suffix="-k1desktop-${BPI_K1_DESKTOP_FINGERPRINT:0:16}"
	[[ "${EXTRA_ROOTFS_NAME:-}" == *"${suffix}"* ]] || EXTRA_ROOTFS_NAME="${EXTRA_ROOTFS_NAME:-}${suffix}"
}

function pre_install_desktop__bpi_k1_desktop() {
	python3 "${SRC}/tools/bpi_k1_desktop.py" install \
		--profile "${BPI_K1_DESKTOP_PROFILE}" --prepared "${BPI_K1_DESKTOP_PREPARED}" --rootfs "${SDCARD}"
	declare -g DESKTOP_INSTALLER="/usr/lib/bpi-k1-configng/bin/armbian-config"
}
