import csv
import html
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

import anthropic
import feedparser
import trafilatura
from deep_translator import GoogleTranslator

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "stage0_bezkromny_parser"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "shared"))

from final_whitelist import FINAL_WHITELIST
from telegram_utils import send_message

RSS_STATUS_PATH = os.path.join(os.path.dirname(__file__), "rss_status.csv")
LOOKBACK_HOURS = 24
MAX_ARTICLES = 3
SUMMARY_SENTENCES = 3
ANTHROPIC_MODEL = "claude-sonnet-4-6"

MAX_CANDIDATES = 30
DIGEST_TOPICS = [
    "личный бренд", "переговоры", "психология влияния", "развитие ИИ",
    "паттерны поведения и принятия решений", "карта смыслов",
]

IRRELEVANT_PHRASES = [
    # спорт — конкретные турниры/события
    "world cup", "premier league", "champions league", "olympics", "olympic",
    "playoffs", "grand slam", "world series", "super bowl", "goalkeeper",
    "quarterback", "tournament", "wimbledon", "leaderboard",
    # спорт — общие категории (жанр, а не конкретное событие)
    "football", "soccer", " nba ", " nfl ", " mlb ", " nhl ", "golf", "tennis",
    "basketball", "baseball", "hockey", "rugby", "cricket", "athlete",
    "sports league", "sporting event",
    # погода
    "weather forecast", "heatwave", "heat wave", "storm warning", "hurricane",
    "wildfire smoke", "air quality alert", "cold front", "snowstorm", "rainfall",
    "climate forecast", "poor air quality",
    # реклама/шопинг
    "% off", "shop now", "black friday", "cyber monday", "promo code",
    "coupon code", "deals of the day", "best deals", "discount code",
    "limited-time offer", "sale ends",
]

# Разделы изданий, указывающие на спортивную рубрику — проверяются по пути URL,
# т.к. заголовок/summary такой статьи может не содержать явных спортивных слов
SPORTS_URL_SEGMENTS = [
    "/sport/", "/sports/", "/football/", "/soccer/", "/nba/", "/nfl/", "/mlb/",
    "/nhl/", "/golf/", "/tennis/", "/basketball/", "/baseball/", "/hockey/",
    "/rugby/", "/cricket/",
]

def is_sports_section(article: dict) -> bool:
    path = urlparse(article["link"]).path.lower()
    return any(segment in path for segment in SPORTS_URL_SEGMENTS)

def is_irrelevant(article: dict) -> bool:
    text = f"{article['title']} {article['summary']}".lower()
    if any(phrase in text for phrase in IRRELEVANT_PHRASES):
        return True
    return is_sports_section(article)

def stage1_candidates(articles: list, max_candidates: int = MAX_CANDIDATES) -> list:
    candidates = [a for a in articles if not is_irrelevant(a)]
    candidates.sort(key=lambda a: a["published"], reverse=True)
    return candidates[:max_candidates]

RANKED_POOL_SIZE = 10

def stage2_select(client: anthropic.Anthropic, candidates: list, pool_size: int = RANKED_POOL_SIZE) -> tuple:
    payload = [
        {"index": i, "domain": a["domain"], "title": a["title"], "summary": a["summary"]}
        for i, a in enumerate(candidates)
    ]
    prompt = (
        f"Вот список тем для дайджеста: [{', '.join(DIGEST_TOPICS)}].\n\n"
        f"Вот список статей (JSON с доменом, заголовком и кратким содержанием):\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        f"Составь ранжированный список из {pool_size} статей, которые ДЕЙСТВИТЕЛЬНО релевантны "
        f"этим темам по смыслу, а не по случайному совпадению слов. Отсортируй от самой "
        f"релевантной к наименее релевантной — это нужно, чтобы при постобработке можно было "
        f"пропустить статью и взять следующую по списку (например, если её домен уже занят).\n\n"
        f"Правила отбора:\n"
        f"1. По возможности не повторяй домен (поле domain) среди первых мест списка.\n"
        f"2. Предпочитай аналитические статьи — разбор, объяснение, исследование, осмысление "
        f"тенденции — над новостными заметками о конкретных заявлениях или событиях "
        f"(например, речь политика, объявление компании, разовая новость), даже если тема "
        f"формально совпадает. Этот дайджест — для чтения и размышления, а не новостная лента.\n\n"
        f"Для каждой статьи в списке укажи: почему она релевантна (одно предложение), "
        f"какая тема подходит лучше всего.\n\n"
        f'Ответь ТОЛЬКО валидным JSON без markdown-разметки в формате: '
        f'{{"ranked": [{{"index": <int>, "topic": "<строка>", "reason": "<строка>"}}, ...]}}, '
        f"порядок массива — от самой релевантной статьи к менее релевантной."
    )
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    raw_text = next(b.text for b in response.content if b.type == "text").strip()

    json_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.MULTILINE).strip()
    ranked = json.loads(json_text)["ranked"]

    ranked_articles = []
    for item in ranked:
        article = candidates[item["index"]]
        ranked_articles.append({
            **article,
            "claude_topic": item.get("topic", ""),
            "claude_reason": item.get("reason", ""),
        })
    return ranked_articles, raw_text

def enforce_domain_diversity(ranked_articles: list, count: int = MAX_ARTICLES) -> tuple:
    selected = []
    skipped = []
    seen_domains = set()
    for article in ranked_articles:
        if len(selected) >= count:
            break
        if article["domain"] in seen_domains:
            skipped.append(article)
            continue
        selected.append(article)
        seen_domains.add(article["domain"])
    return selected, skipped

CLICKBAIT_PHRASES = [
    "we asked", "tell us if", "quiz", "which character", "ranked",
    "you won't believe", "assigned", "guess",
]

def is_clickbait(article: dict) -> bool:
    title = article["title"].lower()
    return any(phrase in title for phrase in CLICKBAIT_PHRASES)

WIRED_REQUIRED_TERMS = ["research", "study", "analysis", "report", "according to"]

def passes_domain_rules(article: dict) -> bool:
    if article["domain"] == "wired.com":
        summary = article["summary"].lower()
        return any(term in summary for term in WIRED_REQUIRED_TERMS)
    return True

def load_rss_status() -> dict:
    status = {}
    with open(RSS_STATUS_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            status[row["domain"]] = row
    return status

def entry_datetime(entry):
    for key in ("published_parsed", "updated_parsed"):
        value = getattr(entry, key, None)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc)
    return None

def shorten_summary(raw_html: str, max_sentences: int = SUMMARY_SENTENCES) -> str:
    text = re.sub(r"<[^>]+>", "", raw_html or "").strip()
    text = html.unescape(text)
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return " ".join(sentences[:max_sentences]).strip()

def fetch_recent_entries(domain: str, rss_url: str, since: datetime) -> list:
    parsed = feedparser.parse(rss_url)
    recent = []
    skipped_no_date = 0
    for entry in parsed.entries:
        published = entry_datetime(entry)
        if published is None:
            skipped_no_date += 1
            continue
        if published >= since:
            recent.append({
                "domain": domain,
                "title": entry.get("title", "").strip(),
                "link": entry.get("link", "").strip(),
                "summary": shorten_summary(entry.get("summary", "")),
                "published": published,
            })
    if skipped_no_date:
        print(f"  ({domain}: у {skipped_no_date} записей нет даты, пропущены)")
    return recent

def collect_articles() -> list:
    rss_status = load_rss_status()
    since = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    articles = []

    for domain in FINAL_WHITELIST:
        row = rss_status.get(domain)
        has_rss = bool(row) and row["has_rss"] in ("True", "true", "1")

        if not has_rss:
            print(f"{domain}: нет RSS, требует ручной проверки")
            continue

        try:
            entries = fetch_recent_entries(domain, row["rss_url"], since)
        except Exception as exc:
            print(f"{domain}: ошибка загрузки фида — {exc}")
            continue

        print(f"{domain}: найдено {len(entries)} записей за последние {LOOKBACK_HOURS}ч")
        articles.extend(entries)

    return articles

def translate_ru(text: str) -> str:
    if not text:
        return text
    try:
        return GoogleTranslator(source="auto", target="ru").translate(text)
    except Exception as exc:
        print(f"  (перевод не удался, оставляю оригинал: {exc})")
        return text

def fetch_full_text(url: str) -> Optional[str]:
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return None
    return trafilatura.extract(downloaded)

def summarize_with_claude(client: anthropic.Anthropic, text: str) -> str:
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": f"Перескажи эту статью на русском в 2-3 предложениях, только суть: {text}",
        }],
    )
    return next(b.text for b in response.content if b.type == "text").strip()

def build_summary(client: anthropic.Anthropic, article: dict) -> str:
    full_text = fetch_full_text(article["link"])
    if full_text:
        try:
            return summarize_with_claude(client, full_text)
        except Exception as exc:
            print(f"  ({article['domain']}: пересказ через Claude не удался — {exc}, используем RSS-summary)")
    else:
        print(f"  ({article['domain']}: не удалось извлечь полный текст, используем RSS-summary)")
    return translate_ru(article["summary"])

def translate_articles(client: anthropic.Anthropic, articles: list) -> list:
    translated = []
    for article in articles:
        translated.append({
            **article,
            "title": translate_ru(article["title"]),
            "summary": build_summary(client, article),
        })
    return translated

def format_message(articles: list) -> str:
    lines = ["Дайджест за сутки:", ""]
    for i, article in enumerate(articles, 1):
        lines.append(f"{i}. {article['title']}")
        if article["summary"]:
            lines.append(article["summary"])
        lines.append(article["link"])
        lines.append("")
    return "\n".join(lines).strip()

if __name__ == "__main__":
    claude_client = anthropic.Anthropic()

    articles = collect_articles()
    print(f"\nВсего свежих записей за {LOOKBACK_HOURS}ч: {len(articles)}")

    filtered = [a for a in articles if not is_clickbait(a)]
    print(f"После анти-кликбейт фильтра: {len(filtered)}")

    filtered = [a for a in filtered if passes_domain_rules(a)]
    print(f"После доменных правил (wired.com — только research/study/analysis/report/according to): {len(filtered)}")

    candidates = stage1_candidates(filtered)
    print(f"Ступень 1 — отсеяно явно нерелевантное (спорт/погода/реклама), пул кандидатов: {len(candidates)}")

    ranked_articles, claude_raw_response = stage2_select(claude_client, candidates)
    print(f"\nСтупень 2 — ранжированный ответ Claude по отбору (сырой JSON, топ-{RANKED_POOL_SIZE}):\n")
    print(claude_raw_response)

    top_articles, skipped_for_domain = enforce_domain_diversity(ranked_articles)

    if skipped_for_domain:
        print(f"\nПрограммная проверка доменов: пропущено {len(skipped_for_domain)} статей из-за повтора домена "
              f"(взяты следующие по релевантности из ранжированного списка Claude):")
        for a in skipped_for_domain:
            print(f" - [{a['domain']}] {a['title']} — пропущена, домен уже занят")

    print(f"\nОтобрано {len(top_articles)} статей. Домены в топ-{MAX_ARTICLES}: {[a['domain'] for a in top_articles]}")
    for a in top_articles:
        print(f" - [{a['domain']}] {a['title']}")
        print(f"     тема: {a['claude_topic']}")
        print(f"     почему: {a['claude_reason']}")

    if not top_articles:
        print("Claude не выбрал ни одной статьи — сообщение не отправлено.")
    else:
        translated_articles = translate_articles(claude_client, top_articles)
        message = format_message(translated_articles)

        if "--dry-run" in sys.argv:
            print("\nТекст сообщения (dry-run, НЕ отправлено в Telegram):\n")
            print(message)
        else:
            chat_id = os.getenv("TELEGRAM_CHAT_ID_TEST")
            result = send_message(chat_id, message)
            print("\nОтправлено в Telegram:")
            print(result)
