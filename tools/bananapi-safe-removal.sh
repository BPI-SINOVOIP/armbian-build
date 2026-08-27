#!/usr/bin/env bash

bananapi_path_contains_symlink() {
	local path="$1"
	local probe="${path}"

	while [[ "${probe}" != "/" && "${probe}" != "." ]]; do
		if [[ -L "${probe}" ]]; then
			return 0
		fi
		probe="$(dirname -- "${probe}")"
	done
	return 1
}

bananapi_require_safe_removal_target() {
	local target="$1"
	local allowed_prefix="$2"
	local minimum_relative_depth="${3:-1}"
	local canonical_target canonical_prefix command_name relative mount_target
	local -a absolute_parts relative_parts

	for command_name in dirname findmnt mountpoint realpath; do
		command -v "${command_name}" >/dev/null 2>&1 || {
			printf '安全刪除守門缺少必要命令：%s\n' "${command_name}" >&2
			return 1
		}
	done

	[[ -n "${target}" && -n "${allowed_prefix}" ]] || {
		printf '安全刪除守門拒絕空路徑。\n' >&2
		return 1
	}
	[[ "${minimum_relative_depth}" =~ ^[1-9][0-9]*$ ]] || {
		printf '安全刪除守門的最小深度必須是正整數。\n' >&2
		return 1
	}
	[[ -d "${target}" ]] || {
		printf '安全刪除守門只接受既有目錄：%s\n' "${target}" >&2
		return 1
	}
	[[ -d "${allowed_prefix}" ]] || {
		printf '安全刪除守門找不到允許前綴：%s\n' "${allowed_prefix}" >&2
		return 1
	}
	if bananapi_path_contains_symlink "${target}" || bananapi_path_contains_symlink "${allowed_prefix}"; then
		printf '安全刪除守門拒絕含符號連結的路徑：%s\n' "${target}" >&2
		return 1
	fi

	canonical_target="$(realpath -e -- "${target}")"
	canonical_prefix="$(realpath -e -- "${allowed_prefix}")"
	[[ "${canonical_prefix}" != "/" ]] || {
		printf '安全刪除守門拒絕以根目錄作為允許前綴。\n' >&2
		return 1
	}
	case "${canonical_target}" in
		"${canonical_prefix}"/*)
			;;
		*)
			printf '安全刪除守門拒絕前綴外路徑：%s\n' "${canonical_target}" >&2
			return 1
			;;
	esac

	relative="${canonical_target#"${canonical_prefix}"/}"
	IFS='/' read -r -a relative_parts <<< "${relative}"
	if ((${#relative_parts[@]} < minimum_relative_depth)); then
		printf '安全刪除守門拒絕深度不足的路徑：%s\n' "${canonical_target}" >&2
		return 1
	fi
	if mountpoint -q -- "${canonical_target}"; then
		printf '安全刪除守門拒絕掛載點：%s\n' "${canonical_target}" >&2
		return 1
	fi
	while IFS= read -r mount_target; do
		case "${mount_target}" in
			"${canonical_target}" | "${canonical_target}"/*)
				printf '安全刪除守門拒絕含掛載點的目錄：%s\n' "${mount_target}" >&2
				return 1
				;;
		esac
	done < <(findmnt -rn --raw -o TARGET)

	IFS='/' read -r -a absolute_parts <<< "${canonical_target#/}"
	if ((${#absolute_parts[@]} < 4)); then
		printf '安全刪除守門拒絕過淺的絕對路徑：%s\n' "${canonical_target}" >&2
		return 1
	fi
}
