#!/usr/bin/env bash
set -Eeuo pipefail

candidate_release="${CANDIDATE_RELEASE:?必須設定 CANDIDATE_RELEASE}"
candidate_state="${CANDIDATE_STATE:?必須設定 CANDIDATE_STATE}"
source_commit="${SOURCE_COMMIT:?必須設定 SOURCE_COMMIT}"
build_context="${BUILD_CONTEXT_SHA256:?必須設定 BUILD_CONTEXT_SHA256}"
done_signal="${COMPRESSION_DONE_SIGNAL:?必須設定 COMPRESSION_DONE_SIGNAL}"
xz_threads="${XZ_THREADS:-2}"
idle_seconds="${COMPRESSION_IDLE_SECONDS:-5}"
progress="${COMPRESSION_PROGRESS:-${candidate_state}/runs/compression.tsv}"

required_commands=(awk basename date dirname find flock mkdir mv readlink rm sed sha256sum sleep sort sync wc xz)
for command in "${required_commands[@]}"; do
	command -v "${command}" >/dev/null || {
		printf '缺少必要命令：%s\n' "${command}" >&2
		exit 1
	}
done
[[ "${source_commit}" =~ ^[0-9a-f]{40}$ ]] || {
	printf 'SOURCE_COMMIT 格式錯誤。\n' >&2
	exit 2
}
[[ "${build_context}" =~ ^[0-9a-f]{64}$ ]] || {
	printf 'BUILD_CONTEXT_SHA256 格式錯誤。\n' >&2
	exit 2
}
[[ "${xz_threads}" =~ ^[0-9]+$ ]] || {
	printf 'XZ_THREADS 必須是非負整數。\n' >&2
	exit 2
}
[[ "${idle_seconds}" =~ ^[0-9]+$ ]] || {
	printf 'COMPRESSION_IDLE_SECONDS 必須是非負整數。\n' >&2
	exit 2
}
[[ -d "${candidate_release}" && -d "${candidate_state}" ]] || {
	printf '候選發布或狀態目錄不存在。\n' >&2
	exit 1
}

read_value() {
	local marker="$1" key="$2"
	sed -n "s/^${key}=//p" "${marker}" | awk 'NR == 1 { print }'
}

verify_sidecar() {
	local image="$1" digest="$2" sidecar_digest sidecar_name extra
	[[ -f "${image}" && -f "${image}.sha" ]] || return 1
	[[ "$(wc -l < "${image}.sha")" -eq 1 ]] || return 1
	read -r sidecar_digest sidecar_name extra < "${image}.sha" || return 1
	[[ -z "${extra:-}" && "${sidecar_digest}" == "${digest}" &&
		"${sidecar_name#\*}" == "$(basename "${image}")" ]]
}

claim_next_marker() {
	local marker claimed
	marker="$(
		find "${candidate_state}/raw-items" -maxdepth 1 -type f \
			\( -name '*.compressing' -o -name '*.ready' \) -print |
			LC_ALL=C sort | awk 'NR == 1 { print }'
	)"
	[[ -n "${marker}" ]] || return 1
	if [[ "${marker}" == *.compressing && -e "${marker%.compressing}.ready" ]] ||
		[[ "${marker}" == *.ready && -e "${marker%.ready}.compressing" ]]; then
		printf '同一項目同時具有 ready 與 compressing 標記：%s\n' "${marker}" >&2
		return 2
	fi
	if [[ "${marker}" == *.ready ]]; then
		claimed="${marker%.ready}.compressing"
		mv -T "${marker}" "${claimed}" || return 1
		marker="${claimed}"
	fi
	printf '%s\n' "${marker}"
}

compress_marker() {
	local marker="$1" folder board branch release profile archive target raw_image raw_digest
	local resolved_target resolved_release resolved_raw resolved_raw_root actual_raw partial digest decompressed
	local item_marker log log_digest framework_log framework_digest fresh build_uuid
	folder="$(read_value "${marker}" folder)"
	board="$(read_value "${marker}" board)"
	branch="$(read_value "${marker}" branch)"
	release="$(read_value "${marker}" release)"
	profile="$(read_value "${marker}" profile)"
	archive="$(read_value "${marker}" archive)"
	target="$(read_value "${marker}" target_archive)"
	raw_image="$(read_value "${marker}" raw_image)"
	raw_digest="$(read_value "${marker}" raw_sha256)"
	[[ "$(read_value "${marker}" source_commit)" == "${source_commit}" ]] || return 1
	[[ "$(read_value "${marker}" build_context_sha256)" == "${build_context}" ]] || return 1
	[[ "${folder}" =~ ^[a-z0-9-]+$ && "${board}" =~ ^[a-z0-9-]+$ ]] || return 1
	[[ "${branch}" =~ ^(current|edge|legacy|vendor)$ ]] || return 1
	[[ "${release}" =~ ^(trixie|bookworm|jammy|noble|resolute)$ ]] || return 1
	[[ "${profile}" =~ ^(minimal|xfce)$ ]] || return 1
	[[ -n "${archive}" && "${archive}" == "$(basename "${archive}")" ]] || return 1
	[[ "$(basename "${target}")" == "${archive}" ]] || return 1
	[[ "${raw_digest}" =~ ^[0-9a-f]{64}$ ]] || return 1
	resolved_release="$(readlink -f -- "${candidate_release}")"
	resolved_target="$(readlink -m -- "${target}")"
	[[ "${resolved_target}" == "${resolved_release}/.staging-${folder}-"*/* ]] || return 1
	resolved_raw_root="$(readlink -f -- "${candidate_state}/raw-images")"
	resolved_raw="$(readlink -m -- "${raw_image}")"
	[[ "${resolved_raw}" == "${resolved_raw_root}"/* ]] || return 1
	verify_sidecar "${raw_image}" "${raw_digest}" || return 1
	actual_raw="$(sha256sum "${raw_image}" | awk '{ print $1 }')"
	[[ "${actual_raw}" == "${raw_digest}" ]] || return 1

	mkdir -p "$(dirname "${target}")" "${candidate_state}/items" || return 1
	if [[ -f "${target}" ]]; then
		decompressed="$(xz -dc "${target}" | sha256sum | awk '{ print $1 }')" || return 1
		if [[ "${decompressed}" != "${raw_digest}" ]]; then
			printf '既有未完成壓縮檔與原始映像不符，重新產生：%s\n' "${target}"
			rm -f -- "${target}" "${target}.sha"
		fi
	fi
	if [[ ! -f "${target}" ]]; then
		partial="${target}.partial-$(read_value "${marker}" build_uuid)"
		rm -f -- "${partial}"
		xz -T"${xz_threads}" -6 -c "${raw_image}" > "${partial}" || return 1
		xz -t "${partial}" || return 1
		mv -T "${partial}" "${target}" || return 1
		sync -f "${target}" || return 1
	fi
	xz -t "${target}" || return 1
	digest="$(sha256sum "${target}" | awk '{ print $1 }')"
	printf '%s  %s\n' "${digest}" "${archive}" > "${target}.sha.partial" || return 1
	mv -T "${target}.sha.partial" "${target}.sha" || return 1
	sync -f "${target}.sha" || return 1

	log="$(read_value "${marker}" log)"
	log_digest="$(read_value "${marker}" log_sha256)"
	framework_log="$(read_value "${marker}" framework_log)"
	framework_digest="$(read_value "${marker}" framework_log_sha256)"
	[[ -s "${log}" && "$(sha256sum "${log}" | awk '{ print $1 }')" == "${log_digest}" ]] || return 1
	[[ -s "${framework_log}" && "$(sha256sum "${framework_log}" | awk '{ print $1 }')" == "${framework_digest}" ]] || return 1
	fresh="$(read_value "${marker}" fresh_artifacts)"
	build_uuid="$(read_value "${marker}" build_uuid)"
	item_marker="${candidate_state}/items/${folder}-${release}-${profile}.complete"
	{
		printf 'source_commit=%s\n' "${source_commit}"
		printf 'bsp_base_commit=%s\n' "$(read_value "${marker}" bsp_base_commit)"
		printf 'matrix_sha256=%s\n' "$(read_value "${marker}" matrix_sha256)"
		printf 'userpatches_sha256=%s\n' "$(read_value "${marker}" userpatches_sha256)"
		printf 'build_context_sha256=%s\n' "${build_context}"
		printf 'folder=%s\nboard=%s\nbranch=%s\n' "${folder}" "${board}" "${branch}"
		printf 'release=%s\nprofile=%s\n' "${release}" "${profile}"
		printf 'archive=%s\nsha256=%s\n' "${archive}" "${digest}"
		printf 'fresh_artifacts=%s\nbuild_uuid=%s\n' "${fresh}" "${build_uuid}"
		printf 'log=%s\nlog_sha256=%s\n' "${log}" "${log_digest}"
		printf 'framework_log=%s\nframework_log_sha256=%s\n' "${framework_log}" "${framework_digest}"
		printf 'status=complete\ncompleted_utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	} > "${item_marker}.partial" || return 1
	mv -T "${item_marker}.partial" "${item_marker}" || return 1
	sync -f "${item_marker}" || return 1
	rm -f -- "${raw_image}" "${raw_image}.sha" "${marker}"
	rmdir "$(dirname "${raw_image}")" 2>/dev/null || true
	printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
		"$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${folder}" "${board}" "${release}" \
		"${profile}" "${archive}" "${digest}" >> "${progress}"
	printf '壓縮完成：%s %s %s\n' "${board}" "${release}" "${profile}"
}

mkdir -p "${candidate_state}/raw-items" "${candidate_state}/raw-images" "${candidate_state}/runs"
exec 9> "${candidate_state}/.compression-worker.lock"
flock -n 9 || {
	printf '另一個壓縮工作正在執行。\n' >&2
	exit 73
}
if [[ ! -f "${progress}" ]]; then
	printf '時間UTC\t板目錄\t板卡\t發行版\t類型\t映像\tSHA256\n' > "${progress}"
fi

while true; do
	claim_status=0
	marker="$(claim_next_marker)" || claim_status=$?
	if ((claim_status == 0)); then
		compress_marker "${marker}" || {
			printf '壓縮工作失敗並保留續跑狀態：%s\n' "${marker}" >&2
			exit 1
		}
		continue
	fi
	((claim_status == 1)) || exit "${claim_status}"
	if [[ -f "${done_signal}" ]]; then
		printf '壓縮佇列已清空。\n'
		exit 0
	fi
	sleep "${idle_seconds}"
done
