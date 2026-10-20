"""Capture published chapters through the site's own renderer and public API."""

import argparse
import base64
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote_to_bytes, urljoin, urlsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup, Comment
from playwright.sync_api import sync_playwright

SOURCE = "https://book.gradepro.org"
REPOSITORY = "https://github.com/KianBahasadri/grade-book-offline"
SCHEMA = 1


def digest(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()


def soup(html):
    return BeautifulSoup(html, "html.parser")


def semantic_html(html):
    """Ignore editor IDs, wrappers, styling and upstream timestamp-only edits."""
    document = soup(html)
    for item in document.select("[data-upstream-timestamp]"):
        item.decompose()
    for item in document.find_all(string=lambda s: isinstance(s, Comment)):
        item.extract()
    for item in document.find_all(True):
        item.attrs = {
            key: value for key, value in item.attrs.items()
            if key in {"href", "src", "alt", "colspan", "rowspan", "start", "type"}
        }
    for item in document.find_all(["div", "span"]):
        item.unwrap()
    return re.sub(r"\s+", " ", str(document)).strip()


def download_image(url):
    if url.startswith("data:"):
        header, payload = url.split(",", 1)
        mime = header[5:].split(";")[0]
        data = base64.b64decode(payload) if ";base64" in header else unquote_to_bytes(payload)
    else:
        if urlsplit(url).scheme not in {"https", "http"}:
            raise ValueError(f"Unsupported image URL: {url[:100]}")
        for attempt in range(3):
            try:
                req = Request(url, headers={"User-Agent": f"GRADE-Book-Offline/1.0 ({REPOSITORY})"})
                with urlopen(req, timeout=60) as response:
                    mime = response.headers.get_content_type()
                    data = response.read(32 * 1024 * 1024 + 1)
                break
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)
    extensions = {
        "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
        "image/svg+xml": "svg", "image/webp": "webp", "image/avif": "avif",
    }
    if mime not in extensions or not data or len(data) > 32 * 1024 * 1024:
        raise ValueError(f"Invalid or oversized image: {mime}, {len(data)} bytes")
    return data, extensions[mime]


def clean_fragment(html, page_url, directory, image_cache):
    document = soup(html)
    for item in document.select("script, button, .Toastify, .breadcrumbs, .ProseMirror-separator"):
        item.decompose()
    if document.select("iframe, video, audio, object, embed"):
        raise ValueError(f"Unsupported embedded media in {page_url}; review before publishing")
    for item in document.find_all(string=lambda s: isinstance(s, Comment)):
        item.extract()
    for image in document.find_all("img"):
        source = image.get("src")
        if not source:
            raise ValueError(f"Content image has no source in {page_url}")
        source = urljoin(page_url, source)
        if source not in image_cache:
            data, extension = download_image(source)
            name = f"assets/{hashlib.sha256(data).hexdigest()}.{extension}"
            (directory / name).write_bytes(data)
            image_cache[source] = name
        image["src"] = image_cache[source]
        image["alt"] = image.get("alt", "")
    # Mark the display date so it is retained in downloads but not the digest.
    for label in document.find_all("b"):
        if label.get_text(strip=True) == "Last modified:":
            label.parent["data-upstream-timestamp"] = ""
    for item in document.find_all(True):
        for key in list(item.attrs):
            if key.startswith("on") or key.startswith("data-") and key != "data-upstream-timestamp":
                del item[key]
            elif key in {"srcset", "sizes", "loading", "contenteditable", "draggable",
                         "tabindex", "role", "target", "translate"}:
                del item[key]
        if "url(" in item.get("style", "").lower():
            raise ValueError(f"CSS image requires explicit capture in {page_url}")
        if item.has_attr("href"):
            href = item["href"]
            if urlsplit(href).scheme.lower() in {"javascript", "data", "file"}:
                del item["href"]
            else:
                item["href"] = urljoin(page_url, href)
    return str(document)


def capture_cover(about_html, directory, image_cache):
    """Keep the official cover before interactive About-page controls are removed."""
    images = soup(about_html).select(".about-cover-trigger img")
    if len(images) != 1 or not images[0].get("src"):
        raise ValueError("The official book cover is missing or ambiguous")
    image = images[0]
    source = urljoin(SOURCE + "/about", image["src"])
    fragment = clean_fragment(str(image), SOURCE + "/about", directory, image_cache)
    filename = soup(fragment).img["src"]
    return {
        "file": filename, "source_url": source,
        "content_sha256": hashlib.sha256((directory / filename).read_bytes()).hexdigest(),
        "alt": image.get("alt") or "Official cover of The GRADE Book",
    }


def content_fingerprint(about, cover, chapters, excluded):
    # Use cover bytes and description, not the asset URL's deployment hash.
    return digest({
        "schema": SCHEMA, "about": semantic_html(about),
        "cover": {key: cover[key] for key in ("content_sha256", "alt")},
        "chapters": [{key: chapter[key] for key in ("id", "title", "group", "content_sha256")}
                     for chapter in chapters],
        "excluded": excluded,
    })


def validate_catalog(catalog, ordered):
    if not isinstance(catalog.get("guidelines"), list) or not ordered:
        raise ValueError("Missing or empty chapter catalog")
    expected = {item["id"] for item in catalog["guidelines"] if item["version"] == "final"}
    actual = [item["id"] for item in ordered]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError(f"Published catalog mismatch: missing={expected - set(actual)}, "
                         f"unexpected={set(actual) - expected}")


def published_sections(data):
    """Follow the same tree as the site; the API can also contain orphan drafts."""
    by_key = {item["id"].rsplit("/", 1)[-1]: item for item in data["chapters"]}
    if len(by_key) != len(data["chapters"]):
        raise ValueError("Ambiguous section identifiers in source API")
    ordered = []
    visited = set()

    def visit(key):
        if key in visited or key not in by_key:
            raise ValueError(f"Invalid published section tree: {key}")
        visited.add(key)
        chapter = by_key[key]
        ordered.append(chapter)
        for child in chapter.get("subchapters", []):
            visit(child)

    for key in data["guideline"]["chapters"]:
        visit(key)
    return ordered


def validate_chapter(data, html):
    document = soup(html)
    if data["guideline"]["version"] != "final":
        raise ValueError("Chapter ceased to be published during capture")
    headings = document.select(".chapter-full")
    published = published_sections(data)
    if len(headings) != len(published) or not headings:
        raise ValueError("Rendered chapter is incomplete")
    expected = sorted(item["title"].strip() for item in published)
    actual = sorted(item.get_text(strip=True) for item in document.select(".chapter-full__title"))
    if expected != actual:
        raise ValueError("Rendered section titles do not match the public API")
    if len(document.get_text(strip=True)) < 200:
        raise ValueError("Chapter is unexpectedly empty")
    if "CSL STYLE ERROR" in document.get_text():
        raise ValueError("Upstream citation rendering failed")
    # Test body content as well as headings, excluding citation editor markup.
    for reference in document.select(".reference, .inserted-references"):
        reference.decompose()
    rendered = " ".join(document.get_text(" ", strip=True).split())
    sections = {item["id"]: item for item in data["sections"]}
    for chapter in published:
        for section_id in chapter["sections"]:
            section = sections[section_id]
            expected_body = soup(section.get("htmlContent") or "")
            for reference in expected_body.select(".reference, .inserted-references"):
                reference.decompose()
            text = " ".join(expected_body.get_text(" ", strip=True).split())
            if text and text not in rendered:
                raise ValueError(f"Rendered section body is incomplete: {section_id}")


def navigate(page, url, api_path=None):
    for attempt in range(3):
        try:
            if api_path:
                with page.expect_response(
                    lambda response: urlsplit(response.url).path == api_path,
                    timeout=60000,
                ) as response:
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                if response.value.status != 200:
                    raise ValueError(f"Source API returned {response.value.status}: {api_path}")
                return response.value.json()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            return None
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def capture(directory, browser_path=None):
    if directory.exists() and any(directory.iterdir()):
        raise ValueError(f"Snapshot directory must be empty: {directory}")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "assets").mkdir(exist_ok=True)
    (directory / "chapters").mkdir(exist_ok=True)
    images = {}
    with sync_playwright() as playwright:
        options = {"headless": True}
        if browser_path:
            options["executable_path"] = browser_path
        browser = playwright.chromium.launch(**options)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1080}, locale="en-US",
            timezone_id="UTC", reduced_motion="reduce",
        )
        context.route(re.compile(r".*(googletagmanager|google-analytics)\.com/.*"),
                      lambda route: route.abort())
        page = context.new_page()
        catalog = navigate(page, SOURCE + "/?lng=en", "/api/guidelines")
        listing = page.locator(".gradebook-chapters-list").first
        listing.locator(".guideline-item").first.wait_for()
        listing.locator(".expandable.collapsed").evaluate_all(
            "(items) => items.forEach(item => item.click())")
        listing.locator('a[href^="/guideline/"]').first.wait_for(state="attached")
        ordered = listing.locator(".guideline-item").evaluate_all("""groups =>
            groups.flatMap(group => [...group.querySelectorAll('a[href^="/guideline/"]')]
                .map(a => ({id: a.getAttribute('href').split('/').pop(),
                    title: a.textContent.trim(),
                    group: group.querySelector('h2').textContent.trim()})))""")
        validate_catalog(catalog, ordered)

        navigate(page, SOURCE + "/about?lng=en")
        page.locator(".about-content").wait_for()
        about_html = page.locator(".about-content").inner_html()
        cover = capture_cover(about_html, directory, images)
        about = clean_fragment(about_html,
                               SOURCE + "/about", directory, images)
        if "BY-NC-ND" not in soup(about).get_text():
            raise ValueError("Upstream license notice changed; review before publishing")
        (directory / "about.html").write_text(about, encoding="utf-8")
        chapters = []
        for number, chapter in enumerate(ordered, 1):
            slug = chapter["id"]
            if not re.fullmatch(r"[a-z0-9-]+", slug):
                raise ValueError(f"Unexpected chapter identifier: {slug}")
            print(f"[{number}/{len(ordered)}] {chapter['title']}", flush=True)
            url = SOURCE + "/guideline/" + slug
            data = navigate(page, url + "?lng=en", f"/api/guidelines/{slug}/_bulk")
            page.locator("#scroll-container .chapter-full").first.wait_for()
            html = page.locator("#scroll-container").inner_html()
            validate_chapter(data, html)
            references = data["guideline"].get("references", [])
            if references:
                page.get_by_role("button", name="References", exact=True).click()
                page.wait_for_function("""count =>
                    document.querySelectorAll('.sidebar-references__list .references-list__reference')
                        .length === count""", arg=len(references))
                html += '<h2>References</h2>' + page.locator(".sidebar-references__list").inner_html()
            if data["guideline"].get("linksAndVideos"):
                page.get_by_role("button", name="Additional resources", exact=True).click()
                page.locator(".additional-resources").wait_for()
                html += '<h2>Additional resources</h2>' + page.locator(".additional-resources").inner_html()
            html = clean_fragment(html, url, directory, images)
            name = f"chapters/{slug}.html"
            (directory / name).write_text(html, encoding="utf-8")
            chapters.append({
                **chapter, "url": url, "file": name,
                "content_sha256": digest(semantic_html(html)),
                "source_modified": data["guideline"].get("lastModified"),
                "sections": len(published_sections(data)), "references": len(references),
                "images": len(soup(html).find_all("img")),
            })
            time.sleep(0.25)
        browser.close()
    excluded = sorted(
        [{"id": item["id"], "title": item["title"], "version": item["version"],
          "legacy_url": item.get("oldGradebookLink") or None}
         for item in catalog["guidelines"] if item["version"] != "final"],
        key=lambda item: item["id"],
    )
    # Download bytes are in the image filenames; replacing an image at the same
    # URL therefore changes the fingerprint. Display dates and build time do not.
    manifest = {
        "schema": SCHEMA, "source": SOURCE, "language": "en",
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "content_sha256": content_fingerprint(about, cover, chapters, excluded),
        "about_sha256": digest(semantic_html(about)),
        "cover": cover, "chapters": chapters, "excluded": excluded,
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def changed(current, previous, force=False):
    if previous is not None:
        if (previous.get("schema") != SCHEMA
                or not re.fullmatch(r"[0-9a-f]{64}", previous.get("content_sha256", ""))
                or not previous.get("chapters")):
            raise ValueError("Invalid previous release manifest; refusing to assume a change")
    return force or previous is None or current["content_sha256"] != previous["content_sha256"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".work/snapshot"))
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--browser", default=os.environ.get("CHROMIUM_PATH"))
    args = parser.parse_args()
    previous = json.loads(args.previous.read_text()) if args.previous else None
    current = capture(args.output, args.browser)
    needs_release = changed(current, previous, args.force)
    print(f"Content SHA-256: {current['content_sha256']}\nRelease needed: {needs_release}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write(f"changed={str(needs_release).lower()}\n")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(f"Checked {len(current['chapters'])} published chapters. "
                          f"**{'New edition needed' if needs_release else 'No content changes'}**.\n\n"
                          f"Content SHA-256: `{current['content_sha256']}`\n")


if __name__ == "__main__":
    main()
