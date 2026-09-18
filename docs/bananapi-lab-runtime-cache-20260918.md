# 跨架構執行環境離線實證

本文件對應計畫 E1／E2。來源為本機可信且固定 SHA-256 的 Armbian rootfs 建置快取；
不是從目前板子的 SD 讀取，不需先啟動目標板，也不執行快取內的 init 或套件腳本。
此工具只產生供既有封裝器使用的 Python 子集，不是可直接冷啟動的完整救援系統。

## 操作順序

1. 固定 rootfs `.tar.zst`、原配 initrd 及對應 QEMU 的 SHA-256，核對來源可信。
2. 使用下列入口擷取 Python 與遞迴 ELF 相依；輸出目錄必須尚不存在。
3. 將 `cache-extraction.json` 中的 `archive` 交給既有 `bpi_lab_external_bundle.py`。
4. 使用原配 initrd 與真正目標 QEMU 執行四項探測；只有 `runtime-probe.json`
   記錄 `runtime_executed=true` 才代表已執行，封裝完成本身不是通過。

```sh
python3 -B tools/bpi_lab_runtime_cache.py \
  --cache "$ROOTFS_CACHE" --cache-sha256 "$ROOTFS_SHA256" \
  --architecture arm --python /usr/bin/python3 --stdlib /usr/lib/python3.11 \
  --library-dir /usr/lib/arm-linux-gnueabihf --library-dir /usr/lib \
  --output "$CACHE_OUTPUT"
```

RISC-V 的本次組合使用 `--architecture riscv64`、`/usr/lib/python3.10`、
`/usr/lib/riscv64-linux-gnu`，不是把 ARM32 路徑改板名後直接套用。
後續封裝及探測命令見[根保護與執行環境文件](bananapi-lab-external-root-20260918.md)。

## 擷取規則

- 完整核對壓縮檔摘要，以同一已開啟的一般檔案描述符解壓，結束前重核來源身分。
- 壓縮來源最多 4 GiB、暫存 tar 最多 16 GiB、選入 newc 最多 256 MiB，操作有總期限。
  解壓檔僅為匿名暫存檔，結束後自動回收；不在磁碟解開桌面目錄樹。
- tar 最多 300,000 個標頭；PAX／GNU／Solaris 延伸標頭各限 1 MiB、巢狀八層。
  所有 GNU／PAX 稀疏映射在解析前拒絕，重複路徑、越界、特殊權限或選入硬連結也拒絕。
- 僅選明示 Python、指定版本標準函式庫，以及 ELF 真正引用的相依；未知架構、
  未核定搜尋路徑或含糊的多份相依拒絕。原客戶 initrd 的既有函式庫由封裝器保留。
- 排除 `__pycache__`、`sitecustomize.py`、`usercustomize.py`，以及指定標準函式庫下
  `config-3.*` 建置目錄；排除規則寫入收據，不帶入 `/etc` 自訂邏輯或編譯開發資料。
- 符號連結不得含 `..`；標準函式庫連結的整條解析路徑不得離開該函式庫。
  這是受限格式，遇到其他有效但未支援的快取時保持拒絕，不默默改寫原連結。
- 不帶入帳號、金鑰、網路設定、核心模組或原 init，不安裝套件、不更換主機系統 QEMU。

## 本次實證

| 架構 | 原配來源 | 快取組合 | 實際結果 |
| --- | --- | --- | --- |
| ARM32 | BPI-M1 Bookworm minimal 原 initrd | ARMHF Bookworm XFCE 建置快取，Python 3.11 | 原 shell、固定 Python 模組匯入、blkid、注入相依後 shell 均通過 |
| RISC-V | BPI-F3 Jammy minimal 原 initrd | RISC-V Jammy XFCE 建置快取，Python 3.10 | 同上四項均通過 |
| ARM64 | 前輪 BPI-M4 Zero Bookworm 原 initrd | 已保存的原生救援，Python 3.11 | 前輪四項通過，未用其他架構結果代替 |

ARM32 與 RISC-V 使用本機既有 QEMU 8.2.2 的固定摘要副本，沒有修改客戶 shell。
ARM32 在 QEMU 6.2 的初始 shell 探測失敗，指定訪客偏移亦失敗；診斷為 SIGSEGV。
切換 8.2.2 後正式探測通過，不以更換 initrd 或忽略退出碼取得通過。
這是本機版本對照，不推定所有 QEMU 或所有 ARM32 程式都有同一問題。

新證據位於 `output/evidence/bpi-crossarch-runtime-20260918/`，正式使用
`arm-source-004`、`arm-bundle-004`、`riscv64-source-003`、`riscv64-bundle-001`。
初期快取範圍拒絕及 QEMU 失敗均保留，不覆蓋成成功。
彙整摘要見[跨架構實證清單](evidence/bpi-multiboard-integrate-20260917/crossarch-runtime-audit.json)。

## 驗證界線

兩份實證只覆蓋這兩組來源、ABI 與模擬器，不是所有 ARM32／RISC-V OS 組合的通過聲明。
各實站仍要建置相符的核心、DTB、模組、韌體及救援入口，完成媒體配對與首次循環。
使用者空間探測不驗證 ROM／SPL、DDR、儲存控制器、網路、GPU 或其他周邊。
固定摘要證明內容一致，不等同來源簽章；只可執行已確認可信的快取與模擬器。

```sh
output/evidence/bpi-sram-supervisor/model-venv/bin/python -B -m unittest discover \
  -s tests -p 'test_bpi_lab_runtime_cache.py'
/home/pi/.local/bin/ruff check tools/bpi_lab_runtime_cache.py tests/test_bpi_lab_runtime_cache.py
```
