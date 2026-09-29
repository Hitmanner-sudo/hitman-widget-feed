#!/usr/bin/env python3

import html as htmllib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

OUT_DIR = Path(__file__).parent / "data"
USER_AGENT = "Mozilla/5.0 (compatible; HitmanWidgetFeed/1.0; +personal use script)"
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

HITMAPS_HOME_API = "https://api.hitmaps.com/api/web/home"

IOI_NEWS_SOURCES = [
    {"key": "hitman", "label": "HITMAN", "news_url": "https://ioi.dk/hitman/news", "base_url": "https://ioi.dk"},
]

TWITCH_DROPS_SOURCES = [
    {
        "key": "hitman",
        "label": "HITMAN World of Assassination",
        "slug": "hitman-world-of-assassination",
        "account_link_url": "https://account.ioi.dk/",
    },
    {
        "key": "007",
        "label": "007 First Light",
        "slug": "007-first-light",
        "account_link_url": "https://account.ioi.dk/",
    },
]


def fetch(url: str, browser: bool = False) -> str:
    headers = {"User-Agent": BROWSER_UA if browser else USER_AGENT,
               "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
               "Accept-Language": "en-US,en;q=0.9"}
    req = Request(url, headers=headers)
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
            "url": f"https://www.hitmaps.com{et.get('missionUrl', '').rstrip('/')}",
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



POST_KINDS = {"news": "News", "roadmaps": "Roadmap", "patch-notes": "Patch Notes", "blogs": "Blog Post"}


def extract_post_links(news_html: str, base_url: str, prefix: str):
    """prefix e.g. '/hitman'. Accepts relative and absolute hrefs, same game only."""
    pat = re.compile(
        r'href=["\'](?:https?://(?:www\.)?ioi\.dk)?(' + re.escape(prefix) +
        r'/(?:news|roadmaps|patch-notes|blogs)/[^"\'#?\s]+)["\']')
    links, seen = [], set()
    for m in pat.finditer(news_html):
        full = base_url + m.group(1).rstrip("/")
        if full not in seen:
            seen.add(full)
            links.append({"url": full})
    return links


def get_meta(page: str, key: str) -> str:
    k = re.escape(key)
    for p in (
        rf'<meta[^>]+(?:property|name)=["\']{k}["\'][^>]*content=["\']([^"\']*)["\']',
        rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']{k}["\']',
    ):
        m = re.search(p, page, re.IGNORECASE)
        if m and m.group(1).strip():
            return htmllib.unescape(m.group(1)).strip()
    return ""


def get_title(page: str) -> str:
    t = get_meta(page, "og:title")
    if t and t != "undefined":
        return t
    m = re.search(r"<h1[^>]*>(.*?)</h1>", page, re.DOTALL | re.IGNORECASE)
    if m:
        t = re.sub(r"<[^>]+>", " ", m.group(1))
        t = " ".join(htmllib.unescape(t).split())
        if t:
            return t
    m = re.search(r"<title>(.*?)</title>", page, re.DOTALL | re.IGNORECASE)
    return " ".join(htmllib.unescape(m.group(1)).split()) if m else "Untitled"


IMG_JUNK = ("logo", "icon", "sprite", "pixel", "avatar", "blank", "favicon")


def first_page_image(page: str, url: str) -> str:
    """og:image / twitter:image, else the first real picture on the page."""
    for key in ("og:image", "twitter:image"):
        img = get_meta(page, key)
        if img and img != "undefined":
            return urljoin(url, img)
    for m in re.finditer(r"<img\b[^>]*>", page, re.IGNORECASE):
        tag = m.group(0)
        src = ""
        for attr in ("src", "data-src", "data-lazy-src", "srcset", "data-srcset"):
            a = re.search(rf'\b{attr}=["\']([^"\']+)["\']', tag, re.IGNORECASE)
            if a and not a.group(1).startswith("data:"):
                src = a.group(1).split(",")[0].strip().split(" ")[0]
                break
        if not src:
            continue
        src = urljoin(url, htmllib.unescape(src))
        path = urlparse(src).path.lower()
        if path.endswith((".svg", ".gif")) or any(j in src.lower() for j in IMG_JUNK):
            continue
        return src
    return ""


def build_news(max_per_source=8):
    items, errors = [], []
    for src in IOI_NEWS_SOURCES:
        prefix = urlparse(src["news_url"]).path.rsplit("/news", 1)[0]
        try:
            listing_html = fetch(src["news_url"], browser=True)
        except (URLError, HTTPError) as e:
            print(f"[news:{src['key']}] failed to fetch listing: {e}", file=sys.stderr)
            errors.append(f"{src['key']}: {e}")
            continue

        links = extract_post_links(listing_html, src["base_url"], prefix)[:max_per_source]
        if not links:
            msg = f"{src['key']}: no post links found in listing ({len(listing_html)} bytes)"
            print(f"[news] {msg}", file=sys.stderr)
            errors.append(msg)

        for link in links:
            url = link["url"]
            try:
                post_html = fetch(url, browser=True)
            except (URLError, HTTPError) as e:
                print(f"[news:{src['key']}] failed to fetch post {url}: {e}", file=sys.stderr)
                continue

            kind = next((v for k, v in POST_KINDS.items() if f"/{k}/" in url), "News")
            image = first_page_image(post_html, url)
            items.append({
                "game": src["label"],
                "type": kind,
                "title": get_title(post_html),
                "image": image,
                "url": url,
            })

    out = {"generated": now_iso(), "count": len(items), "items": items}
    if errors:
        out["errors"] = errors
    return out


def parse_active_campaigns(html: str) -> list:
    campaigns = []

    past_marker = re.search(r'<h2>Past Drops</h2>|<h2>Past Campaigns</h2>', html)
    active_html = html[:past_marker.start()] if past_marker else html

    campaign_pattern = re.compile(
        r'<div class="campaign-banner(?!\s+expired)[^"]*"[^>]*>(.*?)</div>\s*</div>\s*</div>',
        re.DOTALL
    )

    for camp_m in campaign_pattern.finditer(active_html):
        block = camp_m.group(0)

        m_name = re.search(r'<span class="cb-name">([^<]+)</span>', block)
        name = m_name.group(1).strip() if m_name else "Unknown Campaign"

        m_dates = re.search(r'<span class="cb-dates">([^<]+)</span>', block)
        dates_text = m_dates.group(1).strip() if m_dates else ""

        m_owner = re.search(r'<span class="cb-owner">([^<]+)</span>', block)
        owner = m_owner.group(1).strip() if m_owner else ""

        m_end_ts = re.search(r'data-end-ts="(\d+)"', block)
        end_iso = None
        if m_end_ts:
            ts_ms = int(m_end_ts.group(1))
            end_iso = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat()

        m_ch = re.search(r'<strong>Channels:</strong>\s*<span[^>]*>([^<]+)</span>', block)
        channels = m_ch.group(1).strip() if m_ch else "All Channels"
        if not m_ch:
            channel_links = re.findall(r'<a[^>]+class="channel-link"[^>]*>([^<]+)</a>', block)
            if channel_links:
                channels = ", ".join(channel_links[:5])
                total = re.search(r'\+\s*(\d+)\s*more', block)
                if total:
                    channels += f" + {total.group(1)} more"

        m_desc = re.search(r'<div class="cb-desc">([^<]+)</div>', block)
        description = m_desc.group(1).strip() if m_desc else ""

        campaigns.append({
            "name": name,
            "owner": owner,
            "dates_text": dates_text,
            "end_iso": end_iso,
            "channels": channels,
            "description": description,
        })

    return campaigns


def parse_active_rewards(html: str, active_campaign_names: list) -> dict:
    past_drops_marker = re.search(r'<h2>Past Drops</h2>', html)
    active_html = html[:past_drops_marker.start()] if past_drops_marker else html

    reward_pattern = re.compile(
        r'<div class="drop-card(?!\s+drop-expired)[^"]*"[^>]*>\s*'
        r'<img\s+src="(https://static-cdn\.jtvnw\.net/twitch-quests-assets/REWARD/[^"]+)"'
        r'\s+alt="([^"]+)"[^>]*>\s*'
        r'<div class="drop-name">([^<]+)</div>\s*'
        r'<div class="drop-time">([^<]+)</div>\s*'
        r'<div class="drop-campaign">([^<]+)</div>',
        re.DOTALL
    )

    rewards_by_campaign = {}
    seen_imgs = set()

    for m in reward_pattern.finditer(active_html):
        img = m.group(1).strip()
        if img in seen_imgs:
            continue
        seen_imgs.add(img)

        campaign_name = m.group(5).strip()
        reward = {
            "name": m.group(3).strip(),
            "image": img,
            "requirement": m.group(4).strip(),
        }

        if campaign_name not in rewards_by_campaign:
            rewards_by_campaign[campaign_name] = []
        rewards_by_campaign[campaign_name].append(reward)

    return rewards_by_campaign


def parse_drops_page(html: str, slug: str, label: str, account_link_url: str) -> dict:
    m = re.search(r'<meta\s+(?:property|name)="og:image"\s+content="([^"]+)"', html)
    if not m:
        m = re.search(r'content="([^"]+)"\s+(?:property|name)="og:image"', html)
    campaign_image = m.group(1).strip() if m else ""

    end_date_human = re.findall(
        r'campaigns?\s+end\s+(?:on\s+)?([A-Za-z]+ \d+(?:,\s*\d{4})?)',
        html, re.IGNORECASE
    )
    end_human = end_date_human[0] if end_date_human else None

    active_campaigns = parse_active_campaigns(html)
    active_campaign_names = [c["name"] for c in active_campaigns]
    rewards_by_campaign = parse_active_rewards(html, active_campaign_names)

    soonest_end_iso = None
    end_isos = [c["end_iso"] for c in active_campaigns if c["end_iso"]]
    if end_isos:
        now = datetime.now(timezone.utc)
        future = []
        for t in end_isos:
            try:
                dt = datetime.fromisoformat(t)
                if dt > now:
                    future.append(dt)
            except ValueError:
                pass
        if future:
            soonest_end_iso = min(future).isoformat()

    for camp in active_campaigns:
        camp["rewards"] = rewards_by_campaign.get(camp["name"], [])
        camp["reward_count"] = len(camp["rewards"])

    total_rewards = sum(c["reward_count"] for c in active_campaigns)

    return {
        "game": label,
        "slug": slug,
        "campaign_image": campaign_image,
        "account_link_url": account_link_url,
        "soonest_end_iso": soonest_end_iso,
        "end_human": end_human,
        "campaign_count": len(active_campaigns),
        "total_reward_count": total_rewards,
        "campaigns": active_campaigns,
        "url": f"https://twitchdrops.app/game/{slug}",
    }


def build_drops() -> dict:
    items = []
    for src in TWITCH_DROPS_SOURCES:
        url = f"https://twitchdrops.app/game/{src['slug']}"
        try:
            page_html = fetch(url)
        except (URLError, HTTPError) as e:
            print(f"[drops:{src['key']}] failed to fetch page: {e}", file=sys.stderr)
            items.append({
                "game": src["label"],
                "slug": src["slug"],
                "url": url,
                "error": str(e),
            })
            continue

        entry = parse_drops_page(
            page_html,
            slug=src["slug"],
            label=src["label"],
            account_link_url=src.get("account_link_url", ""),
        )
        items.append(entry)

    return {
        "generated": now_iso(),
        "items": items,
        "attribution": "Data scraped from twitchdrops.app (unofficial fan site)",
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
