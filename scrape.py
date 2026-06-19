"""World Cup Match 94 ticket alert for Lumen Field, Seattle."""

import json
import os
import re
import smtplib
import ssl
from email.message import EmailMessage

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

SOURCES = [
    {"name": "SeatGeek", "url": "https://seatgeek.com/fifa-world-cup-tickets/international-soccer/2026-07-06-5-pm/17248700"},
    {"name": "TickPick", "url": "https://www.tickpick.com/buy-fifa-world-cup-26-round-of-16-w81-vs-w82-match-94-tickets-lumen-field-7-6-26-5pm/6259622/"},
    {"name": "StubHub", "url": "https://www.stubhub.com/world-cup-seattle-tickets-7-6-2026/event/153020574/"},
    {"name": "Vivid Seats", "url": "https://www.vividseats.com/world-cup-soccer-tickets-lumen-field-7-6-2026--sports-soccer/production/5080857"},
    {"name": "Gametime", "url": "https://gametime.co/soccer/fifa-world-cup-match-94-tickets/7-6-2026-seattle-wa-lumen-field/events/66a7e76989fae77676133f65"},
]


def _env(name, default):
    value = os.environ.get(name)
    return value if value not in (None, "") else default


PRICE_LIMIT = float(_env("PRICE_LIMIT", "2500"))
MIN_QTY = int(_env("MIN_QTY", "4"))
SECTION_MIN = int(_env("SECTION_MIN", "100"))
SECTION_MAX = int(_env("SECTION_MAX", "299"))
DEBUG = _env("DEBUG", "1").lower() not in ("", "0", "false", "no")
STATE_FILE = "state.json"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)

FEED_HINTS = (
    "listing", "inventory", "offer", "quote", "ticket", "seat", "event",
    "graphql", "search", "production", "map", "availability", "manifest",
)
DATA_MARKERS = (
    "section", "section_id", "sectionname", "row", "price", "displayprice",
    "quantity", "qty", "listing", "inventory", "offer",
)


def payload_looks_useful(data):
    try:
        text = json.dumps(data).lower()
    except Exception:
        return False
    hits = sum(marker in text for marker in DATA_MARKERS)
    return hits >= 2 and "price" in text


def scrape_source(url, name):
    captured = []
    json_urls = []
    captured_signatures = set()

    def add_capture(source_url, data):
        if not payload_looks_useful(data):
            return
        try:
            signature = json.dumps(data, sort_keys=True)[:2000]
        except Exception:
            signature = str(data)[:2000]
        if signature in captured_signatures:
            return
        captured_signatures.add(signature)
        captured.append({"url": source_url, "data": data})

    def on_response(resp):
        try:
            ctype = (resp.headers or {}).get("content-type", "").lower()
            if "json" not in ctype:
                return
            json_urls.append(resp.url)
            data = resp.json()
            if any(hint in resp.url.lower() for hint in FEED_HINTS) or payload_looks_useful(data):
                add_capture(resp.url, data)
        except Exception:
            pass

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    with Stealth().use_sync(sync_playwright()) as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = browser.new_context(
            user_agent=UA,
            locale="en-US",
            timezone_id="America/Los_Angeles",
            viewport={"width": 1440, "height": 1000},
        )
        page = context.new_page()
        page.on("response", on_response)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(8000)
            for _ in range(5):
                page.mouse.wheel(0, 3500)
                page.wait_for_timeout(1200)

            # Capture JSON embedded in Next.js / hydration / structured-data scripts.
            scripts = page.locator("script[type='application/json'], script[type='application/ld+json']")
            for index in range(min(scripts.count(), 150)):
                try:
                    text = scripts.nth(index).text_content()
                    if text:
                        add_capture(f"{page.url}#embedded-{index}", json.loads(text))
                except Exception:
                    pass

            title = page.title()
            body_text = page.locator("body").inner_text(timeout=5000)[:3000]
            challenge_text = f"{title} {body_text}".lower()
            if any(marker in challenge_text for marker in (
                "captcha", "verify you are human", "access denied", "datadome",
                "unusual traffic", "blocked", "just a moment",
            )):
                print(f"[{name}] anti-bot challenge detected; title={title!r}")
        except Exception as exc:
            print(f"[{name}] page load issue: {exc}")

        try:
            page.screenshot(path=f"debug-{slug}.png", full_page=True)
        except Exception:
            pass
        browser.close()

    return captured, json_urls


def extract_listings(blob):
    found = []

    def walk(node):
        if isinstance(node, dict):
            keys = {str(k).lower() for k in node}
            has_section = any(k in keys for k in (
                "section", "section_id", "section_name", "sectionname",
                "sid", "lid", "sg_section", "sec",
            ))
            has_price = any(k in keys for k in (
                "price", "display_price", "displayprice", "ticket_price",
                "amount", "p", "dp", "pf",
            ))
            if has_section and has_price:
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(blob)
    return found


def to_number(value):
    if isinstance(value, dict):
        for key in ("amount", "value", "price", "display"):
            if key in value:
                return to_number(value[key])
        return None
    try:
        cleaned = re.sub(r"[^\d.]", "", str(value))
        return float(cleaned) if cleaned else None
    except Exception:
        return None


def normalize(listing):
    low = {str(k).lower(): v for k, v in listing.items()}

    section = None
    for key in ("sid", "section_id", "section", "sg_section", "sec", "section_name", "sectionname", "lid"):
        if key in low and low[key] not in (None, ""):
            match = re.search(r"\d+", str(low[key]))
            if match:
                section = int(match.group())
                break

    price = None
    for key in ("price", "display_price", "displayprice", "ticket_price", "amount", "dp", "pf", "p"):
        if key in low and low[key] not in (None, ""):
            price = to_number(low[key])
            if price:
                break

    qty = None
    for key in ("quantity", "qty", "q", "available_quantity", "availablequantity"):
        if key in low:
            try:
                qty = int(low[key])
            except Exception:
                pass
            if qty is not None:
                break

    if qty is None:
        for key in ("splits", "split_options", "available_quantities", "availablequantities"):
            values = low.get(key)
            if isinstance(values, list) and values:
                try:
                    qty = max(int(x) for x in values)
                except Exception:
                    pass
                break

    return {"section": section, "price": price, "qty": qty}


def find_matches(captured):
    seen = set()
    matches = []
    all_prices = []

    for entry in captured:
        for raw in extract_listings(entry["data"]):
            normalized = normalize(raw)
            if normalized["price"]:
                all_prices.append(normalized["price"])
            if not all(normalized.get(k) for k in ("section", "price", "qty")):
                continue
            if not (SECTION_MIN <= normalized["section"] <= SECTION_MAX):
                continue
            if normalized["qty"] < MIN_QTY or normalized["price"] >= PRICE_LIMIT:
                continue
            key = (normalized["section"], normalized["qty"], normalized["price"])
            if key not in seen:
                seen.add(key)
                matches.append(normalized)

    matches.sort(key=lambda item: item["price"])
    return matches, min(all_prices) if all_prices else None


def load_state():
    try:
        with open(STATE_FILE) as file:
            state = json.load(file)
    except Exception:
        state = {}
    if not isinstance(state.get("best_prices"), dict):
        state["best_prices"] = {}
    return state


def save_state(state):
    with open(STATE_FILE, "w") as file:
        json.dump(state, file, indent=2, sort_keys=True)


def send_email(subject, body):
    user = os.environ.get("MAIL_USER")
    password = os.environ.get("MAIL_PASS")
    recipient = os.environ.get("MAIL_TO")
    if not (user and password and recipient):
        print("Mail secrets missing — printing instead:\n", subject, "\n", body)
        return
    message = EmailMessage()
    message["From"] = user
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as server:
        server.login(user, password)
        server.send_message(message)
    print("Alert email sent.")


def main():
    all_matches = []
    floors = {}

    print(
        f"Filters: sections {SECTION_MIN}-{SECTION_MAX}, qty {MIN_QTY}+, "
        f"price under ${PRICE_LIMIT:.0f}"
    )

    for source in SOURCES:
        try:
            captured, json_urls = scrape_source(source["url"], source["name"])
            matches, floor = find_matches(captured)
            floors[source["name"]] = floor
            for match in matches:
                match["source"] = source["name"]
                match["url"] = source["url"]
            all_matches.extend(matches)

            print(
                f"[{source['name']}] saw {len(json_urls)} JSON response(s), "
                f"captured {len(captured)} useful feed(s), "
                f"floor {('$%.0f' % floor) if floor else 'no data'}, "
                f"qualifying {len(matches)}"
            )
            if DEBUG:
                for endpoint in json_urls[:20]:
                    print(f"[{source['name']}] endpoint: {endpoint}")
        except Exception as exc:
            print(f"[{source['name']}] error: {exc}")

    floor_line = ", ".join(
        f"{name} {('$%.0f' % floor) if floor else 'no data'}"
        for name, floor in floors.items()
    )
    print("Floors:", floor_line)

    state = load_state()
    best_prices = state["best_prices"]
    fresh = []

    for match in all_matches:
        key = f"{match['source']}|{match['section']}|{match['qty']}"
        previous = best_prices.get(key)
        try:
            previous = float(previous) if previous is not None else None
        except (TypeError, ValueError):
            previous = None
        if previous is None or match["price"] < previous:
            match["previous_price"] = previous
            fresh.append(match)

    if not fresh:
        print("Nothing new or newly cheaper qualifies this run.")
        return

    grouped = {}
    for match in fresh:
        grouped.setdefault(match["source"], []).append(match)

    blocks = []
    for source_name, items in grouped.items():
        rows = []
        for item in items:
            change = (
                "new qualifying listing"
                if item["previous_price"] is None
                else f"down from ${item['previous_price']:.0f}"
            )
            rows.append(
                f"  Section {item['section']} — {item['qty']} seats — "
                f"${item['price']:.0f} each ({change})"
            )
        blocks.append(f"{source_name} ({items[0]['url']})\n" + "\n".join(rows))

    body = (
        f"100-200 level seats with {MIN_QTY}+ together under ${PRICE_LIMIT:.0f} "
        "for Match 94 at Lumen Field:\n\n"
        + "\n\n".join(blocks)
        + f"\n\nVenue floor by site: {floor_line}\n\nBuy fast.\n"
    )
    send_email(
        f"MATCH: {len(fresh)} new or cheaper set(s) under ${PRICE_LIMIT:.0f}",
        body,
    )

    for match in fresh:
        key = f"{match['source']}|{match['section']}|{match['qty']}"
        best_prices[key] = match["price"]
    state.pop("alerted", None)
    save_state(state)


if __name__ == "__main__":
    main()
