"""Shared helpers for the DemoPay PDFs: styles, tables, images, screenshot crops, page footer."""
import sys
from pathlib import Path

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, KeepTogether, Paragraph, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[2]
A = ROOT / "docs" / "demopay-assets"

NAVY, TEAL, AMBER, GRID, LIGHT, GREEN, RED = (colors.HexColor(c) for c in ("#14325c", "#1b7f8c", "#b86b00", "#c4cfdb", "#eef3f8", "#2e7d4f", "#a32020"))
ss = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=ss["Normal"], fontName="Helvetica", fontSize=9.6, leading=13.6, spaceAfter=5)
SMALL = ParagraphStyle("small", parent=BODY, fontSize=7.8, leading=10, spaceAfter=2, textColor=colors.HexColor("#444444"))
CAP = ParagraphStyle("cap", parent=SMALL, alignment=1, spaceBefore=2, spaceAfter=6)
CELL = ParagraphStyle("cell", parent=BODY, fontSize=8.2, leading=10.4, spaceAfter=0)
CELLC = ParagraphStyle("cellc", parent=CELL, alignment=1, fontSize=7.4, leading=9)
CELLB = ParagraphStyle("cellb", parent=CELL, fontName="Helvetica-Bold")
HEADC = ParagraphStyle("headc", parent=CELL, fontName="Helvetica-Bold", textColor=colors.white)
HEADCC = ParagraphStyle("headcc", parent=HEADC, alignment=1, fontSize=7.4, leading=9)
H1 = ParagraphStyle("h1", parent=BODY, fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=NAVY, spaceBefore=2, spaceAfter=4, keepWithNext=1)
H2 = ParagraphStyle("h2", parent=BODY, fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=TEAL, spaceBefore=8, spaceAfter=3, keepWithNext=1)
BUL = ParagraphStyle("bul", parent=BODY, leftIndent=12, bulletIndent=2, spaceAfter=2.5)
ONE = ParagraphStyle("one", parent=BODY, fontName="Helvetica-Oblique", fontSize=10.5, leading=14.5, textColor=colors.HexColor("#33445c"), spaceAfter=6)
NOTE = ParagraphStyle("note", parent=BODY, fontSize=9, leading=12.5, textColor=colors.HexColor("#5a3a00"), backColor=colors.HexColor("#fff4de"),
                      borderColor=AMBER, borderWidth=0.6, borderPadding=6, spaceBefore=4, spaceAfter=10)
WHYB = ParagraphStyle("why", parent=BODY, fontSize=9, leading=12.5, textColor=colors.HexColor("#123b44"), backColor=colors.HexColor("#e6f4f6"),
                      borderColor=TEAL, borderWidth=0.6, borderPadding=6, spaceBefore=4, spaceAfter=10)
COVT = ParagraphStyle("covt", parent=BODY, fontName="Helvetica-Bold", fontSize=27, leading=32, textColor=NAVY, spaceAfter=8)
COVS = ParagraphStyle("covs", parent=BODY, fontSize=13, leading=18, textColor=TEAL, spaceAfter=14)
W = 174 * mm


def P(t, st=BODY):
    return Paragraph(t, st)


def bullets(items):
    return [Paragraph(t, BUL, bulletText="•") for t in items]


def table(rows, widths, head=True, first_bold=False, extra=()):
    data = []
    for i, r in enumerate(rows):
        data.append([c if not isinstance(c, str) else Paragraph(c, HEADC if (head and i == 0) else (CELLB if (first_bold and j == 0) else CELL)) for j, c in enumerate(r)])
    t = Table(data, colWidths=[w * W for w in widths], repeatRows=1 if head else 0)
    st = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (0, 0), (-1, -1), 0.4, GRID), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
          ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
    if head:
        st.append(("BACKGROUND", (0, 0), (-1, 0), NAVY))
    st += [("BACKGROUND", (0, i), (-1, i), LIGHT) for i in range(2 if head else 1, len(rows), 2)]
    t.setStyle(TableStyle(st + list(extra)))
    return t


def img(name, width):
    p = A / name
    w, h = PILImage.open(p).size
    return Image(str(p), width=width, height=width * h / w)


BG = (239, 242, 247)


def crop(name, part):
    """Cut a web screenshot down to the phone card, the X-ray panel, or both (no empty page margin), upscaled for print."""
    out = A / "_crop" / f"{Path(name).stem}_{part}.png"
    out.parent.mkdir(exist_ok=True)
    im = PILImage.open(A / name).convert("RGB")
    x0, x1 = {"card": (44, 320), "xray": (324, 756), "all": (44, 756)}[part]
    reg = im.crop((x0, 24, x1, im.height))
    px = reg.load()
    bottom = 0
    for y in range(reg.height - 1, -1, -1):
        if any(max(abs(px[x, y][k] - BG[k]) for k in range(3)) > 10 for x in range(0, reg.width, 3)):
            bottom = y
            break
    reg = reg.crop((0, 0, reg.width, min(reg.height, bottom + 8)))
    reg = reg.resize((reg.width * 2, reg.height * 2), PILImage.LANCZOS)
    reg.save(out)
    return f"_crop/{out.name}"


def _card(name, width):
    return img(name, width * 0.78) if name.startswith("android_") else img(crop(name, "card"), width)


def side(items, flow, width=W * 0.21):
    """One or two screenshots on the left, explanation on the right."""
    n = len(items)
    cells = [[_card(a, width), P(c, CAP)] for a, c in items]
    t = Table([cells + [flow]], colWidths=[W * 0.235] * n + [W - W * 0.235 * n])
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 5)]))
    return t


def row_cards(items, width=W * 0.225):
    n = len(items)
    t = Table([[_card(a, width) for a, _ in items], [P(c, CAP) for _, c in items]], colWidths=[W / n] * n)
    t.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
    return t


def fig(name, caption, width=W):
    return KeepTogether([img(name, width), P(caption, CAP)])


def shot(name, caption, width=W * 0.78):
    t = Table([[img(name, width)]], colWidths=[W])
    t.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"), ("BOX", (0, 0), (-1, -1), 0.4, GRID), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    return KeepTogether([t, P(caption, CAP)])


def pair(a, ca, b, cb, width=W * 0.485):
    t = Table([[img(a, width), img(b, width)], [P(ca, CAP), P(cb, CAP)]], colWidths=[W / 2, W / 2])
    t.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
    return KeepTogether([t])


def phones(items, width=W * 0.235):
    n = len(items)
    t = Table([[img(a, width) for a, _ in items], [P(c, CAP) for _, c in items]], colWidths=[W / n] * n)
    t.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
    return KeepTogether([t])


def make_footer(text):
    def footer(c, doc):
        c.saveState()
        c.setFont("Helvetica", 7.5); c.setFillColor(colors.HexColor("#666666"))
        c.drawString(18 * mm, 10 * mm, text)
        c.drawRightString(A4[0] - 18 * mm, 10 * mm, f"Page {doc.page}")
        c.setStrokeColor(GRID); c.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
        c.restoreState()
    return footer


