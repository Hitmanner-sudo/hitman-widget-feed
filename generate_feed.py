#!/usr/bin/env python3

import html as htmllib
import http.client
import json
import re
import sys
from datetime import datetime, timedelta, timezone
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
    {"key": "hitman", "label": "HITMAN", "news_url": "https://ioi.dk/hitman", "base_url": "https://ioi.dk"},
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


FETCH_ERRORS = (URLError, HTTPError, OSError, ValueError, http.client.HTTPException)


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
    except FETCH_ERRORS + (json.JSONDecodeError,) as e:
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


def build_news(max_per_source=10):
    items, errors = [], []
    for src in IOI_NEWS_SOURCES:
        prefix = urlparse(src["news_url"]).path.rsplit("/news", 1)[0]
        try:
            listing_html = fetch(src["news_url"], browser=True)
        except FETCH_ERRORS as e:
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
            except FETCH_ERRORS as e:
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


# ---------------- Roadmap ("what's next up") ----------------

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7,
          "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_M = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
RANGE_RE = re.compile(
    rf"\b({_M})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d\d))?"
    rf"(?:\s*[-\u2013\u2014]\s*(?:({_M})\.?\s+)?(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d\d))?)?",
    re.IGNORECASE)
BASE_RE = re.compile(rf"\b({_M})[a-z]*\.?\s+(\d{{1,2}}),?\s+(20\d\d)", re.IGNORECASE)
TITLE_STOP = {"new challenge", "ioi account unlock", "premium content", "new featured contract",
              "how to link your ioi account to receive twitch drops"}


def html_to_lines(page: str):
    """Visible text of a page, one block per line, with image URLs kept as [[IMG:url]] markers."""
    page = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", page)

    def img(m):
        tag = m.group(0)
        for attr in ("src", "data-src", "data-lazy-src", "srcset", "data-srcset"):
            a = re.search(rf'\b{attr}=["\']([^"\']+)["\']', tag, re.IGNORECASE)
            if a and not a.group(1).startswith("data:"):
                return "\n[[IMG:" + a.group(1).split(",")[0].strip().split(" ")[0] + "]]\n"
        return " "

    page = re.sub(r"(?is)<img\b[^>]*>", img, page)
    page = re.sub(r"(?i)<br\s*/?>|</?(?:p|div|h[1-6]|li|ul|ol|blockquote|section|article|tr|td|header|footer)\b[^>]*>", "\n", page)
    page = re.sub(r"<[^>]+>", "", page)
    page = htmllib.unescape(page).replace("\xa0", " ")
    lines = []
    for ln in page.split("\n"):
        ln = " ".join(ln.split())
        if ln:
            lines.append(ln)
    return lines


def _clean_title(t: str) -> str:
    t = re.sub(r"[*\[\]|\u2022]", " ", t)
    t = re.sub(r"(?i)^twitch drop no\.?\s*\d+\s*:\s*", "", t)
    t = " ".join(t.split()).strip(" -\u2013\u2014:")
    if t.isupper():
        t = t.title()
    return t


def _section_kind(section: str) -> str:
    s = section.lower()
    if "twitch" in s:
        return "Twitch Drop"
    if "elusive" in s and "dlc" not in s and "expanded" not in s:
        return "Elusive Target"
    if "challenge" in s:
        return "Challenge"
    if "featured" in s or "contract" in s:
        return "Featured Contracts"
    if "event" in s:
        return "Event"
    if "account" in s:
        return "IOI Account"
    if "premium" in s or "dlc" in s:
        return "Premium"
    return section.title()


def _is_section(ln: str) -> bool:
    return (5 <= len(ln) <= 70 and ln.upper() == ln and not re.search(r"\d", ln)
            and re.search(r"[A-Z]{3}", ln) is not None and ln not in ("GO TO TOP", "CLOSE", "PREVIOUS", "NEXT"))


def parse_roadmap(page: str, url: str):
    lines = html_to_lines(page)
    base = None
    start_i = 0
    for i, ln in enumerate(lines[:300]):
        m = BASE_RE.search(ln)
        if m and any(re.search(r"\broadmaps?\b", x, re.IGNORECASE) for x in lines[max(0, i - 2):i + 2]):
            base = datetime(int(m.group(3)), MONTHS[m.group(1)[:3].lower()], int(m.group(2))).date()
            start_i = i + 1
            break
    if base is None:  # no publish date found: trust the year in the URL and never roll forward
        ym = re.search(r"/roadmaps/(20\d\d)/", url)
        base = datetime(int(ym.group(1)) if ym else datetime.now(timezone.utc).year, 1, 1).date()

    def mk(mon, day, year, ref_year=None):
        mon = MONTHS[mon[:3].lower()]
        y = int(year) if year else (ref_year or base.year)
        if not year and ref_year is None and mon < base.month - 2:
            y += 1
        try:
            return datetime(y, mon, int(day)).date()
        except ValueError:
            return None

    def rng(m):
        s = mk(m.group(1), m.group(2), m.group(3))
        if s is None:
            return None, None
        e = None
        if m.group(5):
            e = mk(m.group(4) or m.group(1), m.group(5), m.group(6), s.year)
            if e is not None and e < s:
                if s.month >= 10 and e.month <= 3:
                    e = mk(m.group(4) or m.group(1), m.group(5), None, s.year + 1)
                else:
                    e = None
        return s, e

    events = []
    section, hd, after_heading, pending_img, last_short = "", None, False, "", ""

    def add(title, s, e, kind=None):
        nonlocal pending_img
        title = _clean_title(title)
        if (not title or title.lower() in TITLE_STOP or s is None or len(title.split()) > 9
                or not re.search(r"[A-Za-z]{3,}", title) or title.lower().startswith("between")
                or title.lower().startswith(("please note", "note:", "note "))):
            return
        events.append({"title": title, "kind": kind or _section_kind(section), "section": section.title(),
                       "start": s.isoformat(), "end": e.isoformat() if e else None,
                       "image": pending_img, "url": url})
        pending_img = ""

    for ln in lines[start_i:]:
        if ln.lower().startswith("go to top"):
            break
        im = re.fullmatch(r"\[\[IMG:(.+)\]\]", ln)
        if im:
            pending_img = urljoin(url, im.group(1))
            continue
        if _is_section(ln):
            section, hd, after_heading, last_short = ln, None, False, ""
            continue
        m = RANGE_RE.search(ln)
        if m:
            s, e = rng(m)
            before = ln[:m.start()]
            if before.rstrip().endswith("["):
                title = _clean_title(before)
            else:
                title = _clean_title(before + " " + ln[m.end():])
            if not title:
                hd, after_heading = (s, e), True
                continue
            if not re.search(r"[A-Za-z]{3,}", re.sub(r"(?i)\b(between|from|until|during|ends?|starts?|on)\b", "", title)):
                if last_short:
                    add(last_short, s, e, "Twitch Drop" if "twitch drop" in last_short.lower() else None)
                after_heading = False
                continue
            after_heading = False
            if len(title) <= 80:
                add(title, s, e)
                last_short = title
            elif re.search(r"\bbetween\b", ln, re.IGNORECASE) and last_short:
                add(last_short, s, e, "Twitch Drop" if "twitch drop" in last_short.lower() else None)
            continue
        short = len(ln) <= 80 and not ln.endswith((".", "!", "?", ":"))
        if (after_heading and hd and short
                and not ln.lower().startswith(("note", "reward", "for ", "twitch drop"))):
            add(ln, hd[0], hd[1])
        after_heading = False
        if len(ln) <= 100:
            last_short = ln
    best = {}
    for ev in events:
        k = (ev["title"].lower(), ev["start"])
        if k not in best or (best[k]["end"] is None and ev["end"]):
            best[k] = ev
    return list(best.values())


def _norm_name(n: str) -> str:
    n = re.sub(r"\s*\([^)]*\)\s*$", "", n or "")
    n = re.sub(r"(?i)\s*[-\u2013\u2014:]?\s*year\s*\d+\s*$", "", n)
    n = re.sub(r"\s*#\d+\s*$", "", n).lower().strip()
    n = re.sub(r"^the\s+", "", n)
    return re.sub(r"[^a-z0-9]+", "", n).rstrip("s")


def _day(iso):
    try:
        return datetime.fromisoformat((iso or "").replace("Z", "+00:00")).date()
    except ValueError:
        return None


def cross_reference(events, et, drops):
    """Trust HITMAPS (exact Elusive Target times) and twitchdrops.app (exact drop end) over the roadmap text."""
    if et:
        for e in (et.get("ongoing") or []) + (et.get("incoming") or []):
            b, en = _day(e.get("begin")), _day(e.get("end"))
            if not b:
                continue
            key = _norm_name(e.get("name", ""))
            hit = None
            for ev in events:
                if _norm_name(ev["title"]) == key and abs((datetime.fromisoformat(ev["start"]).date() - b).days) <= 6:
                    hit = ev
                    break
            if hit:
                hit.update(start=b.isoformat(), end=en.isoformat() if en else hit["end"],
                           kind="Elusive Target", source="hitmaps")
                if not hit.get("image") and e.get("image"):
                    hit["image"] = e["image"]
            else:
                events.append({"title": re.sub(r"(\s*(\([^)]*\)|#\d+|[-\u2013\u2014]\s*Year\s*\d+))+\s*$", "", e.get("name", "")).strip() or "Elusive Target",
                               "kind": "Elusive Target", "section": "Elusive Targets",
                               "start": b.isoformat(), "end": en.isoformat() if en else None,
                               "image": e.get("image", ""), "url": e.get("url", ""), "source": "hitmaps"})
    today = datetime.now(timezone.utc).date()
    # Things the roadmap says are running that the live feeds say are over: drop them.
    et_ok = bool(et) and not et.get("error")
    if et_ok:
        live_names = {_norm_name(e.get("name", "")) for e in (et.get("ongoing") or [])}
        events[:] = [ev for ev in events
                     if not (ev["kind"] == "Elusive Target" and ev.get("source") != "hitmaps"
                             and ev["start"] <= today.isoformat() <= (ev["end"] or ev["start"])
                             and _norm_name(ev["title"]) not in live_names)]
    hit = next((i for i in (drops or {}).get("items") or [] if "hitman" in (i.get("slug") or "")), None)
    if hit is not None and hit.get("page_ok") and not hit.get("error"):
        camps = hit.get("campaigns") or []
        ends = {_day(c.get("end_iso")) for c in camps if _day(c.get("end_iso"))}
        solo_end = next(iter(ends)) if len(ends) == 1 else None
        pairs = []
        for c in camps:
            for rw in c.get("rewards") or []:
                pairs.append((rw, _day(c.get("end_iso"))))
        for rw in hit.get("active_rewards") or []:
            pairs.append((rw, solo_end))
        active = {_norm_name(rw.get("name", "")) for rw, _ in pairs}
        if active or not camps:  # page read fine: either we know the active rewards or nothing is running
            events[:] = [ev for ev in events
                         if not (ev["kind"] == "Twitch Drop" and ev["start"] <= today.isoformat()
                                 and _norm_name(ev["title"]) not in active)]
        for rw, end in pairs:
            key = _norm_name(rw.get("name", ""))
            for ev in events:
                if ev["kind"] == "Twitch Drop" and _norm_name(ev["title"]) == key:
                    if end:
                        ev["end"] = end.isoformat()
                    ev["source"] = "twitchdrops"
                    if not ev.get("image") and rw.get("image"):
                        ev["image"] = rw["image"]
    return events


def build_roadmap(max_roadmaps=3, et=None, drops=None):
    today = datetime.now(timezone.utc).date()
    roadmaps, events, errors = [], {}, []
    for src in IOI_NEWS_SOURCES:
        prefix = urlparse(src["news_url"]).path.rsplit("/news", 1)[0]
        try:
            listing = fetch(src["news_url"], browser=True)
        except FETCH_ERRORS as e:
            errors.append(f"{src['key']}: {e}")
            continue
        links = [l["url"] for l in extract_post_links(listing, src["base_url"], prefix) if "/roadmaps/" in l["url"]]
        for url in links[:max_roadmaps]:
            try:
                page = fetch(url, browser=True)
            except FETCH_ERRORS as e:
                errors.append(f"{url}: {e}")
                continue
            evs = parse_roadmap(page, url)
            roadmaps.append({"title": get_title(page), "url": url, "events": len(evs)})
            for ev in evs:  # newer roadmaps are listed first and win
                events.setdefault((ev["title"].lower(), ev["start"]), ev)
    all_events = cross_reference(list(events.values()), et, drops)
    cutoff = (today - timedelta(days=1)).isoformat()
    keep = [ev for ev in all_events if (ev["end"] or ev["start"]) >= cutoff]
    keep.sort(key=lambda e: (e["start"], e["title"]))
    out = {"generated": now_iso(), "count": len(keep), "roadmaps": roadmaps, "events": keep}
    if errors:
        out["errors"] = errors
    elif not roadmaps:
        out["errors"] = ["no roadmap links found in listing"]
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


def _attr(tag: str, name: str) -> str:
    m = re.search(rf'\b{name}=["\']([^"\']*)["\']', tag, re.IGNORECASE)
    return m.group(1) if m else ""


def scrape_active_rewards(page: str) -> list:
    """Reward images before the 'Past Drops' heading (same approach the app uses on the page)."""
    cut = page.find("Past Drops")
    if cut < 0:
        cut = page.find("Past Campaigns")
    if cut < 0:
        cut = len(page)
    head = page[:cut]
    out, seen = [], set()
    for m in re.finditer(r"<img[^>]*>", head, re.IGNORECASE):
        tag = m.group(0)
        if "/REWARD/" not in tag:
            continue
        src = _attr(tag, "src")
        if "/REWARD/" not in src:
            src = _attr(tag, "data-src")
        if not src or src in seen:
            continue
        seen.add(src)
        name = htmllib.unescape(_attr(tag, "alt")).strip()
        if not name:
            continue
        after = re.sub(r"<[^>]*>", " ", head[m.end():m.end() + 400])
        rm = re.search(r"(?i)watch\s+(\d+\s*(?:h|hr|hrs|hour|hours|m|min|mins|minutes)\b)", after)
        out.append({"name": name, "image": src,
                    "requirement": ("Watch " + rm.group(1).replace(" ", "")) if rm else ""})
    return out


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
        "page_ok": True,
        "active_rewards": scrape_active_rewards(html),
        "campaigns": active_campaigns,
        "url": f"https://twitchdrops.app/game/{slug}",
    }


def build_drops() -> dict:
    items = []
    for src in TWITCH_DROPS_SOURCES:
        url = f"https://twitchdrops.app/game/{src['slug']}"
        try:
            page_html = fetch(url)
        except FETCH_ERRORS as e:
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
    jobs = [
        ("elusive_targets.json", build_elusive_targets, {"ongoing": [], "incoming": []}),
        ("news.json", build_news, {"count": 0, "items": []}),
        ("drops.json", build_drops, {"items": []}),
        ("roadmap.json", build_roadmap, {"count": 0, "events": []}),
    ]
    failed = []
    results = {}
    for name, fn, fallback in jobs:
        try:
            if name == "roadmap.json":
                data = fn(et=results.get("elusive_targets.json"), drops=results.get("drops.json"))
            else:
                data = fn()
            results[name] = data
            write_json(OUT_DIR / name, data)
        except Exception as e:  # one broken source must not stop the others
            import traceback
            traceback.print_exc()
            failed.append(f"{name}: {type(e).__name__}: {e}")
            if not (OUT_DIR / name).exists():
                write_json(OUT_DIR / name, {"generated": now_iso(), **fallback, "errors": [f"{type(e).__name__}: {e}"]})
    if failed:
        print("FAILED (kept previous data):", *failed, sep="\n  ", file=sys.stderr)


if __name__ == "__main__":
    main()
