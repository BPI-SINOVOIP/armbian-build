#!/usr/bin/env python3
"""Build the customer-facing Banana Pi M4Zero A1 DDR report PDF."""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

import markdown
from weasyprint import HTML


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MARKDOWN = REPO_ROOT / (
    "docs/reports/"
    "bananapi-m4zero-a1-porting-ddr-tuning-customer-report-en-20260820.md"
)
DEFAULT_PDF = REPO_ROOT / (
    "docs/reports/"
    "bananapi-m4zero-a1-porting-ddr-tuning-customer-report-en-20260820.pdf"
)

REQUIRED_TEXT = (
    "A1 at 792 MHz is an engineering validation candidate",
    "not production-qualified",
    "not a stable release",
    "P02e5",
    "TPR6=0x3a808080",
    "0x24242422",
    "0x25252523",
    "0x110f1111",
    "0x110f0f10",
    "0x30..0x42",
    "0x2e",
    "0x44",
    "64 MiB M2 five-pass pattern",
    "20/20",
    "322 records",
    "270 passes",
    "52 retained",
    "2 GiB single-rank",
    "4 GiB dual-rank",
    "Bookworm",
    "Jammy",
    "Noble",
    "Resolute",
    "Trixie",
    "Controlled complete power-cycle cold boot",
    "Flashed-media and bootloader readback",
    "Long-duration concurrent stress",
    "Broader common-window coverage",
    "complete UART",
    "exact image identity",
)

FORBIDDEN_LITERAL = (
    "Bluetooth",
    "bluetooth",
    "\u85cd\u7259",
    "Reset failed",
    "BCM:",
    "0256",
    "0438",
    "0845",
    "1116",
)

FORBIDDEN_PATTERNS = (
    ("standalone forbidden acronym", re.compile(r"\bBT\b")),
    ("email address", re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")),
    ("raw internal absolute path", re.compile(r"/(?:home|media|mnt|tmp|var/tmp)/")),
    (
        "raw repository path",
        re.compile(r"(?<![A-Za-z0-9_-])(?:docs|tools|tests|output|patch)/"),
    ),
    ("raw Git commit SHA", re.compile(r"\b[0-9a-f]{40}\b")),
)

FORBIDDEN_DASHES = tuple(chr(codepoint) for codepoint in range(0x2010, 0x2016))

CSS = r"""
@page {
  size: A4;
  margin: 17mm 15mm 17mm 15mm;
  @top-left {
    content: "BANANA PI M4ZERO | CUSTOMER ENGINEERING REPORT";
    font-family: "DejaVu Sans", sans-serif;
    font-size: 7.1pt;
    font-weight: 700;
    color: #126b78;
    letter-spacing: .35pt;
    border-bottom: .35pt solid #afc8ce;
    padding-bottom: 2mm;
  }
  @top-right {
    content: "A1 DDR | REVISION 1.0";
    font-family: "DejaVu Sans", sans-serif;
    font-size: 7.1pt;
    color: #526970;
    border-bottom: .35pt solid #afc8ce;
    padding-bottom: 2mm;
  }
  @bottom-left {
    content: "ENGINEERING VALIDATION CANDIDATE | 20 AUGUST 2026";
    font-family: "DejaVu Sans", sans-serif;
    font-size: 6.8pt;
    color: #687980;
    border-top: .35pt solid #c3d2d6;
    padding-top: 2mm;
  }
  @bottom-right {
    content: "PAGE " counter(page) " OF " counter(pages);
    font-family: "DejaVu Sans", sans-serif;
    font-size: 6.8pt;
    color: #687980;
    border-top: .35pt solid #c3d2d6;
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
  color: #203239;
  font-family: "DejaVu Sans", sans-serif;
  font-size: 9.15pt;
  line-height: 1.48;
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
  color: #ffffff;
  background:
    radial-gradient(circle at 82% 17%, rgba(73, 207, 196, .23) 0, rgba(73, 207, 196, 0) 28%),
    linear-gradient(145deg, #092d3b 0%, #105468 57%, #0d7074 100%);
}

.cover::after {
  content: "";
  position: absolute;
  right: -31mm;
  bottom: -33mm;
  width: 133mm;
  height: 133mm;
  border: 1.3mm solid rgba(255, 255, 255, .11);
  border-radius: 50%;
  box-shadow:
    0 0 0 18mm rgba(255, 255, 255, .035),
    0 0 0 36mm rgba(255, 255, 255, .023);
}

.cover-accent {
  position: absolute;
  left: 0;
  top: 0;
  width: 8mm;
  height: 297mm;
  background: #e7b948;
}

.cover-grid {
  position: absolute;
  right: 20mm;
  top: 21mm;
  width: 43mm;
  height: 43mm;
  opacity: .16;
  background-image:
    linear-gradient(rgba(255, 255, 255, .8) .25mm, transparent .25mm),
    linear-gradient(90deg, rgba(255, 255, 255, .8) .25mm, transparent .25mm);
  background-size: 7mm 7mm;
}

.cover-content {
  position: absolute;
  z-index: 2;
  left: 25mm;
  right: 23mm;
  top: 31mm;
}

.cover h1 {
  margin: 9mm 0 5mm;
  padding: 0;
  border: 0;
  color: #ffffff;
  font-family: "DejaVu Serif", serif;
  font-size: 28pt;
  line-height: 1.22;
  letter-spacing: .2pt;
}

.cover-kicker {
  margin: 0;
  color: #8ee4df;
  font-size: 9pt;
  font-weight: 700;
  letter-spacing: 1.6pt;
}

.cover-subtitle {
  width: 91%;
  margin: 0;
  color: #d6eef0;
  font-size: 12.5pt;
  line-height: 1.52;
}

.status-chip {
  display: inline-block;
  margin: 11mm 0 8mm;
  padding: 3mm 5mm;
  border: .4mm solid #e7b948;
  border-radius: 1.5mm;
  color: #ffe6a2;
  background: rgba(231, 185, 72, .12);
  font-size: 9.3pt;
  font-weight: 700;
  letter-spacing: .35pt;
}

.cover table {
  width: 91%;
  margin: 0;
  color: #f5fbfc;
  background: rgba(255, 255, 255, .06);
  border: .25mm solid rgba(255, 255, 255, .21);
  font-size: 8.2pt;
}

.cover th,
.cover td {
  padding: 2.05mm 3mm;
  border-color: rgba(255, 255, 255, .17);
  background: transparent;
}

.cover tbody tr:nth-child(even) td,
.cover tbody tr:nth-child(odd) td {
  color: #f5fbfc;
  background: transparent;
}

.cover th { color: #a1e6e2; }
.cover code { color: #ffffff; background: transparent; }
.cover-note {
  width: 91%;
  margin-top: 9mm;
  color: #bed9dd;
  font-size: 7.9pt;
  line-height: 1.5;
}

.report-page {
  break-before: page;
}

.section-kicker {
  margin: 0 0 1.3mm;
  color: #b07a10;
  font-size: 7.2pt;
  font-weight: 700;
  letter-spacing: 1.3pt;
}

h1, h2, h3 {
  color: #123f50;
  line-height: 1.23;
  break-after: avoid;
}

h1 {
  margin: 0 0 4.2mm;
  padding-bottom: 2.2mm;
  border-bottom: .9mm solid #208696;
  font-family: "DejaVu Serif", serif;
  font-size: 19.5pt;
}

h2 {
  margin: 4.5mm 0 2.1mm;
  color: #176475;
  font-size: 12.1pt;
}

h3 {
  margin: 4mm 0 1.8mm;
  font-size: 10.5pt;
}

p { margin: 0 0 2.55mm; orphans: 3; widows: 3; }
ul, ol { margin: 1.5mm 0 3mm 5mm; padding-left: 4.5mm; }
li { margin: 0 0 .9mm; orphans: 3; widows: 3; }
strong { color: #153e49; }

code {
  padding: .12em .28em;
  border-radius: .6mm;
  color: #123e4d;
  background: #edf3f4;
  font-family: "Noto Sans Mono", "DejaVu Sans Mono", monospace;
  font-size: .9em;
  overflow-wrap: anywhere;
}

table {
  width: 100%;
  margin: 2.6mm 0 4mm;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 7.8pt;
  line-height: 1.34;
}

thead { display: table-header-group; }
tr { break-inside: avoid; }
th, td {
  padding: 1.75mm 2mm;
  border: .23mm solid #bdcfd4;
  vertical-align: top;
  overflow-wrap: anywhere;
}
th {
  color: #ffffff;
  background: #1d6676;
  font-weight: 700;
  text-align: left;
}
tbody tr:nth-child(even) td { background: #f0f6f7; }
td code { font-size: 7.15pt; }

.status-panel,
.insight-panel,
.risk-panel {
  margin: 3.8mm 0;
  padding: 3.1mm 3.8mm;
  border-radius: 1.2mm;
  break-inside: avoid;
}

.status-panel {
  border: .4mm solid #b9881e;
  color: #584618;
  background: #fff7df;
}

.insight-panel {
  border-left: 1.2mm solid #17808d;
  color: #214b55;
  background: #edf7f8;
}

.risk-panel {
  border-left: 1.2mm solid #b85d43;
  color: #5f352a;
  background: #fff1ec;
}

.executive table { font-size: 7.6pt; }
.executive h2 { margin-top: 3.7mm; }
.appendix table { font-size: 7.6pt; }
.appendix h2 { margin-top: 3.6mm; }
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the customer-facing A1 DDR tuning report PDF."
    )
    parser.add_argument(
        "--markdown", type=Path, default=DEFAULT_MARKDOWN, help="Input Markdown file."
    )
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF, help="Output PDF file.")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate the Markdown without generating the PDF.",
    )
    return parser.parse_args()


def validate_source(source: str) -> None:
    errors: list[str] = []

    missing = [item for item in REQUIRED_TEXT if item not in source]
    if missing:
        errors.append("missing required content: " + ", ".join(missing))

    forbidden = [item for item in FORBIDDEN_LITERAL if item in source]
    if forbidden:
        errors.append("contains forbidden literal text: " + ", ".join(forbidden))

    for label, pattern in FORBIDDEN_PATTERNS:
        if pattern.search(source):
            errors.append(f"contains {label}")

    present_dashes = [character for character in FORBIDDEN_DASHES if character in source]
    if present_dashes:
        codepoints = ", ".join(f"U+{ord(character):04X}" for character in present_dashes)
        errors.append("contains forbidden Unicode dash characters: " + codepoints)

    if not source.isascii():
        errors.append("contains non-ASCII report text")

    if errors:
        raise SystemExit("Report validation failed:\n- " + "\n- ".join(errors))


def render_html(source: str) -> str:
    body = markdown.markdown(
        source,
        extensions=("extra", "sane_lists"),
        output_format="html5",
    )
    title = "Banana Pi M4Zero A1 Porting Correction and DDR Tuning Customer Report"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="author" content="Engineering Documentation">
<meta name="description" content="Customer-facing engineering report for the Banana Pi M4Zero A1 792 MHz porting correction and DDR tuning candidate.">
<meta name="keywords" content="Banana Pi M4Zero,A1,DDR,792 MHz,P02e5,engineering validation">
<meta name="dcterms.created" content="2026-08-20">
<meta name="dcterms.modified" content="2026-08-20">
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

    if args.check_only:
        print(f"Validated Markdown: {markdown_path}")
        return 0

    rendered = render_html(source)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=rendered, base_url=str(REPO_ROOT)).write_pdf(pdf_path)
    print(f"Generated PDF: {pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
