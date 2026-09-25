"""Paint the sample student's details out of the supplied certificate scan.

The scan we were given is a filled-in certificate, so before anything can be
generated the existing SL. No., names, registration number, year and photo have
to go -- while the watermark, the dotted rules and the uneven lighting of the
scan all have to stay.

Text is replaced by a *morphological closing* of the image.  On light paper
with dark ink, a closing swallows the strokes and leaves the paper tone, which
keeps any lighting gradient intact.  Plain inpainting was tried first and
smeared the dotted rules upwards into the cleared boxes.

A photograph is too large for a closing to absorb, so if one is present its box
is rebuilt row by row from the paper immediately left and right of it.  The
current card ships with an empty photo box, so ``PHOTO_ERASE_BOX`` is ``None``
and that step is skipped -- but it is kept for cards that arrive filled in.
"""

import cv2
import numpy as np

from .layout import CANVAS, ERASE_BOXES, PHOTO_ERASE_BOX, PRESERVE_BOXES

# Wide enough to swallow the thickest strokes in the sample text, narrow enough
# to leave the watermark and the page's lighting alone. Measured against the
# reference canvas, so it is scaled to whatever resolution the card arrives at
# -- at half size a fixed 25 px kernel is twice as aggressive and eats the
# watermark.
_CLOSE_KERNEL = 25
_MEDIAN_BLUR = 9
_FEATHER_SIGMA = 2.0


def _odd(value, minimum=3):
    """Nearest odd integer at or above ``minimum`` (OpenCV wants odd kernels)."""
    value = max(int(round(value)), minimum)
    return value if value % 2 else value + 1


def _scaled(box, sx, sy):
    x0, y0, x1, y1 = box
    return (int(round(x0 * sx)), int(round(y0 * sy)),
            int(round(x1 * sx)), int(round(y1 * sy)))


def _fill_text_boxes(img, boxes, scale=1.0):
    size = _odd(_CLOSE_KERNEL * scale, minimum=5)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    background = cv2.medianBlur(
        cv2.morphologyEx(img, cv2.MORPH_CLOSE, kernel), _odd(_MEDIAN_BLUR * scale)
    )

    mask = np.zeros(img.shape[:2], np.uint8)
    for x0, y0, x1, y1 in boxes:
        mask[y0:y1, x0:x1] = 255

    # Grow the mask before feathering so the feather sits *outside* each box.
    # Feathering inwards leaves a rim of the original ink behind -- that is what
    # produced the grey ghost under the year on the first attempt. Both the
    # growth and the blur scale too, so on a smaller card the feather cannot
    # reach past a box into the ruling just below it.
    grow = _odd(5 * scale)
    soft = cv2.GaussianBlur(
        cv2.dilate(mask, np.ones((grow, grow), np.uint8)), (0, 0),
        max(_FEATHER_SIGMA * scale, 0.6),
    )
    alpha = cv2.merge([soft.astype(np.float32) / 255.0] * 3)
    return (img.astype(np.float32) * (1 - alpha)
            + background.astype(np.float32) * alpha).astype(np.uint8)


def _fill_photo_box(img, box, seed=7):
    x0, y0, x1, y1 = box
    height, width = img.shape[:2]
    x0, x1 = max(x0, 0), min(x1, width)
    y0, y1 = max(y0, 0), min(y1, height)

    patch = np.zeros((y1 - y0, x1 - x0, 3), np.float32)
    for i, y in enumerate(range(y0, y1)):
        left = img[y, max(x0 - 30, 0):max(x0 - 6, 1)].reshape(-1, 3)
        right = img[y, min(x1 + 6, width - 1):min(x1 + 22, width)].reshape(-1, 3)
        samples = np.vstack([s for s in (left, right) if len(s)])
        patch[i, :, :] = np.median(samples, axis=0)

    patch = cv2.GaussianBlur(patch, (0, 0), 4)
    # A touch of grain stops the fill reading as a flat printed rectangle.
    patch += np.random.default_rng(seed).normal(0, 2.0, patch.shape)

    filled = img.copy()
    filled[y0:y1, x0:x1] = np.clip(patch, 0, 255).astype(np.uint8)

    mask = np.zeros(img.shape[:2], np.uint8)
    mask[y0:y1, x0:x1] = 255
    alpha = cv2.merge([cv2.GaussianBlur(mask, (0, 0), 2).astype(np.float32) / 255.0] * 3)
    return (img.astype(np.float32) * (1 - alpha)
            + filled.astype(np.float32) * alpha).astype(np.uint8)


def build_blank_template(source_path, destination_path, quality=96):
    """Write a blank version of ``source_path`` to ``destination_path``.

    The erase boxes are defined against the reference canvas in ``layout``, so
    a card supplied at any other resolution is handled by scaling them -- and
    the cleaning kernels with them.
    """
    img = cv2.imread(str(source_path))
    if img is None:
        raise FileNotFoundError(f"Could not read certificate template: {source_path}")

    height, width = img.shape[:2]
    sx = width / CANVAS["width"]
    sy = height / CANVAS["height"]

    original = img.copy()
    img = _fill_text_boxes(img, [_scaled(b, sx, sy) for b in ERASE_BOXES], scale=min(sx, sy))
    if PHOTO_ERASE_BOX is not None:
        img = _fill_photo_box(img, _scaled(PHOTO_ERASE_BOX, sx, sy))

    for x0, y0, x1, y1 in (_scaled(b, sx, sy) for b in PRESERVE_BOXES):
        img[y0:y1, x0:x1] = original[y0:y1, x0:x1]

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    # A PNG blank keeps the card lossless, so the only JPEG generation is the
    # finished certificate.
    params = []
    if destination_path.suffix.lower() in (".jpg", ".jpeg"):
        params = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
    cv2.imwrite(str(destination_path), img, params)
    return destination_path
