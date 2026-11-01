#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

# Accept the existing optional BOARD BRANCH BUILD_TYPE positional arguments.
board=bananapim8
branch=legacy
extra=()
if (( $# )) && [[ $1 != *=* ]]; then board=$1; shift; fi
if (( $# )) && [[ $1 != *=* ]]; then branch=$1; shift; fi
if (( $# )) && [[ $1 != *=* ]]; then extra+=("BUILD_TYPE=$1"); shift; fi

# Pass KEY=value overrides last. Cache cleaning and mirror/SSH policy are
# explicit caller choices; a normal build never deletes all cached packages.
exec ./compile.sh build \
    BOARD="$board" BRANCH="$branch" RELEASE=resolute \
    BUILD_DESKTOP=yes BUILD_MINIMAL=no \
    DESKTOP_ENVIRONMENT=xfce DESKTOP_ENVIRONMENT_CONFIG_NAME=config_base \
    DESKTOP_TIER=mid KERNEL_CONFIGURE=no INSTALL_HEADERS=yes \
    COMPRESS_OUTPUTIMAGE=sha,xz SKIP_ARMBIAN_REPO=yes \
    KERNEL_USE_MAINLINE_BARE_REPO=no UBOOT_USE_MAINLINE_BARE_REPO=no \
    "${extra[@]}" "$@"
