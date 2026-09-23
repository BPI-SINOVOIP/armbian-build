#!/usr/bin/env python3
"""將固定的繁體中文 CM6 rc3 操作手冊產生為可列印 PDF。"""
from pathlib import Path
import markdown
from weasyprint import HTML

ROOT = Path(__file__).resolve().parents[1]
STEM = "BPI-CM6-rc3-完整測試與驗證手冊-20260923"
CSS = """
@page {
  size: A4;
  margin: 16mm 15mm 17mm;
  @bottom-left { content: "BPI-CM6 rc3｜候選驗證｜2026-09-23";
    font: 8pt "Noto Sans CJK TC"; color: #546172; }
  @bottom-right { content: counter(page) " / " counter(pages);
    font: 8pt "DejaVu Sans"; color: #546172; }
}
html { font-family: "Noto Sans CJK TC", sans-serif; font-size: 10pt;
  line-height: 1.55; color: #182533; }
body { margin: 0; }
h1 { font-size: 19pt; line-height: 1.3; margin: 0 0 5mm; color: #123c5b; }
h2 { font-size: 14pt; margin: 6mm 0 3mm; padding-bottom: 1.4mm;
  border-bottom: 0.5pt solid #becbd5; color: #123c5b; }
h3 { font-size: 11.6pt; margin: 5mm 0 2mm; color: #174e70; }
h1,h2,h3 { break-after: avoid; }
p { margin: 2mm 0; orphans: 3; widows: 3; }
ul,ol { padding-left: 6mm; margin: 2mm 0; }
li { margin: 1mm 0; }
a { color: #175d82; text-decoration: none; overflow-wrap: anywhere; }
strong { font-weight: 700; }
code { font-family: "DejaVu Sans Mono", "Noto Sans CJK TC", monospace;
  font-size: 8.3pt; overflow-wrap: anywhere; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-all;
  background: #f1f4f6; border-left: 2pt solid #6f91a8; padding: 2.3mm;
  margin: 2.5mm 0; line-height: 1.42; }
pre code { white-space: pre-wrap; }
table { width: 100%; border-collapse: collapse; margin: 3mm 0;
  font-size: 9pt; table-layout: fixed; }
thead { display: table-header-group; background: #e7eef3; }
th,td { border: 0.5pt solid #c5cfd8; padding: 1.8mm; vertical-align: top;
  text-align: left; overflow-wrap: anywhere; }
th { font-weight: 700; }
tr { break-inside: avoid; }
table code { font-size: 7.5pt; }
body > table:first-of-type th:nth-child(1) { width: 8%; }
body > table:first-of-type th:nth-child(2) { width: 24%; }
.pagebreak { break-after: page; }
"""


def main():
    source = ROOT / "docs" / f"{STEM}.md"
    body = markdown.markdown(source.read_text(), extensions=["tables", "fenced_code"])
    html = ('<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            '<title>BPI-CM6 rc3 完整測試與驗證手冊</title><style>' + CSS +
            '</style></head><body>' + body + '</body></html>')
    output = source.with_suffix(".pdf")
    HTML(string=html, base_url=str(source.parent)).write_pdf(output)
    print(f"已產生：{output}")


if __name__ == "__main__":
    main()
