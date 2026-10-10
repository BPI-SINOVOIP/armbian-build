# Mesa 補丁來源與授權通知

本目錄的 G31 回補修改 `src/panfrost/lib/pan_blitter.c`。
[LICENSE.pan-blitter](LICENSE.pan-blitter) 逐字保留兩版原檔第 1–26 行。
保留原始著作權、授權及作者通知，是為維持授權條件與來源追溯；中文說明不替代原文。
通知 SHA-256：`05911ccdd40d8cc9571183b6c0a324a9b57f1e2262bb4389e1484ba15f4944fc`。

## 官方來源

- [Mesa 22.3.6](https://deb.debian.org/debian/pool/main/m/mesa/mesa_22.3.6.orig.tar.gz)：封存 SHA-256 `737cb8e5d380ce1a0ad01fb8b74db7f27303435c353e753d2302e8cf3927bad2`。
  原檔 `mesa-22.3.6/src/panfrost/lib/pan_blitter.c` SHA-256 `ae4625ae2e69665f4be5993da3d32501da2b64467e1e5e0f9f64c35dadf9f49d`。
- [Mesa 23.2.1](https://archive.ubuntu.com/ubuntu/pool/main/m/mesa/mesa_23.2.1.orig.tar.gz)：封存 SHA-256 `c1458ab511b87d60644b2c46d67f7d9d5c0e3db691db6aae11762b1cc5b43ca0`。
  原檔 `mesa-23.2.1/src/panfrost/lib/pan_blitter.c` SHA-256 `7ae74dd3f9ca7ff31cbcae7fa38b74102003561a29036a3963fc7713243be146`。

回補依據：[上游提交 13b35a3c9c3cd8e1f4bb129b0634831f13398259](https://gitlab.freedesktop.org/mesa/mesa/-/commit/13b35a3c9c3cd8e1f4bb129b0634831f13398259.patch)。
補丁作者為 `Eric R. Smith <eric.smith@collabora.com>`。
上游補丁 SHA-256：`c843ac5e343d69ff8b9914e8d8cda88c7278fd7f242cef52d1fee6ecd2a5ff3b`。
本目錄另依舊 API 與發行版封裝調整；實際修改以各版本補丁為準。
各版本 `SOURCE-SHA256SUMS.txt` 固定本地補丁位元組，不以本通知替代來源驗簽。
