#!/usr/bin/env python3
"""產生 Banana Pi M4Zero A1 移植與 DDR 調適正式 PDF。"""

from __future__ import annotations

import argparse
import html
from pathlib import Path

import markdown
from weasyprint import HTML


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MARKDOWN = REPO_ROOT / "docs/reports/bananapi-m4zero-a1-porting-ddr-tuning-report-20260820.md"
DEFAULT_PDF = REPO_ROOT / "docs/reports/bananapi-m4zero-a1-porting-ddr-tuning-report-20260820.pdf"

REQUIRED_TEXT = (
    "Banana Pi M4Zero A1",
    "P02e5",
    "TPR6=0x3a808080",
    "TPR11=0x25252523",
    "TPR12=0x110f0f10",
    "0x30..0x42",
    "270 通過",
    "52 筆",
    "20/20",
    "受控完全斷電冷啟動",
)

FORBIDDEN_TEXT = (
    "Bluetooth",
    "bluetooth",
    "藍牙",
    "Reset failed",
    "BCM:",
)

CSS = r"""
@page {
  size: A4;
  margin: 18mm 15mm 18mm 15mm;
  @top-left {
    content: "BANANA PI M4ZERO｜工程交付";
    font-family: "Noto Sans CJK TC";
    font-size: 7.5pt;
    font-weight: 700;
    color: #1f5b70;
    letter-spacing: .4pt;
    border-bottom: .4pt solid #b7cbd3;
    padding-bottom: 2mm;
  }
  @top-right {
    content: "A1 移植修正與 DDR 調適報告";
    font-family: "Noto Sans CJK TC";
    font-size: 7.5pt;
    color: #526872;
    border-bottom: .4pt solid #b7cbd3;
    padding-bottom: 2mm;
  }
  @bottom-left {
    content: "工程候選｜2026-08-20";
    font-family: "Noto Sans CJK TC";
    font-size: 7.2pt;
    color: #65757c;
    border-top: .4pt solid #c9d6db;
    padding-top: 2mm;
  }
  @bottom-right {
    content: "第 " counter(page) " 頁／共 " counter(pages) " 頁";
    font-family: "Noto Sans CJK TC";
    font-size: 7.2pt;
    color: #65757c;
    border-top: .4pt solid #c9d6db;
    padding-top: 2mm;
  }
}

@page cover {
  margin: 0;
  @top-left { content: none; }
  @top-right { content: none; }
  @bottom-left { content: none; }
  @bottom-right { content: none; }
}

html {
  color: #22333b;
  font-family: "Noto Sans CJK TC", "Noto Sans CJK", sans-serif;
  font-size: 9.4pt;
  line-height: 1.62;
  text-rendering: optimizeLegibility;
}

body { margin: 0; }

.cover {
  page: cover;
  width: 210mm;
  height: 297mm;
  break-after: page;
  position: relative;
  overflow: hidden;
  background:
    radial-gradient(circle at 83% 18%, rgba(29, 166, 174, .24) 0, rgba(29, 166, 174, 0) 30%),
    linear-gradient(145deg, #0e3041 0%, #164f62 58%, #0f6970 100%);
  color: #fff;
}

.cover::after {
  content: "";
  position: absolute;
  right: -24mm;
  bottom: -30mm;
  width: 128mm;
  height: 128mm;
  border: 1.5mm solid rgba(255,255,255,.11);
  border-radius: 50%;
  box-shadow: 0 0 0 18mm rgba(255,255,255,.035), 0 0 0 36mm rgba(255,255,255,.025);
}

.cover-band {
  position: absolute;
  left: 0;
  top: 0;
  width: 8mm;
  height: 297mm;
  background: #e4b849;
}

.cover-content {
  position: absolute;
  z-index: 2;
  left: 25mm;
  right: 23mm;
  top: 34mm;
}

.cover h1 {
  margin: 8mm 0 5mm;
  color: #fff;
  font-family: "Noto Serif CJK TC", serif;
  font-size: 29pt;
  line-height: 1.28;
  letter-spacing: .5pt;
  border: 0;
}

.cover-kicker {
  margin: 0;
  color: #8fe1de;
  font-size: 9pt;
  font-weight: 700;
  letter-spacing: 1.5pt;
}

.cover-subtitle {
  width: 90%;
  color: #d8eef1;
  font-size: 13pt;
  line-height: 1.55;
}

.cover-status {
  display: inline-block;
  margin: 11mm 0 8mm;
  padding: 3mm 5mm;
  border: .4mm solid #e4b849;
  border-radius: 2mm;
  background: rgba(228,184,73,.13);
  color: #ffe4a3;
  font-size: 10.5pt;
  font-weight: 700;
}

.cover table {
  width: 88%;
  color: #f5fbfc;
  background: rgba(255,255,255,.06);
  border: .3mm solid rgba(255,255,255,.2);
}

.cover th, .cover td {
  padding: 2.2mm 3mm;
  border-color: rgba(255,255,255,.17);
  background: transparent;
  font-size: 8.5pt;
}

.cover tbody tr:nth-child(even) td,
.cover tbody tr:nth-child(odd) td {
  color: #f5fbfc;
  background: transparent;
}

.cover th { color: #9fe0df; }
.cover code { color: #fff; background: transparent; }
.cover-note { margin-top: 10mm; width: 88%; color: #b9d8dc; font-size: 8.2pt; }

h1, h2, h3 {
  font-family: "Noto Sans CJK TC", sans-serif;
  color: #123f52;
  line-height: 1.3;
  break-after: avoid;
}

h1 {
  margin: 6mm 0 4mm;
  padding-bottom: 2.2mm;
  font-size: 20pt;
  border-bottom: 1.1mm solid #1f8191;
}

h2 {
  margin: 5.5mm 0 2.4mm;
  font-size: 13.5pt;
  color: #185e70;
}

h3 { margin: 4.5mm 0 2mm; font-size: 11pt; }
p { margin: 0 0 2.8mm; text-align: justify; }
ul, ol { margin: 1.5mm 0 3.5mm 6mm; padding-left: 4mm; }
li { margin: 0 0 1mm; }
strong { color: #153f4c; }

code {
  font-family: "Noto Sans Mono CJK TC", "Noto Sans Mono CJK", monospace;
  font-size: .91em;
  background: #edf3f5;
  color: #123c4e;
  padding: .15em .28em;
  border-radius: .7mm;
  overflow-wrap: anywhere;
  word-break: break-all;
}

pre {
  margin: 3mm 0 4mm;
  padding: 3mm 4mm;
  color: #e8f6f7;
  background: #173744;
  border-left: 1.3mm solid #d5a93c;
  border-radius: 1mm;
  font-size: 8pt;
  line-height: 1.45;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

pre code { color: inherit; background: transparent; padding: 0; }
blockquote {
  margin: 4mm 0;
  padding: 3.2mm 4mm;
  border-left: 1.4mm solid #1b8b97;
  background: #edf7f7;
  color: #234b56;
}
blockquote p:last-child { margin-bottom: 0; }

table {
  width: 100%;
  margin: 3mm 0 5mm;
  border-collapse: collapse;
  table-layout: auto;
  font-size: 8.1pt;
  line-height: 1.43;
}

thead { display: table-header-group; }
tr { break-inside: avoid; }
th, td {
  padding: 2mm 2.2mm;
  border: .25mm solid #bdced4;
  vertical-align: top;
  overflow-wrap: anywhere;
  word-break: normal;
}
th {
  color: #fff;
  background: #1c6274;
  font-weight: 700;
  text-align: left;
}
tbody tr:nth-child(even) td { background: #f1f6f7; }
td code { font-size: 7.45pt; word-break: break-all; }

.matrix-table table {
  table-layout: auto;
  font-size: 7pt;
}
.matrix-table td:nth-child(1),
.matrix-table td:nth-child(2) {
  white-space: nowrap;
  word-break: normal;
}
.matrix-table td code { font-size: 6.75pt; }

.management-summary {
  break-after: page;
}

.management-summary h1 { margin-top: 0; }
.management-summary table { font-size: 7.85pt; }
.management-summary h2 { margin-top: 4mm; }
.management-summary ol { margin-bottom: 2mm; }
.management-summary code { white-space: nowrap; word-break: normal; }

.summary-verdict, .final-verdict {
  margin: 4mm 0;
  padding: 3.5mm 4.5mm;
  border: .45mm solid #bf8d22;
  border-radius: 1.5mm;
  background: #fff7df;
  color: #604b17;
}

.toc-wrapper {
  break-after: page;
  font-size: 8.2pt;
  line-height: 1.32;
}

.toc-wrapper h1 { margin-top: 0; }
.toc > ul {
  columns: 2;
  column-gap: 9mm;
  column-rule: .2mm solid #d6e0e3;
}
.toc ul { list-style: none; margin: 0; padding: 0; }
.toc ul ul { margin-left: 3.5mm; }
.toc > ul > li { break-inside: avoid; margin-bottom: 1.2mm; }
.toc li { margin: .35mm 0; border-bottom: .2mm dotted #c8d4d8; }
.toc a { color: #244e5d; text-decoration: none; }
.toc a::after {
  content: target-counter(attr(href), page);
  float: right;
  color: #677b84;
}

a { color: #0f6f7e; }
hr { border: 0; border-top: .3mm solid #b9cbd1; margin: 6mm 0; }
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="產生 A1 DDR 調適報告 PDF")
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN, help="輸入 Markdown")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF, help="輸出 PDF")
    return parser.parse_args()


def validate_source(source: str) -> None:
    missing = [item for item in REQUIRED_TEXT if item not in source]
    forbidden = [item for item in FORBIDDEN_TEXT if item in source]
    if missing:
        raise SystemExit(f"報告缺少必要文字：{', '.join(missing)}")
    if forbidden:
        raise SystemExit(f"報告含有禁止文字：{', '.join(forbidden)}")


def render_html(source: str) -> str:
    body = markdown.markdown(
        source,
        extensions=(
            "extra",
            "toc",
            "sane_lists",
            "codehilite",
        ),
        extension_configs={
            "toc": {"permalink": False, "toc_depth": "1-3"},
            "codehilite": {"guess_lang": False, "noclasses": True},
        },
        output_format="html5",
    )
    title = "Banana Pi M4Zero A1 移植修正與 DDR 調適報告"
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="author" content="Banana Pi 工程團隊">
<meta name="description" content="Banana Pi M4Zero A1 792 MHz 移植修正、DDR 參數收斂、映像矩陣、驗證證據與後續 Gate。">
<meta name="keywords" content="Banana Pi M4Zero,A1,DDR,792 MHz,P02e5,工程候選">
<meta name="dcterms.created" content="2026-08-20">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
{body}
</body>
</html>
"""


def main() -> int:
    args = parse_args()
    markdown_path = args.markdown.resolve()
    pdf_path = args.pdf.resolve()
    source = markdown_path.read_text(encoding="utf-8")
    validate_source(source)
    rendered = render_html(source)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=rendered, base_url=str(REPO_ROOT)).write_pdf(pdf_path)
    print(f"已產生：{pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
