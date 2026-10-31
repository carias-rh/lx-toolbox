# QA Report DOCX is built for Google Docs

The QA Report is shared as a DOCX opened in Google Docs. We generate AsciiDoc,
convert via DocBook + Pandoc, then tint PASS/FAIL rows in the OpenXML so colours
survive the import. Navigation is the Summary table (hyperlinks to Sections),
not an in-document TOC — Google Docs already has an outline from headings, and
the Summary table is the jump menu. Screenshot titles sit below each image
inside a bordered box because Pandoc's ImageCaption lands between stacked
pictures and is otherwise ambiguous.

## Considered Options

- **HTML-first (CSS)**: reliable colours and a left TOC. Rejected: the share
  path is Google Docs, not a browser.
- **Captions above the image**: clearer in HTML. Rejected: Pandoc writes
  Word `ImageCaption` below the drawing, sandwiching the title between two
  screenshots.
- **Extra in-document TOC**: duplicates the Summary table, which already lists
  every Section and now links to it.
