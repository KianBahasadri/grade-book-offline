"""Build and validate release assets from an already captured snapshot."""

import argparse
import hashlib
import html
import json
import os
import posixpath
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from playwright.sync_api import sync_playwright

from scripts.snapshot import SOURCE, soup

ROOT = Path(__file__).resolve().parents[1]
LICENSE_URL = "https://creativecommons.org/licenses/by-nc-nd/4.0/"


def localize_links(fragments):
    """Namespace source anchors, then resolve links across captured chapters."""
    documents, targets = {}, {}
    for slug, fragment in fragments.items():
        document = soup(fragment)
        targets[(slug, "")] = slug
        seen = set()
        for index, item in enumerate(document.select("[id]")):
            old = item["id"]
            new = f"{slug}-{hashlib.sha256(old.encode()).hexdigest()[:12]}"
            if new in seen:
                new += f"-{index}"
            seen.add(new)
            targets.setdefault((slug, old), new)
            item["id"] = new
        documents[slug] = document
    for slug, document in documents.items():
        for link in document.select("a[href]"):
            url = urlsplit(link["href"])
            parts = url.path.strip("/").split("/")
            if url.netloc == urlsplit(SOURCE).netloc and parts[:1] == ["guideline"] and len(parts) >= 2:
                target_slug = parts[1]
                anchor = unquote(url.fragment) or "/".join(parts[2:])
                target = targets.get((target_slug, anchor))
                if target:
                    link["href"] = "#" + target
            elif not url.netloc and not url.path and url.fragment:
                target = targets.get((slug, unquote(url.fragment)))
                if target:
                    link["href"] = "#" + target
    return {slug: str(document) for slug, document in documents.items()}


def document_html(manifest, fragments, about):
    timestamp = html.escape(manifest["captured_at"])
    cover = manifest["cover"]
    toc = []
    for chapter in manifest["chapters"]:
        toc.append(f'<li><a href="#{chapter["id"]}">{html.escape(chapter["title"])}</a>'
                   f' <small>— {html.escape(chapter["group"])}</small></li>')
    sections = []
    for chapter in manifest["chapters"]:
        fragment = soup(fragments[chapter["id"]])
        source_link = soup(
            f'<p class="source-link">Source: <a href="{chapter["url"]}">'
            f'{chapter["url"]}</a></p>').p
        fragment.h1.insert_after(source_link)
        sections.append(
            f'<section class="chapter" id="{chapter["id"]}">'
            f'{fragment}</section>')
    omitted = []
    for item in manifest["excluded"]:
        if item["legacy_url"]:
            status = f'Linked to the <a href="{html.escape(item["legacy_url"], quote=True)}">older handbook</a>'
        else:
            status = "Not published in the current book"
        omitted.append(f'<li>{html.escape(item["title"])}: {status}.</li>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The GRADE Book — offline edition</title>
<link rel="stylesheet" href="book.css"></head><body>
<section class="official-cover" aria-label="Official book cover"><p>
<img id="official-cover" src="{html.escape(cover["file"], quote=True)}"
alt="{html.escape(cover["alt"], quote=True)}" style="width: 5.9in"></p></section>
<section class="frontmatter"><h1>The GRADE Book</h1>
<p>Edited by Ignacio Neumann and Holger J. Schünemann<br>GRADE Working Group</p>
<div class="edition-note"><p>Unofficial offline edition. Source captured {timestamp}.
This edition is not endorsed by the GRADE Working Group or Evidence Prime.</p>
<p>Source: <a href="{SOURCE}/">{SOURCE}/</a>.
Book content: <a href="{LICENSE_URL}">CC BY-NC-ND 4.0</a>.
Original authorship, citations, and notices are retained. Changes are limited to
formatting, navigation, and packaging for offline reading.</p>
<p>Includes {len(manifest["chapters"])} published English chapters, their figures,
tables, references, and the book's About page. External websites and videos
remain links requiring internet access. Entries pointing to the older handbook
and unfinished chapters are listed at the end.</p></div></section>
<nav aria-label="Contents"><h1>Contents</h1><ol>{''.join(toc)}</ol>
<p><a href="#about">About the original book</a> ·
<a href="#coverage">Other catalog entries</a></p></nav>
<section class="about" id="about"><h1>About the original book</h1>{about}</section>
{''.join(sections)}
<section class="coverage" id="coverage"><h1>Other catalog entries</h1>
<p>The following entries are not published chapters of the current book and are
not included in this edition's chapter content.</p><ul>{''.join(omitted)}</ul></section>
</body></html>"""


def validate_html(document, directory):
    parsed = soup(document)
    ids = [item["id"] for item in parsed.select("[id]")]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate offline anchors")
    for link in parsed.select('a[href^="#"]'):
        if unquote(link["href"][1:]) not in ids:
            raise ValueError(f"Broken internal link: {link['href']}")
    for image in parsed.find_all("img"):
        source = image.get("src", "")
        path = (directory / source).resolve()
        if not source or directory.resolve() not in path.parents or not path.is_file():
            raise ValueError(f"Image is not available offline: {source[:100]}")
    if parsed.select("script, iframe, video, audio, object, embed"):
        raise ValueError("Offline HTML contains an active external resource")


def run(*command):
    subprocess.run(command, check=True, timeout=300)


def conversion_html(document, *, keep_contents=False, keep_cover=True):
    """Expose headings to Pandoc's EPUB splitter without losing source anchors."""
    conversion = soup(document)
    if keep_contents:
        conversion.nav.name = "div"
    else:
        conversion.nav.decompose()
    if not keep_cover:
        conversion.select_one(".official-cover").decompose()
    for index, heading in enumerate(conversion.find_all(re.compile(r"^h[1-6]$"))):
        if not heading.has_attr("id"):
            heading["id"] = f"offline-heading-{index}"
    # html-native_divs preserves text/block boundaries and gives the EPUB writer
    # top-level headings. Redirect wrapper anchors to their headings, keeping
    # other anchors as spans. Empty spans inside headings would pollute EPUB nav.
    aliases = {}
    for container in conversion.select("div[id], section[id]"):
        heading = container.find(re.compile(r"^h[1-6]$"))
        if heading:
            aliases[container["id"]] = heading["id"]
        else:
            anchor = conversion.new_tag("span", id=container["id"])
            container.insert_before(anchor)
        del container["id"]
    for link in conversion.select('a[href^="#"]'):
        target = link["href"][1:]
        if target in aliases:
            link["href"] = "#" + aliases[target]
    return conversion


def validate_cover(directory, manifest):
    cover = manifest.get("cover")
    if not cover:
        raise ValueError("Snapshot has no official cover; capture it again before building")
    path = (directory / cover["file"]).resolve()
    if directory.resolve() not in path.parents or not path.is_file():
        raise ValueError("Official cover is not available offline")
    if hashlib.sha256(path.read_bytes()).hexdigest() != cover["content_sha256"]:
        raise ValueError("Official cover does not match the captured original")
    return path


def validate_outputs(output, manifest, expected_words):
    cover_hash = manifest["cover"]["content_sha256"]
    with zipfile.ZipFile(output / "grade-book.epub") as epub:
        if epub.read("mimetype") != b"application/epub+zip":
            raise ValueError("Invalid EPUB mimetype")
        epub_text = " ".join(soup(epub.read(name)).get_text(" ", strip=True)
                             for name in epub.namelist() if name.endswith(".xhtml"))
        for name in epub.namelist():
            if name.endswith(".xhtml"):
                ids = [item["id"] for item in soup(epub.read(name)).select("[id]")]
                if len(ids) != len(set(ids)):
                    raise ValueError(f"EPUB contains duplicate anchors in {name}")
        if not any("/media/" in name for name in epub.namelist()) and any(
            chapter["images"] for chapter in manifest["chapters"]
        ):
            raise ValueError("EPUB has lost all images")
        container = ET.fromstring(epub.read("META-INF/container.xml"))
        opf_name = container.find(".//{*}rootfile").attrib["full-path"]
        opf = ET.fromstring(epub.read(opf_name))
        covers = [item for item in opf.findall(".//{*}manifest/{*}item")
                  if "cover-image" in item.get("properties", "").split()]
        if len(covers) != 1:
            raise ValueError("EPUB does not identify its cover for reading apps")
        cover_name = posixpath.normpath(posixpath.join(
            posixpath.dirname(opf_name), unquote(covers[0].attrib["href"])))
        if hashlib.sha256(epub.read(cover_name)).hexdigest() != cover_hash:
            raise ValueError("EPUB has lost the official cover")
    with zipfile.ZipFile(output / "grade-book.docx") as docx:
        if "word/document.xml" not in docx.namelist() or docx.testzip():
            raise ValueError("Invalid DOCX")
        if not any(hashlib.sha256(docx.read(name)).hexdigest() == cover_hash
                   for name in docx.namelist() if name.startswith("word/media/")):
            raise ValueError("DOCX has lost the official cover")
        body = ET.fromstring(docx.read("word/document.xml")).find(".//{*}body")
        if body[0].find(".//{*}drawing") is None:
            raise ValueError("DOCX does not start with the cover")
    pdf_images = subprocess.check_output(
        ["pdfimages", "-f", "1", "-l", "1", "-list", str(output / "grade-book.pdf")],
        text=True, timeout=60)
    if not re.search(r"^\s*1\s+\d+\s+image\s+", pdf_images, re.MULTILINE):
        raise ValueError("PDF does not start with a cover image")
    pdf_text = subprocess.check_output(
        ["pdftotext", str(output / "grade-book.pdf"), "-"], text=True, timeout=60)
    plain_text = (output / "grade-book.txt").read_text()
    for kind, text in [("PDF", pdf_text), ("EPUB", epub_text), ("TXT", plain_text)]:
        normalized = " ".join(text.split())
        for chapter in manifest["chapters"]:
            if " ".join(chapter["title"].split()) not in normalized:
                raise ValueError(f"{kind} missing chapter: {chapter['title']}")
        if len(normalized.split()) < expected_words * 0.95:
            raise ValueError(f"{kind} has lost a substantial amount of text")
    for path in output.iterdir():
        if path.is_file() and path.stat().st_size == 0:
            raise ValueError(f"Empty release asset: {path.name}")


def build(snapshot, output, browser_path=None):
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    book = output / "html"
    book.mkdir()
    shutil.copytree(snapshot / "assets", book / "assets")
    shutil.copyfile(ROOT / "styles/book.css", book / "book.css")
    manifest = json.loads((snapshot / "manifest.json").read_text())
    cover_path = validate_cover(book, manifest)
    fragments = {chapter["id"]: (snapshot / chapter["file"]).read_text()
                 for chapter in manifest["chapters"]}
    fragments = localize_links(fragments)
    about = soup((snapshot / "about.html").read_text())
    for heading in about.find_all("h1"):
        heading.name = "h2"
    document = document_html(manifest, fragments, str(about))
    validate_html(document, book)
    (book / "index.html").write_text(document, encoding="utf-8")
    shutil.copyfile(ROOT / "CONTENT-LICENSE.md", book / "CONTENT-LICENSE.md")
    manifest["build_commit"] = os.environ.get("GITHUB_SHA", "local")
    manifest["pandoc_version"] = subprocess.check_output(
        ["pandoc", "--version"], text=True).splitlines()[0]
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    shutil.copyfile(output / "manifest.json", book / "manifest.json")

    with sync_playwright() as playwright:
        options = {"headless": True}
        if browser_path:
            options["executable_path"] = browser_path
        browser = playwright.chromium.launch(**options)
        page = browser.new_page()
        # The offline validation/print browser cannot fetch anything over HTTP.
        page.route(re.compile(r"^https?://"), lambda route: route.abort())
        page.goto((book / "index.html").resolve().as_uri(), wait_until="load")
        page.evaluate("document.fonts.ready")
        page.wait_for_function("""() => [...document.images].every(
            image => image.complete && image.naturalWidth > 0)""")
        page.pdf(
            path=str(output / "grade-book.pdf"), prefer_css_page_size=True,
            print_background=True, tagged=True, outline=True,
            display_header_footer=True, header_template="<span></span>",
            footer_template='<div style="font-size:8px;width:100%;text-align:center">'
                            'The GRADE Book · <span class="pageNumber"></span> / '
                            '<span class="totalPages"></span></div>',
        )
        browser.close()

    # Pandoc creates its own navigation; keep the human-readable contents in HTML/PDF.
    conversion = conversion_html(document, keep_cover=False)
    input_file = output / "conversion.html"
    input_file.write_text(str(conversion), encoding="utf-8")
    common = [
        "pandoc", str(input_file), "--from=html-native_divs", f"--resource-path={book.resolve()}",
        "--metadata=title=The GRADE Book", "--metadata=lang=en",
        "--metadata=author=Neumann I; Schünemann H (editors); GRADE Working Group",
        f"--metadata=rights=CC BY-NC-ND 4.0 — {LICENSE_URL}",
    ]
    run(*common, "--to=epub3", "--toc", "--toc-depth=2",
        "--epub-chapter-level=1", "--epub-title-page=false",
        f"--epub-cover-image={cover_path}",
        f"--css={book / 'book.css'}", "-o", str(output / "grade-book.epub"))
    # Keep human-readable contents in Word and put its cover before all metadata.
    input_file.write_text(str(conversion_html(document, keep_contents=True)), encoding="utf-8")
    run(*common, "--to=docx", f"--lua-filter={ROOT / 'scripts/docx-cover.lua'}",
        "-o", str(output / "grade-book.docx"))
    run(*common, "--to=plain", "--wrap=none", "-o", str(output / "grade-book.txt"))
    input_file.unlink()
    with zipfile.ZipFile(output / "grade-book-html.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(book.rglob("*")):
            if path.is_file():
                archive.write(path, Path("grade-book") / path.relative_to(book))
    expected_words = len(conversion.get_text(" ", strip=True).split())
    validate_outputs(output, manifest, expected_words)
    files = sorted(path for path in output.iterdir() if path.is_file())
    checksums = "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                        for path in files)
    (output / "SHA256SUMS").write_text(checksums)
    print(f"Validated {len(manifest['chapters'])} chapters; release assets in {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=Path(".work/snapshot"))
    parser.add_argument("--output", type=Path, default=Path("dist"))
    parser.add_argument("--browser", default=os.environ.get("CHROMIUM_PATH"))
    args = parser.parse_args()
    build(args.snapshot, args.output, args.browser)


if __name__ == "__main__":
    main()
