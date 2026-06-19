"""Run the ticket scraper with source-specific validation."""

import json

import scrape

MATCH_94_ID = "66a7e76989fae77676133f65"
_original_scrape_source = scrape.scrape_source


def is_match_94_payload(data):
    try:
        text = json.dumps(data, ensure_ascii=False).lower()
    except Exception:
        text = str(data).lower()
    return (
        MATCH_94_ID in text
        or "match 94" in text
        or ("2026-07-06" in text and "lumen field" in text)
        or ("round of 16" in text and "july 6" in text)
    )


def validated_scrape_source(url, name):
    captured, json_urls, blocked = _original_scrape_source(url, name)

    # SeatGeek may return only a challenge endpoint without challenge text in
    # the rendered page, so classify it from the response URL as well.
    if name == "SeatGeek" and any(
        "captcha-delivery.com" in endpoint.lower() for endpoint in json_urls
    ):
        blocked = True
        print("[SeatGeek] challenge response detected from endpoint URL")

    # Never process Gametime inventory or event payloads for another event.
    if name == "Gametime":
        valid = []
        rejected = 0
        for entry in captured:
            if is_match_94_payload(entry.get("data")):
                valid.append(entry)
            else:
                rejected += 1
        if rejected:
            print(f"[Gametime] rejected {rejected} payload(s) that did not match Match 94")
        captured = valid

    return captured, json_urls, blocked


scrape.scrape_source = validated_scrape_source

if __name__ == "__main__":
    scrape.main()
