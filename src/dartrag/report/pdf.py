"""기업 리포트를 PDF 로 그린다 (reportlab).

한글 글꼴은 설치된 TTF(나눔고딕, 애플고딕, 맑은 고딕 순)를 PDF 에 넣는다.
하나도 없으면 PDF 표준 한글 글꼴(HYGothic-Medium)을 쓰는데, 이 경우 글꼴이 파일에
들어가지 않아 보는 프로그램에 따라 모양이 달라질 수 있다. REPORT_FONT 로 경로를 정할 수 있다.
"""

import io
import os
import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.shapes import Drawing, Line
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from dartrag.finance.accounts import METRICS, RATIOS
from dartrag.report.data import DART_URL, DISCLAIMER, CompanyReport

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "~/Library/Fonts/NanumGothic.ttf",
    "/Library/Fonts/NanumGothic.ttf",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)
CID_FONT = "HYGothic-Medium"

INK = colors.HexColor("#1f2328")
MUTED = colors.HexColor("#656d76")
RULE = colors.HexColor("#d0d7de")
BAND = colors.HexColor("#f6f8fa")
ACCENT = colors.HexColor("#2a78d6")
SERIES = [colors.HexColor(c) for c in ("#2a78d6", "#eb6834", "#1baf7a")]

AMOUNT_ROWS = ("revenue", "operating_income", "net_income", "total_liabilities", "total_equity")
RATIO_ROWS = ("operating_margin", "net_margin", "debt_ratio")
STATUS = {"added": "새 섹션", "removed": "삭제", "changed": "변경"}


def register_font(path: str | None = None) -> str:
    """쓸 글꼴 이름. path 가 있으면 그 TTF 를, 없으면 후보를 차례로 찾는다."""
    for p in [path or os.environ.get("REPORT_FONT"), *FONT_CANDIDATES]:
        if not p:
            continue
        f = Path(p).expanduser()
        if f.is_file():
            name = "Report-" + re.sub(r"\W", "", f.stem)
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(f), subfontIndex=0))
            return name
    if CID_FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(CID_FONT))
    return CID_FONT


# --- 숫자 표기 ---------------------------------------------------------------


def eok(amount: int | None) -> str:
    """원 → 억원 (반올림, 천 단위 쉼표)."""
    if amount is None:
        return "-"
    return f"{round(amount / 10**8):,}"


def pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}%"


def chart_unit(values: list[int]) -> tuple[int, str]:
    big = max((abs(v) for v in values), default=0)
    return (10**12, "조원") if big >= 10**12 else (10**8, "억원")


# --- 스타일 ------------------------------------------------------------------


def styles(font: str) -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("base", fontName=font, fontSize=9.5, leading=15, textColor=INK)
    return {
        "body": base,
        "small": ParagraphStyle("small", base, fontSize=8, leading=11.5, textColor=MUTED),
        "cell": ParagraphStyle("cell", base, fontSize=8.5, leading=11.5),
        "num": ParagraphStyle("num", base, fontSize=8.5, leading=11.5, alignment=TA_RIGHT),
        "title": ParagraphStyle("title", base, fontSize=20, leading=26, spaceAfter=4),
        "subtitle": ParagraphStyle("subtitle", base, fontSize=10.5, leading=15, textColor=MUTED),
        "h1": ParagraphStyle(
            "h1", base, fontSize=13.5, leading=19, spaceBefore=14, spaceAfter=6, textColor=ACCENT
        ),
        "h2": ParagraphStyle("h2", base, fontSize=11, leading=16, spaceBefore=8, spaceAfter=4),
        "notice": ParagraphStyle(
            "notice",
            base,
            fontSize=8.5,
            leading=13,
            textColor=MUTED,
            backColor=BAND,
            borderColor=RULE,
            borderWidth=0.5,
            borderPadding=7,
            spaceBefore=10,
            spaceAfter=10,
        ),
    }


def _table(rows: list[list], widths: list[float], numeric_from: int = 1, header: bool = True):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    cmds = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, RULE),
    ]
    if header:
        cmds += [
            ("BACKGROUND", (0, 0), (-1, 0), BAND),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, RULE),
            ("LINEABOVE", (0, 0), (-1, 0), 0.5, RULE),
        ]
    t.setStyle(TableStyle(cmds))
    return t


# --- 차트 --------------------------------------------------------------------


def _legend(font: str, items: list[tuple[colors.Color, str]], x: float, y: float) -> Legend:
    lg = Legend()
    lg.x, lg.y = x, y
    lg.fontName, lg.fontSize = font, 8
    lg.alignment = "right"
    lg.columnMaximum = 1
    lg.deltax = 70
    lg.boxAnchor = "nw"
    lg.dx = lg.dy = 7
    lg.strokeWidth = 0
    lg.colorNamePairs = items
    return lg


def annual_chart(report: CompanyReport, font: str, width: float) -> Drawing | None:
    keys = ("revenue", "operating_income", "net_income")
    points = [p for p in report.series if any(p.values.get(k) is not None for k in keys)]
    if not points:
        return None
    unit, unit_label = chart_unit([p.values[k] for p in points for k in keys if p.values.get(k)])
    d = Drawing(width, 150)
    c = VerticalBarChart()
    c.x, c.y, c.width, c.height = 40, 22, width - 50, 100
    c.data = [[(p.values.get(k) or 0) / unit for p in points] for k in keys]
    c.categoryAxis.categoryNames = [str(p.year) for p in points]
    c.categoryAxis.labels.fontName = c.valueAxis.labels.fontName = font
    c.categoryAxis.labels.fontSize = c.valueAxis.labels.fontSize = 7.5
    c.categoryAxis.strokeColor = c.valueAxis.strokeColor = RULE
    c.valueAxis.visibleGrid = True
    c.valueAxis.gridStrokeColor = colors.HexColor("#eaeef2")
    c.valueAxis.labelTextFormat = lambda v: f"{v:,.0f}" if unit_label == "억원" else f"{v:,.1f}"
    c.barSpacing, c.groupSpacing = 2, 10
    for i, color in enumerate(SERIES):
        c.bars[i].fillColor = color
        c.bars[i].strokeColor = None
    d.add(c)
    d.add(Line(c.x, c.y, c.x + c.width, c.y, strokeColor=INK, strokeWidth=0.5))
    d.add(
        _legend(
            font,
            [(SERIES[i], f"{METRICS[k].label} ({unit_label})") for i, k in enumerate(keys)],
            40,
            148,
        )
    )
    return d


def quarter_chart(report: CompanyReport, font: str, width: float) -> Drawing | None:
    keys = ("revenue", "operating_income")
    points = [p for p in report.quarters if p.values.get("revenue") is not None]
    if len(points) < 2:
        return None
    unit, unit_label = chart_unit([p.values[k] for p in points for k in keys if p.values.get(k)])
    d = Drawing(width, 140)
    c = HorizontalLineChart()
    c.x, c.y, c.width, c.height = 40, 22, width - 50, 90
    c.data = [[(p.values.get(k) or 0) / unit for p in points] for k in keys]
    c.categoryAxis.categoryNames = [f"{str(p.year)[2:]}.{p.quarter}Q" for p in points]
    c.categoryAxis.labels.fontName = c.valueAxis.labels.fontName = font
    c.categoryAxis.labels.fontSize = c.valueAxis.labels.fontSize = 7.5
    c.categoryAxis.strokeColor = c.valueAxis.strokeColor = RULE
    c.valueAxis.visibleGrid = True
    c.valueAxis.gridStrokeColor = colors.HexColor("#eaeef2")
    c.valueAxis.labelTextFormat = lambda v: f"{v:,.0f}" if unit_label == "억원" else f"{v:,.1f}"
    c.joinedLines = 1
    for i, color in enumerate(SERIES[:2]):
        c.lines[i].strokeColor = color
        c.lines[i].strokeWidth = 2
    d.add(c)
    d.add(
        _legend(
            font,
            [(SERIES[i], f"{METRICS[k].label} ({unit_label})") for i, k in enumerate(keys)],
            40,
            138,
        )
    )
    return d


# --- 본문 --------------------------------------------------------------------


def _para(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text), style)


def _link(url: str, label: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(f'<link href="{escape(url)}" color="#2a78d6">{escape(label)}</link>', style)


def _answer_flowables(text: str, st: dict) -> list:
    out = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        for ln in lines:
            ln = re.sub(r"^\*\*(.+)\*\*$", r"\1", ln)
            bullet = re.match(r"^([-*•]|\d+[.)])\s+(.*)", ln)
            if bullet:
                body = escape(bullet.group(2)).replace("**", "")
                out.append(Paragraph(body, st["body"], bulletText="•"))
            else:
                out.append(Paragraph(escape(ln).replace("**", ""), st["body"]))
        out.append(Spacer(1, 4))
    return out


def annual_table(report: CompanyReport, st: dict, width: float) -> Table | None:
    points = report.series
    if not points:
        return None
    head = [_para("(억원)", st["small"])] + [_para(str(p.year), st["num"]) for p in points]
    rows = [head]
    for k in AMOUNT_ROWS:
        rows.append(
            [_para(METRICS[k].label, st["cell"])]
            + [_para(eok(p.values.get(k)), st["num"]) for p in points]
        )
    for k in RATIO_ROWS:
        rows.append(
            [_para(RATIOS[k].label, st["cell"])]
            + [_para(pct(p.ratios.get(k)), st["num"]) for p in points]
        )
    rows.append(
        [_para("매출액 증가율", st["cell"])]
        + [_para(pct(p.growth.get("revenue")), st["num"]) for p in points]
    )
    rows.append(
        [_para("기준", st["small"])] + [_para(p.fs_div or "-", st["small"]) for p in points]
    )
    first = 32 * mm
    return _table(rows, [first] + [(width - first) / len(points)] * len(points))


def quarter_table(report: CompanyReport, st: dict, width: float) -> Table | None:
    points = report.quarters
    if not points:
        return None
    keys = ("revenue", "operating_income", "net_income")
    head = [_para("(억원)", st["small"])] + [
        _para(f"{str(p.year)[2:]}.{p.quarter}Q" + ("*" if p.derived else ""), st["num"])
        for p in points
    ]
    rows = [head] + [
        [_para(METRICS[k].label, st["cell"])]
        + [_para(eok(p.values.get(k)), st["num"]) for p in points]
        for k in keys
    ]
    first = 26 * mm
    return _table(rows, [first] + [(width - first) / len(points)] * len(points))


def render_pdf(report: CompanyReport, font_path: str | None = None) -> bytes:
    font = register_font(font_path)
    st = styles(font)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title=f"{report.corp_name} 기업 리포트",
        author="DART RAG",
        subject="DART 공시 기반 기업 리포트",
    )
    width = doc.width
    stamp = report.generated_at.strftime("%Y-%m-%d %H:%M")

    def frame(canvas, doc_):
        canvas.saveState()
        canvas.setFont(font, 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(doc_.leftMargin, A4[1] - 12 * mm, f"{report.corp_name} 기업 리포트")
        canvas.drawRightString(A4[0] - doc_.rightMargin, A4[1] - 12 * mm, f"생성 {stamp}")
        canvas.setStrokeColor(RULE)
        canvas.line(doc_.leftMargin, 13 * mm, A4[0] - doc_.rightMargin, 13 * mm)
        canvas.drawString(doc_.leftMargin, 9 * mm, "공시 요약 자료이며 투자 권유가 아닙니다.")
        canvas.drawRightString(A4[0] - doc_.rightMargin, 9 * mm, f"{doc_.page}")
        canvas.restoreState()

    story: list = []
    stock = f" ({report.stock_code})" if report.stock_code else ""
    story.append(_para(f"{report.corp_name}{stock}", st["title"]))
    story.append(
        _para(
            f"DART 공시 기반 기업 리포트 · {stamp} 생성"
            + (f" · 요약 모델 {report.llm_model}" if report.llm_model else ""),
            st["subtitle"],
        )
    )
    story.append(_para(DISCLAIMER, st["notice"]))

    # 1. 재무
    story.append(_para("1. 연간 재무 추이", st["h1"]))
    table = annual_table(report, st, width)
    if table is None:
        story.append(_para("수집된 사업보고서 재무 데이터가 없습니다.", st["body"]))
    else:
        chart = annual_chart(report, font, width)
        block = [chart, Spacer(1, 6)] if chart else []
        block += [
            table,
            _para(
                "사업보고서 기준. 연결재무제표가 있으면 연결, 없으면 별도 기준이며 "
                "비율은 같은 기준의 금액으로 계산했습니다.",
                st["small"],
            ),
        ]
        story.append(KeepTogether(block))

    story.append(_para("2. 분기 실적", st["h1"]))
    qtable = quarter_table(report, st, width)
    if qtable is None:
        story.append(_para("수집된 분기·반기 재무 데이터가 없습니다.", st["body"]))
    else:
        chart = quarter_chart(report, font, width)
        block = [chart, Spacer(1, 6)] if chart else []
        block.append(qtable)
        if any(p.derived for p in report.quarters):
            block.append(
                _para(
                    "* 4분기는 따로 공시되지 않아 연간 금액에서 3분기 누적 금액을 빼서 "
                    "계산했습니다.",
                    st["small"],
                )
            )
        story.append(KeepTogether(block))

    # 3. 요약
    number = 3
    for section in report.sections:
        story.append(_para(f"{number}. {section.title}", st["h1"]))
        story += _answer_flowables(section.text, st)
        if section.warnings:
            story.append(_para("확인 필요: " + " / ".join(section.warnings), st["small"]))
        if section.sources:
            story.append(_para("근거", st["h2"]))
            for s in section.sources:
                label = f"[{s.number}] {s.label}"
                story.append(
                    _link(s.url, label, st["small"]) if s.url else _para(label, st["small"])
                )
        number += 1

    # 변경점
    if report.diff:
        dfs = report.diff
        story.append(_para(f"{number}. 직전 사업보고서 대비 변경점", st["h1"]))
        story.append(
            Paragraph(
                f'<link href="{DART_URL.format(dfs.old["rcept_no"])}" color="#2a78d6">'
                f"{escape(dfs.old['report_nm'])}</link> → "
                f'<link href="{DART_URL.format(dfs.new["rcept_no"])}" color="#2a78d6">'
                f"{escape(dfs.new['report_nm'])}</link>",
                st["body"],
            )
        )
        if dfs.items:
            rows = [[_para(h, st["cell"]) for h in ("섹션", "구분", "추가", "삭제", "수정")]] + [
                [
                    _para(i.section, st["cell"]),
                    _para(STATUS.get(i.status, i.status), st["cell"]),
                    _para(str(i.added), st["num"]),
                    _para(str(i.removed), st["num"]),
                    _para(str(i.modified), st["num"]),
                ]
                for i in dfs.items
            ]
            story.append(Spacer(1, 4))
            story.append(_table(rows, [width - 100 * mm, 25 * mm, 25 * mm, 25 * mm, 25 * mm]))
            samples = [i for i in dfs.items if i.sample][:3]
            if samples:
                story.append(_para("새로 들어간 문장 예시", st["h2"]))
                for i in samples:
                    text = i.sample if len(i.sample) <= 220 else i.sample[:220] + "…"
                    story.append(Paragraph(escape(text), st["body"], bulletText="•"))
        else:
            story.append(_para("본문에서 의미 있는 변경을 찾지 못했습니다.", st["body"]))
        number += 1

    # 공시
    story.append(_para(f"{number}. 최근 주요 공시", st["h1"]))
    if report.disclosures:
        rows = [[_para(h, st["cell"]) for h in ("접수일", "공시", "분류")]] + [
            [
                _para(str(d["rcept_dt"]), st["cell"]),
                _link(d["url"], d["report_nm"], st["cell"]),
                _para(d.get("event_label") or "", st["cell"]),
            ]
            for d in report.disclosures
        ]
        story.append(_table(rows, [24 * mm, width - 64 * mm, 40 * mm]))
    else:
        story.append(_para("최근 중요도 높은 공시가 없습니다.", st["body"]))
    number += 1

    # 출처
    tail = [_para(f"{number}. 출처와 참고", st["h1"])]
    for p in report.series:
        if p.rcept_no:
            tail.append(
                _link(DART_URL.format(p.rcept_no), f"{p.year}년 사업보고서 (재무)", st["small"])
            )
    for note in report.notes:
        tail.append(_para(f"참고: {note}", st["small"]))
    tail.append(
        _para(
            "재무 수치는 OpenDART 단일회사 재무제표 API, 본문 요약은 사업보고서 원문에서 "
            "찾은 근거를 바탕으로 만들었습니다.",
            st["small"],
        )
    )
    story.append(KeepTogether(tail))

    doc.build(story, onFirstPage=frame, onLaterPages=frame)
    return buf.getvalue()
