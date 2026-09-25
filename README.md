# SVU Registration Certificate Editor

A Django + SQLite web app that fills in Swami Vivekananda University registration
certificates — one at a time in a browser editor, or a whole cohort from a single
Excel workbook with the students' photos embedded in it.

Certificates are drawn onto the university's own registration card, so the
watermark, seal, ruled lines, registrar's signature and footer are the real
ones. Output matches the supplied card's resolution -- currently 1350 x 900 --
and can be taken as a JPG or as a PDF.

---

## Quick start

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py setup_certificates      # builds the blank template
python manage.py createsuperuser         # optional, for the admin
python manage.py runserver
```

Then open <http://127.0.0.1:8000/>.

| Page | What it does |
| --- | --- |
| `/` | Single-certificate editor with live preview, and PDF or JPG download |
| `/bulk/` | Bulk generation from one workbook, as PDFs or JPGs |
| `/history/` | Every certificate generated so far, searchable |
| `/admin/` | Template management, batches, generated certificates |

---

## The editable fields

| Field | Where it goes on the certificate |
| --- | --- |
| SL. No. | Top-left, after `SL. NO. :` |
| Student Name | The `Sri / Smt.` line |
| Guardian Name | The `S/D of` line |
| Registration Number | After `His/Her Registration Number is` |
| Year | After `of` on the same line |
| Student Photo | The framed box on the right |
| HOD Signature | Above the ruled `HOD` line, bottom left |

**Every detail is required**, and so is the photo. Long values are shrunk to
fit their ruled line rather than overflowing it. Only the HOD signature is
optional.

Photos are cropped to fill the card's printed photo box without distortion
(centred slightly above the middle, which suits portraits). Any size or shape
is accepted -- nothing has to be passport-sized.

The **HOD signature** is handled differently: it is fitted *inside* its space
rather than cropped to it -- a clipped signature is worse than a small one --
then centred on the printed rule and rested on it. A signature is almost
always scanned on white paper, so unless the file already has transparency
the paper is keyed out by luminance and only the strokes land on the card.
The ink keeps its own colour, and the watermark still shows through behind it.

---

## Bulk generation

Everything about the *students* travels in **one workbook**: their details in
the cells, and their photograph embedded on the same row.

The **HOD signature is uploaded separately, once per batch**, on the same page.
One head of department signs a whole cohort, so it would be busywork to paste
the same signature into every row. It is stamped on every certificate in the
run, and the batch records which signature was used.

Download the ready-made spreadsheet from the **Bulk generation** page
(*Download template*, next to *Spreadsheet format*) and type over it, or build one with these headings:

```
SL_NO | STUDENT_NAME | GUARDIAN_NAME | REGISTRATION_NUMBER | YEAR | PHOTO
```

Example row:

```
103BCS202002 | Apurba Sarkar | Susanta Kumar Sarkar | 002-103-2020-017 | 2020 | <photo embedded>
```

### Adding the photos

1. Click the cell in the **PHOTO** column for that student.
2. **Insert > Pictures > This Device**, and choose the photograph.
3. Drag it so it sits on that student's row.

A picture is matched to a student by **the row it sits on**, so it does not have
to be exactly inside the cell, and it does not have to be in the PHOTO column --
though a picture in that column wins if a row happens to hold two. Excel only
*displays* the picture at whatever size you drag it to; the app reads the
original file back out at full resolution.

> Use the ordinary **Place over Cells** insert. Excel's newer **Place in Cell**
> pictures are stored as rich values rather than drawings and cannot be read
> back out. The app detects them and stops with an error explaining how to
> re-insert them, rather than quietly producing photo-less certificates.

### Photo size

**There is no required photo size or shape.** Whatever you insert is used and
fitted to the certificate's photo box (293 x 342) without being stretched.

If a photo is much smaller than the box it is enlarged to fill it and may print
softer; if it is a very different shape, some of it is trimmed. Either way the
certificate is still made -- the run report just says what to expect, against
the cell it applies to. Around 600 x 700 prints crisply if you have the choice.

The same goes for the HOD signature: any size is accepted and fitted to the
space above the printed rule. What is *not* optional is having a photo at all --
a row without one is reported as an error naming the cell to put it in.

### If something is wrong

Nothing is generated until the whole workbook checks out. Every problem is
reported at once, each naming the exact cell, what is wrong and what to do:

```
ERROR    Sheet 'Students', cell F2
         The photo is 90 x 110 pixels. Filling the certificate's photo box
         (293 x 342) would mean enlarging it 3.3x, which prints blurred --
         its width of 90 px is the limiting side.
         Fix: Replace it with a photo at least 293 px wide and 342 px tall...

ERROR    Sheet 'Students', cell B3
         STUDENT_NAME is empty, so this student cannot be identified.
         Fix: Type the student's full name in B3, or delete row 3 if it is
         not a student.
```

**Errors block the run:**

- any column left empty -- all six are required
- a row with no photograph
- a repeated `SL_NO` or `REGISTRATION_NUMBER`
- a value past the field's length limit
- a required column missing from the heading row, named individually
- a picture that cannot be read at all, or one over 20 MB
- a file that is not an `.xlsx` workbook, including any `.csv`
- photos added with Excel's "Place in Cell"

**Warnings let it through** and appear in the run report:

- a photo that will be enlarged or trimmed to fit (any size is still accepted)
- a `YEAR` that does not look like a year
- an unrecognised extra column, which is ignored
- a picture sitting on a row with no student
- a file name typed into the `PHOTO` column

The checker is also forgiving where it safely can be: headings may sit below a
title row, column order does not matter, headings match ignoring case and
punctuation, and the student list is found even if the Instructions sheet was
the one left selected when the workbook was saved.

### Other notes

- **All six columns are required**, and every student needs a photograph.
- **`SL_NO` and `REGISTRATION_NUMBER` must each be unique.** No two students may
  share either; a repeat names the row it clashes with. Matching ignores letter
  case, so `R-1` and `r-1` count as the same.
- Column order does not matter, and headings are matched ignoring case, spaces
  and underscores -- `Student Name` and `STUDENT_NAME` both work.
- The HOD signature goes in the upload box, not the spreadsheet, and is the one
  optional thing. Any size is accepted; about 600 x 150 prints crisply.
- Photos are fitted to the photo box without distortion, at any size or shape.
- If a certificate still fails while rendering (after the workbook has passed
  its checks), that row is logged and skipped; the rest of the run continues.
- Only `.xlsx` is accepted. A `.csv` cannot carry photographs, and old `.xls`
  workbooks are not supported -- re-save either as `.xlsx`.

The finished batch page shows the counts, the run report, thumbnails of every
certificate, and a download button for whichever bundle you chose. Every
certificate on that page, and in the history, can also be taken individually as
a PDF or a JPG.

---

## Downloading: PDF or JPG

Every certificate can be had either way, and nothing is stored twice -- the
image is what is kept, and a PDF is made from it on request. That means an old
certificate can still be taken in either form.

**One certificate** -- the editor offers *Download PDF* and *Download JPG* side
by side, and both links appear against every certificate in the history and on
a batch page.

**A whole batch** -- pick one of three on the Bulk Generation page:

| Choice | What you get |
| --- | --- |
| JPG images | a ZIP with one `.jpg` per student |
| PDF files | a ZIP with one `.pdf` per student |
| One PDF | a single `.pdf`, one certificate per page, ready to print |

Every certificate is generated either way; the choice only decides how they are
bundled, and the batch remembers which was used.

### Page size

A PDF page is **9 x 6 inches** -- the card's real printed size -- whatever
resolution the card was supplied at. Print it at 100% and the certificate comes
out actual size rather than scaled to the paper. The size is
`PAGE_INCHES` in [certificates/layout.py](certificates/layout.py); change it
there if the card is ever printed at a different size.

The combined PDF is written page by page, so a large cohort is never held in
memory all at once.

---

## How the blank certificate is produced

The card supplied by the university (`assets/source_template.jpg`, 1350 x 900)
is a *filled-in* certificate: it carries its own sample student (SL. NO.
REG/2022/035, DISHA GUCHAIT, MANAS GUCHAIT, 001-134-2022-004, 2022) and a
sample HOD signature. `python manage.py setup_certificates` paints all of that
out and writes `assets/blank_template.png`.

Text is replaced with a morphological *closing* of the image
([certificates/cleaning.py](certificates/cleaning.py)). On light paper with dark
ink, a closing swallows the strokes and leaves the paper tone, which keeps the
watermark and any lighting gradient intact. Plain inpainting was tried first and
smeared the dotted rules upwards into the cleared boxes.

Each erase box stops short of the dotted rule below it and of the printed label
beside it, so the ruling, the labels and the colon after `SL. NO.` all survive.
The card's photo box is already empty and has its own printed rule, so it is
left alone and the renderer does not draw a second frame around pasted photos.

Two details are worth knowing:

- The kernels are measured against the reference canvas and **scaled to the
  card's actual resolution**. A fixed kernel tuned for a 2700 px card is twice
  as aggressive on a 1350 px one and eats the watermark along with the ink.
- The sample HOD signature has a descender that crosses its own green rule, so
  it is cleared in two pieces -- above the rule and below it -- and the rule,
  only two pixels tall, is then **copied back verbatim** (`PRESERVE_BOXES`).
  However gently the fill is feathered it lightens a line that thin; restoring
  it is exact where tuning the feather would only be close.

To re-clean after replacing the card:

```bash
python manage.py setup_certificates --rebuild
```

That re-points the stored default template at the new blank **in place**, so
certificates already generated keep their template link.

---

## Coordinates and fonts

Every coordinate lives in [certificates/layout.py](certificates/layout.py).
They were measured off the card by separating its black sample details from the
green printing on **saturation** -- in greyscale the university green is just as
dark as the text -- then reading the row and column profiles. The numbers
therefore match the printed ruling rather than being eyeballed.

Details are printed in **Times New Roman**, which sits alongside the serif lines
already on the card. Point sizes were calibrated per field so each value's ink
height matches the card's own sample, and each baseline lands within a few
pixels of where those details sat. On Linux the renderer falls back to
Liberation Serif, which is metric-compatible. If no such font is installed, drop
a `.ttf` into `assets/fonts/` as `TimesNewRoman.ttf`.

Coordinates are stored against a 2700 x 1800 **reference space** and scaled by
the card's actual size, so the card can be supplied at any resolution and the
numbers still land. The current card is 1350 x 900, i.e. exactly half the
reference; a higher-resolution re-export would need no code change at all,
only `setup_certificates --rebuild`.

Individual coordinates can be nudged without a code change: edit a template in
the Django admin and set **Layout overrides**, for example

```json
{"fields": {"year": {"x": 2060, "size": 42}}}
```

---

## Branding and interface

The palette and type come from the artefacts the app deals with: the deep green
and gold of the university masthead, and a serif for headings echoing the
certificate's own printing, on a warm paper ground. Gold is an accent only --
the rule under the masthead, focus rings, the active tab -- never a surface.

The crest appears in the masthead, as the favicon and touch icon, and in the
Django admin header. The source is `assets/svu_logo_source.jpg`; the versions
the site serves are in `static/img/`:

| File | Purpose |
| --- | --- |
| `svu-logo.png` | The crest with a transparent surround, for the masthead |
| `favicon.png` | 64px, on a white disc so it stays legible on a dark tab strip |
| `apple-touch-icon.png` | 180px, same treatment |

Because the crest is dark green, the masthead shows it on a white rounded-square
chip (`.brand-mark`) rather than directly on the green bar. To swap in a
different crest, replace the three files in `static/img/`.

The Django admin is re-pointed at the same palette in
[templates/admin/base_site.html](templates/admin/base_site.html), which
overrides the admin's own CSS variables. Only the accents are changed --
surfaces and text keep Django's values, so the built-in dark theme still works.

Layout was checked for horizontal overflow at 1400px, 820px and 390px. Grid
tracks use `minmax(0, 1fr)` and wide tables live inside `.table-scroll`, so a
long registration number or a wide sheet scrolls its own container instead of
stretching the page.

---

## Tests

```bash
python manage.py test certificates
```

72 tests cover the blank-template cleaning (sample details gone; rules, labels,
watermark and photo box kept), field placement measured against the card's own
sample values, long-value shrinking, photo cover-fit, extracting embedded
pictures and matching them to rows, the required-column and uniqueness
rules, HOD signature fitting, keying and placement, PDF output (page count and
printed size), spreadsheet parsing and the views.

---

## Project layout

```
manage.py
assets/source_template.jpg        the university's registration card (1350x900)
assets/blank_template.png         generated: the card with its sample removed
assets/svu_logo_source.jpg        original university crest
svu_certificates/                 Django project (settings, urls, wsgi)
certificates/
    layout.py                     measured coordinates for every field
    cleaning.py                   paints the sample details out of the scan
    renderer.py                   draws details onto the blank certificate
    excel.py                      spreadsheet reading, embedded photos, validation
    validation.py                 located, fixable problem reports
    services.py                   single and bulk generation
    models.py  views.py  forms.py  admin.py  tests.py
    templates/certificates/       the web pages
templates/admin/base_site.html    puts the crest in the Django admin header
static/css/style.css
static/img/                       university crest, favicon, touch icon
media/                            generated certificates, photos, batch ZIPs
```

---

## Deploying beyond a local machine

`settings.py` is set up for local use: `DEBUG` on, `ALLOWED_HOSTS = ["*"]`, and a
checked-in `SECRET_KEY`. Before putting this on a network, set `SVU_DEBUG=0` and
`SVU_SECRET_KEY` in the environment, list the real hostnames in `ALLOWED_HOSTS`,
and serve `media/` and `static/` through the web server rather than Django.
There is no login on the certificate pages — anyone who can reach the server can
generate certificates, so keep it on a trusted network or put an auth layer in
front of it.




Admin
user id:-admin
password:-123456
