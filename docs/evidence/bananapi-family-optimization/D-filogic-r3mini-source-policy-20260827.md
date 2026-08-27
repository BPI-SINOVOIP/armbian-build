# Banana Pi BPI-R3 Mini current 候選來源與 eMMC 政策

更新日期：2026-08-27

## 階段結論

`bananapir3mini` 已完成固定來源、eMMC 啟動鏈、GPT、BL2／FIP、U-Boot、核心 DTB、網路韌體與板卡專用驗證契約。Linux DTB、U-Boot、ATF BL2／BL31 與 FIP 均已執行套用或元件建置驗證，但依任務限制未建置完整 Armbian 映像，也未進行實體板測試；目前證據等級是 `L1 元件候選`，不得宣稱可開機、硬體通過或可公開發布。

候選政策採用受控 L1/L2 狀態機。現行狀態固定為 `candidate_scope=internal-component-only`、`full_rootfs_image_built=false`，而且 L1 明確禁止攜帶 `image_build_evidence`；僅改寫層級名稱或夾帶未受控映像欄位都會被政策檢查器拒絕。

未來升為 `L2 內部軟體候選` 時，必須同時滿足 `candidate_scope=internal-l2`、完整映像狀態為真及下列不可省略的映像證據：

- 來源提交與驗證器提交都是完整 40 位十六進位提交碼，而且兩者相同。
- 建置與驗證使用的 validation SHA-256 都有效，而且兩者相同。
- `CANDIDATES.tsv`、U-Boot 載荷清單及最終核心／U-Boot 設定清單各有獨立 SHA-256。
- IMG 與 XZ 都記錄非零大小、路徑與 SHA-256。
- 唯讀內容驗證為真，實機驗證為假。

上述契約只建立未來 L2 的軟體證據格式，不代表本次已完成映像建置。兩個層級都固定禁止公開發布與硬體通過聲明。

## 固定來源

| 元件 | 來源 | 固定提交 |
| --- | --- | --- |
| Linux | `https://github.com/frank-w/BPI-Router-Linux.git` | `4a4506842b77b597f11e7fc53be1dcdbdc97eea9` |
| U-Boot | `https://github.com/u-boot/u-boot` | `34820924edbc4ec7803eb89d9852f4b870fa760a` |
| ATF | `https://github.com/mtk-openwrt/arm-trusted-firmware.git` | `c34e37802efaea356991a0811c8fc50f8a810f5b` |
| Armbian firmware | `https://github.com/armbian/firmware` | `f50a2a21bcdb77a562b3976930c5c6b521a1df08` |
| MT76 firmware | `https://github.com/openwrt/mt76.git` | `c5a3bd91aa735b669618610d5f0ebfa5786845a6` |
| Linux firmware | `https://gitlab.com/kernel-firmware/linux-firmware.git` | `01205307636157a12c29e6a774bf83b218732050` |
| 原廠板級參考 | `https://github.com/BPI-SINOVOIP/BPI-R3MINI-OPENWRT-V21.02.3` | `9bd78779f267a21c04c5bb4d16c32e83aae8d1d3` |

板檔同時固定 `ARMBIAN_FIRMWARE_GIT_SOURCE` 與 `ARMBIAN_FIRMWARE_GIT_REF`，validation 另外保存 source、ref、commit，並啟用 `verify_firmware_source_resolution=true`。未來完整建置時，共用建置器必須從日誌證明實際解析到同一來源與提交，不能只接受欄位宣告。

原廠使用說明指出 BPI-R3 Mini 不支援 SD 開機。eMMC 初始化須分成兩部分：整碟映像寫入 `/dev/mmcblk0` 的 user area，另外將 `bl2.img` 寫入 `/dev/mmcblk0boot0`，最後執行 `mmc bootpart enable 1 1 /dev/mmcblk0`。因此一般 IMG 只涵蓋 user area，不是空白 eMMC 的完整冷啟動安裝物。本候選不提供自動 eMMC 安裝，也不把 SD 列為支援媒體；eMMC 目前只是候選目標，`supported_boot_media=[]` 明確表示尚未實機核准。

## GPT 與啟動鏈契約

user area 採用固定五分割區 GPT：

| 分割區 | 名稱 | 起始 sector | sector 數 |
| ---: | --- | ---: | ---: |
| 1 | `bl2` | 34 | 8158 |
| 2 | `ubootenv` | 8192 | 1024 |
| 3 | `factory` | 9216 | 4096 |
| 4 | `fip` | 13312 | 8192 |
| 5 | 根檔案系統 | 32768 | 延伸至映像尾端 |

GPT 範本 SHA-256 為 `beb31c2284ec7b8e910faeea8d323f40532b26010e87d0bae851d823705efa1d`。user area 內另於位元組偏移 `17408` 放置 BL2、偏移 `6815744` 放置 FIP，與現有 Filogic 映像寫入器一致；這份 BL2 複本不能取代 eMMC `boot0` 的必要載荷。

U-Boot 採 BootSTD、bootflow、extlinux 與 EXT4 自動開機，預設核心 DTB 固定為 `mediatek/mt7986a-bananapi-bpi-r3-mini.dtb`。`ubootenv` 分割區完整容納兩份各 `0x40000` 的冗餘環境，實際位元組位置為 `0x400000` 與 `0x440000`，不跨入 `factory`。供應商 FIT 的 `root=/dev/fit0`、`production` 與 `recovery` 路徑不屬於本候選契約。

## DTB、核心與韌體契約

固定 Linux DTS 原本保留 `/soc/mmc@11230000` 為停用且缺少完整 eMMC 參數。本候選補上 8-bit、200 MHz、`non-removable`、硬體重置與 HS200 1.8V，並明確加入 `no-mmc-hs400`；HS400 未經實機驗證，不予啟用。套用補丁後單獨建出的核心 DTB SHA-256 為 `5457155de554539c902a22507cbd69ad249fd70a24cf6e24a5753c2b5e8b66ab`。

U-Boot 使用自己的最小 DTB，eMMC 節點位於 `/mmc@11230000`，不是 Linux DTB 的 `/soc/mmc@11230000`。元件建置已確認同樣採 8-bit、200 MHz、HS200 1.8V 與 `no-mmc-hs400`。驗證資料分別記錄兩個路徑，禁止將兩棵裝置樹混用。

固定 Linux 的 EN8811H 驅動會要求 `airoha/EthMD32.dm.bin` 與 `airoha/EthMD32.DSP.bin`，板卡核心設定因此啟用 `CONFIG_AIR_EN8811H_PHY=m`。兩個 Airoha 檔案、原始授權與中文來源說明均固定雜湊；MT7986 的十二個 MT76 檔案則由固定 MT76 提交安裝並逐檔驗證。

## 發布阻擋

ATF 建置會連結 `plat/mediatek/mt7986/drivers/dram/release/dram.o`，其 SHA-256 為 `45acf44f2fe576991d7c0b13862cb41d1ffd37b37e1607e27ca4ddb31820fa79`。目前未找到足以確認該預編譯物件再散布範圍的檔案層級旁證。驗證 JSON 因此設定 `public_release_authorized=false`、`release_gate.status=blocked`，並列出下列必要阻擋：

- `atf_mt7986_dram_object_redistribution_scope_unverified`
- `emmc_boot0_installation_not_hardware_validated`

即使後續完整映像通過 L2，未解除上述阻擋前仍不得對外發布。

## 專用入口

未來受控建置使用：

```bash
./tools/run-bananapi-filogic-r3mini-candidate-isolated-cache.sh
```

直接建置入口：

```bash
./tools/build-bananapi-filogic-r3mini-candidate.sh
```

唯讀映像驗證入口：

```bash
./tools/verify-bananapi-filogic-r3mini-candidate.sh
```

三個入口只選取 `bananapir3mini`、固定驗證 JSON 與獨立 OverlayFS 上層。專用隔離入口明確呼叫 R3 Mini 專用建置封裝，預設保留共用建置器的 80 GiB 空間要求；使用者可提高要求，但任何低於 40 GiB 的值都會先由專用入口拒絕，之後仍須再通過共用建置器的硬下限與其他隔離保護。本次依任務限制只驗證入口與元件，沒有執行上述完整映像建置。

驗證入口固定啟用 XZ 完整性與解壓串流同一性，不接受環境變數降級。板卡專用收尾器會把載荷證據視為不可信輸入，要求 BL2、FIP、GPT 各自恰好一筆，逐項核對板卡、位置、偏移、大小上下限及 SHA-256；BL2 不得超過 `4176896` bytes、FIP 不得超過 `4194304` bytes，GPT 必須恰為 `17408` bytes。

最終 `VERIFICATION_STATUS.json` 另保存 `emmc_image_contract`：user area 目標 `/dev/mmcblk0`、GPT 範本雜湊、user area 映像不是完整冷開機安裝物，以及 boot0 目標 `/dev/mmcblk0boot0`、固定 BL2 雜湊、零偏移、必須分離寫入、必須處理 `force_ro`、必須執行 boot partition enable，且 boot0 尚未實機驗證。狀態同時固定 `internal_candidate_only=true`、`public_release_authorized=false`、`hardware_claims_allowed=false` 與 `release_gate.status=blocked`，避免軟體內容驗證被誤解為 eMMC 安裝、硬體或對外發布核准。

## 升級門檻

1. 先取得 `dram.o` 可再散布的明確授權旁證，或以具可再散布來源的實作取代。
2. 在隔離快取建置完整 minimal CLI，通過 IMG／XZ 同一性、GPT、user area BL2／FIP、U-Boot、DTB、核心設定、套件、韌體與授權驗證，才能形成 L2 映像證據。
3. 建立受控且防呆的 eMMC 安裝程序，分別處理 user area、`boot0` 的 `force_ro`、BL2 與 boot partition enable；不得覆寫 `factory` 校準資料。
4. 以實體板保存 UART 冷啟動、斷電重啟、eMMC、雙 2.5GbE、EN8811H、Wi-Fi、PCIe／NVMe、USB、GPIO、I2C、SPI、熱與壓力測試證據，才能升為 L3。
