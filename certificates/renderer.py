"""Draw student details onto the blank certificate."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .layout import CANVAS, merge_layout

# Pillow moved the resampling constants in 9.1; support both.
try:
    RESAMPLE = Image.Resampling.LANCZOS
except AttributeError:  # Pillow < 9.1
    RESAMPLE = Image.LANCZOS

_BUNDLED_FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"

# The certificate details are set in Times New Roman, which sits naturally
# alongside the serif lines already printed on the certificate. Liberation
# Serif is metric-compatible and stands in on Linux.
_FONT_CANDIDATES = [
    _BUNDLED_FONTS / "TimesNewRoman.ttf",
    _BUNDLED_FONTS / "times.ttf",
    Path("C:/Windows/Fonts/times.ttf"),
    Path("/usr/share/fonts/truetype/msttcorefonts/Times_New_Roman.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf"),
    Path("/Library/Fonts/Times New Roman.ttf"),
    Path("/System/Library/Fonts/Supplemental/Times New Roman.ttf"),
    Path("/usr/share/fonts/truetype/freefont/FreeSerif.ttf"),
]

_font_path_cache = None
_font_cache = {}


class FontUnavailable(RuntimeError):
    pass


def font_path():
    global _font_path_cache
    if _font_path_cache is None:
        for candidate in _FONT_CANDIDATES:
            if candidate.exists():
                _font_path_cache = str(candidate)
                break
        else:
            raise FontUnavailable(
                "No serif TrueType font found. Drop a TimesNewRoman.ttf (or any "
                f"times.ttf) into {_BUNDLED_FONTS} and try again."
            )
    return _font_path_cache


def load_font(size):
    size = max(int(size), 6)
    if size not in _font_cache:
        _font_cache[size] = ImageFont.truetype(font_path(), size)
    return _font_cache[size]


def _fit_font(draw, text, size, max_width):
    """Shrink the font until ``text`` fits ``max_width`` (down to 40% of size)."""
    floor = max(int(size * 0.4), 8)
    while size > floor:
        font = load_font(size)
        if draw.textlength(text, font=font) <= max_width:
            return font
        size -= 1
    return load_font(size)


def _paste_photo(canvas, photo, box, sx, sy, draw_frame=True):
    left, top, right, bottom = box
    target = (
        max(int(round((right - left) * sx)), 1),
        max(int(round((bottom - top) * sy)), 1),
    )
    left, top = int(round(left * sx)), int(round(top * sy))

    if isinstance(photo, Image.Image):
        image = photo
    else:
        image = Image.open(photo)
    image = ImageOps.exif_transpose(image).convert("RGB")
    # Cover-fit: fill the frame without distorting the student's face.
    image = ImageOps.fit(image, target, method=RESAMPLE, centering=(0.5, 0.42))

    canvas.paste(image, (left, top))
    # Only needed for a template whose own photo frame had to be painted out.
    # The current card has its frame printed, so drawing another would double
    # the rule.
    if draw_frame:
        ImageDraw.Draw(canvas).rectangle(
            [left - 1, top - 1, left + target[0], top + target[1]],
            outline=(90, 92, 96),
            width=2,
        )


# Above this the scan's paper counts as background rather than ink; below the
# floor it is solid ink. Between the two the alpha ramps, which keeps the
# stroke edges soft instead of jagged.
_SIGNATURE_PAPER = 240
_SIGNATURE_INK = 120


def prepare_signature(source):
    """Return an RGBA signature, trimmed, with its paper made transparent.

    A signature is nearly always photographed or scanned on white paper. Pasted
    as-is it would drop a white rectangle over the card's watermark, so unless
    the file already carries transparency the paper is keyed out by luminance
    and the ink keeps its own colour.
    """
    image = source if isinstance(source, Image.Image) else Image.open(source)
    image = ImageOps.exif_transpose(image)

    has_alpha = image.mode in ("RGBA", "LA") or (
        image.mode == "P" and "transparency" in image.info
    )
    if has_alpha:
        image = image.convert("RGBA")
    else:
        rgb = image.convert("RGB")
        span = _SIGNATURE_PAPER - _SIGNATURE_INK
        alpha = rgb.convert("L").point(
            lambda v: 0 if v >= _SIGNATURE_PAPER
            else 255 if v <= _SIGNATURE_INK
            else int((_SIGNATURE_PAPER - v) * 255 / span)
        )
        image = rgb.convert("RGBA")
        image.putalpha(alpha)

    # Trim the surrounding paper so the signature fills its box rather than
    # floating in the middle of a mostly empty scan.
    bounds = image.getchannel("A").getbbox()
    if bounds:
        image = image.crop(bounds)
    return image


def _paste_signature(canvas, signature, box, sx, sy):
    """Fit a signature inside ``box``, centred and sitting on its lower edge.

    Unlike the photo this is a *contain* fit: a signature that has been cropped
    is worse than one that is small, and the box's lower edge is the printed
    rule the HOD signs above.
    """
    left, top, right, bottom = box
    max_w = max(int(round((right - left) * sx)), 1)
    max_h = max(int(round((bottom - top) * sy)), 1)
    left, top = int(round(left * sx)), int(round(top * sy))

    image = prepare_signature(signature)
    scale = min(max_w / image.width, max_h / image.height)
    size = (max(int(image.width * scale), 1), max(int(image.height * scale), 1))
    image = image.resize(size, RESAMPLE)

    x = left + (max_w - size[0]) // 2          # centred on the rule
    y = top + (max_h - size[1])                # resting on the rule
    canvas.paste(image, (x, y), image)


def render_certificate(
    data, template_path, layout_overrides=None, photo=None, hod_signature=None
):
    """Return a finished certificate as an RGB :class:`PIL.Image.Image`.

    ``data`` maps field names from :mod:`certificates.layout` to the text to
    draw. ``photo`` and ``hod_signature`` may each be a path, a file-like
    object or a ``PIL`` image.
    """
    layout = merge_layout(layout_overrides)

    canvas = Image.open(template_path)
    canvas = ImageOps.exif_transpose(canvas).convert("RGB")

    # Everything is measured against the reference scan; rescale if the stored
    # template was captured at a different resolution.
    sx = canvas.width / layout.get("canvas", CANVAS)["width"]
    sy = canvas.height / layout.get("canvas", CANVAS)["height"]

    if photo is not None:
        _paste_photo(
            canvas, photo, layout["photo_box"], sx, sy,
            draw_frame=layout.get("photo_frame", True),
        )

    if hod_signature is not None:
        _paste_signature(canvas, hod_signature, layout["hod_signature_box"], sx, sy)

    draw = ImageDraw.Draw(canvas)
    for name, spec in layout["fields"].items():
        text = str(data.get(name, "") or "").strip()
        if not text:
            continue

        size = spec["size"] * (sy if sy < sx else sx)
        font = _fit_font(draw, text, size, spec["max_width"] * sx)
        x = spec["x"] * sx
        y = spec["baseline"] * sy
        # "s" anchors on the baseline, so values sit on the printed rule the
        # same way the sample details did.
        anchor = "ms" if spec.get("align", "center") == "center" else "ls"
        draw.text((x, y), text, font=font, fill=tuple(spec["color"]), anchor=anchor)

    return canvas
