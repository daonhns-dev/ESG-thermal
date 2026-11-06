"""Render the Q15 API contract as a Korean-readable PDF."""

from __future__ import annotations

import html
import re
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "Q15_VTI_INTEGRATION_API_FINAL.md"
OUTPUT = ROOT / "output" / "pdf" / "Q15_API_명세서_v1.0_2차검토반영본_20261106.pdf"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

pdfmetrics.registerFont(TTFont("Malgun", r"C:\Windows\Fonts\malgun.ttf"))
pdfmetrics.registerFont(TTFont("MalgunBold", r"C:\Windows\Fonts\malgunbd.ttf"))
pdfmetrics.registerFontFamily("Malgun", normal="Malgun", bold="MalgunBold")

NAVY = colors.HexColor("#173B5E")
BLUE = colors.HexColor("#195D8B")
PALE = colors.HexColor("#EAF2F7")
GRAY = colors.HexColor("#576674")
RULE = colors.HexColor("#CCD6DD")

BODY = ParagraphStyle(
    "body", fontName="Malgun", fontSize=9.2, leading=15.2,
    textColor=colors.HexColor("#1E2933"), spaceAfter=6,
    wordWrap="CJK", splitLongWords=1,
)
TITLE = ParagraphStyle(
    "title", parent=BODY, fontName="MalgunBold", fontSize=18,
    leading=25, textColor=NAVY, spaceAfter=14, keepWithNext=True,
)
H2 = ParagraphStyle(
    "h2", parent=BODY, fontName="MalgunBold", fontSize=12,
    leading=18, textColor=BLUE, spaceBefore=13, spaceAfter=6,
    keepWithNext=True,
)
LIST = ParagraphStyle("list", parent=BODY, leftIndent=12, firstLineIndent=-9, spaceAfter=5)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8.1, leading=12, spaceAfter=0)
CELLHEAD = ParagraphStyle("cellhead", parent=CELL, fontName="MalgunBold", textColor=colors.white)
CODE = ParagraphStyle(
    "code", parent=BODY, fontName="Malgun", fontSize=7.4, leading=11,
    leftIndent=9, rightIndent=7, spaceAfter=0, backColor=colors.HexColor("#F4F7F9"),
    wordWrap="CJK", splitLongWords=1,
)


def markup(value: str) -> str:
    """Convert the small inline Markdown subset used by this document."""
    links: list[tuple[str, str]] = []

    def hold_link(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        links.append((label, target))
        return f"LINKTOKEN{len(links) - 1}END"

    value = re.sub(r"\[([^]]+)\]\(([^)]+)\)", hold_link, value)
    value = html.escape(value)
    value = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", value)
    value = re.sub(r"`([^`]+)`", r'<font color="#14517C">\1</font>', value)
    for index, (label, target) in enumerate(links):
        # The PDF can be emailed alone, so avoid dead relative file links.
        value = value.replace(
            f"LINKTOKEN{index}END",
            f'<font color="#14517C">{html.escape(label)}</font>',
        )
    return value


def as_table(lines: list[str]) -> Table:
    rows: list[list[Paragraph]] = []
    for line in lines:
        cells = [part.strip() for part in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        style = CELLHEAD if not rows else CELL
        rows.append([Paragraph(markup(cell), style) for cell in cells])
    count = len(rows[0])
    width = 505
    if count == 3:
        if "HTTP" in lines[0]:
            widths = [52, 150, 303]
        elif "포털 백엔드" in lines[0]:
            widths = [110, 198, 197]
        elif "message" in lines[0]:
            widths = [124, 146, 235]
        else:
            widths = [125, 45, 335]
    else:
        widths = [132, 373]
    table = Table(rows, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
        ("GRID", (0, 0), (-1, -1), 0.35, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def header_footer(canvas, doc) -> None:
    canvas.saveState()
    width, height = A4
    canvas.setStrokeColor(RULE)
    canvas.line(45, height - 42, width - 45, height - 42)
    canvas.setFont("MalgunBold", 8)
    canvas.setFillColor(NAVY)
    canvas.drawString(45, height - 33, "DaonH&S  |  Q15 연동 API 명세 2차 검토 반영본")
    canvas.setFont("Malgun", 8)
    canvas.setFillColor(GRAY)
    canvas.drawRightString(width - 45, 30, f"v1.0 · 2026-11-06  |  {doc.page}")
    canvas.restoreState()


def main() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    lines = text.splitlines()
    story = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line.startswith("# "):
            story.append(Paragraph(markup(line[2:]), TITLE))
            index += 1
            continue
        if line.startswith("## "):
            story.append(CondPageBreak(122))
            story.append(Paragraph(markup(line[3:]), H2))
            index += 1
            continue
        if line.startswith("|"):
            block = []
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                block.append(lines[index])
                index += 1
            story.extend([as_table(block), Spacer(1, 9)])
            continue
        if line.startswith("```"):
            index += 1
            block = []
            while index < len(lines) and not lines[index].startswith("```"):
                block.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            code_rows = [Paragraph(html.escape(x) if x else "&nbsp;", CODE) for x in block]
            if code_rows:
                story.append(KeepTogether(code_rows[:3]))
                story.extend(code_rows[3:])
                story.append(Spacer(1, 8))
            continue
        if line.startswith("- "):
            story.append(Paragraph("• " + markup(line[2:]), LIST))
            index += 1
            continue
        block = [line]
        index += 1
        while index < len(lines) and lines[index].strip() and not lines[index].startswith(("#", "|", "```", "- ")):
            block.append(lines[index].strip())
            index += 1
        story.append(Paragraph(markup(" ".join(block)), BODY))

    doc = BaseDocTemplate(
        str(OUTPUT), pagesize=A4, leftMargin=45, rightMargin=45,
        topMargin=58, bottomMargin=47, title="Q15 VTI-DaonH&S 연동 API 명세 2차 검토 반영본",
        author="DaonH&S",
    )
    frame = Frame(45, 47, 505, A4[1] - 105, leftPadding=0, rightPadding=0,
                  topPadding=0, bottomPadding=0)
    doc.addPageTemplates(PageTemplate(id="main", frames=[frame], onPage=header_footer))
    doc.build(story)
    print(OUTPUT)


if __name__ == "__main__":
    main()
