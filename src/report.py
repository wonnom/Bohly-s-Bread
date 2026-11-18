"""Builds the 7-day trial spreadsheet used to rate articles and tune the pipeline."""
import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from src import storage
from src.trial_data import (
    ANALYSIS_COLUMNS,
    CHECK_DAYS,
    GROUP_CODE,
    GROUP_MISSED_CAP,
    GROUP_REJECTED,
    GROUP_SENT,
    HEADERS,
    RATE_COLUMNS,
    REASONS,
    SGT,
    SPLIT_CHECK,
    SPLIT_TUNE,
    article_group as _group,
    parse_time as _parse,
    row_values as _row_values,
)

SAMPLE_SIZE = 20
SAMPLE_SEED = 7
MAX_TERMS = 40
RATE_SHEET = "Rate articles"

HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
INPUT_FILL = PatternFill("solid", fgColor="FFF4CC")
HEADER_FONT = Font(bold=True, color="FFFFFF")
WRAP = Alignment(wrap_text=True, vertical="top")

START_HERE = [
    "How to use this trial report",
    "",
    "1. Open 'Rate articles' and read each article as a reader of the brief would.",
    "2. Choose Yes or No under Useful. For No, also choose a reason.",
    "3. Rate every row. The bot's decisions are hidden so they don't influence your rating.",
    "4. Open 'Summary', 'Feeds' and 'Terms'. They update as you rate.",
    "",
    "Using the results",
    "Tune rules using the 'Tune' days, then confirm the improvement on the 'Check' days.",
    "To see the bot's decisions, use the + button above the columns on 'Rate articles'.",
    "Sent articles are all included. Other groups are random samples of up to 20 articles.",
]


def _in_window(record: Dict[str, Any], start: datetime, now: datetime) -> bool:
    seen = _parse(record.get("first_seen"))
    return seen is not None and start <= seen <= now


def _split(record: Dict[str, Any], start: datetime, days: int) -> str:
    day = (_parse(record["first_seen"]) - start).days
    return SPLIT_CHECK if days > CHECK_DAYS and day >= days - CHECK_DAYS else SPLIT_TUNE


def _sample(records: List[Dict[str, Any]], rng: random.Random) -> List[Dict[str, Any]]:
    ordered = sorted(records, key=lambda record: str(record.get("hash")))
    return ordered if len(ordered) <= SAMPLE_SIZE else rng.sample(ordered, SAMPLE_SIZE)


def _put(sheet, row: int, column: int, value: Any):
    """Write a value, always storing text as text so it can never run as a formula."""
    cell = sheet.cell(row=row, column=column)
    if isinstance(value, str):
        cell.value = ILLEGAL_CHARACTERS_RE.sub("", value)
        cell.data_type = "s"
    else:
        cell.value = value
    return cell


def _style_header(sheet, widths: List[int]) -> None:
    for column, width in enumerate(widths, start=1):
        cell = sheet.cell(row=1, column=column)
        cell.fill, cell.font, cell.alignment = HEADER_FILL, HEADER_FONT, WRAP
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.freeze_panes = "A2"


def _write_rate_sheet(sheet, rows: List[Dict[str, Any]]) -> None:
    for column, header in enumerate(HEADERS, start=1):
        sheet.cell(row=1, column=column, value=header)
    _style_header(sheet, [width for _, width in RATE_COLUMNS + ANALYSIS_COLUMNS])

    for row_number, values in enumerate(rows, start=2):
        for column, header in enumerate(HEADERS, start=1):
            cell = _put(sheet, row_number, column, values.get(header))
            cell.alignment = WRAP
        sheet.cell(row=row_number, column=1).fill = INPUT_FILL
        sheet.cell(row=row_number, column=2).fill = INPUT_FILL
        link = str(values.get("Link") or "")
        if link.lower().startswith(("https://", "http://")):
            title = sheet.cell(row=row_number, column=HEADERS.index("Title") + 1)
            title.hyperlink = link
            title.font = Font(color="0B5CAD", underline="single")

    last_row = max(len(rows) + 1, 2)
    useful = DataValidation(type="list", formula1='"Yes,No"', allow_blank=True)
    reason = DataValidation(type="list", formula1=f'"{",".join(REASONS)}"', allow_blank=True)
    sheet.add_data_validation(useful)
    sheet.add_data_validation(reason)
    useful.add(f"A2:A{last_row}")
    reason.add(f"B2:B{last_row}")

    sheet.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{last_row}"
    first_hidden = get_column_letter(len(RATE_COLUMNS) + 1)
    sheet.column_dimensions.group(first_hidden, get_column_letter(len(HEADERS)), hidden=True)


class _Formulas:
    """Builds live rating formulas that read from the rating sheet."""

    def __init__(self, row_count: int):
        self.last_row = max(row_count + 1, 2)

    def range(self, header: str) -> str:
        column = get_column_letter(HEADERS.index(header) + 1)
        return f"'{RATE_SHEET}'!${column}$2:${column}${self.last_row}"

    def count(self, answer: str, criteria: str) -> str:
        extra = f",{criteria}" if criteria else ""
        return f'COUNTIFS({self.range("Useful")},"{answer}"{extra})'

    def useful(self, *criteria: str) -> str:
        return "=" + "+".join(self.count("Yes", item) for item in criteria)

    def rated(self, *criteria: str) -> str:
        return "=" + "+".join(f'{self.count("Yes", item)}+{self.count("No", item)}' for item in criteria)


def _rating_cells(sheet, row: int, column: int, formulas: _Formulas, *criteria: str) -> None:
    rated, useful = get_column_letter(column), get_column_letter(column + 1)
    sheet.cell(row=row, column=column, value=formulas.rated(*criteria))
    sheet.cell(row=row, column=column + 1, value=formulas.useful(*criteria))
    rate = sheet.cell(row=row, column=column + 2, value=f'=IF({rated}{row}=0,"",{useful}{row}/{rated}{row})')
    rate.number_format = "0%"


def _section(sheet, row: int, title: str, first_header: str) -> int:
    sheet.cell(row=row, column=1, value=title).font = Font(bold=True, size=12)
    for column, header in enumerate((first_header, "Rated", "Useful", "Useful %"), start=1):
        cell = sheet.cell(row=row + 1, column=column, value=header)
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
    return row + 2


def _write_summary(sheet, formulas: _Formulas, group_totals: Counter) -> None:
    sheet.column_dimensions["A"].width = 30
    for column in "BCDE":
        sheet.column_dimensions[column].width = 12

    row = _section(sheet, 1, "Usefulness by article group", "Group")
    sheet.cell(row=row - 1, column=5, value="In trial").fill = HEADER_FILL
    sheet.cell(row=row - 1, column=5).font = HEADER_FONT
    for group in (GROUP_SENT, GROUP_MISSED_CAP, GROUP_REJECTED, GROUP_CODE):
        _put(sheet, row, 1, group)
        _rating_cells(sheet, row, 2, formulas, f'{formulas.range("Group")},$A{row}')
        sheet.cell(row=row, column=5, value=group_totals[group])
        row += 1

    row = _section(sheet, row + 1, "Sent articles: tune days against check days", "Trial split")
    for split in (SPLIT_TUNE, SPLIT_CHECK):
        _put(sheet, row, 1, split)
        _rating_cells(sheet, row, 2, formulas,
                      f'{formulas.range("Trial split")},$A{row},{formulas.range("Group")},"{GROUP_SENT}"')
        row += 1

    row = _section(sheet, row + 1, "Sent articles: position in the brief", "Position")
    for low, high in ((1, 4), (5, 8), (9, 12)):
        _put(sheet, row, 1, f"{low}-{high}")
        position = formulas.range("Brief position")
        _rating_cells(sheet, row, 2, formulas, f'{position},">={low}",{position},"<={high}"')
        row += 1

    row = _section(sheet, row + 1, "Gemini-accepted articles: ranking score", "Score")
    for low, high in ((0, 4), (5, 8), (9, 12), (13, 999)):
        _put(sheet, row, 1, f"{low}-{high}" if high < 999 else f"{low}+")
        score = formulas.range("Total score")
        band = f'{score},">={low}",{score},"<={high}"'
        _rating_cells(sheet, row, 2, formulas,
                      f'{band},{formulas.range("Group")},"{GROUP_SENT}"',
                      f'{band},{formulas.range("Group")},"{GROUP_MISSED_CAP}"')
        row += 1

    row += 1
    sheet.cell(row=row, column=1, value="Reasons articles were not useful").font = Font(bold=True, size=12)
    row += 1
    for column, header in enumerate(("Reason", "Articles"), start=1):
        cell = sheet.cell(row=row, column=column, value=header)
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
    for reason in REASONS:
        row += 1
        _put(sheet, row, 1, reason)
        sheet.cell(row=row, column=2, value=f'=COUNTIFS({formulas.range("Reason if not useful")},$A{row})')


def _write_feeds(sheet, records: List[Dict[str, Any]], formulas: _Formulas) -> None:
    headers = ["Feed", "Removed by code", "Passed code filter", "Gemini accepted", "Gemini rejected", "Sent",
               "Never reviewed", "Rated", "Useful", "Useful %"]
    for column, header in enumerate(headers, start=1):
        sheet.cell(row=1, column=column, value=header)
    _style_header(sheet, [32] + [12] * (len(headers) - 1))

    totals: Dict[str, Counter] = {}
    for record in records:
        feed = record.get("feed") or record.get("provider") or "unknown"
        counts = totals.setdefault(feed, Counter())
        group = _group(record)
        if group == GROUP_CODE:
            counts["removed"] += 1
            continue
        if record.get("status") == "duplicate_event":
            continue
        counts["passed"] += 1
        counts["accepted"] += record.get("gemini_decision") == "accepted"
        counts["rejected"] += record.get("gemini_decision") == "rejected"
        counts["sent"] += record.get("status") == "sent"
        counts["unreviewed"] += group is None

    ordered = sorted(totals.items(), key=lambda item: (-item[1]["sent"], item[0]))
    for row, (feed, counts) in enumerate(ordered, start=2):
        _put(sheet, row, 1, feed)
        for column, key in enumerate(("removed", "passed", "accepted", "rejected", "sent", "unreviewed"), start=2):
            sheet.cell(row=row, column=column, value=counts[key])
        _rating_cells(sheet, row, 8, formulas, f'{formulas.range("Feed")},$A{row}')


def _write_terms(sheet, rows: List[Dict[str, Any]], formulas: _Formulas) -> None:
    for column, header in enumerate(("Matched term", "Rated", "Useful", "Useful %"), start=1):
        sheet.cell(row=1, column=column, value=header)
    _style_header(sheet, [30, 12, 12, 12])

    counts: Counter = Counter()
    for row in rows:
        if row["Group"] in (GROUP_SENT, GROUP_MISSED_CAP):
            counts.update(term for term in str(row.get("Matched terms") or "").split("|") if term)

    terms = formulas.range("Matched terms")
    groups = formulas.range("Group")
    for row, (term, _) in enumerate(counts.most_common(MAX_TERMS), start=2):
        _put(sheet, row, 1, term)
        match = f'{terms},"*|"&$A{row}&"|*"'
        _rating_cells(sheet, row, 2, formulas, f'{match},{groups},"{GROUP_SENT}"', f'{match},{groups},"{GROUP_MISSED_CAP}"')


def build_report(
    history: List[Dict[str, Any]],
    code_rejections: List[Dict[str, Any]],
    now: datetime,
    days: int,
    output: Path,
) -> Path:
    """Create the rating spreadsheet. An existing file is never replaced, because it may hold ratings."""
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"{output} already exists and may contain ratings. Choose another --output path.")

    start = now - timedelta(days=days)
    records = [record for record in history + code_rejections if _in_window(record, start, now)]

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        group = _group(record)
        if group:
            grouped.setdefault(group, []).append(record)

    rng = random.Random(SAMPLE_SEED)
    chosen = [(GROUP_SENT, record) for record in grouped.get(GROUP_SENT, [])]
    for group in (GROUP_CODE, GROUP_REJECTED, GROUP_MISSED_CAP):
        chosen.extend((group, record) for record in _sample(grouped.get(group, []), rng))

    chosen.sort(key=lambda item: str(item[1].get("hash")))
    rng.shuffle(chosen)  # Mixes the groups so their order doesn't influence the rating.
    rows = [_row_values(record, group, _split(record, start, days)) for group, record in chosen]

    workbook = Workbook()
    intro = workbook.active
    intro.title = "Start here"
    intro.column_dimensions["A"].width = 95
    for row, line in enumerate(START_HERE, start=1):
        cell = intro.cell(row=row, column=1, value=line)
        cell.alignment = WRAP
        if line in (START_HERE[0], "Using the results"):
            cell.font = Font(bold=True, size=13)

    formulas = _Formulas(len(rows))
    _write_rate_sheet(workbook.create_sheet(RATE_SHEET), rows)
    group_totals = Counter({group: len(items) for group, items in grouped.items()})
    _write_summary(workbook.create_sheet("Summary"), formulas, group_totals)
    _write_feeds(workbook.create_sheet("Feeds"), records, formulas)
    _write_terms(workbook.create_sheet("Terms"), rows, formulas)

    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    return output


def run_report(days: int = 7, output: Optional[str] = None) -> Path:
    """Read saved trial data and create the spreadsheet."""
    now = datetime.now(timezone.utc)
    default = storage.DATA_DIR.parent / "reports" / f"trial_report_{now.astimezone(SGT):%Y-%m-%d}.xlsx"
    return build_report(storage.load_history(), storage.load_code_rejections(), now, days, Path(output or default))
