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

SECTION_KEYS = (
    "section", "section_id", "section_name", "sectionname", "section_label",
    "sectionlabel", "zone", "zone_name", "zonename", "sid", "lid", "sec",
    "sg_section", "area", "area_name",
)
PRICE_KEYS = (
    "price", "display_price", "displayprice", "ticket_price", "ticketprice",
    "price_with_fees", "pricewithfees", "all_in_price", "allinprice",
    "amount", "current_price", "currentprice", "face_value", "facevalue",
    "cost", "dp", "pf", "p",
)
QTY_KEYS = (
    "quantity", "qty", "q", "available_quantity", "availablequantity",
    "max_quantity", "maxquantity", "ticket_count", "ticketcount",
    "available_tickets", "availabletickets", "count",
)
SPLIT_KEYS = (
    "splits", "split_options", "splitoptions", "available_quantities",
    "availablequantities", "quantities", "valid_quantities", "validquantities",
)


def payload_looks_useful(data):
    try:
        text = json.dumps(data).lower()
    except Exception:
        return False
    return (
        any(key in text for key in ("listing", "inventory", "ticket", "offer"))
        and any(key in text for key in ("price", "amount", "cost"))
    )


def safe_preview(data, limit=1200):
    try:
        return json.dumps(data, ensure_ascii=False)[:limit]
    except Exception:
        return str(data)[:limit]


def click_first(page, selectors):
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if locator.count() and locator.is_visible(timeout=1000):
                locator.click(timeout=3000)
                page.wait_for_timeout(1500)
                return True
        except Exception:
            pass
    return False


def scrape_source(url, name):
    captured = []
    json_urls = []
    captured_signatures = set()
    blocked = False

    def add_capture(source_url, data, force=False):
        if not force and not payload_looks_useful(data):
            return
        try:
            signature = json.dumps(data, sort_keys=True)[:4000]
        except Exception:
            signature = str(data)[:4000]
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
            lower_url = resp.url.lower()
            force = (
                "api.tickpick.com/1.0/listings/internal/event-v2/" in lower_url
                or "stubhub" in lower_url and any(x in lower_url for x in ("listing", "inventory", "ticket"))
                or "gametime" in lower_url and any(x in lower_url for x in ("listing", "inventory", "ticket", "/v1/events/"))
            )
            if force or any(hint in lower_url for hint in FEED_HINTS) or payload_looks_useful(data):
                add_capture(resp.url, data, force=force)
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
            page.wait_for_timeout(7000)

            # Dismiss common cookie/consent dialogs.
            click_first(page, [
                "button:has-text('Accept All')",
                "button:has-text('Accept')",
                "button:has-text('I Agree')",
                "button:has-text('Got it')",
                "button[aria-label='Close']",
            ])

            # Trigger list/inventory loading on sites that defer it until interaction.
            if name == "StubHub":
                click_first(page, [
                    "button:has-text('Show tickets')",
                    "button:has-text('View tickets')",
                    "button:has-text('List view')",
                    "button:has-text('Tickets')",
                    "[data-testid*='list']",
                ])
            elif name == "Gametime":
                click_first(page, [
                    "button:has-text('Buy Tickets')",
                    "button:has-text('See Tickets')",
                    "a:has-text('Buy Tickets')",
                    "a:has-text('See Tickets')",
                ])

            for _ in range(6):
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(1200)

            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

            scripts = page.locator(
                "script[type='application/json'], script[type='application/ld+json'], script#__NEXT_DATA__"
            )
            for index in range(min(scripts.count(), 200)):
                try:
                    text = scripts.nth(index).text_content()
                    if text:
                        add_capture(f"{page.url}#embedded-{index}", json.loads(text))
                except Exception:
                    pass

            title = page.title()
            body_text = page.locator("body").inner_text(timeout=5000)[:4000]
            challenge_text = f"{title} {body_text}".lower()
            if any(marker in challenge_text for marker in (
                "captcha", "verify you are human", "access denied", "datadome",
                "unusual traffic", "blocked", "just a moment",
            )):
                blocked = True
                print(f"[{name}] blocked by anti-bot challenge; title={title!r}")
        except Exception as exc:
            print(f"[{name}] page load issue: {exc}")

        try:
            page.screenshot(path=f"debug-{slug}.png", full_page=True)
        except Exception:
            pass
        browser.close()

    return captured, json_urls, blocked


def to_number(value):
    if isinstance(value, dict):
        for key in (
            "amount", "value", "price", "display", "display_price",
            "formatted", "total", "min", "minimum",
        ):
            if key in value:
                number = to_number(value[key])
                if number is not None:
                    return number
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        cleaned = re.sub(r"[^\d.]", "", str(value))
        return float(cleaned) if cleaned else None
    except Exception:
        return None


def parse_section(value):
    if isinstance(value, dict):
        for key in ("name", "label", "value", "section", "display_name", "displayname"):
            if key in value:
                section = parse_section(value[key])
                if section is not None:
                    return section
        return None
    match = re.search(r"(?<!\d)(1\d\d|2\d\d)(?!\d)", str(value))
    return int(match.group(1)) if match else None


def parse_qty(value):
    if isinstance(value, dict):
        for key in ("max", "maximum", "count", "quantity", "qty", "value"):
            if key in value:
                qty = parse_qty(value[key])
                if qty is not None:
                    return qty
        return None
    if isinstance(value, list):
        values = [parse_qty(item) for item in value]
        values = [item for item in values if item is not None]
        return max(values) if values else None
    try:
        match = re.search(r"\d+", str(value))
        return int(match.group()) if match else None
    except Exception:
        return None


def recursive_values(node, wanted_keys):
    values = []
    if isinstance(node, dict):
        for key, value in node.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in wanted_keys:
                values.append(value)
            values.extend(recursive_values(value, wanted_keys))
    elif isinstance(node, list):
        for item in node:
            values.extend(recursive_values(item, wanted_keys))
    return values


def normalize(listing):
    section = None
    price = None
    qty = None

    for value in recursive_values(listing, set(SECTION_KEYS)):
        section = parse_section(value)
        if section is not None:
            break

    for value in recursive_values(listing, set(PRICE_KEYS)):
        price = to_number(value)
        if price is not None and price > 0:
            break

    for value in recursive_values(listing, set(QTY_KEYS)):
        qty = parse_qty(value)
        if qty is not None:
            break

    if qty is None:
        for value in recursive_values(listing, set(SPLIT_KEYS)):
            qty = parse_qty(value)
            if qty is not None:
                break

    return {"section": section, "price": price, "qty": qty}


def looks_like_listing(node):
    if not isinstance(node, dict):
        return False
    keys = {str(key).lower().replace("-", "_") for key in node}
    return (
        bool(keys.intersection(SECTION_KEYS))
        and bool(keys.intersection(PRICE_KEYS))
    ) or (
        bool(keys.intersection(PRICE_KEYS))
        and bool(keys.intersection(QTY_KEYS + SPLIT_KEYS))
    )


def extract_listings(blob):
    found = []

    def walk(node):
        if isinstance(node, dict):
            if looks_like_listing(node):
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(blob)
    return found


def find_matches(captured, source_name):
    seen = set()
    matches = []
    all_prices = []
    candidates = 0

    for entry in captured:
        listings = extract_listings(entry["data"])
        candidates += len(listings)
        for raw in listings:
            normalized = normalize(raw)
            if normalized["price"]:
                all_prices.append(normalized["price"])
            if not all(normalized.get(key) for key in ("section", "price", "qty")):
                continue
            if not (SECTION_MIN <= normalized["section"] <= SECTION_MAX):
                continue
            if normalized["qty"] < MIN_QTY or normalized["price"] >= PRICE_LIMIT:
                continue
            key = (normalized["section"], normalized["qty"], normalized["price"])
            if key not in seen:
                seen.add(key)
                matches.append(normalized)

    if DEBUG and captured and candidates == 0:
        print(f"[{source_name}] no listing-shaped objects found; sample={safe_preview(captured[0]['data'])}")
    elif DEBUG and captured and not all_prices:
        print(f"[{source_name}] found {candidates} candidate object(s) but no prices; sample={safe_preview(captured[0]['data'])}")

    matches.sort(key=lambda item: item["price"])
    return matches, min(all_prices) if all_prices else None, candidates


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
            captured, json_urls, blocked = scrape_source(source["url"], source["name"])
            matches, floor, candidates = find_matches(captured, source["name"])
            floors[source["name"]] = floor
            for match in matches:
                match["source"] = source["name"]
                match["url"] = source["url"]
            all_matches.extend(matches)

            status = "blocked" if blocked else "ok"
            print(
                f"[{source['name']}] status {status}, saw {len(json_urls)} JSON response(s), "
                f"captured {len(captured)} useful feed(s), candidates {candidates}, "
                f"floor {('$%.0f' % floor) if floor else 'no data'}, "
                f"qualifying {len(matches)}"
            )
            if DEBUG:
                for endpoint in json_urls[:25]:
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
