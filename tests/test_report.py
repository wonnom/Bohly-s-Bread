from datetime import datetime, timedelta, timezone

import pytest
from openpyxl import load_workbook

from src import report

NOW = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)


def record(index, status, days_ago=1.0, **overrides):
    seen = (NOW - timedelta(days=days_ago)).isoformat()
    base = {
        "hash": f"hash-{status}-{index}",
        "url": f"https://example.com/{status}/{index}",
        "title": f"{status} story {index}",
        "source": "Reuters",
        "provider": "rss",
        "feed": "LNG Prime",
        "published_at": seen,
        "first_seen": seen,
        "status": status,
        "score": {"total": 9, "topic": 7, "strong": 1, "deal": 1, "sector": 2, "amount": 0, "trust": 2,
                  "matched_terms": ["green loan", "lng"]},
    }
    base.update(overrides)
    return base


def trial_data():
    history = [record(i, "sent", gemini_decision="accepted", brief_rank=i + 1, brief_id="2026-11-24-AM")
               for i in range(5)]
    history += [record(i, "rejected", gemini_decision="rejected", evidence="Too minor.") for i in range(30)]
    history += [record(i, "expired", gemini_decision="accepted", missed_cap=True) for i in range(30)]
    history += [record(0, "expired"), record(0, "duplicate_event"), record(0, "pending")]
    removed = [{**record(i, "code_rejected"), "reason": "not_apac"} for i in range(30)]
    return history, removed


def rows(path):
    sheet = load_workbook(path)["Rate articles"]
    headers = [cell.value for cell in sheet[1]]
    return [dict(zip(headers, [cell.value for cell in row])) for row in sheet.iter_rows(min_row=2)]


def test_all_sent_articles_and_a_sample_of_the_rest_are_included(tmp_path):
    history, removed = trial_data()
    path = report.build_report(history, removed, NOW, days=7, output=tmp_path / "trial.xlsx")

    groups = [row["Group"] for row in rows(path)]

    assert groups.count(report.GROUP_SENT) == 5
    assert groups.count(report.GROUP_CODE) == 20
    assert groups.count(report.GROUP_REJECTED) == 20
    assert groups.count(report.GROUP_MISSED_CAP) == 20
    assert len(groups) == 65


def test_sample_is_repeatable(tmp_path):
    history, removed = trial_data()
    first = report.build_report(history, removed, NOW, days=7, output=tmp_path / "one.xlsx")
    second = report.build_report(history, removed, NOW, days=7, output=tmp_path / "two.xlsx")

    assert [row["Title"] for row in rows(first)] == [row["Title"] for row in rows(second)]


def test_articles_outside_the_window_are_left_out(tmp_path):
    history = [record(1, "sent", days_ago=2), record(2, "sent", days_ago=9), {"hash": "legacy", "status": "sent"}]
    path = report.build_report(history, [], NOW, days=7, output=tmp_path / "trial.xlsx")

    assert [row["Title"] for row in rows(path)] == ["sent story 1"]


def test_last_two_days_are_held_back_for_checking(tmp_path):
    history = [record(1, "sent", days_ago=6), record(2, "sent", days_ago=1.5)]
    path = report.build_report(history, [], NOW, days=7, output=tmp_path / "trial.xlsx")

    split = {row["Title"]: row["Trial split"] for row in rows(path)}
    assert split["sent story 1"] == report.SPLIT_TUNE
    assert split["sent story 2"] == report.SPLIT_CHECK


def test_rating_columns_have_dropdowns(tmp_path):
    history, removed = trial_data()
    path = report.build_report(history, removed, NOW, days=7, output=tmp_path / "trial.xlsx")

    validations = load_workbook(path)["Rate articles"].data_validations.dataValidation
    formulas = {validation.formula1 for validation in validations}
    assert '"Yes,No"' in formulas
    assert any("Off-topic" in formula and "Old news" in formula for formula in formulas)


def test_headlines_cannot_run_as_spreadsheet_formulas(tmp_path):
    history = [record(1, "sent", title='=HYPERLINK("https://evil.example","click")\x07', source="+cmd")]
    path = report.build_report(history, [], NOW, days=7, output=tmp_path / "trial.xlsx")

    sheet = load_workbook(path)["Rate articles"]
    headers = [cell.value for cell in sheet[1]]
    title = sheet.cell(row=2, column=headers.index("Title") + 1)
    source = sheet.cell(row=2, column=headers.index("Source") + 1)
    assert title.data_type == "s" and source.data_type == "s"
    assert "\x07" not in title.value


def test_only_web_links_become_clickable(tmp_path):
    history = [record(1, "sent", url="javascript:alert(1)")]
    path = report.build_report(history, [], NOW, days=7, output=tmp_path / "trial.xlsx")

    sheet = load_workbook(path)["Rate articles"]
    headers = [cell.value for cell in sheet[1]]
    assert sheet.cell(row=2, column=headers.index("Title") + 1).hyperlink is None


def test_existing_ratings_are_never_overwritten(tmp_path):
    output = tmp_path / "trial.xlsx"
    output.write_text("rated already")

    with pytest.raises(FileExistsError):
        report.build_report([], [], NOW, days=7, output=output)
    assert output.read_text() == "rated already"


def test_feed_summary_counts_every_outcome(tmp_path):
    history, removed = trial_data()
    path = report.build_report(history, removed, NOW, days=7, output=tmp_path / "trial.xlsx")

    sheet = load_workbook(path)["Feeds"]
    headers = [cell.value for cell in sheet[1]]
    row = dict(zip(headers, [cell.value for cell in sheet[2]]))
    assert row["Feed"] == "LNG Prime"
    assert row["Removed by code"] == 30
    assert row["Gemini accepted"] == 35
    assert row["Gemini rejected"] == 30
    assert row["Sent"] == 5
    assert row["Never reviewed"] == 2


def test_lambda_package_does_not_need_the_spreadsheet_library():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    assert "openpyxl" not in (root / "requirements.txt").read_text()
    assert "openpyxl" in (root / "requirements-dev.txt").read_text()
    assert "src.report" not in (root / "main.py").read_text().split("def lambda_handler")[0]
