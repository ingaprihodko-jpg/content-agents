import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from collections import Counter

def get_channel_page(channel: str, before: int | None = None) -> str:
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

def get_earliest_post_id(html: str) -> int | None:
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

if __name__ == "__main__":
    links = collect_links("dbeskromny", pages=15)
    domains = Counter(urlparse(l).netloc for l in links)
    print(f"Всего ссылок: {len(links)}")
    for domain, count in domains.most_common(30):
        print(f"{count:3d}  {domain}")
