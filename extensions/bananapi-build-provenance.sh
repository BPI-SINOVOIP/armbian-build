#!/usr/bin/env bash

function post_customize_image__995_bananapi_build_provenance() {
	[[ "${BPI_RELEASE_PROVENANCE_REQUIRED:-no}" == yes ]] || return 0

	local required
	for required in \
		BPI_RELEASE_BSP_BASE_COMMIT \
		BPI_RELEASE_SOURCE_COMMIT \
		BPI_RELEASE_MATRIX_SHA256 \
		BPI_RELEASE_USERPATCHES_SHA256 \
		BPI_RELEASE_BUILD_CONTEXT_SHA256 \
		BPI_RELEASE_PROFILE; do
		[[ -n "${!required:-}" ]] || exit_with_error "缺少映像來源證據欄位" "${required}"
	done

	install -d -m 0755 "${SDCARD}/etc"
	{
		printf 'bsp_base_commit=%s\n' "${BPI_RELEASE_BSP_BASE_COMMIT}"
		printf 'source_commit=%s\n' "${BPI_RELEASE_SOURCE_COMMIT}"
		printf 'matrix_sha256=%s\n' "${BPI_RELEASE_MATRIX_SHA256}"
		printf 'userpatches_sha256=%s\n' "${BPI_RELEASE_USERPATCHES_SHA256}"
		printf 'build_context_sha256=%s\n' "${BPI_RELEASE_BUILD_CONTEXT_SHA256}"
		printf 'board=%s\n' "${BOARD}"
		printf 'branch=%s\n' "${BRANCH}"
		printf 'release=%s\n' "${RELEASE}"
		printf 'profile=%s\n' "${BPI_RELEASE_PROFILE}"
	} > "${SDCARD}/etc/bananapi-build-provenance"
	chmod 0444 "${SDCARD}/etc/bananapi-build-provenance"
}
