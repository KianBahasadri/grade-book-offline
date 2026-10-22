# GRADE Book offline editions

[![Check and publish](https://github.com/KianBahasadri/grade-book-offline/actions/workflows/publish.yml/badge.svg)](https://github.com/KianBahasadri/grade-book-offline/actions/workflows/publish.yml)

Unofficial downloads of [The GRADE Book](https://book.gradepro.org/), automatically
checked for changes every Sunday at **06:23 UTC**. A new release is published only
when the source content changes.

**[Visit the download website](https://kianbahasadri.github.io/grade-book-offline/)**

Choose a format, get help opening it, or select an earlier edition. No GitHub
account is needed. Downloads are also available in
[GitHub Releases](https://github.com/KianBahasadri/grade-book-offline/releases/latest).

| Asset | Use |
| --- | --- |
| [EPUB](https://github.com/KianBahasadri/grade-book-offline/releases/latest/download/grade-book.epub) | E-readers and reading apps |
| [PDF](https://github.com/KianBahasadri/grade-book-offline/releases/latest/download/grade-book.pdf) | Printing; searchable text and bookmarks |
| [Offline HTML ZIP](https://github.com/KianBahasadri/grade-book-offline/releases/latest/download/grade-book-html.zip) | Extract and open `grade-book/index.html`; no server or connection needed |
| [DOCX](https://github.com/KianBahasadri/grade-book-offline/releases/latest/download/grade-book.docx) | Word and compatible applications |
| [TXT](https://github.com/KianBahasadri/grade-book-offline/releases/latest/download/grade-book.txt) | Text only, including figure descriptions |
| [Manifest](https://github.com/KianBahasadri/grade-book-offline/releases/latest/download/manifest.json) | Source URLs, capture date, chapter inventory and fingerprints |
| [SHA256SUMS](https://github.com/KianBahasadri/grade-book-offline/releases/latest/download/SHA256SUMS) | Verify downloaded files |

## Coverage

The editions contain the **published English chapters** in the order shown on
the book's homepage, plus its About page. Chapter authors, figures, tables,
rendered citations, bibliographies, and additional-resource links are retained.
PDF, EPUB, DOCX, and HTML begin with the original official cover and include local
copies of the figures. The EPUB also identifies the cover for e-reader libraries.

Unfinished catalog entries and entries linking to the older GRADE handbook are
listed separately; their content is not included. External websites, videos,
and interactive services remain online links.

## When a release is warranted

The weekly job renders the source with Chromium and checks the public chapter
API against the rendered content. It compares a normalized content fingerprint
with the manifest attached to the **last successfully published release**.

Text, the official cover, figures (including replacements at the same URL), references, author
attribution, chapter ordering, and catalog changes trigger publication.
Capture timestamps, upstream “Last modified” dates, editor IDs, and presentation
styling do not. A failed check or conversion never advances the baseline.
An unchanged source skips all document conversion and release uploads.

The workflow runs tests, checks chapter completeness, loads the finished HTML
with network access blocked, verifies figures and text in the converted files,
and validates EPUB with EPUBCheck. It uploads all assets to a draft release
before publishing it as the latest edition.

GitHub can disable scheduled workflows after 60 days of repository inactivity.
After successful checks, a small heartbeat commit is made at most once every
28 days. This creates repository activity without publishing an edition.

## Manual runs

Open **Actions → Check and publish offline editions → Run workflow**.
Leave `force` off to check for changes; enable it to rebuild unchanged content,
for example after improving formatting or upgrading a converter.

```bash
gh workflow run publish.yml
gh workflow run publish.yml -f force=true
```

Checks also run after changes to the build code on `main`. Pull requests run
unit tests only and cannot publish. The standard Ubuntu runners are used;
the workflow needs no paid services or custom secrets. Only the publication
job receives `contents: write`.

## Download website

The [GitHub Pages website](https://kianbahasadri.github.io/grade-book-offline/)
is built from complete published releases. Drag the 3D book with your mouse to
rotate it, or use the arrow keys while its cover is focused. Scroll over the
book to zoom in or out; the +/− keys also zoom, and 0 restores its original size.
The title, dropdowns, and open/close button go behind the book as it expands.
Click to open it and choose a download. The model's text and images cannot be
selected while dragging. “Other editions” and “About & help” expand smoothly as
plain text beneath the title. Edition search includes dates, release IDs, and
release notes, plus date filters and newest/oldest sorting.
The layout adapts to phones, supports keyboard navigation and reduced motion,
and provides light and dark themes. Downloads remain visible without JavaScript.
Each edition has a permanent page with links to its own files.

`site/` holds the HTML template, styles, book interaction, and locally hosted font.
`scripts/site.py` generates the website using the GitHub CLI to read release
metadata. It takes the cover from the latest release's verified HTML archive.
When that cover matches the recorded publisher preview, the website uses a
3× render of the publisher's original HTML artwork. A local WebGL 2 renderer
draws the closed book with rounded boards, multisampled edges, extra resolution
for the current display, and mipmapped, anisotropic cover filtering. It redraws
during interaction and stops when the book is still. The CSS model handles the
opening animation and provides a fallback when WebGL is unavailable. Once open,
the spread leaves the 3D layers so live text and SVG icons render directly at
the selected zoom. The high-resolution cover's source and checksums
are recorded in `site/cover-render.json`; a changed publisher cover falls back
to the new release image until its high-resolution render is refreshed.
To refresh the artwork, run `python -m scripts.render_cover` (optionally with
`--browser /path/to/chromium`) and review the generated image and metadata.
Preview it locally with:

```bash
python -m scripts.site
python -m http.server 8000 --directory .work/site
```

The Pages workflow deploys when website files change, or when manually run:

```bash
gh workflow run pages.yml
```

After publishing a new book edition, the book workflow calls the Pages workflow
directly. This is necessary because releases created with `GITHUB_TOKEN` do not
trigger another workflow through a `release` event. A weekly check with no book
changes skips the website deployment too.

Pages must be enabled with **GitHub Actions** as its publishing source. Only
the website deployment job receives `pages: write` and `id-token: write`;
no additional secrets are needed. If an edition is removed or manually edited
on GitHub, rerun `pages.yml` to refresh the website.

## Build locally

Requires Python 3.12+, Pandoc, Poppler (`pdftotext`), and Chromium's system
dependencies. On Ubuntu:

```bash
sudo apt-get install pandoc poppler-utils fonts-dejavu-core epubcheck
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r requirements.txt
python -m playwright install --with-deps chromium
python -m unittest discover -s tests -v
python -m scripts.snapshot
python -m scripts.build
epubcheck dist/grade-book.epub
```

Snapshots go to `.work/snapshot/`; downloads go to `dist/`. To compare with an
earlier edition, pass `--previous path/to/manifest.json` to `scripts.snapshot`.
For an installed Chromium, pass `--browser /path/to/chromium` to both commands.
Use empty directories for new captures and builds (`--output .work/snapshot-new`
and `--output dist-new`, respectively).

Dependencies are pinned with hashes. To update them, edit `requirements.in`
and regenerate with `uv pip compile requirements.in --generate-hashes -o requirements.txt`.

## License and attribution

The GRADE Book’s text, figures, tables, and cover are
**[CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/)**,
as stated on its [About page](https://book.gradepro.org/about): unchanged copies,
with attribution, for noncommercial use. Cited works keep their own terms.
These are noncommercial format conversions with original notices and attribution retained.
They are not endorsed by the GRADE Working Group or Evidence Prime.
See [CONTENT-LICENSE.md](CONTENT-LICENSE.md) for the source's requested citation.

The original automation code and styles use the [MIT license](LICENSE).
That license does not apply to the book. IBM Plex Sans is OFL
(`site/FONT-LICENSE.txt`). The icons are Lucide (`site/LUCIDE-LICENSE.txt`).
