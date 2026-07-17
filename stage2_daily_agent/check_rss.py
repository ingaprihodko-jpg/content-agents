import csv
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "stage0_bezkromny_parser"))
from final_whitelist import FINAL_WHITELIST

HEADERS = {"User-Agent": "Mozilla/5.0"}
TIMEOUT = 6

FEED_PATHS = [
    "/rss", "/feed", "/rss.xml", "/feed.xml", "/atom.xml",
    "/us/articles.atom", "/articles.atom", "/feed/all", "/rss/all.xml",
]

def looks_like_feed(text: str) -> bool:
    snippet = text[:1000].lower()
    return "<rss" in snippet or "<feed" in snippet

def try_feed_url(session: requests.Session, url: str):
    try:
        resp = session.get(url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException:
        return None
    if resp.status_code == 200 and looks_like_feed(resp.text):
        return resp.url
    return None

def find_feed_link_in_html(session: requests.Session, base_url: str):
    try:
        resp = session.get(base_url, headers=HEADERS, timeout=TIMEOUT, allow_redirects=True)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    link = soup.find("link", attrs={"type": ["application/rss+xml", "application/atom+xml"]})
    if link and link.get("href"):
        return urljoin(resp.url, link["href"])
    return None

def find_rss(domain: str):
    session = requests.Session()
    base_url = f"https://{domain}"
    for path in FEED_PATHS:
        found = try_feed_url(session, base_url + path)
        if found:
            return domain, found
    return domain, find_feed_link_in_html(session, base_url)

if __name__ == "__main__":
    results = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(find_rss, domain): domain for domain in FINAL_WHITELIST}
        for future in as_completed(futures):
            domain, rss_url = future.result()
            results[domain] = rss_url

    out_path = os.path.join(os.path.dirname(__file__), "rss_status.csv")
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["domain", "has_rss", "rss_url"])
        for domain in FINAL_WHITELIST:
            rss_url = results.get(domain)
            has_rss = bool(rss_url)
            writer.writerow([domain, has_rss, rss_url or ""])
            status = f"найден: {rss_url}" if has_rss else "не найден"
            print(f"{domain:35s} {status}")

    found_count = sum(1 for v in results.values() if v)
    print(f"\nИтого: {found_count}/{len(FINAL_WHITELIST)} доменов с RSS")
