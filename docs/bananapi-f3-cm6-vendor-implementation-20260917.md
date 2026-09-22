# BPI-F3／BPI-CM6 官方格式候選實作紀錄

> 2026-09-23 更新：CM6 rc2 已獲使用者燒錄／啟動通過回報；後續 SD 實測完成 eth0 與藍牙基本功能修正，並產生兩份 rc3 候選。最新範圍與交付見[實機驗證紀錄](bananapi-cm6-hardware-20260923.md)。以下保留原批次實作與歷史狀態。

> 2026-09-18 更新：CM6 Titan eMMC 已回報 Code 20014 分區表匹配失敗；兩份舊 eMMC 候選撤回待查。先前離線通過不代表 Titan 可燒錄。兩份 SD 仍未驗證；原檔保留追溯。

完成日期：2026-09-17；批次識別：`20260916`。

四份候選已建置完成，使用 Ubuntu 24.04 Noble、GNOME Wayland，分別輸出兩板的 SD 整碟映像與 Titan eMMC 分區套件。依使用者安排，先交付外部人員實測，後續才建立 AI 自動驗證環境。尚未操作實體媒體、UART 或板卡，所有硬體結果保持待測。

## 交付位置

`/media/pi/SMCI/bpi/f3-cm6-vendor-20260916/delivery/`

| 組合 | ZIP 位元組數 | SHA-256 |
| --- | ---: | --- |
| F3 SD | 1741946102 | `200ba36411924d058773dddb82484b2e287de64a0e90b71079bd996b3a39f110` |
| F3 eMMC | 1741939593 | `6b8bc83b08d24f8e39b8a77ee0c7872d65d6c853504be96b5faec92c0d71890c` |
| CM6 SD | 1720652432 | `ad047b9df0ce3081172826052faef8833b36767f8f32a252d029eabe9c84d4ce` |
| CM6 eMMC | 1720610123 | `c9595521e0e448e0e49eae4a528f21a91038051b5eb0bedcb260e7f20d58213a` |

四份最低媒體容量均為 `6716129280` 位元組，根檔案系統初始為 6 GiB。每個組合有獨立根／boot UUID、manifest、SHA-256 與離線驗證報告。首次開機由受控 GPT 擴容服務核對配置後擴大第六分區；實機擴容仍列入外測。

原始工作樹與既有發行映像沒有改寫。本次在隔離工作樹 `bpi-v26.2.1-f3-cm6-vendor-format`、分支 `bpi-f3-cm6-vendor-20260916` 實作；基線提交為 `5f2788df426cc01773dcaf7e60c752ea513ebaf3`，交付時尚未提交或推送此次變更。

## 實作內容

- `tools/prepare_bpi_k1_vendor_rootfs.py`：核對原 Armbian MBR 與 Noble／板型／核心身分；隔離掛載一般映像，在 riscv64 chroot 安裝 GNOME 與簽章驗證過的固定加速套件。禁止建置時啟動服務，還原來源 DNS，保存完整套件清單與安裝後預檢。
- `tools/bpi_k1_acceleration.py`：驗證官方 InRelease、套件索引、公開金鑰、每個 `.deb` 的 SHA-256 與控制欄位；核對相依閉包、Python ABI、核心選項與 DDK。
- `tools/package_bpi_k1_vendor.py`：固定官方 bootinfo／FSBL／OpenSBI／U-Boot／env；建立獨立 bootfs 與媒體 UUID。F3 gzip 核心轉為 raw Image，CM6 FIT 保留，明確指定各板 DTB。SD 保留 bootinfo 與 GPT 保護 MBR；Titan 維持官方分區單位、壓縮描述及暫載流程。
- `tools/bpi_k1_grow_rootfs.py`：僅接受同一 MMC 媒體、正確 UUID、六個指定 GPT 分區。擴容後再核對前五分區、根分區起點／GUID 及前 80 位元組 bootinfo；必要時更新核心分區界線，再擴大 ext4。
- `tools/collect_bpi_k1_runtime.py`：板上蒐集顯示、核心、加速配套與原始命令證據，可附入真實 AI 執行 profile；不把 provider 名稱或套件存在視為硬體通過。

舊安裝器、armbian-config 的分區／bootloader 入口與原 U-Boot 磁區更新路徑透過 diversion 及拒絕入口保護。原 Armbian 擴容服務已停用，保留一般首次登入初始化。核心、BSP、armbian-config 與加速配套固定版本；更新鉤子維持新 bootfs 開機選項，檢查核心格式、載入大小及 DDK。此處不宣稱已完成跨版本升級相容驗證。

| 配套 | CM6 | F3 |
| --- | --- | --- |
| 核心 | `6.6.36-legacy-spacemit` | `6.18.37-current-spacemit` |
| GPU DDK | `23.2@6460340` | `24.2@6603887` |
| GPU 使用者態 | `23.2-6460340bb2` | `24.2-6603887bb8` |
| Mesa | `22.3.5-bb2` | `24.01-bb3` |
| AI | ORT `1.2.2`、CPU RVV／IME | 同左 |
| 多媒體 | MPP、VPU 韌體與測試工具、FFmpeg | 同左 |

每板 30 個固定加速套件，共 37 個唯一 `.deb`；加速鎖 SHA-256 為 `5318fc9fd4fbb6073c1a5b631136e3442f1a35efb9243f2ba4d76339cf1a9df6`。K1 沒有獨立 NPU。本次完成配套整合與相容性預檢，效能提升幅度待實際模型與圖形／影片負載量測。

## 驗證結果

108 項本機測試全數通過，零跳過、零失敗。包含新工具的來源竄改、錯誤板型／分區／UUID、gzip／FIT、GPT 主副本 CRC 與載荷、真實 `growpart` 稀疏映像擴容，以及三組既有 SpacemiT／CM6／來源政策回歸。

實際建置另通過：

1. 原 F3／CM6 Noble 壓縮映像的既有 SHA 檔核對。
2. 兩板真實 chroot 安裝、`dpkg --audit`、加速安裝後預檢；相依缺項均為空。
3. 四份 rootfs／bootfs 的 `e2fsck -fn`、UUID、fstab、extlinux、env 與身分標記核對。
4. 兩份 SD 的 GPT 主副本、MBR、bootinfo、各分區偏移與實讀載荷雜湊核對。
5. 四份 ZIP 的整包 SHA-256、成員集合、大小、逐檔雜湊／CRC 與檔案系統 UUID。
6. 四份候選唯讀掛載與 chroot：DNS 符號連結還原、舊擴容服務遮蔽、新擴容服務啟用；六個安裝／更新入口皆實際拒絕執行，退出碼為 2。

報告保存於交付目錄 `evidence/`，各組合另附 `verification.json`。完整準備與封裝紀錄保存於工作根目錄 `evidence/`。原始來源、固定元件、套件快取及建置中間檔保留於 `reference/`、`work/`、`output/`；交付目錄僅整理測試所需檔案。

## 重跑方式

在本工作樹及已保存的參考資料上執行。準備工具需要 sudo、掛載隔離、riscv64 binfmt／QEMU 與 Ubuntu 套件來源；它只接受一般映像檔案。一般 Ubuntu 相依套件的實際版本記錄在每板 `dpkg-status`，目前不宣稱整個根系統能跨時間逐位元重建。

```bash
build_dir=/media/pi/SMCI/bpi/f3-cm6-vendor-20260916
python3 tools/bpi_k1_acceleration.py prepare --board bpi-f3 \
  --cache "$build_dir/reference/debs" --output "$build_dir/reference/acceleration/bpi-f3"
sudo python3 tools/prepare_bpi_k1_vendor_rootfs.py --board bpi-f3 \
  --image "$build_dir/work/f3-armbian-noble.img" --output "$build_dir/work/f3-prepared-new" \
  --deb-cache "$build_dir/reference/debs"
sudo python3 tools/package_bpi_k1_vendor.py build --board bpi-f3 --storage sd \
  --prepared "$build_dir/work/f3-prepared-new" --reference "$build_dir/reference/vendor-v2.3" \
  --output "$build_dir/output/bpi-f3-sd-new"
python3 tools/package_bpi_k1_vendor.py verify "$build_dir/output/bpi-f3-sd-new/manifest.json"
```

以 `bpi-cm6`／CM6 原映像切換板型，以 `--storage emmc` 產生 Titan 套件。新建置使用新目錄，既有完整候選不會被隱式覆寫；同來源補件需明確使用 `--resume --refresh-packages`，保留準備歷史後重新驗證。

八組回歸可依 `evidence/regression.json` 的 pattern 使用 `python3 -m unittest discover -s tests -p '<pattern>'` 重跑。擴容整合測試需要 `growpart`；本次從候選既有套件抽取到專用測試 PATH，沒有為測試新增主機套件。

## 外測界線

依 [外測指南](bananapi-f3-cm6-vendor-external-validation-20260916.md) 執行。官方下載入口指向 Titan `1.0.35-beta`，本批尚未驗證其匯入／燒錄。eMMC 套件沒有硬鎖儲存目標，須按實際板卡／載板設定 eMMC 開機模式並拔除 SD、NVMe；不能以 ZIP 格式檢查代替目標識別。

官方 U-Boot 二進位帶 `dirty`，對應來源差異尚未閉合，詳見 `config/spacemit-k1-vendor/README.md`。目前交付的是離線驗證通過的外測候選，實物相容性、效能、穩定性與公開發行檢查仍未完成。
