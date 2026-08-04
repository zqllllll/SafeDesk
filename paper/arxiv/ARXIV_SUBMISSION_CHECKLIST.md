# arXiv Submission Checklist

This checklist follows arXiv's current TeX submission guidance as checked on 2026-07-22.

- [x] The top-level source is `main.tex` in the submission root.
- [x] Source is ASCII-only and PDFLaTeX compatible.
- [x] The paper is compact and single-spaced, not in referee mode.
- [x] The date is fixed; `\today` is not used.
- [x] Figures are included inline through standard TeX packages.
- [x] No shell escape, JavaScript, external document links, or on-the-fly figure conversion is used.
- [x] `references.bib` is included.
- [x] `main.bbl` was produced by the final local build and is included.
- [x] The final local PDF is 10 pages and has been rendered and inspected page by page.
- [x] The LaTeX log contains no overfull/underfull boxes, missing citations, or unresolved references.
- [ ] Replace the anonymous author and affiliation fields.
- [ ] Confirm title, abstract, authors, license, category, and comments in arXiv metadata.
- [ ] Upload only the clean source ZIP, not auxiliary files, logs, or the locally generated PDF.
- [ ] Upload the data ZIP as ancillary material if permitted by the selected category and licenses.
- [ ] Inspect arXiv's generated PDF before completing submission.

Official guidance:

- https://info.arxiv.org/help/submit_tex.html
- https://info.arxiv.org/help/submit_pdf.html
