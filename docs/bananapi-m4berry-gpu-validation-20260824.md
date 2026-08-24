# Banana Pi M4 Berry GPU 驗證紀錄

日期：2026-08-24

## 結論

目前映像已啟用 Mali-G31 的 Panfrost 硬體加速，並非 LLVMpipe 軟體繪圖：

- `direct rendering: Yes`
- `Accelerated: yes`
- `GL_RENDERER: Mali-G31 (Panfrost)`
- OpenGL ES 3.1，Mesa 25.2.8
- 存在 `/dev/dri/renderD128`

在 X11、`800x600` 視窗、完整預設場景及 CPU `ondemand` 的受控條件下：

| 條件 | 分數 | 判讀 |
| --- | ---: | --- |
| XFCE 合成器開啟 | 207 | 一般桌面基準 |
| XFCE 合成器關閉 | 268 | 效能基準 |
| 合成器開啟並強制 `vblank_mode=0` | 133 | 效能下降，不採用 |

建議將相同版本及相同測試條件的驗收門檻訂為：

- 一般桌面模式：至少 180 分。
- 關閉合成器的效能模式：至少 240 分。
- 結果低於門檻時，先核對後端、解析度、合成器、Mesa 版本及 `GL_RENDERER`，不可只用總分判定硬體故障。

使用者回報的 99 分不能直接和上述分數比較。桌面命令紀錄包含 `glmark2-es2-wayland`，但目前工作階段是 X11，該命令在此環境回報 `Could not initialize canvas`；此外全螢幕 `1920x1080` 的像素數是 `800x600` 的 4.32 倍。必須保留完整命令及輸出的 `Surface Size` 才能建立有效基線。

## 固定驗證命令

先確認繪圖器：

```bash
glxinfo -B | grep -E 'direct rendering|Accelerated|OpenGL renderer|OpenGL version'
es2_info | grep -E 'EGL_VERSION|GL_RENDERER|GL_VERSION'
ls -l /dev/dri
```

執行可比較的 X11 基準：

```bash
glmark2-es2 --size 800x600
```

每次紀錄至少必須包含：

```text
GL_RENDERER
GL_VERSION
Surface Size
glmark2 Score
```

不要加入 `vblank_mode=0`，本次實測會使分數由 207 降至 133。

## 冷開機可靠性修正

首次冷開機曾出現：

```text
panfrost 1800000.gpu: deferred probe timeout, ignoring dependency
panfrost 1800000.gpu: probe with driver panfrost failed with error -110
```

同一映像暖重啟後 Panfrost 可正常初始化，顯示問題是 H616/H618 PRCM PPU 電源域提供者與 Panfrost 模組的載入順序競態。核心設定調整為：

```text
CONFIG_SUN50I_H6_PRCM_PPU=y
CONFIG_DRM_PANFROST=m
```

PPU 內建可在 Panfrost 模組探測前提供 GPU 電源域；Panfrost 保持模組，不擴大修改範圍。M4 Berry 裝置樹已設定 `mali-supply = <&reg_dcdc1>` 並啟用 GPU。

目前 H618 GPU 沒有 OPP 表及 devfreq 動態調頻，本修正處理冷開機可靠性，不會直接提高 GPU 跑分。

## 驗收程序

安裝新核心後執行：

```bash
uname -a
grep -E 'CONFIG_(SUN50I_H6_PRCM_PPU|DRM_PANFROST)=' /boot/config-$(uname -r)
lsmod | grep -E 'panfrost|sun50i_h6_prcm_ppu'
sudo dmesg | grep -Ei 'panfrost|gpu|deferred probe|error -110'
glxinfo -B
glmark2-es2 --size 800x600
```

預期 `panfrost` 出現在模組清單；`sun50i_h6_prcm_ppu` 不會出現於 `lsmod`，因為它已內建核心。

完成暖重啟後，還要進行至少十次完整斷電再上電。每次都必須符合：

- Panfrost 初始化成功。
- 沒有 `deferred probe timeout` 或 `error -110`。
- `glxinfo -B` 顯示 `Mali-G31 (Panfrost)` 與 `Accelerated: yes`。
- `glmark2-es2 --size 800x600` 一般桌面分數至少 180。

## 證據

本次未追蹤的大型與原始證據位於：

```text
output/evidence/bpi-m4berry-a1-ddr/M4B-power-on-20260824-224812/
```

主要檔案：

- `gpu-renderer-validation.txt`：Panfrost、DRM 與 OpenGL 資訊。
- `gpu-glmark2-controlled-matrix.txt`：三種受控條件的完整場景結果。
- `gpu-render-validation-crosscheck.txt`：Panfrost 與 LLVMpipe 的渲染交叉驗證。
- `gpu-user-desktop-glmark-monitor.txt`：使用者桌面測試期間的溫度與核心錯誤監控。
- `gpu-wayland-command-probe.txt`：目前 X11 工作階段執行 Wayland 後端的失敗證據。
- `dmesg-before.txt`：冷開機 `-110` 探測失敗紀錄。

`glmark2 --validate` 在 Panfrost 的桌面模糊及中等片段函式場景仍有像素差異，而 LLVMpipe 通過相同場景。這不影響硬體加速成立的結論，但在對外宣告圖形相容性前，仍須針對實際廚房秤應用、顯示介面及長時間畫面更新進行驗證。
