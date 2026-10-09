# Bookworm Mesa 重建配方

來源 `22.3.6-1+deb12u2`；候選 `22.3.6-1+deb12u2+bpi1`；架構 `arm64`。
本地候選不是官方發行；G31 回補不代表 TERM 修復。
共同驗收與安裝規則見[上一層](../README.zh-TW.md)。

## 來源與驗簽

官方來源根網址：<https://deb.debian.org/debian/pool/main/m/mesa/>。
`SHA256SUMS.source.txt` 釘選下列完整來源與本地補丁。

| 檔案 | 位元組 |
| --- | ---: |
| `mesa_22.3.6-1+deb12u2.dsc` | 5888 |
| `mesa_22.3.6.orig.tar.gz` | 27497916 |
| `mesa_22.3.6-1+deb12u2.diff.gz` | 116941 |

採 Debian archive keyring 驗證 `InRelease`，再驗證 `Sources.xz` 與三份來源包。
原 `.dsc` 的舊維護者金鑰曾回報 `EXPKEYSIG`；不將它當成有效的獨立簽章。
以下腳本以有效官方封存庫簽章鏈認證同一 `.dsc`，不略過簽章錯誤。
當次 `Sources.xz` SHA 為 `92d11a035571df011f06f28c61cb9825130ef1d92abb2f2e0525e8674dc44af8`。
索引更新後須重新驗簽；仍須包含唯一指定版本及完全相同的來源 SHA／大小。

補丁 SHA 為 `1e62f7653e00dcf4368a902f6d352f796a46c5cc61eac704e3b0ea3b17498ff8`。
只回補全畫面 ZS 預載的 `ALWAYS` 判斷，並將 `EARLY_ZS_ALWAYS` 限於
`arch > 6 && gpu_id >= 0x7200`，不搬入不適用的新版 API。

## 隔離環境

使用 Debian 12 arm64。參考官方容器識別：
`debian@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587`。
啟用官方 `bookworm`、`bookworm-updates`、`bookworm-security` 的 `main`。
維持預設簽章驗證。依賴以完整來源的 `debian/control` 為準。
`build-dependencies.tsv` 是參考環境的精確版本清單，不是目標映像安裝集合。

由隔離環境管理者執行：

```bash
set -euo pipefail
apt-get update
apt-get install --no-install-recommends -y \
  build-essential dpkg-dev fakeroot ca-certificates python3-debian \
  curl xz-utils quilt devscripts gpgv debian-archive-keyring
```

## 取得完整來源

將本子目錄的檔案放入空白工作目錄，以非 root 建置帳號執行：

```bash
set -euo pipefail
base=https://deb.debian.org/debian/pool/main/m/mesa
curl -fLO "$base/mesa_22.3.6-1+deb12u2.dsc"
curl -fLO "$base/mesa_22.3.6.orig.tar.gz"
curl -fLO "$base/mesa_22.3.6-1+deb12u2.diff.gz"
curl -fLo InRelease https://deb.debian.org/debian/dists/bookworm/InRelease
curl -fLo Sources.xz https://deb.debian.org/debian/dists/bookworm/main/source/Sources.xz
python3 verify-official-source.py
sha256sum -c SHA256SUMS.source.txt
dpkg-source -x mesa_22.3.6-1+deb12u2.dsc mesa
```

在同一工作目錄，由隔離環境管理者安裝完整建置依賴：

```bash
set -euo pipefail
apt-get build-dep --no-install-recommends -y ./mesa
```

此命令包含原封裝的 LLVM、OpenCL、Vulkan、Nine、VA／VDPAU 等依賴。
不裁成 Panfrost 專用版本，也不混用其他發行版套件。

## 套用與建包

回到非 root 建置帳號，在同一工作目錄執行：

```bash
set -euo pipefail
cd mesa
export LC_ALL=C.UTF-8
export QUILT_PATCHES=debian/patches QUILT_PATCH_OPTS=--fuzz=0
quilt --quiltrc /dev/null push -a --fuzz=0
cp ../bpi-g31-preload.patch debian/patches/bpi-g31-preload.patch
printf '%s\n' bpi-g31-preload.patch >> debian/patches/series
quilt --quiltrc /dev/null push --fuzz=0 > ../bpi-patch-apply.log 2>&1
if grep -Ei 'offset|with fuzz|FAILED' ../bpi-patch-apply.log; then exit 1; fi
printf '%s  %s\n' \
  06e5d42abef836a5a637fc6d2ea87a0d4073dac234ac9dabd3bae02dd80b9d7a \
  src/panfrost/lib/pan_blitter.c | sha256sum -c -
DEBFULLNAME='BPI Mesa Backport' DEBEMAIL='mesa-backport@example.invalid' \
  dch --newversion '22.3.6-1+deb12u2+bpi1' --distribution bookworm \
  '回補 G31 預載判斷；保留原 Debian 功能與套件組，尚待實板驗證。'
export DEB_BUILD_OPTIONS=parallel=6 DEB_BUILD_PROFILES=
export MAKEFLAGS=-j6 CARGO_BUILD_JOBS=6 MESON_TESTTHREADS=6
dpkg-checkbuilddeps
dpkg-buildpackage -b -us -uc -j6
```

原 Debian 六份補丁保留，部分原補丁本來有偏移；本地補丁要求零 fuzz、零偏移。
不修改原 `debian/rules`、`debian/control` 或 symbols，不使用 `nocheck`。
changelog 時間戳與依賴倉庫可能不同，不保證二進位逐位元一致。

## 成套核對

`mesa-packages.tsv` 列出 33 件參考產物：22 件標準包、11 件 dbgsym，均為 `.deb`。
每件均須 `Source=mesa`、`Version=22.3.6-1+deb12u2+bpi1`、`Architecture=arm64`。
用 `dpkg-deb -f` 核對新產物的 `Source`、`Package`、`Version`、`Architecture`。
`Source` 可帶括號版本；套件名稱、來源名稱與架構集合須和清單一致。
依賴欄位可能因官方依賴版本更新而改變，須重新解算，不能直接複用舊結論。

`runtime-closure.tsv` 是五核心與完整執行期的兩組參考閉包。
只計 `Depends`／`Pre-Depends`，不代表應全裝，也不是完整 apt snapshot。
實際映像安裝集合與驗收限制均遵守上一層規則。
