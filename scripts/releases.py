"""Read the last published baseline, publish complete editions, and keep cron active."""

import argparse
import base64
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


def api(path, payload=None):
    request = Request(
        "https://api.github.com/" + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method="PUT" if payload is not None else "GET",
        headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"],
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "GRADE-Book-Offline",
        },
    )
    with urlopen(request, timeout=60) as response:
        return json.load(response)


def baseline(repo, destination):
    # Verify the repository first: a typo or permissions error must not look
    # like a first publication.
    api(f"repos/{repo}")
    try:
        release = api(f"repos/{repo}/releases/latest")
    except HTTPError as error:
        if error.code != 404:
            raise
        print("No published edition yet.")
        destination.unlink(missing_ok=True)
        return
    manifests = [asset for asset in release["assets"] if asset["name"] == "manifest.json"]
    if len(manifests) != 1:
        raise ValueError("Latest release has no unique manifest.json; refusing to reset baseline")
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "gh", "release", "download", release["tag_name"], "--repo", repo,
        "--pattern", "manifest.json", "--output", str(destination), "--clobber",
    ], check=True, timeout=120)
    value = json.loads(destination.read_text())
    if (value.get("schema") != 1 or not value.get("chapters")
            or not re.fullmatch("[0-9a-f]{64}", value.get("content_sha256", ""))):
        raise ValueError("Latest release manifest is invalid")
    print(f"Baseline: {release['tag_name']} ({value['content_sha256']})")


def release_notes(current, previous):
    before = {item["id"]: item for item in (previous or {}).get("chapters", [])}
    after = {item["id"]: item for item in current["chapters"]}
    added = [item["title"] for key, item in after.items() if key not in before]
    updated = [item["title"] for key, item in after.items()
               if key in before and item["content_sha256"] != before[key]["content_sha256"]]
    removed = [item["title"] for key, item in before.items() if key not in after]
    lines = [
        f"Unofficial offline edition of [The GRADE Book]({current['source']}/).",
        f"Captured **{current['captured_at']}**; includes **{len(after)} published English chapters**.",
        "",
        "Download **grade-book.epub** for an e-reader, **grade-book.pdf** for printing,",
        "**grade-book-html.zip** for a browser (extract it and open `index.html`),",
        "**grade-book.docx** for Word, or **grade-book.txt** for text only.",
        "Figures are embedded in EPUB, PDF, DOCX and the HTML archive.",
        "",
    ]
    if previous is None:
        lines.append("First published edition.")
    elif previous["content_sha256"] == current["content_sha256"]:
        lines.append("Manual rebuild of unchanged source content.")
    else:
        for label, titles in [("Added", added), ("Updated", updated), ("Removed", removed)]:
            if titles:
                lines.extend([f"**{label}:**", "", *[f"- {title}" for title in titles], ""])
        if not (added or updated or removed):
            lines.append("The book's catalog, ordering, attribution, or About page changed.")
    lines.extend([
        "",
        "Entries linking to the older handbook and unfinished chapters are listed separately.",
        "External websites and videos remain online links.",
        "",
        "Book content remains **[CC BY-NC-ND 4.0](https://creativecommons.org/licenses/by-nc-nd/4.0/)**.",
        "Original authorship and notices are retained; this project is not endorsed by the authors.",
        "",
        f"Content SHA-256: `{current['content_sha256']}`.",
        "See `manifest.json` for provenance and `SHA256SUMS` for download checksums.",
    ])
    return "\n".join(lines) + "\n"


def publish(repo, directory, previous_path):
    manifest = json.loads((directory / "manifest.json").read_text())
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else None
    date = manifest["captured_at"][:10]
    suffix = f"{os.environ['GITHUB_RUN_ID']}.{os.environ['GITHUB_RUN_ATTEMPT']}"
    tag = f"edition-{date}-{manifest['content_sha256'][:12]}-{suffix}"
    expected = {
        "grade-book.pdf", "grade-book.epub", "grade-book.docx", "grade-book.txt",
        "grade-book-html.zip", "manifest.json", "SHA256SUMS",
    }
    assets = sorted(path for path in directory.iterdir() if path.is_file())
    if {path.name for path in assets} != expected:
        raise ValueError("Release asset set is incomplete or contains unexpected files")
    subprocess.run(["sha256sum", "--check", "SHA256SUMS"], cwd=directory, check=True)
    notes = directory.parent / "release-notes.md"
    notes.write_text(release_notes(manifest, previous), encoding="utf-8")
    # Assets are uploaded while the release is a draft. The previous latest
    # edition remains the baseline until every upload succeeds.
    subprocess.run([
        "gh", "release", "create", tag, *map(str, assets), "--repo", repo, "--draft",
        "--target", os.environ["GITHUB_SHA"], "--title", f"The GRADE Book — {date}",
        "--notes-file", str(notes),
    ], check=True, timeout=600)
    subprocess.run([
        "gh", "release", "edit", tag, "--repo", repo, "--draft=false", "--latest",
    ], check=True, timeout=120)
    print(f"Published https://github.com/{repo}/releases/tag/{tag}")


def heartbeat(repo, branch):
    """Record success at most every 28 days to avoid the 60-day idle cutoff."""
    now = datetime.now(timezone.utc)
    path = f"repos/{repo}/contents/.github/last-successful-check.json"
    try:
        existing = api(path + "?ref=" + quote(branch, safe=""))
    except HTTPError as error:
        if error.code != 404:
            raise
        existing = None
    if existing:
        state = json.loads(base64.b64decode(existing["content"]))
        checked = datetime.fromisoformat(state["checked_at"])
        if now - checked < timedelta(days=28):
            print("Repository heartbeat is current.")
            return
    data = json.dumps({"checked_at": now.isoformat(timespec="seconds")}, indent=2) + "\n"
    payload = {
        "message": "chore: record successful periodic book check",
        "branch": branch, "content": base64.b64encode(data.encode()).decode(),
    }
    if existing:
        payload["sha"] = existing["sha"]
    api(path, payload)
    print("Recorded repository heartbeat.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["baseline", "publish", "heartbeat"])
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--previous", type=Path, default=Path(".work/previous.json"))
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--branch", default=os.environ.get("DEFAULT_BRANCH", "main"))
    args = parser.parse_args()
    if not args.repo:
        parser.error("--repo or GITHUB_REPOSITORY is required")
    if args.command == "baseline":
        baseline(args.repo, args.previous)
    elif args.command == "publish":
        publish(args.repo, args.dist, args.previous)
    else:
        heartbeat(args.repo, args.branch)


if __name__ == "__main__":
    main()
