PACKAGE_LIST_BOARD+=" gpiod i2c-tools python3-libgpiod python3-spidev v4l-utils"

if [[ "${BUILD_DESKTOP:-no}" == "yes" ]]; then
	PACKAGE_LIST_BOARD+=" gstreamer1.0-tools gstreamer1.0-plugins-bad libdrm-tests"
fi

function image_specific_armbian_env_ready__bananapi_h618_cma() {

	# 為 H.265 1080p 解碼保留足夠的連續記憶體。
	local env_file="${SDCARD}/boot/armbianEnv.txt"
	if grep -Eq '^extraargs=.*cma=' "${env_file}"; then
		sed -E -i '/^extraargs=/ s/cma=[^[:space:]]+/cma=256M/' "${env_file}"
	elif grep -q '^extraargs=' "${env_file}"; then
		sed -i '/^extraargs=/ s/$/ cma=256M/' "${env_file}"
	else
		echo "extraargs=cma=256M" >> "${env_file}"
	fi

}

function post_family_tweaks_bsp__bananapi_h618_io_tools() {

	install -d -m 0755 \
		"${destination}/etc/udev/rules.d" \
		"${destination}/usr/local/bin" \
		"${destination}/usr/local/sbin"

	install -m 0644 "${SRC}/packages/bsp/bananapi-h618/99-bananapi-h618-io.rules" \
		"${destination}/etc/udev/rules.d/99-bananapi-h618-io.rules"
	install -m 0755 "${SRC}/packages/bsp/bananapi-h618/bpi-h618-hw-info" \
		"${destination}/usr/local/bin/bpi-h618-hw-info"
	install -m 0755 "${SRC}/packages/bsp/bananapi-h618/bpi-h618-io-compat-install" \
		"${destination}/usr/local/sbin/bpi-h618-io-compat-install"

}
