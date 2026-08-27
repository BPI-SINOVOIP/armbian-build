# Banana Pi AIM7 vendor 候選映像 L2 建置證據

## 結論

`bananapiaim7` 已由固定提交與專用 OverlayFS 隔離快取完整建置 Debian Trixie vendor minimal CLI，並通過 L1 及 L2 唯讀內容守門。此結果證明候選映像的來源、IMG／XZ 同一性、GPT、U-Boot 載荷、RKBin、AIM7 DTB、最終核心與 U-Boot 設定及診斷套件符合本次內部契約。

此結果不代表實體板已完成冷啟動、儲存、網路、顯示、GPU、VPU、RGA、NPU、PCIe、USB 或排針驗證，也不代表 AIM7 與 AIM7 IO 載板完全等同。候選維持 `.wip` 與內部 L2，未取得公開發布核准。

## 固定來源

- 來源與驗證提交：`052edc9c12fa4eff6213f66753774bc7e7b09d27`
- 來源樹：`9fc00c243c0ae547952a13293ea61683039ec6e8`
- 建置與驗證契約 SHA-256：`489730becc1ac7ce9f350c3088c8b837eb5b1116d735b17757f7de8fb47a8139`
- Linux：`c6157104418d012823413c02f9222f3fe123dd25`
- U-Boot：`39cd993e5d6296635438e84f4576b3a9bf76f86e`
- RKBin：`1d3c61008fa823936ae7a59615393f8294b64456`
- Armbian firmware：`f50a2a21bcdb77a562b3976930c5c6b521a1df08`
- 固定時間戳：`1777288768`

首次完整預檢使用提交 `808ff4e8e8093a05b9f9f2f29d9c91192499f9a0`，唯讀守門發現根分割區實際大小為 `5330944` 個磁區，與原先前置估值不同，因此正確拒絕 L2。契約修正、提交及推送後，已清除首次大型產物，再由 `052edc9c1` 重新完整建置；不得把首次預檢映像視為候選。

## 產物

輸出目錄：

`output/images/2026.08/bananapi-rockchip-rk3588-aim7-trixie-vendor-cli`

| 產物 | 大小 | SHA-256 |
|---|---:|---|
| IMG | `2747269120` | `cda1ddd7729dcfa37909abc6cca14f017f83660d0ad9af86f848c560bc6719ba` |
| XZ | `528840324` | `e8ac39bab434a3b23024c8884cc27d1dc4aa4ccbfe860e2440bc6910f173b2a8` |

- 候選矩陣：`77143efaed9fc60f011fa2a6d995e48273efbaf3ba58d7cf2f729422a60e7a51`
- U-Boot 載荷清單：`6a0dbddcb4c13b307a453e9f7e982d71a36bad3d2a1a891057cd9852acb1f901`
- 最終設定清單：`1fcb2690f2eb3f18ca7128faeee867be6664007fe782cec1fa0d8fd0d8f114e3`
- RKBin 清單：`0d1c36b78dc1dc1919cba54e18b1f719bb260951ba059f80835b0ac6ab90ea91`
- 唯讀驗證時間：`2026-08-27T15:29:03Z`

## 唯讀守門

- XZ 串流可完整解壓，解壓資料 SHA-256 與 IMG 相同。
- GPT 第 1 分割區從 sector `32768` 開始，大小為 `5330944` sectors，類型為 Linux ARM64 root，根檔案系統為 `armbi_root` ext4。
- `idbloader.img` 位於 byte `32768`，大小 `323584`，SHA-256 為 `b168b40fe699e8b435a174eb98f4eff6327f3083dcf48ae86eed5d0d31274b19`。
- `u-boot.itb` 位於 byte `8388608`，大小 `1459712`，SHA-256 為 `9534348ad59993a69e9af9875dda80ba732c8c5f36363afb425d1732a81e3c0c`。
- 映像 DTB SHA-256 為 `fdf3d029773c5374411a08edc6fcfe65532c5fa94d7845b05e28988f338e796f`，板級身分為 Banana Pi AIM7。
- 最終核心設定 SHA-256 為 `24edbbaabf1bd7960e7c2647ec7e96c25e2e9bf4de5a440c30827eb15b162e9e`。
- 最終 U-Boot 設定 SHA-256 為 `83090180148e81624265b0c1e6258fea600940824b30fddf4a2a0e2ec1ac8edd`。
- GPU、VPU、RGA 與 NPU 核心選項及 GPIO、I2C、SPI、PCIe、NVMe、USB、DRM、V4L2、FFmpeg、GStreamer 與圖形診斷工具均已依契約檢查。

## 後續限制

- 比對 AIM7 與 AIM7 IO 原理圖、電源、PHY、PCIe lane、連接器及載板 I/O 差異。
- 完成 RKBin 與其他韌體的產品散布授權審查；目前只有內部候選資格。
- 以 UART 保存多次冷啟動、重新啟動與斷電重啟記錄。
- 實測 SD、eMMC、網路、顯示、GPU、VPU、RGA、NPU、PCIe、NVMe、USB、GPIO、I2C、SPI、音訊與熱壓力。
- 在取得上述實物證據前，不得把 L2 描述為量產完成、所有功能可用或公開發布核准。
