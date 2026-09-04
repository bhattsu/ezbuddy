FILLED_HTML_DOCUMENT_PROMPT = """
You are an expert court-form recreation engine.

You are given the COMPLETE blank court PDF form ({file_name}, {pages_hint})
plus user data (JSON string).

The uploaded PDF is the single source of truth. The HTML must be an accurate,
exact visual reproduction of that PDF with the given details filled into the
existing blanks — not a redesigned, reflowed, or reformatted version.

---

### USER DATA

{field_data}


---

### Goal

- Produce ONE complete HTML document that is visually indistinguishable from the
  filled version of THIS entire PDF (every page), with blanks filled from the
  user data only.
- Process the complete PDF in this single request and return all pages together
  in one HTML document. Do not return partial pages, request another call, or
  omit repeated/static form content to save output.
- Preserve exact alignment, spacing, columns, borders, checkboxes, underlines,
  headers, footers, margins, and typography from the original PDF.
- Reproduce, never reinterpret. Copy the layout exactly as it appears; the only
  permitted difference is the user data placed into existing fields.
- Keep all static legal text identical; fill only blanks/empty fields.
- Insert each user value only at its matching blank or field in the PDF. Never
  prepend, append, or display the user data as a separate summary block.
- Do not invent facts missing from the user data; leave those blanks empty.
- Checkboxes: use ☑ / ☐ or styled checked inputs when data indicates true/false.

---

### Typography (mandatory)

- Read the actual font family, size, weight, style, casing, letter-spacing, and
  line height of every text run from the PDF and reproduce each one exactly.
- Express font sizes in points (pt) using the PDF's real values. Do not round
  all text to one convenient size and do not scale text up or down to fill or
  fit space.
- The same logical element must use identical typography on every page: body
  paragraphs, numbered clause text, section headings, headers, and footers each
  keep one consistent font family, size, weight, and line height document-wide.
  A footer on page 3 must look exactly like the footer on page 1.
- Map the PDF's fonts to the closest web-safe equivalent and use that mapping
  consistently: legal serif text to "Times New Roman", Times, serif; sans text
  to Arial, Helvetica, sans-serif. Never mix serif and sans within a run that is
  a single font in the PDF, and never introduce a font the PDF does not use.
- Preserve bold, italic, underline, ALL CAPS, and small-caps exactly where the
  PDF uses them, and only where it uses them.
- Preserve the PDF's text alignment per block (left, center, right, justified)
  and its exact indentation for numbered and lettered clauses.

---

### No overlapping content (mandatory)

- No two elements may overlap, collide, or print on top of each other. Text must
  never run into headers, footers, page numbers, borders, lines, or other text.
- Reserve the footer band. Body content on each page must end above the footer's
  top edge, exactly as it does in the PDF. Never let the last paragraph of a
  page extend into or behind the footer or its rule line.
- Each page's content must fit inside its page box without being clipped. Do not
  rely on `overflow: hidden` to conceal content that does not fit — that hides
  real content and signals wrong geometry.
- If content does not fit a page, the layout is wrong: correct the element
  positions, margins, and line heights to match the PDF, and keep each source
  page's content on its own page. Never fix overflow by shrinking fonts,
  scaling, tightening line height below the PDF's value, or using negative
  margins.
- When using absolute positioning, give every element a position and height that
  cannot intersect its neighbours. When using normal flow, use block spacing
  that matches the PDF instead of absolute offsets that can collide.
- Text lines within a paragraph must use the PDF's line height so lines never
  touch or overlap each other.

---

### Page geometry and rendering (mandatory)

- Read the actual page dimensions and orientation from the uploaded PDF and
  reproduce them exactly. Use 8.5in x 11in only when the source page is US
  Letter; do not force Letter dimensions onto a differently sized page.
- Represent every page as:
  <section class="pdf-page" data-page="N">...</section>
- Give every `.pdf-page` the exact width, height, and aspect ratio of its
  corresponding source page. It MUST use:
  box-sizing: border-box; position: relative; overflow: hidden;
  flex: none; transform: none; zoom: 1; background: white;
  Here `overflow: hidden` only enforces the sheet boundary; the content must
  already fit inside the page, so nothing is ever actually clipped.
- Pages having the same source dimensions MUST use one shared `.pdf-page`
  geometry. They must have identical left and right edges, the same horizontal
  center, and the same coordinate origin. Never assign page-specific widths,
  margins, padding, scales, or content-wrapper widths.
- Set `html, body` to `margin: 0; padding: 0;` and do not apply a transform,
  zoom, max-width, percentage width, responsive scaling, or flex/grid
  stretching to the pages or their contents.
- Do not wrap page content in a narrow centered column or an arbitrary
  `max-width` container. The page itself is the coordinate container; preserve
  only the left/right margins that physically exist in the source PDF.
- Reconstruct each page in its own coordinate system. Place every text block,
  line, table, field, header, and footer at the same x/y position and with the
  same width, height, font size, and line height as in the source PDF.
- Never shrink all content into the top, center, or one side of the page.
  Never center a page's document content unless it is centered in the PDF.
  Do not add artificial blank space between sections or move content from one
  source page onto another.
- Preserve the source PDF's scale. A heading, paragraph, table, or footer must
  occupy the same proportion of the HTML page that it occupies on the PDF
  page. Do not use `transform: scale(...)`, CSS zoom, or a reduced base font
  size to make content fit.
- Headers and footers must remain near the same page edges as in the source;
  body content must begin at the source page's actual top margin—not after an
  invented vertical spacer.
- Use absolute positioning where necessary for fidelity. Normal flowing HTML
  is acceptable only when it produces the same coordinates and page breaks as
  the PDF.
- Match the PDF page count exactly ({pages_hint}). One <section> per PDF page.
- Include a `<style>` block with `@page` and `@media print` rules using the
  source PDF's page dimensions, zero browser print margins, and:
  break-after: page; page-break-after: always;
- For screen preview, render a PDF-viewer-style canvas: use a neutral gray
  background outside the white sheets and center every `.pdf-page` with
  `margin-left: auto; margin-right: auto`. This prevents the browser's empty
  left/right area from appearing as part of the court form. A small shadow may
  mark the page boundary. All pages must align to the exact same center line.
- Page contents must never be independently centered or rescaled. Use no
  vertical gap between pages unless the uploaded PDF contains that space
  inside a page.
- In `@media print`, remove the gray canvas, shadow, and all outside spacing;
  print only the white PDF-sized pages at 100% scale.
- Before returning the result, compare every generated page against the
  corresponding PDF page and correct page size, scale, x/y alignment,
  overflow, clipping, and page-break errors. Also verify that no element
  overlaps another, that body text never touches the header or footer, and that
  each element's font family, size, and weight match the PDF and are consistent
  across all pages. Fix any difference before responding.
- Reuse shared CSS classes for repeated form styling instead of duplicating
  equivalent CSS on every element. This keeps the response compact without
  reducing visual fidelity or removing any court-form content.

---

### Output rules

- Return ONLY JSON: {{"html": "<!DOCTYPE html>...</html>"}}
- The html value must be a full self-contained HTML document.
- Escape JSON properly (no unescaped newlines inside the JSON string — use \\n).
- Do not wrap the JSON in markdown fences.


"""

# FILLED_HTML_DOCUMENT_PROMPT="""

# Your Role:
# You are an expert Legal Court Form Rendering Engine specializing in accurately filling court PDF forms. You possess expert knowledge of legal document formatting, structured form completion, document rendering, OCR-aware layout preservation, HTML/CSS print rendering, and deterministic field mapping.

# Your responsibility is NOT to redesign, recreate, simplify, modernize, or reinterpret the document.

# Your ONLY responsibility is to generate an HTML document that is a visually faithful representation of the original PDF with user-provided data inserted into the correct fields.

# Accuracy is more important than aesthetics.
# Document fidelity is more important than optimization.
# Never sacrifice layout for convenience.

# ==================================================
# INPUTS
# ==================================================

# You will receive:

# 1. The COMPLETE blank court PDF
#    - filename: {file_name}
#    - page count hint: {pages_hint}

# 2. User data
#    - JSON:
#    {field_data}

# The blank PDF is the single source of truth for:

# • layout
# • spacing
# • typography
# • borders
# • underlines
# • tables
# • columns
# • labels
# • checkboxes
# • signatures
# • page ordering
# • static legal language

# The JSON contains ONLY values that may populate existing blanks.

# ==================================================
# PRIMARY OBJECTIVE
# ==================================================

# Generate ONE complete HTML document that is visually indistinguishable from the original PDF after it has been filled.

# The HTML should represent every page of the PDF while preserving the appearance and structure of the original document.

# The final rendered HTML should closely match the original PDF when printed.

# ==================================================
# ABSOLUTE REQUIREMENTS
# ==================================================

# The original document layout MUST remain unchanged.

# DO NOT:

# • move text
# • resize tables
# • merge cells
# • split tables
# • alter margins
# • change spacing
# • modify page breaks
# • reposition fields
# • remove labels
# • rewrite legal language
# • reflow paragraphs
# • modernize formatting
# • simplify sections
# • change alignment
# • remove borders
# • change indentation
# • alter numbering
# • change headers
# • change footers

# Treat the original PDF as immutable.

# Only fill existing blanks.

# ==================================================
# FIELD MAPPING RULES
# ==================================================

# Field mapping accuracy is the highest priority.

# Before filling any field:

# 1. Determine what the field represents.
# 2. Match it with the correct JSON value.
# 3. Verify semantic meaning.
# 4. Insert only if confidence is high.

# Never populate a field simply because the text appears similar.

# Use contextual clues including:

# • nearby labels
# • surrounding instructions
# • page section
# • legal meaning
# • neighboring fields
# • table headers
# • court terminology

# If multiple JSON fields could match:

# DO NOT GUESS.

# Leave the field blank.

# Incorrect data is worse than missing data.

# ==================================================
# NO HALLUCINATION POLICY
# ==================================================

# Never invent:

# • names
# • addresses
# • dates
# • phone numbers
# • emails
# • case numbers
# • court information
# • attorney information
# • signatures
# • initials
# • counties
# • states
# • zip codes
# • judicial districts
# • monetary values
# • legal statements

# If information is missing:

# Leave the field empty.

# ==================================================
# STATIC CONTENT PROTECTION
# ==================================================

# Every piece of printed legal language already present in the PDF MUST remain identical.

# Do NOT:

# rewrite

# rephrase

# correct grammar

# change punctuation

# change capitalization

# expand abbreviations

# remove duplicated wording

# fix OCR artifacts unless they prevent rendering

# The PDF is the authoritative source.

# ==================================================
# PAGE STRUCTURE
# ==================================================

# Represent every PDF page using:

# <section class="pdf-page" data-page="N">

# ...

# </section>

# Each page must represent US Letter size.

# Width:
# 8.5in

# Height:
# 11in

# Insert proper page breaks.

# ==================================================
# TYPOGRAPHY
# ==================================================

# Preserve typography whenever possible.

# Use fonts matching the original:

# Serif:
# Times New Roman
# Georgia

# Sans:
# Arial
# Helvetica

# Respect:

# font size

# font weight

# italic

# underline

# line spacing

# text alignment

# letter spacing where practical

# ==================================================
# TABLES
# ==================================================

# Every table must remain identical.

# Never:

# resize

# merge cells

# split rows

# change borders

# change alignment

# change padding

# change column widths

# change row heights

# Only insert text into existing cells.

# ==================================================
# UNDERLINES
# ==================================================

# Where the PDF uses underlined blanks:

# Maintain the underline.

# Insert the value on top of the blank without changing its length or placement.

# ==================================================
# CHECKBOXES
# ==================================================

# Render checkboxes faithfully.

# Checked:

# ☑

# Unchecked:

# ☐

# OR use equivalent styled checkbox inputs.

# Only check boxes when the JSON explicitly indicates true.

# Otherwise leave unchecked.

# ==================================================
# RADIO BUTTONS
# ==================================================

# Only one option may be selected.

# Do not infer selections.

# ==================================================
# MULTI-LINE FIELDS
# ==================================================

# For long values:

# Wrap naturally.

# Do NOT overflow outside the field.

# Do NOT overlap nearby content.

# Respect the available space.

# ==================================================
# SIGNATURE FIELDS
# ==================================================

# Never generate signatures.

# Never synthesize handwritten text.

# Only insert signature text if explicitly provided.

# Otherwise leave blank.

# ==================================================
# DATE FIELDS
# ==================================================

# Use exactly the supplied value.

# Never convert formats.

# Never localize.

# Never infer missing dates.

# ==================================================
# EMPTY VALUES
# ==================================================

# If the JSON does not contain a value:

# Leave the blank empty.

# Do not insert:

# N/A

# Unknown

# None

# Null

# --

# or placeholders.

# ==================================================
# CSS REQUIREMENTS
# ==================================================

# Include one self-contained style block.

# The HTML must require no external CSS.

# Include print styling.

# Preserve:

# spacing

# page breaks

# tables

# borders

# fonts

# checkbox rendering

# ==================================================
# HTML REQUIREMENTS
# ==================================================

# Produce a complete HTML document:

# <!DOCTYPE html>

# <html>

# <head>

# <style>

# ...

# </style>

# </head>

# <body>

# ...

# </body>

# </html>

# No external dependencies.

# No JavaScript.

# No CDN.

# ==================================================
# OUTPUT FORMAT
# ==================================================

# Return ONLY valid JSON.

# Exactly this structure:

# {{
#   "html":"<!DOCTYPE html>....</html>"
# }}

# The HTML string must be:

# JSON escaped

# contain escaped newlines (\n)

# contain escaped quotes (\") where required

# contain no markdown

# contain no code fences

# contain no explanations

# contain no comments outside HTML

# ==================================================
# VALIDATION CHECKLIST
# ==================================================

# Before producing the final output, verify internally that:

# ✓ Every PDF page is represented.

# ✓ Every original table exists.

# ✓ Every border exists.

# ✓ Every heading exists.

# ✓ Every footer exists.

# ✓ Every page number is preserved.

# ✓ Every legal paragraph remains unchanged.

# ✓ Every mapped field matches the correct JSON value.

# ✓ No field contains guessed information.

# ✓ Missing data remains blank.

# ✓ Checkboxes accurately reflect boolean values.

# ✓ No content overlaps.

# ✓ No element exceeds page boundaries.

# ✓ Print layout matches US Letter.

# ✓ HTML is syntactically valid.

# ✓ JSON is syntactically valid.

# ==================================================
# NON-NEGOTIABLE PRIORITY ORDER
# ==================================================

# 1. Correct field mapping
# 2. Zero hallucination
# 3. Preserve legal text
# 4. Preserve layout
# 5. Preserve tables
# 6. Preserve typography
# 7. Preserve spacing
# 8. Produce valid HTML
# 9. Produce valid JSON

# If any uncertainty exists regarding a field's mapping, leave it empty rather than risk incorrect legal information.

# This is a legal court document. Accuracy, fidelity, and determinism are mandatory.
# """


FILLED_HTML_PAGE_PROMPT = """
You are an expert PDF-to-HTML document reconstruction agent.

The attached image is page {page_number} of {total_pages} from blank court PDF
"{file_name}". Page size MUST be {page_width_in:.3f}in x {page_height_in:.3f}in
({page_width_pt:.1f}pt x {page_height_pt:.1f}pt).

The source page image is the ONLY visual authority.
Do NOT redesign, modernize, summarize, rewrite, or improve the layout.
Do NOT change static wording or punctuation.
Do NOT remove whitespace or combine sections.

USER DATA (fill blanks only; never invent missing facts):
{field_data}

============================================================
TASK
============================================================
1) Analyze this page image.
2) Identify layout, text, tables, borders, lines, boxes, checkboxes,
   underlines, headers, footers, images, and form blanks.
3) Produce a compact page specification JSON for THIS page only.
4) Put filled user values into fillable blanks that match USER DATA.
5) Leave unmatched blanks empty.

Coordinates use PDF points (72pt = 1in), origin TOP-LEFT of the page.
x,y = top-left of each element. width/height in points.

============================================================
ABSOLUTE BANS
============================================================
- Never explode letter spacing / character-spaced words
- Never invent blue/lavender field backgrounds or modern UI colors
- Never shift clause numbers to mid-page
- Never omit static legal text
- Never return HTML in this step (JSON page spec only)

============================================================
ELEMENT TYPES
============================================================
text      - static printed text (fillable=false) OR typed fill value (fillable=true)
line      - horizontal/vertical rule
box       - rectangle / bordered region (optional fill_color)
checkbox  - square checkbox; checked true/false from USER DATA only
image     - logo/image placeholder (describe, no binary)

For fillable text fields set:
  "fillable": true,
  "label": nearby label,
  "text": filled value or ""

============================================================
OUTPUT (ONLY JSON, no markdown)
============================================================
{{
  "page_number": {page_number},
  "width_pt": {page_width_pt:.1f},
  "height_pt": {page_height_pt:.1f},
  "orientation": "portrait",
  "background": "#ffffff",
  "elements": [
    {{
      "id": "e001",
      "type": "text",
      "x": 36.0,
      "y": 40.0,
      "width": 400.0,
      "height": 12.0,
      "font_family": "Times New Roman",
      "font_size": 11.0,
      "font_weight": "normal",
      "font_style": "normal",
      "line_height": 13.0,
      "letter_spacing": 0,
      "color": "#000000",
      "align": "left",
      "text": "exact words from page or filled value",
      "fillable": false,
      "label": ""
    }},
    {{
      "id": "e002",
      "type": "line",
      "x": 36.0,
      "y": 70.0,
      "width": 540.0,
      "height": 1.0,
      "color": "#000000",
      "thickness": 0.75
    }},
    {{
      "id": "e003",
      "type": "checkbox",
      "x": 400.0,
      "y": 120.0,
      "width": 10.0,
      "height": 10.0,
      "checked": false,
      "label": "District Court",
      "fillable": true
    }},
    {{
      "id": "e004",
      "type": "box",
      "x": 36.0,
      "y": 200.0,
      "width": 540.0,
      "height": 80.0,
      "color": "#000000",
      "thickness": 1.0,
      "fill_color": "transparent"
    }}
  ]
}}

Include EVERY visible text run and form blank on this page.
Prefer more smaller accurate text elements over one giant wrong block.
"""


PAGE_SPEC_TO_HTML_NOTE = """
Deterministic renderer converts page_spec JSON to absolute-position HTML.
"""


FILL_PAGE_OVERLAYS_PROMPT = """
You are a court-form fill engine. The attached image is page {page_number} of
{total_pages} from the ORIGINAL blank court PDF.

Page size: {page_width_in:.3f}in wide × {page_height_in:.3f}in tall.

CRITICAL RULES:
- Do NOT redesign, redraw, or recreate the form.
- The HTML will use this exact page image as the background. Your job is ONLY to
  place filled-in values on top of existing blanks, as if the user typed on the form.
- Copy positions from the image carefully. Alignment must match the blank lines,
  boxes, and checkbox squares on the page.
- Use ONLY colors that belong on a filled paper form: black text (#000000).
  NEVER invent blue/purple field highlights, gray header bars, or new borders.
- Keep static printed text untouched (it is already in the background image).
- Fill only blanks that appear on THIS page and have matching user data.
- Do not invent missing values. Skip blanks with no matching data.
- For checkboxes: set kind="checkbox" and checked=true/false at the exact square.
- Prefer "Times New Roman", Times, serif for typed values unless the form clearly
  uses a different fill font. font_size_pt should match typical typed form text
  for that blank (usually 9–12pt).
- Coordinates are percentages of the page box (0–100). left_pct/top_pct are the
  top-left of the value. width_pct should cover the blank without overflowing
  neighboring fields.

USER DATA:
{field_data}

Return ONLY valid JSON:
{{
  "fills": [
    {{
      "kind": "text",
      "left_pct": 0.0,
      "top_pct": 0.0,
      "width_pct": 0.0,
      "height_pct": 0.0,
      "text": "value",
      "font_size_pt": 11,
      "font_family": "\\"Times New Roman\\", Times, serif",
      "font_weight": "normal",
      "color": "#000000",
      "text_align": "left"
    }},
    {{
      "kind": "checkbox",
      "left_pct": 0.0,
      "top_pct": 0.0,
      "width_pct": 1.5,
      "height_pct": 1.2,
      "checked": true
    }}
  ]
}}
"""


MAP_SLOTS_TO_DATA_PROMPT = """
You map USER DATA onto detected fill SLOTS on one court-form page.

Rules:
- Use ONLY the provided slot_id values. Never invent coordinates.
- Fill a slot only when you are confident the label/context matches the data.
- Incorrect placement is worse than leaving a blank — skip uncertain slots.
- For kind=text or kind=digit: return "value" (digit slots: one character).
- For kind=checkbox: return "checked": true/false (omit value).
- Do not invent facts missing from USER DATA.
- Match nested paths flexibly (petitioner.full_name, children[0].initials, etc.).

PAGE: {page_number} of {total_pages}

SLOTS (JSON):
{slots_json}

USER DATA:
{field_data}

Return ONLY valid JSON:
{{"fills":[{{"slot_id":"p0_t0","value":"Jane Marie Doe"}},{{"slot_id":"p0_c1","checked":true}}]}}
"""


CLASSIFY_PDF_FILLABLE_PROMPT = """
You are a court-form document classifier.

Inspect the attached PDF page image(s) and decide if this form is FILLABLE
or NON_FILLABLE.

---

### Definitions

- FILLABLE means the PDF has interactive form fields / widgets
  (text boxes, checkboxes, dropdowns, signature fields) that software can fill.
- NON_FILLABLE means it is a scanned or flat form with blank lines/underscores
  or static boxes that are not interactive AcroForm widgets.

---

### Output rules

Return ONLY JSON:
{{"classification":"fillable"|"non_fillable","confidence":0.0-1.0,"reason":"short explanation"}}

{acro_hint}
"""


FILLED_FTL_VLM_PROMPT = """
You are a court document generation assistant using vision.

You receive:
1) Page image(s) of a NON-FILLABLE court PDF form
2) Extracted text from the PDF
3) Structured string data (JSON) to fill into the form

---

### Tasks

- Recreate the form content as a FreeMarker (.ftl) document.
- Fill every blank / underscore / empty box using the provided string data.
- Match nested JSON paths flexibly
  (e.g. petitioner.full_name, children[0].full_name, cause_number).
- Preserve court headings, captions, numbered clauses, and layout as text.
- Do not invent facts missing from the data; leave those spots as ${{var!""}} placeholders.

---

### Output rules

- Return ONLY JSON with this shape:
{{
  "ftl_template": "<ftl with ${{placeholders}} before filling>",
  "ftl_content": "<fully filled ftl document>"
}}
- Do not wrap the JSON in markdown fences.

---

### EXTRACTED TEXT

{extracted_text}

---

### STRING DATA

{field_data}
"""


FILL_FTL_CHUNK_PROMPT = """
You are a court document generation assistant.

Fill pages {page_start}-{page_end} of a non-fillable court form.
Use the extracted page text and string data.

---

### Instructions

- Fill blanks/underscores from the JSON data (nested paths OK).
- Do not invent missing facts; leave unmatched blanks as ${{var!""}}.
- Preserve headings and clause structure.
- Do not wrap JSON in markdown.

---

### Output rules

Return ONLY JSON: {{"ftl_content": "<filled FreeMarker text for these pages>"}}

---

### PAGES {page_start}-{page_end} TEXT

{page_text}

---

### STRING DATA

{field_data}
"""


MAP_FIELDS_TO_ACROFORM_PROMPT = """
You are a court form field mapping assistant.

You receive:
1) A list of interactive PDF AcroForm field names extracted from a court PDF.
2) User-provided field data (JSON).

---

### Instructions

- Map each user data field to the most appropriate PDF AcroForm field name.
- Return ONLY a JSON object mapping PDF field names to their string values.
  Example format: {{"PetitionerName[0]": "John Doe", "CaseNo": "2026-DR-001245"}}
- Only include mappings where you are confident the user value corresponds to the PDF field.
- Do not invent new PDF field names; use exact names from the provided PDF field list.

---

### PDF FIELD NAMES

{detected_fields}

---

### USER FIELD DATA

{field_data}
"""


FILL_FTL_WITH_DATA_PROMPT = """
You are a court document generation assistant.

You will receive:
1) A FreeMarker (.ftl) template extracted from a non-fillable court PDF.
2) String data (usually nested JSON) to fill into the template.

---

### Instructions

- Produce the exact filled FreeMarker (.ftl) document.
- Replace placeholders such as ${{field_name}} with the matching values from the data.
- Match nested paths flexibly
  (petitioner.full_name, children[0].full_name, etc.).
- Preserve all static court text, layout, and FreeMarker syntax that is not a blank.
- Do not invent facts that are not in the provided data; leave unmatched
  placeholders as ${{var!""}} if no value exists.

---

### Output rules

- Return ONLY a JSON object with this shape:
  {{"ftl_content": "<full filled ftl document as a string>"}}
- Do not wrap the JSON in markdown fences.

---

### FTL TEMPLATE

{ftl_template}

---

### STRING DATA

{field_data}
"""


ENHANCE_CUSTOM_PROMPT_WITH_FORMAT = """
{custom_prompt}

IMPORTANT: Return a valid JSON object matching this structure:
{custom_output_format}

Return ONLY the JSON object.
"""
