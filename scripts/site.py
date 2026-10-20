"""Build a static download library from complete published GitHub releases."""

import argparse
import html
import json
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
REPO = "KianBahasadri/grade-book-offline"
BASE_URL = "https://kianbahasadri.github.io/grade-book-offline/"
FORMATS = [
    ("pdf", "PDF", "Easy reading & printing", "The familiar page layout, ready for your phone, computer, or printer.", "A good place to start", "Download PDF"),
    ("epub", "EPUB", "An e-book that fits your screen", "Adjust the text size and settle in with your e-reader or favorite EPUB reading app.", "For reading apps", "Download EPUB"),
    ("html", "ZIP", "Offline website", "Read in your browser, with the pictures included. Unzip the folder to get started.", "", "Download website"),
    ("docx", "DOCX", "Word document", "Open the book in Microsoft Word or a compatible document app.", "", "Download Word file"),
    ("txt", "TXT", "Plain text", "A small, simple text file. Includes figure descriptions instead of pictures.", "", "Download text"),
]
ASSETS = {
    "pdf": "grade-book.pdf", "epub": "grade-book.epub", "html": "grade-book-html.zip",
    "docx": "grade-book.docx", "txt": "grade-book.txt",
}
DOWNLOAD_ICON = '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 3v12m-5-5 5 5 5-5M5 16v4h14v-4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>'
DOCUMENT_ICON = '<svg class="format-icon" viewBox="0 0 32 38" fill="none" aria-hidden="true"><path d="M6 2h13l7 7v27H6V2Z" stroke="currentColor" stroke-width="1.3"/><path d="M19 2v8h7M11 18h10M11 23h10M11 28h6" stroke="currentColor" stroke-width="1.3"/></svg>'
BOOK_ICON = '<svg class="format-icon" viewBox="0 0 32 38" fill="none" aria-hidden="true"><rect x="5" y="2" width="23" height="34" rx="3" stroke="currentColor" stroke-width="1.3"/><path d="M9 2v34M14 10h9M14 15h9M14 20h6M5 30h23" stroke="currentColor" stroke-width="1.3"/></svg>'


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
            "tag": tag, "date": label, "timestamp": published.isoformat(),
            "latest": tag == latest_tag,
            "assets": {key: {
                "url": assets[name]["browser_download_url"], "size": assets[name]["size"],
            } for key, name in ASSETS.items()},
            "checksums": assets["SHA256SUMS"]["browser_download_url"],
        })
    if not any(item["latest"] for item in editions):
        raise ValueError("No complete latest book edition found; keeping existing site")
    editions.sort(key=lambda item: (item["latest"], item["timestamp"]), reverse=True)
    # Forced rebuilds can share an edition date; retain each and make it distinct.
    for edition in editions:
        same_date = [item for item in editions if item["date"] == edition["date"]]
        edition["label"] = edition["date"]
        if len(same_date) > 1:
            number = sorted(same_date, key=lambda item: (item["timestamp"], item["tag"])).index(edition) + 1
            edition["label"] += f" · build {number}"
    return editions


def size_label(size):
    if size < 1_000_000:
        return f"{round(size / 1000)} KB"
    return f"{size / 1_000_000:.1f} MB"


def edition_path(edition, prefix):
    return prefix + (f"editions/{edition['tag']}/index.html" if not edition["latest"] else "index.html")


def render_page(editions, selected, prefix, canonical, repo):
    escape = html.escape
    cards = []
    for kind, extension, title, description, badge, action in FORMATS:
        asset = selected["assets"][kind]
        badge_html = f'<span class="format-tag">{badge}</span>' if badge else ""
        icon = BOOK_ICON if kind == "epub" else DOCUMENT_ICON
        cards.append(
            f'<article class="format-card{" featured" if kind == "pdf" else ""}">'
            f'<div class="card-heading">{icon}{badge_html}</div>'
            f'<h3>{title}</h3><p>{description}</p>'
            f'<a class="download-button" data-format="{kind}" href="{escape(asset["url"], quote=True)}">'
            f'{action}{DOWNLOAD_ICON}</a>'
            f'<span class="file-info">{extension} <span aria-hidden="true">·</span> '
            f'{size_label(asset["size"])}</span></article>')
    picker = ""
    if len(editions) > 1:
        options = []
        for edition in editions:
            label = ("Latest · " if edition["latest"] else "") + edition["label"]
            chosen = " selected" if edition["tag"] == selected["tag"] else ""
            options.append(f'<option value="{escape(edition_path(edition, prefix), quote=True)}"{chosen}>{escape(label)}</option>')
        picker = ('<form class="edition-picker" hidden><label for="edition">Choose edition</label>'
                  '<select id="edition" name="edition">' + "".join(options)
                  + '</select><button type="submit">View</button></form>')
    archive = []
    for edition in editions:
        label = edition["label"] + (" — latest" if edition["latest"] else "")
        current = ' <span class="current-edition">(Viewing this edition)</span>' if edition["tag"] == selected["tag"] else ""
        archive.append(f'<li><a href="{escape(edition_path(edition, prefix), quote=True)}">{escape(label)}</a>{current}</li>')
    notice = "" if selected["latest"] else (
        '<p class="edition-notice">You’re viewing an earlier edition.'
        f' <a href="{prefix}index.html#downloads">Get the latest edition</a>.</p>')
    values = {
        "PREFIX": prefix, "CANONICAL": escape(canonical, quote=True),
        "PAGE_TITLE": "The GRADE Book — download for offline reading" if selected["latest"]
        else f"The GRADE Book — {selected['label']} edition",
        "EDITION_STATUS": "Latest edition" if selected["latest"] else "Earlier edition",
        "EDITION_DATE": escape(selected["date"]), "EDITION_PICKER": picker,
        "EDITION_NOTICE": notice, "PRIMARY_FORMATS": "".join(cards[:2]),
        "SECONDARY_FORMATS": "".join(cards[2:]), "ARCHIVE_LINKS": "".join(archive),
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


def build_site(editions, output, base_url, repo):
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output directory must be empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    for name in ["style.css", "site.js", "favicon.svg"]:
        shutil.copyfile(ROOT / "site" / name, output / name)
    base_url = base_url.rstrip("/") + "/"
    latest = next(item for item in editions if item["latest"])
    (output / "index.html").write_text(render_page(editions, latest, "./", base_url, repo))
    # Keep a permanent page for every edition, including today's latest.
    for edition in editions:
        destination = output / "editions" / edition["tag"]
        destination.mkdir(parents=True)
        canonical = base_url + f"editions/{edition['tag']}/"
        (destination / "index.html").write_text(
            render_page(editions, edition, "../../", canonical, repo))
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
    build_site(editions, args.output, args.base_url, args.repo)


if __name__ == "__main__":
    main()
