"""Regenerates the fixture corpus under tests/fixtures/<format>/.

Run with: python tests/fixtures/generate_fixtures.py
Requires the `dev` extra (`pip install -e ".[dev]"`) for `reportlab` and
`python-pptx`.

Per spec #31, the corpus deliberately includes broken files, not just
valid ones — the point is to test the *detection* of real problems.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pypdf
from docx import Document
from openpyxl import Workbook
from PIL import Image
from pptx import Presentation
from pypdf.generic import NameObject
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

# A handful of common install locations for a real TTF, used only to build
# the font-embedding fixtures below. Not vendored into the repo (keeps it
# free of binary font assets); if none of these exist, those two fixtures
# are skipped with a warning rather than failing the whole script — every
# other fixture is independent of having a system font available.
_CANDIDATE_TTF_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:\\Windows\\Fonts\\arial.ttf",
]

PDF_OUT_DIR = Path(__file__).parent / "pdf"
PPTX_OUT_DIR = Path(__file__).parent / "pptx"
DOCX_OUT_DIR = Path(__file__).parent / "docx"
XLSX_OUT_DIR = Path(__file__).parent / "xlsx"
IMAGE_OUT_DIR = Path(__file__).parent / "image"
HTML_OUT_DIR = Path(__file__).parent / "html"
SVG_OUT_DIR = Path(__file__).parent / "svg"


# ---------------------------------------------------------------- PDF ----

def make_good_2page() -> None:
    path = PDF_OUT_DIR / "good_2page.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setTitle("Sample Doc")
    c.drawString(100, 700, "Page one content")
    c.showPage()
    c.drawString(100, 700, "Page two content")
    c.showPage()
    c.save()


def make_blank_page_pdf() -> None:
    """3 pages, the middle one genuinely blank (no drawing calls at all) -
    a different failure mode from empty_0page.pdf (zero pages total).
    blank_pages must flag page 2 specifically."""
    path = PDF_OUT_DIR / "blank_page.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    c.drawString(100, 700, "Page one has content")
    c.showPage()
    c.showPage()  # page two: nothing drawn - genuinely blank
    c.drawString(100, 700, "Page three has content")
    c.showPage()
    c.save()


def make_empty_0page() -> None:
    path = PDF_OUT_DIR / "empty_0page.pdf"
    writer = pypdf.PdfWriter()
    with open(path, "wb") as f:
        writer.write(f)


def make_encrypted() -> None:
    src = PDF_OUT_DIR / "good_2page.pdf"
    path = PDF_OUT_DIR / "encrypted.pdf"
    reader = pypdf.PdfReader(str(src))
    writer = pypdf.PdfWriter()
    writer.append(reader)
    writer.encrypt("secret123")
    with open(path, "wb") as f:
        writer.write(f)


def make_corrupt_pdf() -> None:
    path = PDF_OUT_DIR / "corrupt.pdf"
    path.write_bytes(b"%PDF-1.4\n%% this is not a real pdf body, just garbage\nendobj trailer garbage")


def _find_system_ttf() -> str | None:
    for candidate in _CANDIDATE_TTF_PATHS:
        if Path(candidate).is_file():
            return candidate
    return None


def make_embedded_font_pdf() -> bool:
    """A page using a genuinely embedded TrueType font (not one of the
    standard 14) — verify_structural()'s font_embedding check should PASS."""
    ttf_path = _find_system_ttf()
    if ttf_path is None:
        return False
    path = PDF_OUT_DIR / "embedded_font.pdf"
    pdfmetrics.registerFont(TTFont("EmbeddedTestFont", ttf_path))
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setFont("EmbeddedTestFont", 14)
    c.drawString(100, 700, "Embedded font test")
    c.showPage()
    c.save()
    return True


def make_nonembedded_custom_font_pdf() -> bool:
    """Same starting point as embedded_font.pdf, but with the embedded
    font's /FontFile2 stripped and its name changed to a plausible
    non-standard font — verify_structural()'s font_embedding check should
    WARN (or FAIL, with policy `forbid_unembedded_fonts`)."""
    embedded_path = PDF_OUT_DIR / "embedded_font.pdf"
    if not embedded_path.is_file():
        return False
    path = PDF_OUT_DIR / "nonembedded_custom_font.pdf"
    reader = pypdf.PdfReader(str(embedded_path))
    writer = pypdf.PdfWriter()
    writer.append(reader)
    page = writer.pages[0]
    for font_ref in page["/Resources"]["/Font"].values():
        font = font_ref.get_object()
        base_font = str(font.get("/BaseFont", ""))
        if "+" in base_font:  # the subset-tagged (embedded) font, not standard Helvetica
            font[NameObject("/BaseFont")] = NameObject("/CustomNonEmbedded")
            descriptor = font["/FontDescriptor"].get_object()
            for key in ("/FontFile", "/FontFile2", "/FontFile3"):
                if key in descriptor:
                    del descriptor[key]
            descriptor[NameObject("/FontName")] = NameObject("/CustomNonEmbedded")
    with open(path, "wb") as f:
        writer.write(f)
    return True


def make_mislabeled_html() -> None:
    path = PDF_OUT_DIR / "mislabeled_html.pdf"
    path.write_text("<html><body>not a pdf</body></html>\n")


# --------------------------------------------------------------- PPTX ----

def make_good_2slide_pptx() -> None:
    path = PPTX_OUT_DIR / "good_2slide.pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])  # title + content
    slide.shapes.title.text = "Sample Deck"
    slide.placeholders[1].text = "Some body content"
    prs.slides.add_slide(prs.slide_layouts[6])  # blank layout, no placeholders
    prs.save(str(path))


def make_empty_placeholder_pptx() -> None:
    """One slide with a title placeholder left empty — the 'leftover
    placeholder' case verify_structural()'s empty_placeholders check
    should flag as WARN."""
    path = PPTX_OUT_DIR / "empty_placeholder.pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.placeholders[1].text = "Body has content but the title was never filled in"
    prs.save(str(path))


def make_leftover_placeholder_text_pptx() -> None:
    """Placeholders are filled in (not empty - a different, already-covered
    case), but with unreviewed generation leftovers -
    leftover_placeholder_text must WARN."""
    path = PPTX_OUT_DIR / "leftover_placeholder_text.pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])
    slide.shapes.title.text = "Click to add title"
    slide.placeholders[1].text = "Lorem ipsum dolor sit amet."
    prs.save(str(path))


def make_zero_slide_pptx() -> None:
    path = PPTX_OUT_DIR / "zero_slide.pptx"
    prs = Presentation()
    prs.save(str(path))


def make_corrupt_pptx() -> None:
    path = PPTX_OUT_DIR / "corrupt.pptx"
    path.write_bytes(b"PK\x03\x04this is not a real zip/ooxml body, just garbage bytes")


def make_mislabeled_pdf_as_pptx() -> None:
    """A real PDF saved with a .pptx extension, mirroring the PDF corpus's
    mislabeled_html.pdf — proves type detection never trusts the extension."""
    path = PPTX_OUT_DIR / "mislabeled_pdf.pptx"
    path.write_bytes((PDF_OUT_DIR / "good_2page.pdf").read_bytes())


# --------------------------------------------------------------- DOCX ----

def make_good_docx() -> None:
    path = DOCX_OUT_DIR / "good.docx"
    doc = Document()
    doc.add_heading("Sample Document", level=1)
    doc.add_paragraph("Some body content.")
    doc.add_table(rows=1, cols=2)
    doc.save(str(path))


def make_leftover_placeholder_docx() -> None:
    """A generated-looking document that still has unreviewed placeholder
    text — leftover_placeholder_text must WARN (or FAIL under
    forbid_placeholder_text)."""
    path = DOCX_OUT_DIR / "leftover_placeholder.docx"
    doc = Document()
    doc.add_heading("Click to add title", level=1)
    doc.add_paragraph("Lorem ipsum dolor sit amet, consectetur adipiscing elit.")
    doc.add_paragraph("TODO: write the real conclusion here.")
    doc.save(str(path))


def make_empty_docx() -> None:
    """Zero paragraphs — python-docx always creates at least the body
    element, but a document with no add_paragraph()/add_heading() calls
    has zero *paragraph* entries, which is the case verify_structural()'s
    paragraph_count check should flag as FAIL."""
    path = DOCX_OUT_DIR / "empty.docx"
    doc = Document()
    # Remove the single implicit empty paragraph python-docx starts with.
    for p in list(doc.paragraphs):
        p._element.getparent().remove(p._element)
    doc.save(str(path))


def make_corrupt_docx() -> None:
    path = DOCX_OUT_DIR / "corrupt.docx"
    path.write_bytes(b"PK\x03\x04this is not a real zip/ooxml body, just garbage bytes")


def make_mislabeled_pdf_as_docx() -> None:
    path = DOCX_OUT_DIR / "mislabeled_pdf.docx"
    path.write_bytes((PDF_OUT_DIR / "good_2page.pdf").read_bytes())


# --------------------------------------------------------------- XLSX ----

def _rewrite_zip_member(path: Path, member: str, old: str, new: str) -> None:
    """openpyxl can't write a cached formula result directly (it only ever
    writes the formula text) — so build a normal workbook, then patch the
    one cell's raw XML to add the `<v>` a real spreadsheet app would have
    written. This is the only way to get a deterministic "cached value
    present" or "cached error present" fixture without depending on
    LibreOffice (whose reliability in this exact dev environment is
    exactly what adapters/pptx/adapter.py's docstring documents as flaky)."""
    with zipfile.ZipFile(path, "r") as zf:
        items = {name: zf.read(name) for name in zf.namelist()}
    xml = items[member].decode("utf-8")
    assert old in xml, f"expected substring not found in {member}: {old!r}"
    items[member] = xml.replace(old, new, 1).encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in items.items():
            zf.writestr(name, data)


def _make_formula_workbook(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = "Label"
    ws["B1"] = 10
    ws["B2"] = 20
    ws["B3"] = "=B1+B2"
    wb.create_sheet("Sheet2")
    wb.save(str(path))


def make_good_xlsx() -> None:
    """Two sheets, one formula, a real (non-error) cached result — the
    PASS branch of formula_cached_errors, and the ordinary UNKNOWN branch
    of formula_recalculation (openpyxl never recalculates, regardless of
    what's cached)."""
    path = XLSX_OUT_DIR / "good.xlsx"
    _make_formula_workbook(path)
    _rewrite_zip_member(path, "xl/worksheets/sheet1.xml", '<c r="B3"><f>B1+B2</f><v></v></c>', '<c r="B3"><f>B1+B2</f><v>30</v></c>')


def make_formula_error_xlsx() -> None:
    """A formula cell with a cached #REF! result — must FAIL formula_cached_errors."""
    path = XLSX_OUT_DIR / "formula_error.xlsx"
    _make_formula_workbook(path)
    _rewrite_zip_member(
        path, "xl/worksheets/sheet1.xml",
        '<c r="B3"><f>B1+B2</f><v></v></c>', '<c r="B3" t="e"><f>B1+B2</f><v>#REF!</v></c>',
    )


def make_no_formula_xlsx() -> None:
    """No formulas at all — formula_cached_errors/formula_recalculation
    should both be SKIPPED, not UNKNOWN (a genuinely not-applicable case
    is a different, more benign signal than "we couldn't check")."""
    path = XLSX_OUT_DIR / "no_formula.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Data"
    ws["A1"] = "Label"
    ws["B1"] = 10
    wb.save(str(path))


def make_external_link_xlsx() -> None:
    """A workbook with a real `<externalReferences>` part pointing at
    another workbook file — must produce a non-empty `external_links` in
    inspect() and WARN/FAIL the `external_links` structural check.

    openpyxl has no write-side API for this (confirmed: saving a formula
    string like "=[1]Sheet1!A1" does NOT create the externalLinks parts —
    it's just treated as opaque formula text), so this hand-builds the
    three OOXML parts a real external reference needs and wires them into
    the zip the same way _rewrite_zip_member patches a single cell:
    xl/externalLinks/externalLink1.xml (the external book + sheet names),
    its _rels file (the actual external target path), plus registering
    both in xl/workbook.xml's <externalReferences>, xl/_rels/workbook.xml.rels,
    and [Content_Types].xml. Verified by round-tripping through openpyxl's
    own reader (wb._external_links is populated) before trusting this as a
    fixture, not assumed correct from the XML alone.
    """
    path = XLSX_OUT_DIR / "external_link.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws["A1"] = "Label"
    ws["B1"] = "=[1]Sheet1!A1"  # references the (not-actually-present) external book
    wb.save(str(path))

    with zipfile.ZipFile(path, "r") as zf:
        items = {name: zf.read(name) for name in zf.namelist()}

    wb_xml = items["xl/workbook.xml"].decode()
    assert "</sheets>" in wb_xml
    items["xl/workbook.xml"] = wb_xml.replace(
        "</sheets>",
        '</sheets><externalReferences><externalReference '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="rId4"/>'
        "</externalReferences>",
        1,
    ).encode()

    rels_xml = items["xl/_rels/workbook.xml.rels"].decode()
    assert "</Relationships>" in rels_xml
    items["xl/_rels/workbook.xml.rels"] = rels_xml.replace(
        "</Relationships>",
        '<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/externalLink" '
        'Target="externalLinks/externalLink1.xml" Id="rId4"/></Relationships>',
    ).encode()

    ct_xml = items["[Content_Types].xml"].decode()
    assert "</Types>" in ct_xml
    items["[Content_Types].xml"] = ct_xml.replace(
        "</Types>",
        '<Override PartName="/xl/externalLinks/externalLink1.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.externalLink+xml"/></Types>',
    ).encode()

    items["xl/externalLinks/externalLink1.xml"] = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        b'<externalLink xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        b'<externalBook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:id="rId1">'
        b"<sheetNames><sheetName val=\"Sheet1\"/></sheetNames>"
        b"</externalBook></externalLink>"
    )
    items["xl/externalLinks/_rels/externalLink1.xml.rels"] = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        b'<Relationship Id="rId1" '
        b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/externalLinkPath" '
        b'Target="other_workbook.xlsx" TargetMode="External"/></Relationships>'
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in items.items():
            zf.writestr(name, data)

    # Don't trust the hand-built XML blindly - confirm openpyxl's own
    # reader actually populates _external_links from it before shipping
    # this as a fixture other tests will rely on.
    from openpyxl import load_workbook

    reloaded = load_workbook(str(path))
    assert getattr(reloaded, "_external_links", None), "external_link.xlsx fixture failed its own round-trip check"


def make_corrupt_xlsx() -> None:
    path = XLSX_OUT_DIR / "corrupt.xlsx"
    path.write_bytes(b"PK\x03\x04this is not a real zip/ooxml body, just garbage bytes")


def make_mislabeled_pdf_as_xlsx() -> None:
    path = XLSX_OUT_DIR / "mislabeled_pdf.xlsx"
    path.write_bytes((PDF_OUT_DIR / "good_2page.pdf").read_bytes())


# -------------------------------------------------------------- IMAGE ----

def make_good_png() -> None:
    path = IMAGE_OUT_DIR / "good.png"
    Image.new("RGB", (200, 100), color=(255, 0, 0)).save(path)


def make_alpha_png() -> None:
    path = IMAGE_OUT_DIR / "alpha.png"
    Image.new("RGBA", (150, 150), color=(0, 255, 0, 128)).save(path)


def make_exif_rotated_jpeg() -> None:
    """EXIF orientation 6 (rotate 90° CW) — pure Pillow, no extra
    dependency needed; Image.Exif() round-trips through JPEG save/load."""
    path = IMAGE_OUT_DIR / "exif_rotated.jpg"
    img = Image.new("RGB", (300, 200), color=(0, 0, 255))
    exif = img.getexif()
    exif[274] = 6
    img.save(path, exif=exif)


def make_corrupt_png() -> None:
    path = IMAGE_OUT_DIR / "corrupt.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\nthis is not a real PNG body, just garbage bytes")


def make_mislabeled_pdf_as_png() -> None:
    path = IMAGE_OUT_DIR / "mislabeled_pdf.png"
    path.write_bytes((PDF_OUT_DIR / "good_2page.pdf").read_bytes())


# --------------------------------------------------------------- HTML ----

def make_good_html() -> None:
    path = HTML_OUT_DIR / "good.html"
    path.write_text(
        "<!doctype html>\n<html><head><title>Sample Page</title></head>\n"
        "<body><h1>Hello Artifact Skill</h1><p>Some body content.</p></body></html>\n"
    )


def make_missing_local_resource_html() -> None:
    path = HTML_OUT_DIR / "missing_local_resource.html"
    path.write_text(
        "<!doctype html>\n<html><head><title>Broken Refs</title>"
        '<link rel="stylesheet" href="does-not-exist.css"></head>\n'
        '<body><img src="also-missing.png"></body></html>\n'
    )


def make_external_resource_html() -> None:
    path = HTML_OUT_DIR / "external_resource.html"
    path.write_text(
        "<!doctype html>\n<html><head><title>External Refs</title></head>\n"
        '<body><img src="https://example.com/some-image.png"></body></html>\n'
    )


def make_leftover_placeholder_html() -> None:
    path = HTML_OUT_DIR / "leftover_placeholder.html"
    path.write_text(
        "<!doctype html>\n<html><head><title>Sample Page</title></head>\n"
        "<body><h1>Click to add title</h1>"
        "<p>Lorem ipsum dolor sit amet, consectetur adipiscing elit.</p>"
        "<script>// TODO: this is JS code, not document text - must not be flagged</script>"
        "</body></html>\n"
    )


def make_no_title_html() -> None:
    path = HTML_OUT_DIR / "no_title.html"
    path.write_text("<!doctype html>\n<html><body><p>No title element here.</p></body></html>\n")


def make_binary_garbage_html() -> None:
    path = HTML_OUT_DIR / "binary_garbage.html"
    path.write_bytes(b"\xff\xfe\x00\x01not valid utf-8 \xfe\xff pretending to be html")


def make_mislabeled_pdf_as_html() -> None:
    path = HTML_OUT_DIR / "mislabeled_pdf.html"
    path.write_bytes((PDF_OUT_DIR / "good_2page.pdf").read_bytes())


# ---------------------------------------------------------------- SVG ----

def make_good_svg() -> None:
    path = SVG_OUT_DIR / "good.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
        '<circle cx="50" cy="50" r="40" fill="red"/></svg>\n'
    )


def make_missing_local_resource_svg() -> None:
    path = SVG_OUT_DIR / "missing_local_resource.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
        '<image href="does-not-exist.png" width="10" height="10"/></svg>\n'
    )


def make_external_resource_svg() -> None:
    path = SVG_OUT_DIR / "external_resource.svg"
    path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
        '<image href="https://example.com/some-image.png" width="10" height="10"/></svg>\n'
    )


def make_no_size_svg() -> None:
    path = SVG_OUT_DIR / "no_size.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><circle cx="5" cy="5" r="4"/></svg>\n')


def make_malformed_svg() -> None:
    path = SVG_OUT_DIR / "malformed.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg"><circle cx="5" cy="5" r="4"></svg>\n')  # unclosed <circle>


def make_entity_bomb_svg() -> None:
    """A DOCTYPE declaring a custom entity — not an actual expansion bomb
    (a real one would be unpleasant to keep in a test fixture directory),
    just enough to prove _reject_xml_entities() catches the pattern before
    any parsing is attempted."""
    path = SVG_OUT_DIR / "entity_bomb.svg"
    path.write_text(
        '<?xml version="1.0"?>\n<!DOCTYPE svg [<!ENTITY lol "lol">]>\n'
        '<svg xmlns="http://www.w3.org/2000/svg"><title>&lol;</title></svg>\n'
    )


def make_mislabeled_pdf_as_svg() -> None:
    path = SVG_OUT_DIR / "mislabeled_pdf.svg"
    path.write_bytes((PDF_OUT_DIR / "good_2page.pdf").read_bytes())


if __name__ == "__main__":
    PDF_OUT_DIR.mkdir(parents=True, exist_ok=True)
    PPTX_OUT_DIR.mkdir(parents=True, exist_ok=True)
    DOCX_OUT_DIR.mkdir(parents=True, exist_ok=True)
    XLSX_OUT_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_OUT_DIR.mkdir(parents=True, exist_ok=True)
    HTML_OUT_DIR.mkdir(parents=True, exist_ok=True)
    SVG_OUT_DIR.mkdir(parents=True, exist_ok=True)

    make_good_2page()
    make_blank_page_pdf()
    make_empty_0page()
    make_encrypted()
    make_corrupt_pdf()
    make_mislabeled_html()
    if make_embedded_font_pdf():
        make_nonembedded_custom_font_pdf()
    else:
        print("WARNING: no system TTF found in _CANDIDATE_TTF_PATHS; skipped "
              "embedded_font.pdf / nonembedded_custom_font.pdf (leaving any existing copies as-is).")

    make_good_2slide_pptx()
    make_empty_placeholder_pptx()
    make_leftover_placeholder_text_pptx()
    make_zero_slide_pptx()
    make_corrupt_pptx()
    make_mislabeled_pdf_as_pptx()

    make_good_docx()
    make_empty_docx()
    make_leftover_placeholder_docx()
    make_corrupt_docx()
    make_mislabeled_pdf_as_docx()

    make_good_xlsx()
    make_formula_error_xlsx()
    make_no_formula_xlsx()
    make_external_link_xlsx()
    make_corrupt_xlsx()
    make_mislabeled_pdf_as_xlsx()

    make_good_png()
    make_alpha_png()
    make_exif_rotated_jpeg()
    make_corrupt_png()
    make_mislabeled_pdf_as_png()

    make_good_html()
    make_missing_local_resource_html()
    make_external_resource_html()
    make_no_title_html()
    make_leftover_placeholder_html()
    make_binary_garbage_html()
    make_mislabeled_pdf_as_html()

    make_good_svg()
    make_missing_local_resource_svg()
    make_external_resource_svg()
    make_no_size_svg()
    make_malformed_svg()
    make_entity_bomb_svg()
    make_mislabeled_pdf_as_svg()

    print(f"Wrote PDF fixtures to {PDF_OUT_DIR}")
    print(f"Wrote PPTX fixtures to {PPTX_OUT_DIR}")
    print(f"Wrote DOCX fixtures to {DOCX_OUT_DIR}")
    print(f"Wrote XLSX fixtures to {XLSX_OUT_DIR}")
    print(f"Wrote image fixtures to {IMAGE_OUT_DIR}")
    print(f"Wrote HTML fixtures to {HTML_OUT_DIR}")
    print(f"Wrote SVG fixtures to {SVG_OUT_DIR}")
