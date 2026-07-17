import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from collections import Counter
from typing import Optional

def get_channel_page(channel: str, before: Optional[int] = None) -> str:
    url = f"https://t.me/s/{channel}"
    if before:
        url += f"?before={before}"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.text

def extract_links(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    return [
        a["href"] for a in soup.select(".tgme_widget_message_text a")
        if a.get("href") and not a["href"].startswith("https://t.me/")
    ]

def get_earliest_post_id(html: str) -> Optional[int]:
    soup = BeautifulSoup(html, "html.parser")
    ids = [
        int(msg["data-post"].split("/")[-1])
        for msg in soup.select(".tgme_widget_message")
        if msg.get("data-post")
    ]
    return min(ids) if ids else None

def collect_links(channel: str, pages: int = 10) -> list[str]:
    all_links = []
    before = None
    for _ in range(pages):
        html = get_channel_page(channel, before)
        all_links.extend(extract_links(html))
        new_before = get_earliest_post_id(html)
        if not new_before or new_before == before:
            break
        before = new_before
    return all_links

EXCLUDED_DOMAINS = {
    "t.me", "x.com", "telegram.me", "youtu.be",
    "apps.apple.com", "bit.ly", "clck.ru",
}

def normalize_domain(url: str) -> str:
    netloc = urlparse(url).netloc.lower()
    for prefix in ("http://", "https://"):
        if netloc.startswith(prefix):
            netloc = netloc[len(prefix):]
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc

def count_domains(links: list[str]) -> Counter:
    domains = (normalize_domain(l) for l in links)
    return Counter(d for d in domains if d and d not in EXCLUDED_DOMAINS)

if __name__ == "__main__":
    links = collect_links("dbeskromny", pages=15)
    domains = count_domains(links)
    print(f"Всего ссылок: {len(links)}")
    for domain, count in domains.most_common(30):
        print(f"{count:3d}  {domain}")
