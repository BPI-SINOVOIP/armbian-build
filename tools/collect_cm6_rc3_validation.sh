#!/usr/bin/env bash
# CM6 rc3 板端唯讀蒐證；不選取磁碟、不寫入裝置、不改變周邊狀態。
set -u
set -o pipefail
umask 077

usage() {
    cat <<'EOF'
用法：bash collect_cm6_rc3_validation.sh --output 新目錄 [--dry-run]
      bash collect_cm6_rc3_validation.sh --help

在待測 CM6 執行；需要核心日誌時可由 sudo 執行。父目錄須已存在。
--dry-run 只印出命令計畫，不建立目錄或執行蒐集命令。
每個命令最多執行 12 秒，另有 2 秒終止寬限。
既有檔案、目錄、符號連結與 /dev、/proc、/sys 路徑一律拒絕。

輸出：各命令的 .stdout.txt、.stderr.txt、command-status.tsv、
      collection-info.txt 與 SHA256SUMS。
COLLECTED 只代表命令完成，不能代替硬體測試 PASS。
BLOCKED 表示工具缺少或逾時；FAIL 表示命令回傳其他非零狀態。
結束碼：0＝命令均完成；2＝部分命令未完成但證據已保存；
        64＝參數錯誤；69＝缺少必要工具；73＝輸出目錄不安全或不可建立。

本工具不讀取密碼檔、連線設定檔、私密金鑰或程序環境，不掃描藍牙。
原始日誌可能含 IP、MAC、序號或現場自行寫入的資料；請依內部規則保管。
EOF
}

output=''
dry_run=0
while (($#)); do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --dry-run) dry_run=1; shift ;;
        --output)
            if (($# < 2)) || [[ -z $2 || $2 == --* ]]; then
                printf '錯誤：--output 必須提供新目錄。\n' >&2; exit 64
            fi
            if [[ -n $output ]]; then
                printf '錯誤：--output 不可重複。\n' >&2; exit 64
            fi
            output=$2; shift 2 ;;
        *) printf '錯誤：未知參數 %s\n' "$1" >&2; exit 64 ;;
    esac
done
if [[ -z $output ]]; then
    printf '錯誤：必須指定 --output 新目錄。\n' >&2; exit 64
fi

incomplete=0
run() {
    local id=$1 rc state command_text
    shift
    printf -v command_text '%q ' "$@"
    if ((dry_run)); then
        printf '%s\t%s\n' "$id" "$command_text"
        return
    fi
    if ! command -v "$1" >/dev/null 2>&1; then
        rc=127
        : >"$output/$id.stdout.txt"
        printf '缺少工具：%s\n' "$1" >"$output/$id.stderr.txt"
    else
        timeout --signal=TERM --kill-after=2s 12s "$@" \
            >"$output/$id.stdout.txt" 2>"$output/$id.stderr.txt"
        rc=$?
    fi
    case "$rc" in
        0) state=COLLECTED ;;
        124|127|137) state=BLOCKED; incomplete=1 ;;
        *) state=FAIL; incomplete=1 ;;
    esac
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "$id" "$state" "$rc" "$id.stdout.txt" "$id.stderr.txt" "$command_text" \
        >>"$output/command-status.tsv"
}

if ((!dry_run)); then
    for dependency in realpath mkdir date timeout sha256sum; do
        if ! command -v "$dependency" >/dev/null 2>&1; then
            printf '錯誤：缺少必要工具 %s，尚未開始蒐集。\n' "$dependency" >&2
            exit 69
        fi
    done
    # 先拒絕連結本身，再解析父目錄的連結，避免將 /dev 別名當作證據目錄。
    if [[ -e $output || -L $output ]]; then
        printf '錯誤：輸出位置已存在，拒絕覆寫：%s\n' "$output" >&2; exit 73
    fi
    output=$(realpath -m -- "$output") || exit 73
    case "$output" in
        /dev|/dev/*|/proc|/proc/*|/sys|/sys/*)
            printf '錯誤：不可將裝置或核心介面目錄作為輸出。\n' >&2; exit 73 ;;
    esac
    parent=${output%/*}
    [[ -n $parent ]] || parent=/
    if [[ ! -d $parent ]]; then
        printf '錯誤：父目錄不存在：%s\n' "$parent" >&2; exit 73
    fi
    if ! mkdir -m 700 -- "$output"; then
        printf '錯誤：無法建立全新輸出目錄。\n' >&2; exit 73
    fi
    printf 'command_id\tcollection_status\texit_code\tstdout_file\tstderr_file\tcommand\n' \
        >"$output/command-status.tsv"
    {
        printf '用途：BPI-CM6 rc3 唯讀板端蒐證\n'
        printf '開始時間（UTC）：'; date -u '+%Y-%m-%dT%H:%M:%SZ'
        printf '狀態語意：COLLECTED 只代表命令完成，不是硬體 PASS。\n'
        printf 'BLOCKED：缺工具或逾時；FAIL：其他非零結束碼。\n'
        printf '限制：不含實際傳輸、配對、推論、攝影、播放或磁碟讀寫驗證。\n'
        printf '保管：日誌可能包含 IP、MAC、裝置序號；不宜公開原始證據。\n'
    } >"$output/collection-info.txt"
fi

run identity-uname uname -a
run identity-os cat /etc/os-release
run identity-armbian cat /etc/armbian-release
run identity-vendor cat /etc/bpi-k1-vendor.json
# 固定命令交由子 Bash 展開，避免外層讀取任何程序環境。
# shellcheck disable=SC2016
run identity-model bash -c 'p=/proc/device-tree/model; if [[ -r $p ]]; then tr "\000" "\n" < "$p"; else printf "無法讀取板型\n" >&2; exit 1; fi'
run storage-lsblk lsblk -b -o NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,UUID,MOUNTPOINTS,MODEL,SERIAL,RO
run storage-findmnt findmnt -rn -o TARGET,SOURCE,FSTYPE,UUID
run storage-df df -hT
run network-link ip -s link show
run network-address ip -br address show
run network-route ip route show
run network-nmcli nmcli -f DEVICE,TYPE,STATE,CONNECTION device status
run bluetooth-rfkill rfkill list
run bluetooth-list bluetoothctl list
run bluetooth-show bluetoothctl show
run kernel-dmesg dmesg --time-format iso
run system-failed systemctl --no-pager --failed
run service-bluetooth systemctl --no-pager status bluetooth.service bpi-cm6-bluetooth.service
run service-network systemctl --no-pager status NetworkManager.service
run usb-list lsusb
run usb-tree lsusb -t
run pci-list lspci -nnk
# shellcheck disable=SC2016
run gpu-drm bash -c 'shopt -s nullglob; found=0; for p in /sys/class/drm/card*/status /sys/class/drm/card*/enabled /sys/class/drm/card*/modes /sys/module/pvrsrvkm/version /sys/kernel/debug/pvr/status; do if [[ -f $p && -r $p ]]; then found=1; printf "\n路徑：%s\n" "$p"; cat -- "$p" || exit; fi; done; ((found)) || { printf "沒有可讀取的 DRM／GPU 狀態\n" >&2; exit 1; }'
# shellcheck disable=SC2016
run thermal-state bash -c 'shopt -s nullglob; found=0; for p in /sys/class/thermal/thermal_zone*/type /sys/class/thermal/thermal_zone*/temp /sys/class/thermal/thermal_zone*/trip_point_*_temp /sys/class/thermal/cooling_device*/type /sys/class/thermal/cooling_device*/cur_state /sys/class/thermal/cooling_device*/max_state; do if [[ -f $p && -r $p ]]; then found=1; printf "%s\t" "$p"; cat -- "$p" || exit; fi; done; ((found)) || { printf "沒有可讀取的溫度節點\n" >&2; exit 1; }'
# shellcheck disable=SC2016
run cpu-frequency bash -c 'shopt -s nullglob; found=0; for p in /sys/devices/system/cpu/cpufreq/policy*/scaling_cur_freq /sys/devices/system/cpu/cpufreq/policy*/scaling_max_freq /sys/devices/system/cpu/cpufreq/policy*/cpuinfo_max_freq /sys/devices/system/cpu/cpufreq/policy*/scaling_governor; do if [[ -f $p && -r $p ]]; then found=1; printf "%s\t" "$p"; cat -- "$p" || exit; fi; done; ((found)) || { printf "沒有可讀取的 CPU 頻率節點\n" >&2; exit 1; }'
run audio-playback aplay -l
run audio-capture arecord -l
run video-devices v4l2-ctl --list-devices

if ((dry_run)); then
    exit 0
fi
printf '結束時間（UTC）：' >>"$output/collection-info.txt"
date -u '+%Y-%m-%dT%H:%M:%SZ' >>"$output/collection-info.txt"
if ! (cd -- "$output" && sha256sum -- \
    command-status.tsv collection-info.txt ./*.stdout.txt ./*.stderr.txt) \
    >"$output/SHA256SUMS"; then
    printf '錯誤：校驗檔產生失敗；請保留現有原始證據。\n' >&2
    exit 2
fi
printf '蒐證已保存：%s\n請核對 command-status.tsv；本工具不判定硬體 PASS。\n' "$output"
if ((incomplete)); then
    exit 2
fi
