"""Turn the reconciliation CSV into the workbook a risk officer can open.

Four tabs: the recorded rows, a formula summary, an XLOOKUP reconciliation and
the dictionary the definition comes from. `expected` and the summary counts are
live formulas rather than pasted numbers, so changing a loan amount in the sheet
changes the verdict; `status` stays the value the pipeline recorded, because the
point of the deliverable is to show a disputed figure, not to re-derive it.

openpyxl cannot create a native PivotTable, so `Summary` is COUNTIFS/SUMIFS
formulas over the same range. Excel needs 365 or 2021 for XLOOKUP; the cell is
written as `_xlfn.XLOOKUP`, which is how the file format spells a function newer
than the base spec, and Excel displays it as `XLOOKUP`.

Bytes are not reproducible (a zip entry carries its write time), so this
workbook is compared by cell values in the tests, not byte-for-byte like the
generated markdown reports.
"""

from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
DICTIONARY_CSV = ROOT / "data" / "raw" / "data_dictionary_control.csv"
VALIDATION_CSV = ROOT / "reports" / "calculation_validation.csv"
OUTPUT = ROOT / "reports" / "calculation_validation.xlsx"

HEADERS = [
    "loan_id",
    "loan_amount_gbp",
    "annual_revenue_gbp",
    "reported_dti_ratio",
    "expected_dti_ratio",
    "difference",
    "abs_difference",
    "tolerance",
    "recorded_status",
]
WIDTHS = [12, 18, 20, 20, 20, 14, 15, 12, 16]
MONEY = "#,##0.00"
RATIO = "0.000000"
STATUSES = ["pass", "mismatch", "blocked_input"]


def _number(value):
    """Excel has no NaN. A missing input stays an empty cell, which is also what
    the dictionary stores, so the workbook cannot invent a zero."""
    if value is None or pd.isna(value):
        return None
    return float(value)


def _text(value):
    """The control CSV spells empty as the word None; the loaded dictionary
    stores NULL. Show the same thing the catalog holds."""
    text_value = "" if value is None else str(value).strip()
    return None if text_value.lower() in ("", "none", "nan") else text_value


def _style(sheet, widths):
    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDDDDD")
        cell.alignment = Alignment(horizontal="left")
    sheet.freeze_panes = "A2"


def _validation_sheet(workbook, frame):
    sheet = workbook.active
    sheet.title = "Validation"
    sheet.append(HEADERS)
    for row in frame.itertuples(index=False):
        sheet.append(
            [
                row.loan_id,
                _number(row.loan_amount_gbp),
                _number(row.annual_revenue_gbp),
                _number(row.dti_ratio),
                None,
                None,
                None,
                _number(row.tolerance),
                row.status,
            ]
        )
    for number in range(2, len(frame) + 2):
        expected = sheet.cell(row=number, column=5)
        expected.value = (
            f"=IF(AND(ISNUMBER(C{number}),C{number}>0)," f'B{number}/C{number},"")'
        )
        expected.number_format = RATIO
        difference = sheet.cell(row=number, column=6)
        difference.value = f'=IF(E{number}="","",D{number}-E{number})'
        difference.number_format = RATIO
        absolute = sheet.cell(row=number, column=7)
        absolute.value = f'=IF(F{number}="","",ABS(F{number}))'
        absolute.number_format = RATIO
        for column, fmt in ((2, MONEY), (3, MONEY), (4, RATIO), (8, "0.000000")):
            sheet.cell(row=number, column=column).number_format = fmt
    _style(sheet, WIDTHS)
    return sheet


def _summary_sheet(workbook, rows):
    sheet = workbook.create_sheet("Summary")
    sheet.append(
        ["recorded_status", "loans", "share of rows", "largest absolute difference"]
    )
    for status in STATUSES:
        sheet.append(
            [
                status,
                f'=COUNTIF(Validation!$I$2:$I${rows},"{status}")',
                f'=COUNTIF(Validation!$I$2:$I${rows},"{status}")/COUNTA(Validation!$A$2:$A${rows})',
                f'=IFERROR(MAXIFS(Validation!$G$2:$G${rows},Validation!$I$2:$I${rows},"{status}"),"")',
            ]
        )
    sheet.append([])
    sheet.append(
        [
            "note",
            "Counts are formulas over the Validation tab, not a pasted "
            "snapshot. openpyxl cannot write a native PivotTable, so "
            "this is a formula summary.",
        ]
    )
    sheet.append(
        [
            "note",
            "A zero revenue or a missing revenue is blocked_input: the "
            "expected ratio has no denominator, so no value is claimed.",
        ]
    )
    for number in range(2, len(STATUSES) + 2):
        sheet.cell(row=number, column=3).number_format = "0.0%"
        sheet.cell(row=number, column=4).number_format = RATIO
    _style(sheet, [16, 10, 14, 26])
    return sheet


def _reconciliation_sheet(workbook, frame, rows):
    sheet = workbook.create_sheet("Reconciliation")
    sheet.append(
        [
            "loan_id",
            "reported_dti_ratio",
            "expected_dti_ratio",
            "agrees_within_tolerance",
            "documented_definition",
        ]
    )
    lookup = "_xlfn.XLOOKUP"
    for number, loan_id in enumerate(frame.loan_id.tolist(), start=2):
        sheet.cell(row=number, column=1, value=loan_id)
        reported = sheet.cell(row=number, column=2)
        reported.value = (
            f"={lookup}(A{number},Validation!$A$2:$A${rows},"
            f'Validation!$D$2:$D${rows},"no row")'
        )
        reported.number_format = RATIO
        expected = sheet.cell(row=number, column=3)
        expected.value = (
            f"=IFERROR({lookup}(A{number},Validation!$A$2:$A${rows},"
            f'Validation!$E$2:$E${rows}),"")'
        )
        expected.number_format = RATIO
        sheet.cell(
            row=number,
            column=4,
            value=f'=IF(C{number}="","blocked",IF(ABS(B{number}-C{number})<=1E-6,'
            f'"agree","disagrees"))',
        )
        definition = sheet.cell(row=number, column=5)
        definition.value = (
            f'={lookup}("dti_ratio",Dictionary!$A$2:$A$12,'
            f'Dictionary!$C$2:$C$12,"undocumented")'
        )
    _style(sheet, [12, 20, 20, 22, 46])
    return sheet


def _dictionary_sheet(workbook, dictionary):
    sheet = workbook.create_sheet("Dictionary")
    sheet.append(
        ["field_name", "data_type", "valid_range", "source_system", "known_issues"]
    )
    for row in dictionary.itertuples(index=False):
        sheet.append(
            [
                _text(row.field_name),
                _text(row.data_type),
                _text(row.valid_range),
                _text(row.source_system),
                _text(row.known_issues),
            ]
        )
    for cell in sheet["E"][1:]:
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    _style(sheet, [24, 12, 34, 24, 60])
    return sheet


def build(validation_frame, dictionary_frame):
    """Return the workbook in memory so tests can compare it cell by cell."""
    workbook = Workbook()
    workbook.properties.creator = "Credit Risk Metadata & Lineage Platform"
    workbook.properties.title = "Loan/revenue ratio reconciliation"
    rows = len(validation_frame) + 1
    _validation_sheet(workbook, validation_frame)
    _summary_sheet(workbook, rows)
    _reconciliation_sheet(workbook, validation_frame, rows)
    _dictionary_sheet(workbook, dictionary_frame)
    return workbook


def run():
    frame = pd.read_csv(VALIDATION_CSV)
    dictionary = pd.read_csv(DICTIONARY_CSV, keep_default_na=False)
    workbook = build(frame, dictionary)
    workbook.save(OUTPUT)
    print(
        f"[excel] wrote {OUTPUT.relative_to(ROOT)} with "
        f"{len(workbook.sheetnames)} tabs"
    )
    return OUTPUT


if __name__ == "__main__":
    run()
