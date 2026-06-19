"""World Cup Match 94 ticket alert for Lumen Field, Seattle."""

import json
import os
import re
import smtplib
import ssl
from email.message import EmailMessage

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

MATCH_94_ID = "66a7e76989fae77676133f65"
SOURCES = [
    {"name": "SeatGeek", "url": "https://seatgeek.com/fifa-world-cup-tickets/international-soccer/2026-07-06-5-pm/17248700"},
    {"name": "TickPick", "url": "https://www.tickpick.com/buy-fifa-world-cup-26-round-of-16-w81-vs-w82-match-94-tickets-lumen-field-7-6-26-5pm/6259622/"},
    {"name": "StubHub", "url": "https://www.stubhub.com/world-cup-seattle-tickets-7-6-2026/event/153020574/"},
    {"name": "Vivid Seats", "url": "https://www.vividseats.com/world-cup-soccer-tickets-lumen-field-7-6-2026--sports-soccer/production/5080857"},
    {"name": "Gametime", "url": f"https://gametime.co/soccer/fifa-world-cup-match-94-tickets/7-6-2026-seattle-wa-lumen-field/events/{MATCH_94_ID}"},
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
SECTION_KEYS = {
    "section", "section_id", "section_name", "sectionname", "section_label",
    "sectionlabel", "zone", "zone_name", "zonename", "sid", "lid", "sec",
    "sg_section", "area", "area_name",
}
PRICE_KEYS = {
    "price", "display_price", "displayprice", "ticket_price", "ticketprice",
    "price_with_fees", "pricewithfees", "all_in_price", "allinprice",
    "amount", "current_price", "currentprice", "face_value", "facevalue",
    "cost", "dp", "pf", "p",
}
QTY_KEYS = {
    "quantity", "qty", "q", "available_quantity", "availablequantity",
    "max_quantity", "maxquantity", "ticket_count", "ticketcount",
    "available_tickets", "availabletickets", "count",
}
SPLIT_KEYS = {
    "splits", "split_options", "splitoptions", "available_quantities",
    "availablequantities", "quantities", "valid_quantities", "validquantities",
}


def dump_text(data):
    try:
        return json.dumps(data, ensure_ascii=False).lower()
    except Exception:
        return str(data).lower()


def payload_looks_useful(data):
    text = dump_text(data)
    return any(x in text for x in ("listing", "inventory", "ticket", "offer")) and any(
        x in text for x in ("price", "amount", "cost")
    )


def is_match_94_payload(data):
    text = dump_text(data)
    return (
        MATCH_94_ID in text
        or "match 94" in text
        or ("2026-07-06" in text and "lumen field" in text)
        or ("round of 16" in text and "july 6" in text)
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
    signatures = set()
    blocked = False
    rejected_wrong_event = 0

    def add_capture(source_url, data, force=False):
        nonlocal rejected_wrong_event
        if name == "Gametime" and not is_match_94_payload(data):
            lower_url = source_url.lower()
            if "gametime" in lower_url and any(x in lower_url for x in ("/v1/events", "listing", "inventory", "ticket")):
                rejected_wrong_event += 1
            return
        if not force and not payload_looks_useful(data):
            return
        signature = dump_text(data)[:5000]
        if signature not in signatures:
            signatures.add(signature)
            captured.append({"url": source_url, "data": data})

    def on_response(response):
        nonlocal blocked
        try:
            lower_url = response.url.lower()
            if "captcha-delivery.com" in lower_url or "geo.captcha-delivery.com" in lower_url:
                blocked = True
            content_type = (response.headers or {}).get("content-type", "").lower()
            if "json" not in content_type:
                return
            json_urls.append(response.url)
            data = response.json()
            force = (
                "api.tickpick.com/1.0/listings/internal/event-v2/" in lower_url
                or ("stubhub" in lower_url and any(x in lower_url for x in ("listing", "inventory", "ticket")))
                or ("gametime" in lower_url and any(x in lower_url for x in ("/v1/events", "listing", "inventory", "ticket")))
            )
            if force or any(hint in lower_url for hint in FEED_HINTS) or payload_looks_useful(data):
                add_capture(response.url, data, force=force)
        except Exception:
            pass

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    with Stealth().use_sync(sync_playwright()) as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"],
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
            click_first(page, [
                "button:has-text('Accept All')", "button:has-text('Accept')",
                "button:has-text('I Agree')", "button:has-text('Got it')",
                "button[aria-label='Close']",
            ])

            if name == "StubHub":
                click_first(page, [
                    "button:has-text('Show tickets')", "button:has-text('View tickets')",
                    "button:has-text('List view')", "button:has-text('Tickets')",
                    "[data-testid*='list']",
                ])
            elif name == "Gametime":
                click_first(page, [
                    "button:has-text('Buy Tickets')", "button:has-text('See Tickets')",
                ])
                if MATCH_94_ID not in page.url:
                    print(f"[Gametime] navigation left Match 94; restoring canonical URL: {page.url}")
                    page.goto(url, wait_until="domcontentloaded", timeout=60000)
                    page.wait_for_timeout(5000)

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
            body = f"{title} {page.locator('body').inner_text(timeout=5000)[:4000]}".lower()
            if any(marker in body for marker in (
                "captcha", "verify you are human", "access denied", "datadome",
                "unusual traffic", "blocked", "just a moment",
            )):
                blocked = True
        except Exception as error:
            print(f"[{name}] page load issue: {error}")

        try:
            page.screenshot(path=f"debug-{slug}.png", full_page=True)
        except Exception:
            pass
        browser.close()

    if blocked:
        print(f"[{name}] challenge response detected")
    if name == "Gametime" and rejected_wrong_event:
        print(f"[Gametime] rejected {rejected_wrong_event} payload(s) for a different event")

    return captured, json_urls, blocked


def recursive_values(node, wanted_keys):
    values = []
    if isinstance(node, dict):
        for key, value in node.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in wanted_keys:
                values.append(value)
            values.extend(recursive_values(value, wanted_keys))
    elif isinstance(node, list):
        for value in node:
            values.extend(recursive_values(value, wanted_keys))
    return values


def to_number(value):
    if isinstance(value, dict):
        for key in ("amount", "value", "price", "display", "display_price", "formatted", "total", "min", "minimum"):
            if key in value:
                result = to_number(value[key])
                if result is not None:
                    return result
        return None
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r"[^\d.]", "", str(value))
    return float(cleaned) if cleaned else None


def parse_section(value):
    if isinstance(value, dict):
        for key in ("name", "label", "value", "section", "display_name", "displayname"):
            if key in value:
                result = parse_section(value[key])
                if result is not None:
                    return result
        return None
    match = re.search(r"(?<!\d)(1\d\d|2\d\d)(?!\d)", str(value))
    return int(match.group(1)) if match else None


def parse_qty(value):
    if isinstance(value, dict):
        for key in ("max", "maximum", "count", "quantity", "qty", "value"):
            if key in value:
                result = parse_qty(value[key])
                if result is not None:
                    return result
        return None
    if isinstance(value, list):
        values = [parse_qty(item) for item in value]
        values = [item for item in values if item is not None]
        return max(values) if values else None
    match = re.search(r"\d+", str(value))
    return int(match.group()) if match else None


def normalize(listing):
    section = next((parse_section(v) for v in recursive_values(listing, SECTION_KEYS) if parse_section(v) is not None), None)
    price = next((to_number(v) for v in recursive_values(listing, PRICE_KEYS) if to_number(v) not in (None, 0)), None)
    qty = next((parse_qty(v) for v in recursive_values(listing, QTY_KEYS) if parse_qty(v) is not None), None)
    if qty is None:
        qty = next((parse_qty(v) for v in recursive_values(listing, SPLIT_KEYS) if parse_qty(v) is not None), None)
    return {"section": section, "price": price, "qty": qty}


def extract_listings(blob):
    found = []

    def walk(node):
        if isinstance(node, dict):
            keys = {str(key).lower().replace("-", "_") for key in node}
            if (keys & PRICE_KEYS) and ((keys & SECTION_KEYS) or (keys & (QTY_KEYS | SPLIT_KEYS))):
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(blob)
    return found


def find_matches(captured, source_name):
    seen, matches, all_prices = set(), [], []
    candidates = 0
    for entry in captured:
        listings = extract_listings(entry["data"])
        candidates += len(listings)
        for raw in listings:
            item = normalize(raw)
            if item["price"]:
                all_prices.append(item["price"])
            if not all(item.values()):
                continue
            if SECTION_MIN <= item["section"] <= SECTION_MAX and item["qty"] >= MIN_QTY and item["price"] < PRICE_LIMIT:
                key = (item["section"], item["qty"], item["price"])
                if key not in seen:
                    seen.add(key)
                    matches.append(item)

    if DEBUG and captured and candidates == 0:
        print(f"[{source_name}] no listing-shaped objects found; sample={safe_preview(captured[0]['data'])}")
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
    user, password, recipient = os.getenv("MAIL_USER"), os.getenv("MAIL_PASS"), os.getenv("MAIL_TO")
    if not all((user, password, recipient)):
        print("Mail secrets missing — printing instead:\n", subject, "\n", body)
        return
    message = EmailMessage()
    message["From"], message["To"], message["Subject"] = user, recipient, subject
    message.set_content(body)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as server:
        server.login(user, password)
        server.send_message(message)
    print("Alert email sent.")


def main():
    print(f"Filters: sections {SECTION_MIN}-{SECTION_MAX}, qty {MIN_QTY}+, price under ${PRICE_LIMIT:.0f}")
    all_matches, floors = [], {}

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
                f"floor {('$%.0f' % floor) if floor else 'no data'}, qualifying {len(matches)}"
            )
            if DEBUG:
                for endpoint in json_urls[:25]:
                    print(f"[{source['name']}] endpoint: {endpoint}")
        except Exception as error:
            print(f"[{source['name']}] error: {error}")

    floor_line = ", ".join(
        f"{name} {('$%.0f' % floor) if floor else 'no data'}" for name, floor in floors.items()
    )
    print("Floors:", floor_line)

    state = load_state()
    best_prices = state["best_prices"]
    fresh = []
    for match in all_matches:
        key = f"{match['source']}|{match['section']}|{match['qty']}"
        previous = best_prices.get(key)
        previous = float(previous) if previous is not None else None
        if previous is None or match["price"] < previous:
            match["previous_price"] = previous
            fresh.append(match)

    if not fresh:
        print("Nothing new or newly cheaper qualifies this run.")
        return

    rows = []
    for match in fresh:
        change = "new qualifying listing" if match["previous_price"] is None else f"down from ${match['previous_price']:.0f}"
        rows.append(
            f"{match['source']}: Section {match['section']} — {match['qty']} seats — "
            f"${match['price']:.0f} each ({change})\n{match['url']}"
        )

    body = (
        "Match 94 qualifying tickets:\n\n" + "\n\n".join(rows)
        + f"\n\nVenue floor by site: {floor_line}\n\nBuy fast.\n"
    )
    send_email(f"MATCH: {len(fresh)} new or cheaper set(s) under ${PRICE_LIMIT:.0f}", body)

    for match in fresh:
        best_prices[f"{match['source']}|{match['section']}|{match['qty']}"] = match["price"]
    state.pop("alerted", None)
    save_state(state)


if __name__ == "__main__":
    main()
