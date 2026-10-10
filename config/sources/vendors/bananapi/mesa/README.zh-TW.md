# Mesa arm64 候選重建配方

本目錄只保存公開來源、補丁、建包命令與參考清單，不參與自動建置。
這些是本地未簽署候選，不是 Debian、Ubuntu 或 Armbian 官方發行套件。
補丁的原始著作權、授權及作者通知見 [NOTICE.zh-TW.md](NOTICE.zh-TW.md)。

| 系統 | 官方來源版本 | 本地候選版本 | 配方 |
| --- | --- | --- | --- |
| Debian 12 Bookworm | `22.3.6-1+deb12u2` | `22.3.6-1+deb12u2+bpi1` | [bookworm](bookworm/README.zh-TW.md) |
| Ubuntu 22.04 Jammy | `23.2.1-1ubuntu3.1~22.04.4` | `23.2.1-1ubuntu3.1~22.04.4+bpi1` | [jammy](jammy/README.zh-TW.md) |

兩者保留原發行版的功能、套件組、架構條件與 ABI 守門。
回補依據為 [Mesa 提交 13b35a3](https://gitlab.freedesktop.org/mesa/mesa/-/commit/13b35a3c9c3cd8e1f4bb129b0634831f13398259)。
補丁修正 G31 預載判斷，不宣稱修復 TERM 或 Mesa 26.1.6 終止錯誤。

## 重建與驗收

1. 使用對應發行版的隔離 arm64 環境。不要在共用主機安裝建置依賴。
2. 驗證官方簽章、索引、來源 SHA 與版本，再解開完整來源。
3. 保留原封裝，零 fuzz、零偏移套用本地補丁，使用 `dpkg-buildpackage` 建包。
4. 核對完整套件組、`Source=mesa`、候選版本、架構、測試結果與 ABI。
5. 在實際映像的組裝環境解算安裝交易；來源重建不能取代實板驗收。

參考資源為 6 CPU、8 GiB RAM、無額外 swap、約 20 GiB 磁碟。
使用既有 QEMU／binfmt 或原生 arm64；本配方不修改主機設定。
執行者須設定固定總期限與磁碟停止界線。QEMU 速度不能按原生環境推估。

原封裝的測試規則可能容許失敗；建包 exit 0 不代表所有測試通過。
另檢查每項 Meson 結果、arm64 ELF、公開 SONAME、原 symbols `-c4` 與產物清單。
重建後重新產生 `.deb` 相對檔名的 `SHA256SUMS.txt`。
參考清單的產物 SHA 僅識別既有候選，不保證不同環境逐位元重現。

## 同版安裝集合

五核心為 `libgl1-mesa-dri`、`libegl-mesa0`、`libglx-mesa0`、`libgbm1`、`libglapi-mesa`。
再加入原映像已安裝的其他 `source=mesa` 套件，同版本、單一交易更新。
不要用建置容器的已裝套件清單代替原映像清單。
在映像組裝環境查詢原集合：

```bash
dpkg-query -W -f='${source:Package}\t${binary:Package}\t${Version}\t${Architecture}\n'
```

依 `source:Package=mesa` 篩選。缺少候選、架構不符或混版時停止，不擅自移除套件。
不強裝所有 `-dev`、`-dbgsym` 或 `.ddeb`，也不對整個產物目錄執行安裝。
若原映像已裝除錯套件，仍須取得對應版本，不能略過。
非 Mesa 依賴只採同發行版官方套件；GLVND 前端不由此配方替代。
先用 `apt-get -s --no-remove --no-install-recommends install` 模擬選定檔案。
`--no-download` 額外要求套件快取齊全，不能把缺快取誤判為版本相依無解。

## 文件審查界線

補丁與來源 SHA 清單逐位元沿用已審查的公開配方。
Jammy 另補可執行的官方簽章鏈核對步驟；Bookworm 沿用公開核對腳本。
本次只做文件、語法、公開內容與清單一致性審查，不重抓來源、不重編或重驗實板。
11 份沿用檔案逐位元相同，三份補丁 SHA 相符。
9 段 Bash 與兩份 Python 通過語法檢查；兩 OS 套件清單同版且五核心齊備。
Jammy 核對腳本通過 21 個記憶體模型案例，涵蓋錯指紋、失效簽章、期限、重複與 SHA 拒絕。
模型案例不等同實際密碼學驗簽，也不證明外部網址目前可用。
浮動索引可能移除指定版本；驗簽失敗、索引過期或來源 SHA 改變時須停止。
未固定完整 apt snapshot，因此不保證逐位元重現。
缺少所需簽章鏈材料時停止，不以已下載的檔名或候選驗證結果取代來源驗證。
