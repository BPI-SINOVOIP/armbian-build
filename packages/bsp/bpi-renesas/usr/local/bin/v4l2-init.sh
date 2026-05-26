#!/bin/sh
mode=$1
scriptname=`basename $0`
log() {
    echo "$scriptname: $@"
}
logn() {
    echo -n "$scriptname: $@"
}

load_modules() {
    udev_services="systemd-udevd systemd-udevd-kernel.socket systemd-udevd-control.socket"
    is_mod=`zcat /proc/config.gz | sed -ne 's/^CONFIG_VIDEO_RZV2N_ISP=//p'`
    case "$is_mod" in
        y)
            ;;
        *)
            if ! lsmod | grep -q mali_iv021_isp_iq; then
                systemctl stop $udev_services
                insmod /lib/modules/`uname -r`/kernel/drivers/media/platform/rzv2n-isp/subdev/sensor/mali_iv021_isp_sensor.ko
                insmod /lib/modules/`uname -r`/kernel/drivers/media/platform/rzv2n-isp/subdev/lens/mali_iv021_isp_lens.ko
                insmod /lib/modules/`uname -r`/kernel/drivers/media/platform/rzv2n-isp/subdev/iq/mali_iv021_isp_iq.ko
                systemctl start $udev_services
            fi
            ;;
    esac
    devices=""
    for m in 0 1; do
        if [ ! -c /dev/video${m}fr ]; then
            continue;
        fi
        devices="$devices /dev/video${m}fr"
    done
}

run_userspace_driver() {
    ps | grep mali_iv021_is[p] | awk '{print $1}' | xargs kill 2> /dev/null
    if [ -c /dev/video1fr ]; then
        mali_iv021_isp.elf &
    else
        mali_iv021_isp-single.elf &
    fi
}

calc_selection() {
    : ${width:=$1}
    : ${height:=$2}
    : ${effective_width:=$3}
    : ${effective_height:=$4}
    : ${top:=$(((height-effective_height)/2))}
    : ${left:=$(((width-effective_width)/2))}
}

errend() {
    echo $1
    exit 1
}

check_selection() {
    err=""
    [ $width -le 0 ] && err="width <= 0\n$err"
    [ $height -le 0 ] && err="height <= 0 $$err"
    [ $effective_height -le 0 ] && err="effective_height <= 0\n$err"
    [ $effective_width -le 0 ] && err="effective_width <= 0\n$err"
    [ $top -lt 0 ] && err="top < 0\n$err"
    [ $left -lt 0 ] && err="left < 0\n$err"
    if [ "$err" != "" ]; then
        echo "selection error."
        echo -ne $err
        exit 1
    fi
}

if [ "$mode" = "" ]; then
    mode=4k
fi
case "$mode" in
# MODE_START
    2k)
        calc_selection 1932 1088 1920 1080
        preset=2
        ;;
    2kdol)
        calc_selection 1932 1088 1920 1080
        preset=8
        ;;
    2khdr)
        calc_selection 1932 1088 1920 1080
        preset=10
        ;;
    dol)
        calc_selection 3864 2176 3840 2160
        preset=4
        ;;
    hdr)
        calc_selection 3864 2176 3840 2160
        preset=6
        ;;
    4k)
        calc_selection 3864 2176 3840 2160
        preset=0
        ;;
# MODE_END
    *)
        echo "unknown mode - $mode" 1>&2
        echo -n "supported modes: "
        sed -ne '/^# MODE_START/,/^# MODE_END/s/\s*\(.*\))/\1/p' < $0 | tr "\n" " "
        echo
        exit 1
        ;;
esac

run_media_ctl() {
    log "preset=$preset, ($width, $height) => ${effective_width}x${effective_height}+$left+$top"
    check_selection

    fmt="fmt:SBGGR12_1X12/${width}x${height} field:none"
    for m in 0 1; do
        if [ ! -c /dev/video${m}fr ]; then
            continue;
        fi
        csi2=""
        sensor=""
        cru=""
        while read entity; do
            case "$entity" in
                rzg2l_csi2*) csi2="$entity" ;;
                imx415*) sensor="$entity" ;;
                CRU*) cru="$entity" ;;
            esac
        done <<EOT
    `media-ctl -p -d /dev/media$m | sed -ne 's/- entity [0-9]*: \(.*\) (.*$/\1/p'`
EOT
        if [ "$csi2" != "" ] && [ "$sensor" != "" ] && [ "$cru" != "" ]; then
            media-ctl -d /dev/media$m -r
            media-ctl -d /dev/media$m -V "'$csi2':1 [$fmt]"
            media-ctl -d /dev/media$m -V "'$sensor':0 [$fmt]"
            media-ctl -d /dev/media$m -l "'$csi2':1 -> '$cru':0 [1]"
            media-ctl -d /dev/media$m -l "'$sensor':0 -> '$csi2':0 [1]"
            v4l2-ctl -d `media-ctl -d /dev/media$m -e "$cru"` --set-fmt-video=width=$width,height=$height,pixelformat=BG12
        fi
        v4l2-ctl -d /dev/video${m}fr -c isp_sensor_preset=$(($preset+$m))
    done
}

set_preset() {
    for m in 0 1; do
        if [ ! -c /dev/video${m}fr ]; then
            continue;
        fi
        v4l2-ctl -d /dev/video${m}fr -c isp_sensor_preset=$(($preset+$m))
    done
}

set_selection() {
    for dev in $devices; do
        for t in `seq 0 10`; do
            v4l2-ctl -d $dev --set-selection target=crop,left=$left,top=$top,width=$effective_width,height=$effective_height
            if [ "`v4l2-ctl -d $dev --get-selection target=crop | awk '{print $6$8$10$12}'`" = "$left,$top,${effective_width},${effective_height}," ]; then
                break
            fi
            if [ "$t" -eq 10 ]; then
                log "failed to set selections."
                break
            fi
            sleep 0.01
        done
    done
}

run_dummy_sample_app() {
    (
        echo q | ./sample-app $devices -w $effective_width -h $effective_height > /dev/null 2>&1 &
        pid=$!
        (
            sleep 3
            kill $pid > /dev/null 2>&1  /dev/null
        ) > /dev/null 2>&1 &
        pid2=$!
        wait $pid > /dev/null 2>&1
        status=$?
        kill $pid2 > /dev/null 2>&1
    )
}

run_dummy_gstreamer() {
    (
        exec > /dev/null
        exec 2> /dev/null
        gst-launch-1.0 -v v4l2src device=/dev/video1fr ! fakesink > /dev/null 2>&1 &
        pid3=$!
        sleep 1
        kill $pid3
    )
}

load_modules

run_media_ctl
logn "Initializing ."
set_preset
echo -n "."

for i in 1 2; do
    echo -n "."
    run_dummy_sample_app
    echo -n "."
    run_dummy_gstreamer
done

echo
run_userspace_driver
run_dummy_sample_app
set_selection

log "done."
exit 0
