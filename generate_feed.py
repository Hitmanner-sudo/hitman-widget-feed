#!/usr/bin/env python3

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

OUT_DIR = Path(__file__).parent / "data"
USER_AGENT = "Mozilla/5.0 (compatible; HitmanWidgetFeed/1.0; +personal use script)"

HITMAPS_HOME_API = "https://api.hitmaps.com/api/web/home"

IOI_NEWS_SOURCES = [
    {"key": "hitman", "label": "HITMAN", "news_url": "https://ioi.dk/hitman/news", "base_url": "https://ioi.dk"},
    {"key": "007", "label": "007 First Light", "news_url": "https://ioi.dk/007firstlightgame/news", "base_url": "https://ioi.dk"},
]

TWITCH_DROPS_SOURCES = [
    {"key": "hitman", "label": "HITMAN World of Assassination", "slug": "hitman-world-of-assassination"},
    {"key": "007", "label": "007 First Light", "slug": "007-first-light"},
]


def fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def build_elusive_targets():
    try:
        raw = fetch(HITMAPS_HOME_API)
        data = json.loads(raw)
    except (URLError, HTTPError, json.JSONDecodeError) as e:
        print(f"[et] fetch failed: {e}", file=sys.stderr)
        return {"generated": now_iso(), "ongoing": [], "incoming": [], "error": str(e)}

    ets = data.get("elusiveTargets", [])
    now = datetime.now(timezone.utc)

    ongoing, incoming = [], []
    for et in ets:
        try:
            begin = datetime.fromisoformat(et["beginningTime"].replace("Z", "+00:00"))
            end = datetime.fromisoformat(et["endingTime"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue

        entry = {
            "name": et.get("name", "Unknown Target"),
            "begin": et.get("beginningTime"),
            "end": et.get("endingTime"),
            "image": et.get("tileUrl", ""),
            "url": f"https://www.hitmaps.com{et.get('missionUrl', '')}",
        }

        if begin <= now <= end:
            ongoing.append(entry)
        elif begin > now:
            incoming.append(entry)

    ongoing.sort(key=lambda e: e["end"])
    incoming.sort(key=lambda e: e["begin"])

    return {
        "generated": now_iso(),
        "ongoing_count": len(ongoing),
        "incoming_count": len(incoming),
        "ongoing": ongoing,
        "incoming": incoming,
        "attribution": "Data via HITMAPS (hitmaps.com)",
    }


def extract_post_links(news_html: str, base_url: str):
    links = []
    for pattern in (r'href="(/[a-z0-9\-/]*?/news/[^"#?]+)"', r'href="(/[a-z0-9\-/]*?/roadmaps/[^"#?]+)"',
                    r'href="(/[a-z0-9\-/]*?/patch-notes/[^"#?]+)"'):
        for m in re.finditer(pattern, news_html):
            href = m.group(1)
            if href.rstrip("/").endswith(("/news", "/roadmaps", "/patch-notes")):
                continue
            full = base_url + href
            if full not in [l["url"] for l in links]:
                links.append({"url": full})
    return links


def get_meta(html: str, key: str) -> str:
    m = re.search(rf"meta-{re.escape(key)}:\s*(.+)", html)
    return m.group(1).strip() if m else ""


def build_news(max_per_source=8):
    items = []
    for src in IOI_NEWS_SOURCES:
        try:
            listing_html = fetch(src["news_url"])
        except (URLError, HTTPError) as e:
            print(f"[news:{src['key']}] failed to fetch listing: {e}", file=sys.stderr)
            continue

        links = extract_post_links(listing_html, src["base_url"])[:max_per_source]

        for link in links:
            url = link["url"]
            try:
                post_html = fetch(url)
            except (URLError, HTTPError) as e:
                print(f"[news:{src['key']}] failed to fetch post {url}: {e}", file=sys.stderr)
                continue

            title = get_meta(post_html, "og:title") or get_meta(post_html, "title") or "Untitled"
            image = get_meta(post_html, "og:image")
            kind = "Roadmap" if "/roadmaps/" in url else ("Patch Notes" if "/patch-notes/" in url else "News")

            items.append({
                "game": src["label"],
                "type": kind,
                "title": title,
                "image": image,
                "url": url,
            })

    return {
        "generated": now_iso(),
        "count": len(items),
        "items": items,
    }


def build_drops():
    items = []
    for src in TWITCH_DROPS_SOURCES:
        url = f"https://twitchdrops.app/game/{src['slug']}"
        chatbot_url = f"https://twitchdrops.app/api/chatbot/{src['slug']}"
        try:
            summary_line = fetch(chatbot_url).strip()
        except (URLError, HTTPError) as e:
            print(f"[drops:{src['key']}] failed to fetch chatbot line: {e}", file=sys.stderr)
            summary_line = ""

        image = ""
        try:
            page_html = fetch(url)
            image = get_meta(page_html, "og:image")
        except (URLError, HTTPError) as e:
            print(f"[drops:{src['key']}] failed to fetch page for image: {e}", file=sys.stderr)

        items.append({
            "game": src["label"],
            "summary": summary_line,
            "image": image,
            "url": url,
        })

    return {
        "generated": now_iso(),
        "items": items,
        "attribution": "Data via twitchdrops.app (unofficial fan site)",
    }


def write_json(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    print(f"wrote {path} ({path.stat().st_size} bytes)")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    write_json(OUT_DIR / "elusive_targets.json", build_elusive_targets())
    write_json(OUT_DIR / "news.json", build_news())
    write_json(OUT_DIR / "drops.json", build_drops())


if __name__ == "__main__":
    main()
