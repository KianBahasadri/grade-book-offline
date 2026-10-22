"""Build a static download library from complete published GitHub releases."""

import argparse
import hashlib
import html
import io
import json
import re
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
REPO = "KianBahasadri/grade-book-offline"
BASE_URL = "https://kianbahasadri.github.io/grade-book-offline/"
FORMATS = [
    ("pdf", "PDF", "For most devices & printing", "Download PDF"),
    ("epub", "EPUB", "For e-readers & reading apps", "Download EPUB"),
    ("html", "Offline website", "Unzip, then open in your browser", "Download offline website"),
    ("docx", "Word", "For Word & compatible apps", "Download Word document"),
    ("txt", "Plain text", "Just the words, in a small file", "Download plain text"),
]
ASSETS = {
    "pdf": "grade-book.pdf", "epub": "grade-book.epub", "html": "grade-book-html.zip",
    "docx": "grade-book.docx", "txt": "grade-book.txt",
}
MIME_TYPES = {
    "pdf": "application/pdf",
    "epub": "application/epub+zip",
    "html": "application/zip",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
}
DOWNLOAD_ICON = '<svg aria-hidden="true" focusable="false"><use href="#icon-download"></use></svg>'


def github_json(path):
    return json.loads(subprocess.check_output(
        ["gh", "api", path], text=True, timeout=90))


def load_releases(repo):
    releases = []
    page = 1
    while True:
        batch = github_json(f"repos/{repo}/releases?per_page=100&page={page}")
        releases.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    latest = github_json(f"repos/{repo}/releases/latest")
    return releases, latest["tag_name"]


def editions_from_releases(releases, latest_tag, repo):
    editions = []
    for release in releases:
        if release["draft"] or release["prerelease"]:
            continue
        tag = release["tag_name"]
        if not re.fullmatch(r"edition-[a-zA-Z0-9.-]+", tag):
            continue
        assets = {item["name"]: item for item in release["assets"] if item["state"] == "uploaded"}
        required = set(ASSETS.values()) | {"manifest.json", "SHA256SUMS"}
        if not required <= assets.keys():
            if tag == latest_tag:
                raise ValueError("The latest edition is missing required downloads")
            continue
        for name in required:
            asset = assets[name]
            prefix = f"https://github.com/{repo}/releases/download/{quote(tag, safe='')}/"
            if (asset["size"] <= 0 or not asset["browser_download_url"].startswith(prefix)
                    or urlsplit(asset["browser_download_url"]).scheme != "https"):
                raise ValueError(f"Invalid release download: {tag}/{name}")
        published = datetime.fromisoformat(release["published_at"].replace("Z", "+00:00"))
        match = re.match(r"edition-(\d{4}-\d{2}-\d{2})-", tag)
        if not match:
            raise ValueError(f"Missing source capture date in {tag}")
        captured = datetime.fromisoformat(match[1])
        label = f"{captured.day} {captured.strftime('%B %Y')}"
        editions.append({
            "tag": tag, "date": label, "label": label, "capture_date": captured.date().isoformat(),
            "timestamp": published.isoformat(), "title": release.get("name") or "The GRADE Book",
            "notes": release.get("body") or "No release notes were provided.",
            "latest": tag == latest_tag,
            "assets": {key: {
                "url": assets[name]["browser_download_url"], "size": assets[name]["size"],
            } for key, name in ASSETS.items()},
            "checksums": assets["SHA256SUMS"]["browser_download_url"],
        })
    if not any(item["latest"] for item in editions):
        raise ValueError("No complete latest book edition found; keeping existing site")
    editions.sort(key=lambda item: (item["latest"], item["timestamp"]), reverse=True)
    return editions


def size_label(size):
    if size < 1_000_000:
        return f"{round(size / 1000)} KB"
    return f"{size / 1_000_000:.1f} MB"


def edition_path(edition, prefix):
    return prefix + (f"editions/{edition['tag']}/index.html" if not edition["latest"] else "index.html")


def readable_notes(notes):
    """Show release prose without Markdown syntax or interpreting embedded HTML."""
    notes = re.sub(r"!?\[([^\]]+)\]\([^)]+\)", r"\1", notes)
    notes = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda match: match[1] or match[2], notes)
    notes = re.sub(r"`([^`]+)`", r"\1", notes)
    return re.sub(r"^#{1,6}\s+", "", notes, flags=re.MULTILINE)


# Repeated on every historical release. The edition row already shows the date,
# and the release id is shown beside these notes.
_REPEATED_NOTE = (
    re.compile(r"Unofficial offline edition of The GRADE Book\.?"),
    re.compile(r"Captured .+; includes \d+ published English chapters\.?"),
    re.compile(r"Download grade-book\.epub .+"),
    re.compile(r"grade-book-html\.zip .+"),
    re.compile(r"grade-book\.docx .+"),
    re.compile(r"(?:The official cover and figures|Figures) are embedded in EPUB.+"),
    re.compile(r"Entries linking to the older handbook.+"),
    re.compile(r"External websites and videos remain online links\.?"),
    re.compile(r"Book content remains .+"),
    re.compile(r"Original authorship and notices are retained.+"),
    re.compile(r"Content SHA-256: .+"),
    re.compile(r"See manifest\.json for provenance.+"),
)
_NOTE_REWRITES = {
    "The official book cover was added or updated in every illustrated format.": "Cover updated.",
    "The official book cover was added or updated.": "Cover updated.",
    "Manual rebuild of unchanged source content.": "Rebuild of unchanged content.",
    "The book's catalog, ordering, attribution, or About page changed.": "Catalog, ordering, attribution, or About page changed.",
}


def brief_notes(notes):
    """Keep only what distinguishes this edition from the others."""
    text = readable_notes(notes).strip()
    kept = []
    label = None
    items = []

    def flush():
        nonlocal label, items
        if label:
            kept.append(f"{label} " + "; ".join(items) if items else label.rstrip(":"))
        label = None
        items = []

    for line in text.splitlines():
        line = _NOTE_REWRITES.get(line.strip(), line.strip())
        if not line or any(pattern.fullmatch(line) for pattern in _REPEATED_NOTE):
            continue
        if re.fullmatch(r"(?:Added|Updated|Removed):", line):
            flush()
            label = line
        elif line.startswith("- ") and label:
            items.append(line[2:].strip())
        else:
            flush()
            kept.append(line)
    flush()
    if kept == ["First published edition."]:
        chapters = re.search(r"includes (\d+) published English chapters", text)
        if chapters:
            kept = [f"First published edition. {chapters.group(1)} English chapters."]
    if kept:
        return "\n".join(kept)
    chapters = re.search(r"includes (\d+) published English chapters", text)
    if chapters:
        return f"{chapters.group(1)} English chapters."
    return "No release notes were provided."


def structured_data(selected, canonical, cover_url, repo):
    data = {
        "@context": "https://schema.org",
        "@type": "Book",
        "name": "The GRADE Book",
        "alternateName": [
            "Grading of Recommendations, Assessment, Development and Evaluation",
            "GRADE Book",
            "GRADE Handbook",
            "GRADEpro Book",
        ],
        "description": (
            "Offline editions of The GRADE Book (Grading of Recommendations, Assessment, "
            "Development and Evaluation) in PDF, EPUB, offline HTML website, Word (DOCX), "
            "and plain text formats."
        ),
        "url": canonical,
        "sameAs": "https://book.gradepro.org/",
        "isBasedOn": "https://book.gradepro.org/",
        "inLanguage": "en",
        "isAccessibleForFree": True,
        "license": "https://creativecommons.org/licenses/by-nc-nd/4.0/",
        "bookEdition": selected["label"],
        "datePublished": selected["capture_date"],
        "dateModified": selected["timestamp"],
        "image": cover_url,
        "author": {
            "@type": "Organization",
            "name": "GRADE Working Group",
            "url": "https://www.gradeworkinggroup.org/",
        },
        "editor": [
            {"@type": "Person", "name": "Ignacio Neumann"},
            {"@type": "Person", "name": "Holger J. Schünemann"},
        ],
        "maintainer": {
            "@type": "Person",
            "name": "Kian Bahasadri",
            "url": "https://www.linkedin.com/in/kian-bahasadri/",
            "sameAs": [f"https://github.com/{repo}"],
        },
        "encoding": [
            {
                "@type": "MediaObject",
                "name": f"The GRADE Book ({title})",
                "description": description,
                "encodingFormat": MIME_TYPES[kind],
                "contentUrl": selected["assets"][kind]["url"],
                "contentSize": size_label(selected["assets"][kind]["size"]),
            }
            for kind, title, description, _ in FORMATS
        ],
    }
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def render_page(editions, selected, prefix, canonical, repo, cover_file,
                cover_size=(866, 1174), cover_flat=False, base_url=BASE_URL):
    escape = html.escape
    links = []
    for kind, title, description, action in FORMATS:
        asset = selected["assets"][kind]
        extension = escape(Path(ASSETS[kind]).suffix)
        links.append(
            f'<a class="download-link" data-format="{kind}" aria-label="{action} ({extension})" '
            f'href="{escape(asset["url"], quote=True)}"><span><span class="format-name">'
            f'{title}<span class="file-size">{size_label(asset["size"])}<code>{extension}</code></span></span>'
            f'<span class="format-hint">{escape(description)}</span></span>{DOWNLOAD_ICON}</a>')
    archive = []
    for edition in editions:
        current = edition["tag"] == selected["tag"]
        badges = ('<span class="edition-badge">Latest</span>' if edition["latest"] else "")
        if current:
            badges += '<span class="edition-badge">Viewing</span>'
        captured = datetime.fromisoformat(edition["capture_date"])
        date_aliases = " ".join(captured.strftime(pattern) for pattern in
                                ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%b %d %Y"))
        notes = readable_notes(edition["notes"])
        summary = brief_notes(edition["notes"])
        search = " ".join([edition["label"], date_aliases, edition["title"], edition["tag"],
                           notes, edition["notes"], "latest" if edition["latest"] else "earlier",
                           "viewing current" if current else ""])
        archive.append(
            f'<li data-search="{escape(search, quote=True)}" '
            f'data-date="{edition["capture_date"]}" data-published="{edition["timestamp"]}">'
            f'<div class="edition-row"><a class="edition-link" '
            f'href="{escape(edition_path(edition, prefix), quote=True)}#downloads">'
            f'{escape(edition["label"])}</a><div class="edition-badges">{badges}</div></div>'
            f'<details class="release-details"><summary>Release details '
            '<svg aria-hidden="true" focusable="false"><use href="#icon-chevron"></use></svg>'
            f'</summary><pre class="release-block"><span class="release-id">{escape(edition["tag"])}</span>'
            f'{escape(summary)}</pre></details></li>')
    notice = "" if selected["latest"] else (
        '<p class="edition-notice">You’re viewing an earlier edition.'
        f' <a href="{prefix}index.html#downloads">Get the latest edition</a>.</p>')
    cover_url = base_url.rstrip("/") + "/" + cover_file
    is_root = prefix == "./"
    description = (
        "Download The GRADE Book (Grading of Recommendations, Assessment, Development and Evaluation) "
        "for offline reading. Free PDF, EPUB, offline website, Word (DOCX), and plain text editions "
        "of the GRADE handbook."
        if selected["latest"] else
        f"Download the {selected['label']} offline edition of The GRADE Book in PDF, EPUB, "
        "offline website, Word (DOCX), and plain text formats."
    )
    values = {
        "PREFIX": prefix, "CANONICAL": escape(canonical, quote=True),
        "ASSET_VERSION": hashlib.sha256(b"".join((ROOT / "site" / name).read_bytes()
            for name in ("style.css", "book-renderer.js", "site.js", "favicon.svg"))).hexdigest()[:12],
        "PAGE_TITLE": "The GRADE Book — Offline Download (PDF, EPUB, HTML, Word)" if selected["latest"]
        else f"The GRADE Book — {selected['label']} edition (Offline Download)",
        "META_DESCRIPTION": escape(description, quote=True),
        "ROBOTS": "index, follow" if is_root else "noindex, follow",
        "COVER_URL": escape(cover_url, quote=True),
        "STRUCTURED_DATA": structured_data(selected, canonical, cover_url, repo),
        "EDITION_STATUS": "Latest edition" if selected["latest"] else "Earlier edition",
        "EDITION_DATE": escape(selected["label"]), "COVER_FILE": cover_file,
        "COVER_WIDTH": str(cover_size[0]), "COVER_HEIGHT": str(cover_size[1]),
        "COVER_CLASS": "cover-flat" if cover_flat else "cover-preview",
        "EDITION_NOTICE": notice, "FORMAT_LINKS": "".join(links), "ARCHIVE_LINKS": "".join(archive),
        "EDITION_COUNT": f"{len(editions)} edition{'s' if len(editions) != 1 else ''}",
        "COVERAGE": f"This edition contains the published English chapters of The GRADE Book "
        f"as captured on {escape(selected['date'])}, along with the book’s About page.",
        "REPOSITORY_URL": f"https://github.com/{repo}",
        "CHECKSUM_URL": escape(selected["checksums"], quote=True),
    }
    template = (ROOT / "site/index.html").read_text()
    result = re.sub(r"\{\{([A-Z_]+)\}\}", lambda match: values[match[1]], template)
    if "{{" in result:
        raise ValueError("Unresolved page template value")
    return result


def published_cover(edition):
    """Use the release's verified original cover, keeping future site covers in sync."""
    with urlopen(edition["assets"]["html"]["url"], timeout=90) as response:
        data = response.read(64 * 1024 * 1024 + 1)
    if len(data) > 64 * 1024 * 1024:
        raise ValueError("HTML archive exceeds the website cover download limit")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        manifest = json.loads(archive.read("grade-book/manifest.json"))
        cover = manifest.get("cover")
        if not cover:
            # The first historical edition predates cover capture.
            return "official-cover.png", (ROOT / "site/official-cover.png").read_bytes()
        suffix = Path(cover["file"]).suffix
        if suffix not in {".png", ".jpg", ".webp", ".avif"}:
            raise ValueError("Unsupported website cover format")
        name = "grade-book/" + cover["file"]
        if archive.getinfo(name).file_size > 32 * 1024 * 1024:
            raise ValueError("Official cover exceeds the image limit")
        image = archive.read(name)
        if hashlib.sha256(image).hexdigest() != cover["content_sha256"]:
            raise ValueError("Published cover checksum mismatch")
        return "official-cover" + suffix, image


def build_site(editions, output, base_url, repo, cover=None):
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    for name in ["style.css", "book-renderer.js", "site.js", "favicon.svg", "plex-sans-latin.woff2",
                 "FONT-LICENSE.txt", "LUCIDE-LICENSE.txt"]:
        shutil.copyfile(ROOT / "site" / name, output / name)
    cover_file, cover_bytes = cover or ("official-cover.png", (ROOT / "site/official-cover.png").read_bytes())
    cover_size, cover_flat = (866, 1174), False
    rendered_cover = json.loads((ROOT / "site/cover-render.json").read_text())
    # A new publisher cover must never be replaced by an older high-res render.
    if hashlib.sha256(cover_bytes).hexdigest() == rendered_cover["preview_sha256"]:
        cover_bytes = (ROOT / "site/official-cover-3x.png").read_bytes()
        if hashlib.sha256(cover_bytes).hexdigest() != rendered_cover["render_sha256"]:
            raise ValueError("High-resolution cover checksum mismatch")
        cover_file = "official-cover-3x.png"
        cover_size = (rendered_cover["width"], rendered_cover["height"])
        cover_flat = True
    cover_file = f"official-cover-{hashlib.sha256(cover_bytes).hexdigest()[:12]}{Path(cover_file).suffix}"
    (output / cover_file).write_bytes(cover_bytes)
    base_url = base_url.rstrip("/") + "/"
    latest = next(item for item in editions if item["latest"])
    (output / "index.html").write_text(
        render_page(editions, latest, "./", base_url, repo, cover_file, cover_size, cover_flat, base_url))
    # Keep a permanent page for every edition, including today's latest.
    for edition in editions:
        destination = output / "editions" / edition["tag"]
        destination.mkdir(parents=True)
        canonical = base_url + f"editions/{edition['tag']}/"
        (destination / "index.html").write_text(
            render_page(editions, edition, "../../", canonical, repo, cover_file, cover_size, cover_flat, base_url))
    sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"  <url>\n    <loc>{html.escape(base_url)}</loc>\n"
        f"    <lastmod>{latest['capture_date']}</lastmod>\n  </url>\n"
        "</urlset>\n"
    )
    (output / "sitemap.xml").write_text(sitemap)
    (output / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {base_url}sitemap.xml\n")
    (output / ".nojekyll").touch()
    print(f"Built download library with {len(editions)} edition(s): {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--output", type=Path, default=Path(".work/site"))
    args = parser.parse_args()
    releases, latest = load_releases(args.repo)
    editions = editions_from_releases(releases, latest, args.repo)
    build_site(editions, args.output, args.base_url, args.repo, published_cover(editions[0]))


if __name__ == "__main__":
    main()
