"""Reading the bulk spreadsheet, and building the sample one.

One workbook carries everything: the text for each student in the cells, and
their photograph embedded on the same row. Nothing else has to be uploaded.

Everything read out of it is validated before a single certificate is made, and
every problem is reported against the cell or row it lives in -- see
:mod:`certificates.validation`.
"""

import csv
import io
import zipfile

from openpyxl import Workbook, load_workbook
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from PIL import Image, ImageDraw, UnidentifiedImageError

from .layout import PHOTO_BOX
from .validation import ERROR, WARNING, Issue, SpreadsheetError, split

# Column heading -> model field. Headings are matched case-insensitively and
# ignoring spaces/underscores, so "Student Name" and "STUDENT_NAME" both work.
COLUMNS = {
    "SL_NO": "sl_no",
    "STUDENT_NAME": "student_name",
    "GUARDIAN_NAME": "guardian_name",
    "REGISTRATION_NUMBER": "registration_number",
    "YEAR": "year",
    "PHOTO": "photo_note",
}

HEADINGS = list(COLUMNS)
PHOTO_HEADING = "PHOTO"

# Every column must be filled in, and every student must have a photograph.
REQUIRED_TEXT = [
    "SL_NO", "STUDENT_NAME", "GUARDIAN_NAME", "REGISTRATION_NUMBER", "YEAR",
]
REQUIRED_COLUMNS = REQUIRED_TEXT + [PHOTO_HEADING]
# Two students may not share either of these.
UNIQUE = ["SL_NO", "REGISTRATION_NUMBER"]
# Enough to recognise the heading row; the rest are checked separately so a
# missing column is reported as itself rather than as "no headings found".
HEADER_ANCHOR = ["STUDENT_NAME"]

# What to tell someone whose cell is empty, per column.
_REQUIRED_HINT = {
    "SL_NO": "Type the serial number in {cell}.",
    "STUDENT_NAME": ("Type the student's full name in {cell}, or delete row "
                     "{row} if it is not a student."),
    "GUARDIAN_NAME": "Type the guardian's name in {cell}.",
    "REGISTRATION_NUMBER": "Type the registration number in {cell}.",
    "YEAR": "Type the year in {cell}, such as 2020.",
}

SAMPLE_ROWS = [
    ["103BCS202002", "Apurba Sarkar", "Susanta Kumar Sarkar", "002-103-2020-017", "2020"],
    ["103BCS202003", "Rima Das", "Nirmal Das", "002-103-2020-018", "2020"],
    ["103BCS202004", "Sourav Ghosh", "Bikash Ghosh", "002-103-2020-019", "2020"],
]

# The photo box on the certificate. There is no required photo size: whatever
# the user supplies is fitted to this box. These are only used to say what to
# expect of the result -- how much a picture will be enlarged or trimmed.
PHOTO_BOX_W = PHOTO_BOX[2] - PHOTO_BOX[0]
PHOTO_BOX_H = PHOTO_BOX[3] - PHOTO_BOX[1]
RECOMMENDED_PHOTO = (600, 700)
# Enlarging by less than this is not worth mentioning.
NOTICEABLE_UPSCALE = 1.25
# Cover-fitting trims the overflow; past this much the face tends to get cut.
MAX_CROP_FRACTION = 0.40
MAX_PHOTO_BYTES = 20 * 1024 * 1024
# How far down the sheet to look for the heading row before giving up.
HEADER_SEARCH_ROWS = 15


def _normalise(heading):
    return "".join(ch for ch in str(heading or "").upper() if ch.isalnum())


_LOOKUP = {_normalise(h): h for h in HEADINGS}


def _clean(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        # Excel hands back 2020.0 for a plain year; don't print the ".0".
        value = int(value)
    return str(value).strip()


def _cell(column_index, row_number):
    """A1-style reference for a zero-based column and one-based row."""
    return f"{get_column_letter(column_index + 1)}{row_number}"


def _max_lengths():
    """Field size limits, read straight off the model so they cannot drift."""
    from .models import GeneratedCertificate

    return {
        field: GeneratedCertificate._meta.get_field(field).max_length
        for field in ("sl_no", "student_name", "guardian_name",
                      "registration_number", "year")
    }


# --------------------------------------------------------------------------
# Structure: which sheet, which header row, which columns
# --------------------------------------------------------------------------

def _map_headings(raw_headings):
    """Map a candidate header row onto our column names, or return None."""
    mapping = {}
    for index, heading in enumerate(raw_headings or []):
        canonical = _LOOKUP.get(_normalise(heading))
        if canonical and canonical not in mapping:
            mapping[canonical] = index
    return mapping or None


def _find_header_row(values, sheet_name):
    """Locate the heading row and map its columns.

    The headings are normally row 1, but a title or a blank line above them is
    a common habit, so the first few rows are searched before giving up.
    """
    for offset, row in enumerate(values[:HEADER_SEARCH_ROWS]):
        mapping = _map_headings(row)
        if mapping and all(c in mapping for c in HEADER_ANCHOR):
            return offset, mapping

    # Nothing usable -- say what was actually found, so the user can compare.
    first = [str(c).strip() for c in (values[0] if values else []) if _clean(c)]
    found = ", ".join(first[:8]) if first else "(the first row is empty)"
    raise SpreadsheetError(
        "The spreadsheet has no usable heading row.",
        [Issue(
            ERROR,
            f"Sheet '{sheet_name}', row 1",
            f"No heading row was found. The first row contains: {found}.",
            "Put these headings in row 1, one per column: "
            + ", ".join(HEADINGS)
            + ". All of them are required. Or start again from the Excel "
            "template on this page.",
        )],
    )


def _pick_sheet(workbook):
    """Return the sheet that looks like the student list.

    A workbook built from our template has an Instructions sheet too, and that
    is often the one left selected when it is saved.
    """
    candidates = []
    for sheet in workbook.worksheets:
        head = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), None)
        mapping = _map_headings(head)
        score = len(mapping) if mapping else 0
        candidates.append((score, sheet))

    best_score, best_sheet = max(candidates, key=lambda pair: pair[0])
    if best_score:
        return best_sheet
    return workbook.active


def _unknown_column_issues(header_row, mapping, header_row_number, sheet_name):
    """Flag headings we do not recognise -- usually a typo."""
    used = set(mapping.values())
    issues = []
    for index, heading in enumerate(header_row or []):
        text = _clean(heading)
        if not text or index in used:
            continue
        issues.append(Issue(
            WARNING,
            f"Sheet '{sheet_name}', cell {_cell(index, header_row_number)}",
            f"The heading '{text}' is not recognised, so this column is ignored.",
            "Rename it to one of " + ", ".join(HEADINGS)
            + ", or leave it -- it does no harm.",
        ))
    return issues


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------

def _image_bytes(excel_image):
    """Return the raw bytes behind an openpyxl image, whatever it wraps."""
    ref = excel_image.ref
    if hasattr(ref, "read"):
        ref.seek(0)
        return ref.read()
    if isinstance(ref, Image.Image):
        buffer = io.BytesIO()
        ref.save(buffer, format=ref.format or "PNG")
        return buffer.getvalue()
    if isinstance(ref, (bytes, bytearray)):
        return bytes(ref)
    with open(ref, "rb") as handle:  # a path on disk
        return handle.read()


def _extract_images(sheet, photo_column_index):
    """Map embedded pictures to the 1-based worksheet row they sit on.

    Excel anchors a picture to a cell, so the anchor's row tells us which
    student it belongs to. A picture in the PHOTO column wins over one placed
    elsewhere on the same row, which lets a sheet carry a logo or a note
    without it being mistaken for a portrait.
    """
    best = {}
    unanchored = 0
    for image in getattr(sheet, "_images", []):
        marker = getattr(getattr(image, "anchor", None), "_from", None)
        if marker is None:
            # An AbsoluteAnchor is positioned in raw drawing units rather than
            # against a cell, so there is no row to attribute it to.
            unanchored += 1
            continue
        row = marker.row + 1  # openpyxl counts rows from 0
        in_photo_column = photo_column_index is not None and marker.col == photo_column_index
        if row not in best or (in_photo_column and not best[row][0]):
            best[row] = (in_photo_column, image)

    images = {}
    for row, (_, image) in best.items():
        try:
            data = _image_bytes(image)
        except Exception:  # noqa: BLE001 - reported per row further down
            data = None
        images[row] = data
    return images, unanchored


def _photo_issues(data, row_number, sheet_name, photo_cell):
    """Check one embedded picture; return (issues, usable_bytes_or_None)."""
    where = f"Sheet '{sheet_name}', cell {photo_cell}"

    if data is None:
        return [Issue(
            ERROR, where,
            "The picture on this row could not be read out of the workbook.",
            "Delete it and insert the photo again with "
            "Insert > Pictures > This Device.",
        )], None

    if len(data) > MAX_PHOTO_BYTES:
        return [Issue(
            ERROR, where,
            f"The picture is {len(data) / 1024 / 1024:.1f} MB, over the "
            f"{MAX_PHOTO_BYTES // 1024 // 1024} MB limit.",
            "Save a smaller copy of the photo and insert that instead.",
        )], None

    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.load()
            width, height = probe.size
    except (UnidentifiedImageError, OSError, ValueError):
        return [Issue(
            ERROR, where,
            "The picture on this row is not a readable image file.",
            "Delete it and insert a JPG or PNG with "
            "Insert > Pictures > This Device.",
        )], None

    issues = []
    box_ratio = PHOTO_BOX_W / PHOTO_BOX_H
    ratio = width / height
    # Cover-fitting scales until the photo covers the box, then trims the rest.
    scale = max(PHOTO_BOX_W / width, PHOTO_BOX_H / height)
    cropped = 1 - (box_ratio / ratio if ratio > box_ratio else ratio / box_ratio)

    # No size or shape is required: the photo is used whatever it is. These
    # notes only say what the result will look like, so nothing below returns
    # early or withholds the picture.
    if scale > NOTICEABLE_UPSCALE:
        limiting = "width" if (PHOTO_BOX_W / width) >= (PHOTO_BOX_H / height) else "height"
        issues.append(Issue(
            WARNING, where,
            f"The photo is {width} x {height} pixels, smaller than the "
            f"certificate's photo box ({PHOTO_BOX_W} x {PHOTO_BOX_H}), so it is "
            f"enlarged {scale:.1f}x to fill it (its {limiting} is the limiting "
            "side) and may look soft in print. The certificate is still made.",
            f"Nothing has to change. If you would rather it were sharper, "
            f"replace the picture in {photo_cell} with a larger one -- around "
            f"{RECOMMENDED_PHOTO[0]} x {RECOMMENDED_PHOTO[1]} prints crisply.",
        ))

    if cropped > MAX_CROP_FRACTION:
        shape = "wider" if ratio > box_ratio else "taller"
        issues.append(Issue(
            WARNING, where,
            f"The photo is {width} x {height}, much {shape} than the photo box, "
            f"so about {cropped * 100:.0f}% of it is trimmed off to fill the "
            "box. The certificate is still made.",
            f"Nothing has to change. If the trimming would cut the face, crop "
            f"the picture yourself before inserting it in {photo_cell} so the "
            "part you want is in the middle.",
        ))
    return issues, data


# --------------------------------------------------------------------------
# Rows
# --------------------------------------------------------------------------

def _row_issues(record, mapping, row_number, sheet_name):
    issues = []
    limits = _max_lengths()

    for canonical, index in mapping.items():
        field = COLUMNS[canonical]
        value = record.get(field, "")
        cell = _cell(index, row_number)
        where = f"Sheet '{sheet_name}', cell {cell}"

        if canonical in REQUIRED_TEXT and not value:
            issues.append(Issue(
                ERROR, where,
                f"{canonical} is empty, and every column is required.",
                _REQUIRED_HINT[canonical].format(cell=cell, row=row_number),
            ))
            continue

        limit = limits.get(field)
        if limit and len(value) > limit:
            issues.append(Issue(
                ERROR, where,
                f"{canonical} is {len(value)} characters; the limit is {limit}.",
                f"Shorten the value in {cell} to {limit} characters or fewer.",
            ))

        if canonical == "YEAR" and value and not _looks_like_year(value):
            issues.append(Issue(
                WARNING, where,
                f"YEAR is '{value}', which does not look like a year.",
                f"Put a four-digit year in {cell}, such as 2020, or a range "
                "like 2020-21. It is printed on the certificate exactly as "
                "typed.",
            ))

        if canonical == PHOTO_HEADING and value:
            issues.append(Issue(
                WARNING, where,
                f"The PHOTO column contains the text '{value}'.",
                "Photos are no longer matched by file name -- the picture "
                f"itself goes in the sheet. Clear {cell}, then use "
                "Insert > Pictures > This Device and drop the photo on row "
                f"{row_number}.",
            ))
    return issues


def _looks_like_year(value):
    digits = [part for part in value.replace("/", "-").split("-") if part.strip()]
    return bool(digits) and all(
        part.strip().isdigit() and 2 <= len(part.strip()) <= 4 for part in digits
    )


def _uniqueness_issues(rows, mapping, sheet_name):
    """SL_NO and REGISTRATION_NUMBER must each be unique across the sheet."""
    issues = []
    for canonical in UNIQUE:
        index = mapping.get(canonical)
        if index is None:
            continue
        field = COLUMNS[canonical]
        seen = {}
        for record in rows:
            value = record.get(field, "")
            if not value:
                continue                      # already reported as empty
            key = value.casefold()
            first = seen.get(key)
            if first is None:
                seen[key] = record["row_number"]
                continue
            cell = _cell(index, record["row_number"])
            issues.append(Issue(
                ERROR,
                f"Sheet '{sheet_name}', cell {cell}",
                f"{canonical} '{value}' is already used on row {first}, and "
                f"{canonical} must be unique.",
                f"Give this student their own {canonical} in {cell}, or delete "
                f"row {record['row_number']} if it repeats row {first}.",
            ))
    return issues


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------

def _has_in_cell_images(raw_bytes):
    """True if the workbook uses Excel's newer 'place in cell' pictures.

    Those are stored as rich values rather than drawings, and openpyxl cannot
    read them -- worth telling the user plainly rather than silently producing
    photo-less certificates.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as bundle:
            return any(name.startswith("xl/richData/") for name in bundle.namelist())
    except zipfile.BadZipFile:
        return False


def _read_xlsx(raw_bytes):
    """Return (sheet_name, rows_of_values, images_by_row, unanchored_count)."""
    try:
        # read_only would be lighter, but it skips drawings entirely -- and the
        # drawings are where the photographs live.
        workbook = load_workbook(io.BytesIO(raw_bytes), data_only=True)
    except zipfile.BadZipFile:
        raise SpreadsheetError(
            "That file is not a readable Excel workbook.",
            [Issue(
                ERROR, "Workbook",
                "The file could not be opened as a .xlsx workbook.",
                "Open it in Excel and use File > Save As to save a fresh copy "
                "as 'Excel Workbook (.xlsx)', then upload that.",
            )],
        ) from None

    try:
        sheet = _pick_sheet(workbook)
        values = [list(row) for row in sheet.iter_rows(values_only=True)]

        photo_column_index = None
        for row in values[:HEADER_SEARCH_ROWS]:
            mapping = _map_headings(row)
            if mapping and PHOTO_HEADING in mapping:
                photo_column_index = mapping[PHOTO_HEADING]
                break

        images, unanchored = _extract_images(sheet, photo_column_index)
        return sheet.title, values, images, unanchored
    finally:
        workbook.close()


def _read_csv(handle):
    handle.seek(0)
    text = handle.read()
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig", errors="replace")
    return "CSV", [row for row in csv.reader(io.StringIO(text))], {}, 0


def read_rows(uploaded_file):
    """Parse and validate an uploaded .xlsx/.csv.

    Returns ``(rows, warnings)``. Each row carries a ``row_number`` so later
    problems can still be reported against the line the user sees in Excel, and
    a ``photo`` holding the picture embedded on that row (``None`` if there is
    none).

    Raises :class:`~certificates.validation.SpreadsheetError` if anything would
    stop a certificate being made correctly. The exception carries every issue
    found, so the whole sheet can be fixed in one pass rather than one upload
    at a time.
    """
    name = (getattr(uploaded_file, "name", "") or "").lower()
    issues = []

    if name.endswith(".csv"):
        raise SpreadsheetError(
            "A .csv cannot carry photographs.",
            [Issue(
                ERROR, "Workbook",
                "A .csv file holds text only, and every student now needs a "
                "photograph embedded on their row.",
                "Open it in Excel, add the photos with Insert > Pictures > "
                "This Device, and save it with File > Save As > Excel Workbook "
                "(.xlsx). The Excel template on this page is already set up "
                "for it.",
            )],
        )
    elif name.endswith((".xlsx", ".xlsm")):
        uploaded_file.seek(0)
        raw_bytes = uploaded_file.read()
        sheet_name, raw, images, unanchored = _read_xlsx(raw_bytes)
        if not images and _has_in_cell_images(raw_bytes):
            issues.append(Issue(
                ERROR, f"Sheet '{sheet_name}'",
                "The photos were added with Excel's 'Place in Cell', which "
                "stores them in a form that cannot be read back out.",
                "Select each picture, delete it, then re-insert it with "
                "Insert > Pictures > This Device (the 'Place over Cells' "
                "option) so it floats on top of the row.",
            ))
    else:
        raise SpreadsheetError(
            "Unsupported file type.",
            [Issue(
                ERROR, "Workbook",
                f"'{getattr(uploaded_file, 'name', 'the file')}' is not a "
                ".xlsx or .csv file.",
                "Upload a .xlsx workbook. An old .xls file must be opened in "
                "Excel and re-saved with File > Save As > Excel Workbook "
                "(.xlsx). A .csv works for the text but cannot carry photos.",
            )],
        )

    if unanchored:
        issues.append(Issue(
            WARNING, f"Sheet '{sheet_name}'",
            f"{unanchored} picture(s) are not attached to any cell, so they "
            "cannot be matched to a student.",
            "Click each one and drag it onto the student's row in the PHOTO "
            "column.",
        ))

    # Blank spacer rows are ignored, but they still occupy a row number in
    # Excel, so the original index is kept for matching photos and reporting.
    numbered = [
        (index, row)
        for index, row in enumerate(raw, start=1)
        if row and any(_clean(cell) for cell in row)
    ]
    if not numbered:
        raise SpreadsheetError(
            "The spreadsheet is empty.",
            [Issue(
                ERROR, f"Sheet '{sheet_name}'",
                "There are no filled-in rows at all.",
                "Enter the headings in row 1 and one student per row below, "
                "or start from the Excel template on this page.",
            )],
        )

    header_offset, mapping = _find_header_row([row for _, row in numbered], sheet_name)
    header_row_number, header_row = numbered[header_offset]

    missing = [c for c in REQUIRED_COLUMNS if c not in mapping]
    if missing:
        # Every row would fail the same way, so say it once, up front.
        raise SpreadsheetError(
            "The spreadsheet is missing required column(s): " + ", ".join(missing),
            [Issue(
                ERROR, f"Sheet '{sheet_name}', row {header_row_number}",
                "These required columns are missing: " + ", ".join(missing) + ".",
                "Add them to row " + str(header_row_number) + ". The full set is "
                + ", ".join(HEADINGS) + ", and all are required. The Excel "
                "template on this page already has them.",
            )],
        )

    issues += _unknown_column_issues(header_row, mapping, header_row_number, sheet_name)

    photo_column = mapping.get(PHOTO_HEADING)
    rows = []
    data_row_numbers = set()

    for row_number, raw_row in numbered[header_offset + 1:]:
        record = {"row_number": row_number}
        for canonical, index in mapping.items():
            value = raw_row[index] if index < len(raw_row) else None
            record[COLUMNS[canonical]] = _clean(value)

        data_row_numbers.add(row_number)
        row_problems = _row_issues(record, mapping, row_number, sheet_name)
        issues += row_problems

        photo = None
        cell = _cell(photo_column if photo_column is not None else 0, row_number)
        if row_number in images:
            photo_problems, data = _photo_issues(
                images[row_number], row_number, sheet_name, cell
            )
            issues += photo_problems
            row_problems += photo_problems
            if data:
                photo = io.BytesIO(data)
                photo.name = f"row{row_number}.png"
        else:
            missing_photo = Issue(
                ERROR, f"Sheet '{sheet_name}', cell {cell}",
                "This student has no photograph, and a photo is required.",
                f"Click {cell}, then Insert > Pictures > This Device, and drop "
                f"the photo on row {row_number}. Any size or shape works.",
            )
            issues.append(missing_photo)
            row_problems = row_problems + [missing_photo]
        record["photo"] = photo

        if not any(p.is_error for p in row_problems):
            rows.append(record)

    # Pictures sitting on the heading row, or below the last student.
    for row_number in sorted(set(images) - data_row_numbers):
        issues.append(Issue(
            WARNING, f"Sheet '{sheet_name}', row {row_number}",
            f"There is a picture on row {row_number}, which has no student on it.",
            f"Drag it onto the row of the student it belongs to, or delete it. "
            f"Students start on row {header_row_number + 1}.",
        ))

    issues += _uniqueness_issues(rows, mapping, sheet_name)

    errors, warnings = split(issues)
    if errors:
        raise SpreadsheetError(
            f"{len(errors)} problem(s) in the spreadsheet must be fixed before "
            "the certificates can be generated.",
            issues,
        )

    if not rows:
        raise SpreadsheetError(
            "No students were found.",
            [Issue(
                ERROR, f"Sheet '{sheet_name}'",
                "The headings were found but there are no student rows under them.",
                f"Enter one student per row starting at row "
                f"{header_row_number + 1}.",
            )],
        )
    return rows, warnings


# --------------------------------------------------------------------------
# The sample workbook
# --------------------------------------------------------------------------

_PLACEHOLDER_SIZE = (RECOMMENDED_PHOTO[0], RECOMMENDED_PHOTO[1])


def _placeholder_photo(seed=0):
    """A neutral portrait placeholder, so the PHOTO column shows its purpose."""
    width, height = _PLACEHOLDER_SIZE
    image = Image.new("RGB", (width, height), (226, 232, 236))
    draw = ImageDraw.Draw(image)
    tint = (150 + seed * 6, 162 + seed * 4, 172)
    # Head and shoulders.
    draw.ellipse((width * 0.31, height * 0.17, width * 0.69, height * 0.55), fill=tint)
    draw.ellipse((width * 0.13, height * 0.62, width * 0.87, height * 1.28), fill=tint)
    draw.rectangle((0, 0, width - 1, height - 1), outline=(176, 186, 194), width=3)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    buffer.seek(0)
    return buffer


def build_sample_workbook():
    """Return a ready-to-fill .xlsx as bytes, photos embedded and all."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Students"

    header_fill = PatternFill("solid", fgColor="0E2E2C")
    header_font = Font(bold=True, color="FFFFFF")
    thin = Side(style="thin", color="D5D0C4")
    for column, heading in enumerate(HEADINGS, start=1):
        cell = sheet.cell(row=1, column=column, value=heading)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
    sheet.row_dimensions[1].height = 22

    photo_column = HEADINGS.index(PHOTO_HEADING) + 1
    for row_index, values in enumerate(SAMPLE_ROWS, start=2):
        for column, value in enumerate(values, start=1):
            # Keep everything as text so IDs like "002-103-2020-017" and years
            # survive the round trip exactly as typed.
            cell = sheet.cell(row=row_index, column=column, value=value)
            cell.number_format = "@"
            cell.alignment = Alignment(vertical="center")
            cell.border = Border(bottom=thin)

        picture = ExcelImage(_placeholder_photo(row_index))
        picture.width, picture.height = 56, 71
        sheet.add_image(picture, f"{get_column_letter(photo_column)}{row_index}")
        sheet.row_dimensions[row_index].height = 58

    for column, width in enumerate([18, 26, 30, 24, 10, 13], start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.freeze_panes = "A2"

    notes = workbook.create_sheet("Instructions")
    lines = [
        "SVU Registration Certificate - bulk generation",
        "",
        "Everything lives in this one workbook. There is nothing else to upload.",
        "",
        "1. Fill in one row per student on the 'Students' sheet.",
        "   Keep the headings in row 1 exactly as they are.",
        "2. Every column is required, and every student needs a photo.",
        "   SL_NO and REGISTRATION_NUMBER must each be unique - no two",
        "   students may share either one.",
        "3. For the photo, click the cell in the PHOTO column, then use",
        "   Insert > Pictures > This Device, and pick the student's photograph.",
        "   Drag it so it sits on that student's row.",
        "",
        "   Important: use 'Place over Cells' (the normal Insert > Pictures).",
        "   Excel's newer 'Place in Cell' pictures are stored differently and",
        "   cannot be read back out.",
        "",
        "4. The photo is matched to a student by the row it sits on, so the",
        "   picture does not have to be exactly inside the cell.",
        "5. Any photo size or shape works - there is no passport-size",
        f"   requirement. Each photo is fitted to the photo box ({PHOTO_BOX_W} x {PHOTO_BOX_H})",
        "   without being stretched.",
        "6. A photo much smaller than the box is enlarged and may print softer;",
        "   one of a very different shape has some of it trimmed to fill the",
        f"   box. The certificate is still made either way. About {RECOMMENDED_PHOTO[0]} x {RECOMMENDED_PHOTO[1]}",
        "   prints crisply if you have the choice.",
        "7. A row with no photo is reported as an error naming the cell.",
        "8. Save as .xlsx and upload it on the Bulk Generation page. A .csv",
        "   cannot carry photos, so it can no longer be used.",
        "",
        "If anything is wrong the upload page lists every problem with the exact",
        "cell to edit, so you can fix them all in one pass.",
        "",
        "The placeholder pictures in the PHOTO column are only there to show",
        "the layout - replace them with the real photographs.",
    ]
    for index, line in enumerate(lines, start=1):
        cell = notes.cell(row=index, column=1, value=line)
        if index == 1:
            cell.font = Font(bold=True, size=13, color="0E2E2C")
    notes.column_dimensions["A"].width = 76

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
