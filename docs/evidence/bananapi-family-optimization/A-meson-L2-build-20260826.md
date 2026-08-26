# Banana Pi Meson 第一批 L2 建置證據

日期：2026-08-26

## 結論

`bananapim5`、`bananapim2pro`、`bananapicm4io` 與 `bananapim2s` 已由乾淨來源完整建置 Trixie current CLI，四張映像均通過 L2 唯讀守門。這項結果證明來源、建置產物與映像內容符合受控政策，不代表四張板卡已完成實機開機或功能驗證。

## 可重現基線

| 項目 | 值 |
| --- | --- |
| Armbian 來源提交 | `93f91ab6804449925de148d71b255707738c9d49` |
| Armbian 來源樹 | `db851a419c86315918227490660fd87a11462f2d` |
| Linux | `6.18.46`，來源提交 `1f99e9ab748fc5c32120de9c4eca31abfe54a4d5` |
| M5／M2 Pro U-Boot | `v2024.07` |
| CM4IO／M2S U-Boot | `v2026.01` |
| FIP 提交 | `e11ae32f65219e9cba903e9744f216239b41386a` |
| 發行版與設定 | `RELEASE=trixie BRANCH=current BUILD_DESKTOP=no BUILD_MINIMAL=yes` |
| 建置狀態 | 四板完整建置完成 |
| 驗證狀態 | 四板 L2 通過 |

建置使用 OverlayFS 隔離快取；既有快取只作唯讀下層，U-Boot、核心與 rootfs 均要求重新產生。大型映像與完整日誌保留在本機輸出目錄，不加入 Git。

## 映像雜湊

| 板卡 | IMG SHA-256 | XZ SHA-256 | XZ 大小 |
| --- | --- | --- | ---: |
| `bananapim5` | `b84d9690e72e20884d234ed0f126292997a2b30c14f84becf0a37607f3e24ee6` | `c02cca0febca25236fb366084e58aab50b0a833b74122909a26d1f2d8ac5ead1` | 383163372 |
| `bananapim2pro` | `ac71bb06b9ce43e4c168d05f0aa3879302d4a76322da36b746627d60f9a310fa` | `02e2287f3760ccd97c8ac1c37ca52b5578d0605c6ae05350b14294b1474180a9` | 384592632 |
| `bananapicm4io` | `84d9dc5c1b7c42c1270ac9dba06864b4689cc26c088986ce14f7de898eddab92` | `2245d8915a3678e5b0fef5da0975614ce1ea69a278eb30fe3bbb02bb3f046383` | 385461324 |
| `bananapim2s` | `4ebee88a2932e04929cd4880b6e0c72ced719b44dfa9be8d5f590d68fff0737b` | `f1d665598486e441634e63668aad6d686949c4b35d145deb843b25c073b59e26` | 385098148 |

每張 IMG 大小均為 1744830464 位元組；每個 XZ 均通過 `xz -t`，串流解壓 SHA-256 與對應 IMG 相同。

## L2 守門範圍

1. 檢查候選矩陣、中繼資料、來源提交、來源樹、驗證設定與 FIP 提交一致。
2. 檢查 IMG／XZ 大小、SHA-256、壓縮串流與解壓後同一性。
3. 檢查 MBR、Amlogic 開機區及映像內 U-Boot 套件 payload 與實際寫入區一致。
4. 以唯讀 loop 與 `mount -o ro,noload` 檢查映像，不執行映像內程式。
5. 檢查核心、initrd、板級 DTB、overlay、Armbian 套件及 GPIO、I2C、SPI、影像與無線診斷工具。
6. 檢查 Meson GPU、VPU、Crypto、USB gadget、MMC、Ethernet、I2C、SPI、PWM 與 Bluetooth 核心設定。
7. 由 `/aliases/mmc1` 定位逐板 eMMC；M5 與 CM4IO 保守設定、M2 Pro 與 M2S 原始設定均符合政策。
8. 檢查 CM4IO 與 M2S 的 `ondemand` CPU 調速器設定。

## 已知限制

- L2 不證明 SD／eMMC 實機開機、冷啟動、重新啟動、關機或長時間穩定性。
- M5 與 CM4IO 的 Hynix eMMC 修正仍須多片、多廠牌及非正常斷電實測。
- GPU、VPU、USB gadget、Ethernet、Wi-Fi、Bluetooth 與 40-pin 只有映像內容證據，尚無本批映像實機功能證據。
- M5 與 M2 Pro 的 BL31／BL30 關機問題仍受封閉韌體限制，未因本次建置宣告修正。
- CM4 核心修補郵件在建置時出現一個內容剖析警告；最終 DTB 與 L2 設定檢查通過，但修補格式仍須獨立修正並做針對性重建。

## 本機證據位置

```text
output/images/2026.08/bananapi-meson-trixie-current-cli/
```

此目錄包含 `CANDIDATES.tsv`、`COMPLETION_STATUS.json`、`VERIFICATION.tsv`、`VERIFICATION_STATUS.json`、四板 IMG／XZ／SHA-256、中繼資料、FIP 雜湊清單及完整建置日誌。
