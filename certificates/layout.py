"""Geometry of the Swami Vivekananda University registration certificate.

Every coordinate below was measured off the supplied card
(``assets/source_template.jpg``) by separating the black sample
details from the green printing on saturation, then reading the row and column
profiles. The numbers therefore match the printed ruling rather than being
eyeballed.

``renderer`` scales every value by the real template size, so the same numbers
still land correctly if the card is ever re-exported at a different resolution.
Keep ``CANVAS`` in sync if the card's proportions themselves change.
"""

CANVAS = {"width": 2700, "height": 1800}

# Regions of the supplied card that hold its sample student's details (SL. NO.
# REG/2022/035, DISHA GUCHAIT, MANAS GUCHAIT, 001-134-2022-004, 2022). These
# get painted out once, by ``cleaning.build_blank_template``, to give us a
# reusable blank. Each box stops short of the dotted rule below it.
ERASE_BOXES = [
    (306, 148, 640, 204),      # SL. NO. value (the printed colon ends at x=303)
    (880, 772, 1640, 858),     # student name (rule starts at y=864)
    (900, 946, 1640, 1018),    # guardian name (rule starts at y=1023)
    (1400, 1286, 1820, 1338),  # registration number (rule starts at y=1343)
    (1976, 1286, 2120, 1338),  # year
    # The sample HOD signature. Its green rule sits at y=1524-1527 and the
    # printed "HOD" label at y>=1546, so the signature is cleared in two
    # pieces -- above the rule, then the descender that crosses below it --
    # leaving the rule and the label untouched.
    (190, 1458, 620, 1521),
    (215, 1528, 285, 1552),
]

# Copied back verbatim from the source after the fill. The HOD rule is only
# two pixels tall and runs directly between the two boxes above, so however
# gently they are feathered they lighten it; restoring it is exact where
# tuning the feather would only be close.
PRESERVE_BOXES = [
    (182, 1524, 622, 1528),    # the green rule the HOD signs above
]

# This card ships with an empty, black-ruled photo box, so unlike a filled-in
# scan there is nothing to paint out and no frame for us to redraw.
PHOTO_ERASE_BOX = None
PHOTO_FRAME = False

# Inside the printed photo rule: border pixels run 2190-2197 / 2494-2499
# horizontally and 461-469 / 812-820 vertically.
PHOTO_BOX = (2198, 470, 2491, 812)

# Where an uploaded HOD signature is placed: centred on the printed rule that
# runs x=182-618 at y=1524, sitting just above it. A signature is fitted
# *inside* this box rather than cropped to it -- a clipped signature is worse
# than a small one.
HOD_SIGNATURE_BOX = (190, 1430, 610, 1519)

# The printed card measures 9 x 6 inches. A PDF page is made that size
# whatever resolution the card is supplied at, so a printed certificate comes
# out actual size rather than following the file's pixel count.
PAGE_INCHES = (9.0, 6.0)

INK = (0, 0, 0)

# One entry per editable field.
#   x         : anchor column
#   baseline  : text sits on this row
#   align     : "center" anchors x at the middle, "left" at the left edge
#   size      : nominal font size; shrunk automatically if the value is long
#   max_width : widest the value may be before it is shrunk to fit
#
# Sizes are calibrated to Times New Roman (see ``renderer._FONT_CANDIDATES``):
# each was chosen so the value's ink height matches the card's own sample
# details. Changing the typeface means recalibrating them, since cap height per
# point differs between faces. ``max_width`` is the clearance between the
# printed label and the end of the rule, so it is independent of the font.
FIELDS = {
    "sl_no": {
        "label": "SL. No.",
        "x": 304,
        "baseline": 194,
        "align": "left",
        "size": 45,
        "max_width": 620,
        "color": INK,
    },
    "student_name": {
        "label": "Student Name",
        "x": 1250,
        "baseline": 843,
        "align": "center",
        "size": 75,
        "max_width": 1040,
        "color": INK,
    },
    "guardian_name": {
        "label": "Guardian Name",
        "x": 1250,
        "baseline": 1011,
        "align": "center",
        "size": 75,
        "max_width": 1180,
        "color": INK,
    },
    "registration_number": {
        "label": "Registration Number",
        "x": 1606,
        "baseline": 1330,
        "align": "center",
        "size": 45,
        "max_width": 540,
        "color": INK,
    },
    "year": {
        "label": "Year",
        "x": 2046,
        "baseline": 1329,
        "align": "center",
        "size": 44,
        "max_width": 195,
        "color": INK,
    },
}

FIELD_ORDER = [
    "sl_no",
    "student_name",
    "guardian_name",
    "registration_number",
    "year",
]

DEFAULT_LAYOUT = {
    "canvas": CANVAS,
    "photo_box": PHOTO_BOX,
    "hod_signature_box": HOD_SIGNATURE_BOX,
    "photo_frame": PHOTO_FRAME,
    "fields": FIELDS,
}


def merge_layout(overrides):
    """Overlay per-template tweaks (stored as JSON in the admin) on the default.

    Only the keys present in ``overrides`` are replaced, and field tweaks merge
    key-by-key so the admin can nudge a single coordinate without restating the
    whole field.
    """
    import copy

    layout = copy.deepcopy(DEFAULT_LAYOUT)
    if not overrides:
        return layout

    for key, value in overrides.items():
        if key == "fields" and isinstance(value, dict):
            for field_name, field_overrides in value.items():
                if field_name in layout["fields"] and isinstance(field_overrides, dict):
                    layout["fields"][field_name].update(field_overrides)
                else:
                    layout["fields"][field_name] = field_overrides
        else:
            layout[key] = value
    return layout
