# Jammy Mesa 重建配方

來源 `23.2.1-1ubuntu3.1~22.04.4`；候選 `23.2.1-1ubuntu3.1~22.04.4+bpi1`；架構 `arm64`。
本地候選不是官方發行；G31 回補不代表 TERM 修復。
共同驗收與安裝規則見[上一層](../README.zh-TW.md)。

## 來源與驗簽

官方來源根網址：<https://archive.ubuntu.com/ubuntu/pool/main/m/mesa/>。
`SOURCE-SHA256SUMS.txt` 釘選下列完整來源及兩份本地補丁。

| 檔案 | 位元組 |
| --- | ---: |
| `mesa_23.2.1.orig.tar.gz` | 30631591 |
| `mesa_23.2.1-1ubuntu3.1~22.04.4.diff.gz` | 230800 |
| `mesa_23.2.1-1ubuntu3.1~22.04.4.dsc` | 5839 |

公開來源紀錄已核對 Jammy security 的簽章鏈及 `.dsc` 上傳者簽章。
Archive 指紋為 `F6ECB3762474EDA9D21B7022871920D1991BC93C`。
上傳者指紋為 `50C4A0DDCF31E452CEB19B516569D855A744BE93`。
當次 `Sources.xz` SHA 為 `de8b562ed67bb8bf452ce20cb62e29ea274c66766579adcd35643b063bb397e3`。
浮動索引日後可更新；重新驗簽後仍須包含唯一指定版本及相同來源 SHA／大小。

以下核對腳本改寫自 Bookworm 公開配方，支援 Jammy 的 Python 3.10。
它檢查官方 archive keyring、上述指紋、索引期限與完整來源。
上傳者公鑰服務只是傳輸來源；信任依據是釘選指紋，不是下載成功。
金鑰失效、索引缺版或任一 SHA 不符時停止，不停用驗簽。
本次整理未重新連線驗證這條取得流程，也未執行建包。

## 補丁

`jammy-23.2.1-g31-preload.patch` SHA：
`378e65c7ad85e3490c46cded2a9e093791b425d68ae6003deed35b6f1cf9bd50`。
沿用舊版 `gpu_id`，修正全畫面 `ALWAYS` 判斷與 `EARLY_ZS_ALWAYS` 架構界線。
上游依據見上一層；不搬入新版 API。
另一提交 `548f652d101fab0de6facc17465e9d1e254e1544` 不適用：
本版仍使用 `0xff/0x28` 分散遮罩，不含該提交修正的新連續位元遮罩程式。

`packaging-version.patch` 只加 changelog 與 quilt series 項目。
其中維護者名稱與本機信箱是公開配方的占位識別，不是憑證。
兩份補丁逐位元保留，不改原 `rules`、`control` 或 symbols。

## 隔離環境

使用 Ubuntu 22.04 arm64。參考官方容器識別：
`ubuntu@sha256:5ec03bb3441e8b0bf3b4f9cd4629a1ae763010dc3035bb8da3ae6cf026486401`。
啟用 `ports.ubuntu.com/ubuntu-ports` 的 `jammy`、`jammy-updates`、`jammy-security`。
類型包含 `deb deb-src`，components 為 `main restricted universe multiverse`。
保持 Ubuntu archive keyring 驗簽，只在隔離環境安裝依賴。

由隔離環境管理者執行：

```bash
set -euo pipefail
apt-get update
apt-get install --no-install-recommends -y \
  build-essential dpkg-dev fakeroot ca-certificates python3-debian \
  curl xz-utils quilt patch gpgv gnupg ubuntu-keyring
```

## 取得完整來源

將本子目錄的檔案放入空白工作目錄，以非 root 建置帳號執行：

```bash
set -euo pipefail
base=https://archive.ubuntu.com/ubuntu/pool/main/m/mesa
curl -fLO "$base/mesa_23.2.1.orig.tar.gz"
curl -fLO "$base/mesa_23.2.1-1ubuntu3.1~22.04.4.diff.gz"
curl -fLO "$base/mesa_23.2.1-1ubuntu3.1~22.04.4.dsc"
index=https://security.ubuntu.com/ubuntu/dists/jammy-security
curl -fLo InRelease "$index/InRelease"
curl -fLo Sources.xz "$index/main/source/Sources.xz"
curl -fLo mesa-uploader.asc \
  'https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x50C4A0DDCF31E452CEB19B516569D855A744BE93'
export GNUPGHOME="$PWD/gnupg"
mkdir -m 700 "$GNUPGHOME"
gpg --batch --dearmor --output mesa-uploader.gpg mesa-uploader.asc
python3 verify-official-source.py
sha256sum -c SOURCE-SHA256SUMS.txt
dpkg-source -x mesa_23.2.1-1ubuntu3.1~22.04.4.dsc mesa-23.2.1
```

`dpkg-source` 的預設 keyring 可能缺上傳者金鑰而警告。
只有上述獨立簽章鏈與 `.dsc` 核對成功後，才可繼續。
在同一工作目錄，由隔離環境管理者安裝原來源宣告的完整依賴：

```bash
set -euo pipefail
apt-get build-dep --no-install-recommends -y ./mesa-23.2.1
```

此處使用已驗證的 `debian/control`，不從浮動索引換用別版 Mesa 的依賴宣告。
保留 GCC、LLVM、OpenCL、Vulkan、Nine、VA／VDPAU 等原封裝依賴。
相同 Jammy 倉庫無法滿足依賴時停止，不混用其他發行版或 PPA。

## 套用與建包

### 補丁核對

回到非 root 建置帳號，在同一工作目錄執行：
先套用釘選來源的官方補丁；只接受下列兩筆指定補丁、檔案與 hunk 的已知偏移。
新增、缺少、重複或不同偏移均停止；任何 fuzz 或失敗也停止。

```bash
set -euo pipefail
export LC_ALL=C.UTF-8
cd mesa-23.2.1
export QUILT_PATCHES=debian/patches QUILT_PATCH_OPTS=--fuzz=0
quilt --quiltrc /dev/null push -a --fuzz=0 > ../ubuntu-patch-apply.log 2>&1
if grep -Ei 'fuzz|FAILED' ../ubuntu-patch-apply.log; then exit 1; fi
awk '
  /^Applying patch / { patch = $3; sub(/^debian\/patches\//, "", patch) }
  /^patching file / { file = substr($0, 15) }
  /offset/ { print patch "\t" file "\t" $0 }
' ../ubuntu-patch-apply.log > ../ubuntu-offsets.tsv
diff -u <(printf '%s\t%s\t%s\n' \
  fix-clover-build-without-spirv.diff \
  src/compiler/meson.build 'Hunk #1 succeeded at 58 (offset -23 lines).' \
  use_common_clear_texture.patch \
  src/gallium/drivers/virgl/virgl_context.c 'Hunk #2 succeeded at 931 (offset 4 lines).') \
  ../ubuntu-offsets.tsv
cp ../jammy-23.2.1-g31-preload.patch debian/patches/bpi-g31-preload.patch
patch --batch --fuzz=0 -p1 -i ../packaging-version.patch > ../packaging-apply.log 2>&1
if grep -Ei 'fuzz|offset|FAILED' ../packaging-apply.log; then exit 1; fi
quilt --quiltrc /dev/null push --fuzz=0 > ../bpi-patch-apply.log 2>&1
if grep -Ei 'fuzz|offset|FAILED' ../bpi-patch-apply.log; then exit 1; fi
quilt --quiltrc /dev/null pop -a
```

封裝與 BPI 補丁均須零 fuzz、零偏移；官方補丁只允許上述精確匹配。

### 建包

補丁核對成功後，保持在 `mesa-23.2.1` 目錄執行：

```bash
set -euo pipefail
export DEB_BUILD_OPTIONS=parallel=6 DEB_BUILD_PROFILES=
dpkg-checkbuilddeps
dpkg-buildpackage -b -uc -us -j6
```

保留原測試設定，不使用 `nocheck`。
保留 `dh_missing --fail-missing`、`dh_makeshlibs -a -- -c4`。
不更動建包配置來減少套件或測試。

## 成套核對

`ALL-PACKAGES.tsv` 列出 19 個主要 `.deb` 與 12 個除錯 `.ddeb`。
每件均須 `Source=mesa`、`Version=23.2.1-1ubuntu3.1~22.04.4+bpi1`、`Architecture=arm64`。
以 `dpkg-deb -f` 核對新產物的 `Source`、`Package`、`Version`、`Architecture`。
`Source` 可帶括號版本；套件名稱、來源名稱與架構集合須和清單一致。
`.ddeb` 保持原副檔名，不冒充 `.deb`；`libxatracker*` 由原架構條件排除。

`arm64-runtime-closure.txt` 提供五核心與全部主要套件的參考閉包。
只計 `Depends`／`Pre-Depends`，不代表應全裝，也不是完整 apt snapshot。
依賴版本可因官方倉庫更新而改變；須重新解算，不能沿用舊安裝結論。
實際映像安裝集合與驗收限制均遵守上一層規則。
