import os
import sys
import requests
import yfinance as yf
import feedparser
import google.generativeai as genai
import holidays
import html
import json
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import time
import logging

# --- Holiday Check Module ---
def check_holidays(today=None):
    """
    Checks if today is a KR market holiday or if the previous weekday was a US market holiday.
    Returns: (is_kr_holiday, is_us_holiday_prev_close, holiday_name_kr, holiday_name_us)
    """
    if today is None:
        today = datetime.now().date()
    
    # 1. Check KR Market Holiday (Today)
    kr_holidays = holidays.KR()
    is_kr_holiday = today in kr_holidays
    holiday_name_kr = kr_holidays.get(today) if is_kr_holiday else None
    
    # 2. Check US Market Holiday (Previous Weekday)
    # Market close data usually comes from the previous trading day.
    # We need to check if the day we expect data from (yesterday, or Friday if today is Monday) was a holiday.
    us_holidays = holidays.US(state='NY') # APPROXIMATION for NYSE holidays
    
    # Find previous weekday
    offset = 1
    while True:
        prev_date = today - timedelta(days=offset)
        if prev_date.weekday() < 5: # Mon-Fri
            break
        offset += 1
        
    is_us_holiday_prev_close = prev_date in us_holidays
    holiday_name_us = us_holidays.get(prev_date) if is_us_holiday_prev_close else None
            
    return is_kr_holiday, is_us_holiday_prev_close, holiday_name_kr, holiday_name_us

# --- Configuration ---
# User-Agent to avoid being blocked by some news sites
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

PEF_HARD_EXCLUDE_KEYWORDS = [
    "태풍", "강풍", "폭우", "산불", "지진", "홍수", "한파", "폭염",
    "연예", "가수", "배우", "콘서트", "축제", "경기 결과", "야구", "축구",
    "농구", "배구", "epl", "kbo", "nba", "mlb"
]

PEF_SOFT_EXCLUDE_KEYWORDS = [
    "채용", "신제품", "출시", "프로모션", "이벤트", "전시", "리뷰", "쿠폰",
    "할인", "신차", "영화", "드라마", "공연"
]

PEF_STRONG_SIGNAL_KEYWORDS = [
    "m&a", "인수", "매각", "인수합병", "우선협상", "우협", "본입찰",
    "예비입찰", "실사", "경영권", "카브아웃", "carve-out", "spin-off",
    "ipo", "상장", "엑시트", "회수", "리파이낸싱", "인수금융", "대주단",
    "드라이파우더", "private credit", "사모대출", "밸류업", "구조조정",
    "turnaround", "운영효율화", "pmi", "tsa", "day-1", "day 1",
    "pef", "지분 매각", "경영권 매각"
]

PEF_MEDIUM_SIGNAL_KEYWORDS = [
    "사모펀드", "지분", "구주", "매물", "바이아웃", "바이사이드", "소수지분",
    "투자 유치", "소송", "분쟁", "주주", "주주간계약", "펀드레이징",
    "인프라", "에너지", "터미널", "발전소"
]

PEF_CATEGORY_KEYWORDS = {
    "deal_sourcing": [
        "m&a", "인수", "매각", "인수합병", "우선협상", "우협", "본입찰",
        "예비입찰", "실사", "경영권", "카브아웃", "carve-out", "분할 매각",
        "지분", "지분 매각", "구주", "매물", "경영권 매각", "바이아웃", "소수지분"
    ],
    "financing_exit": [
        "인수금융", "리파이낸싱", "대주단", "회사채", "차환", "유동성",
        "private credit", "사모대출", "상장", "ipo", "엑시트", "회수",
        "유상증자", "메자닌"
    ],
    "portfolio_ops": [
        "밸류업", "구조조정", "턴어라운드", "turnaround", "운영효율화",
        "원가절감", "현금흐름", "거버넌스", "포트폴리오", "시너지"
    ],
    "it_pmi": [
        "전산", "전산 통합", "it 통합", "it 인프라", "erp", "sap", "crm",
        "클라우드", "데이터센터", "사이버", "보안", "cyber", "데이터 거버넌스",
        "시스템 통합", "애플리케이션", "application", "tsa", "day-1", "day 1", "분리"
    ],
    "macro_regulation": [
        "금리", "관세", "규제", "정책", "환율", "공정위", "금감원", "반독점",
        "유가", "원자재", "거시", "매크로"
    ],
    "governance_legal": [
        "소송", "분쟁", "주주간계약", "배임", "횡령", "책임론", "거버넌스"
    ],
}

PEF_CATEGORY_LABELS = {
    "deal_sourcing": "Deal Sourcing",
    "financing_exit": "Financing & Exit",
    "portfolio_ops": "Portfolio Ops",
    "it_pmi": "IT PMI",
    "macro_regulation": "Macro & Regulation",
    "governance_legal": "Governance & Legal",
}

PEF_TRUSTED_SOURCE_KEYWORDS = [
    "연합인포맥스", "연합뉴스", "한국경제", "매일경제", "머니투데이", "더벨",
    "딜사이트", "reuters", "bloomberg", "financial times", "wsj", "wall street journal"
]

PEF_LOW_SIGNAL_SOURCE_KEYWORDS = [
    "냉동공조저널", "기계신문", "주달", "ipdaily", "pressclub global", "brunch.co.kr"
]

FIRM_SHORT_NAME_CONTEXT_KEYWORDS = [
    "pef", "사모펀드", "m&a", "인수", "매각", "컨소시엄", "트랙레코드",
    "임태호", "애큐온", "캐피탈", "저축은행", "운용사"
]

TELEGRAM_MESSAGE_LIMIT = 3900
PEF_MIN_ACCEPTED_ARTICLES = 3
PEF_FIRM_MENTION_MAX_ARTICLES = 5
DEFAULT_NEWS_HISTORY_FILE = ".news_history.json"
DEFAULT_NEWS_HISTORY_RETENTION_DAYS = 30
DEFAULT_NEWS_HISTORY_TITLE_MATCH_DAYS = 7


# --- Logging Configuration ---
def setup_logging():
    # Create a custom logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Remove existing handlers if any
    if logger.hasHandlers():
        logger.handlers.clear()

    # File Handler - Writes to configured log file (default: latest_run.log), overwriting each time
    log_file_path = os.getenv("LOG_FILE_PATH", "latest_run.log")
    file_handler = logging.FileHandler(log_file_path, mode='w', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    # Console Handler - Writes to stdout
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(message)s') # Keep console clean
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)


def normalize_text(*parts):
    return " ".join(part for part in parts if part).lower()


def extract_article_source(title):
    parts = title.rsplit(" - ", 1)
    return parts[1].strip() if len(parts) == 2 else "Unknown"


def evaluate_pef_article(title, link, content):
    """
    Score a candidate article for PEF usefulness before it reaches the prompt.
    """
    source = extract_article_source(title)
    text = normalize_text(title, content, source, link)

    hard_noise_hits = sorted({kw for kw in PEF_HARD_EXCLUDE_KEYWORDS if kw in text})
    soft_noise_hits = sorted({kw for kw in PEF_SOFT_EXCLUDE_KEYWORDS if kw in text})
    strong_signal_hits = sorted({kw for kw in PEF_STRONG_SIGNAL_KEYWORDS if kw in text})
    medium_signal_hits = sorted({kw for kw in PEF_MEDIUM_SIGNAL_KEYWORDS if kw in text})

    categories = []
    category_reason_samples = []
    score = 0

    for category, keywords in PEF_CATEGORY_KEYWORDS.items():
        hits = [kw for kw in keywords if kw in text]
        if hits:
            categories.append(category)
            score += 2 if category in {"deal_sourcing", "financing_exit", "it_pmi"} else 1
            label = PEF_CATEGORY_LABELS.get(category, category)
            category_reason_samples.append(f"{label}:{', '.join(hits[:2])}")

    if strong_signal_hits:
        score += min(len(strong_signal_hits), 3) * 2
    if medium_signal_hits:
        score += min(len(medium_signal_hits), 2)

    if content:
        if len(content) >= 250:
            score += 1
        elif len(content) < 120:
            score -= 1
    else:
        score -= 1

    source_lower = source.lower()
    trusted_source = any(token.lower() in source_lower for token in PEF_TRUSTED_SOURCE_KEYWORDS)
    low_signal_source = any(token.lower() in source_lower for token in PEF_LOW_SIGNAL_SOURCE_KEYWORDS)

    if trusted_source:
        score += 2
    if low_signal_source:
        score -= 2
    if hard_noise_hits:
        score -= 6
    if soft_noise_hits:
        score -= 2

    categories_set = set(categories)
    has_core_signal = bool(
        strong_signal_hits
        or medium_signal_hits
        or (categories_set - {"macro_regulation"})
        or ("macro_regulation" in categories_set and trusted_source)
    )
    accepted = score >= 3 and has_core_signal and not hard_noise_hits
    promotable = score >= 1 and has_core_signal and not hard_noise_hits and not low_signal_source

    reasons = []
    if strong_signal_hits:
        reasons.append(f"signal:{', '.join(strong_signal_hits[:3])}")
    if medium_signal_hits:
        reasons.append(f"medium_signal:{', '.join(medium_signal_hits[:3])}")
    reasons.extend(category_reason_samples[:3])
    if trusted_source:
        reasons.append(f"trusted_source:{source}")
    if low_signal_source:
        reasons.append(f"low_signal_source:{source}")
    if soft_noise_hits:
        reasons.append(f"soft_noise:{', '.join(soft_noise_hits[:2])}")
    if hard_noise_hits:
        reasons.append(f"hard_noise:{', '.join(hard_noise_hits[:2])}")
    if not content:
        reasons.append("missing_content")

    return {
        "accepted": accepted,
        "score": score,
        "source": source,
        "categories": [PEF_CATEGORY_LABELS.get(category, category) for category in categories],
        "reasons": reasons or ["no_strong_signal"],
        "trusted_source": trusted_source,
        "promotable": promotable,
    }


def append_article_context(existing_context, entry, content, target="general", pef_meta=None):
    article_context = ""

    if content:
        article_context += f"\n\n--- ARTICLE START ---\n"
        article_context += f"Title: {entry.title}\n"
        article_context += f"Link: {entry.link}\n"
        article_context += f"Date: {getattr(entry, 'published', 'Unknown')}\n"
        if target == "pef" and pef_meta:
            article_context += f"Category: {', '.join(pef_meta['categories'])}\n"
        article_context += f"Content:\n{content}\n"
        article_context += f"--- ARTICLE END ---\n"
    else:
        article_context += f"\nTitle: {entry.title}\nLink: {entry.link}\n(Content scraping failed)\n"
        if target == "pef" and pef_meta:
            article_context += f"Category: {', '.join(pef_meta['categories'])}\n"

    return existing_context + article_context


def get_pef_persona_config():
    return {
        "firm_name": os.getenv("PEF_FIRM_NAME", "Baikal Investment"),
        "pmi_role": os.getenv("PEF_PMI_ROLE", "IT PMI Lead"),
    }


def parse_int_env(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def parse_bool_env(name, default=True):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def build_firm_news_queries(firm_name):
    firm_name = firm_name or ""
    env_queries = os.getenv("PEF_FIRM_NEWS_QUERIES")
    if env_queries:
        return [query.strip() for query in env_queries.split(",") if query.strip()]

    queries = {firm_name.strip()} if firm_name else set()
    if "baikal" in firm_name.lower() or "바이칼" in firm_name:
        queries.update({
            "바이칼인베스트먼트",
            "바이칼 인베스트먼트",
            "바이칼인베",
        })

    return sorted(query for query in queries if query)


def build_firm_match_terms(firm_name):
    firm_name = firm_name or ""
    terms = {firm_name.strip().lower()} if firm_name else set()
    terms.update({query.lower() for query in build_firm_news_queries(firm_name)})
    if "baikal" in firm_name.lower() or "바이칼" in firm_name:
        terms.update({
            "baikal investment",
            "바이칼인베스트먼트",
            "바이칼 인베스트먼트",
            "바이칼인베",
        })
    return sorted(term for term in terms if term)


def match_firm_mention(searchable_text, match_terms, firm_name):
    if any(term in searchable_text for term in match_terms):
        return True, "exact_name"

    firm_name = firm_name or ""
    if ("baikal" in firm_name.lower() or "바이칼" in firm_name) and "바이칼" in searchable_text:
        if any(keyword in searchable_text for keyword in FIRM_SHORT_NAME_CONTEXT_KEYWORDS):
            return True, "short_name_with_deal_context"

    return False, "firm name not found in title/content"


def normalize_title_for_dedupe(title):
    normalized = re.sub(r"\s+", " ", title or "").strip().lower()
    return re.sub(r"\s+-\s+[^-]+$", "", normalized)


def get_news_history_path():
    return os.getenv("NEWS_HISTORY_FILE", DEFAULT_NEWS_HISTORY_FILE)


def parse_history_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def normalize_history_articles(raw_data):
    if isinstance(raw_data, dict):
        articles = raw_data.get("articles", [])
    elif isinstance(raw_data, list):
        articles = raw_data
    else:
        articles = []

    normalized = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        link = article.get("link")
        title = article.get("title", "")
        title_key = article.get("title_key") or normalize_title_for_dedupe(title)
        if not link and not title_key:
            continue
        normalized.append({
            "link": link,
            "title": title,
            "title_key": title_key,
            "target": article.get("target", "unknown"),
            "collected_at": article.get("collected_at"),
        })
    return normalized


def build_news_history_state(articles, path, title_key_cutoff_date=None, title_match_enabled=True):
    links = {article["link"] for article in articles if article.get("link")}
    title_keys = set()
    if title_match_enabled:
        for article in articles:
            title_key = article.get("title_key")
            if not title_key:
                continue
            collected_date = parse_history_date(article.get("collected_at"))
            if title_key_cutoff_date and collected_date and collected_date < title_key_cutoff_date:
                continue
            title_keys.add(title_key)
    return {
        "path": path,
        "articles": articles,
        "links": links,
        "title_keys": title_keys,
        "title_match_enabled": title_match_enabled,
    }


def load_news_history(today=None):
    path = get_news_history_path()
    reference_date = today or datetime.now().date()
    retention_days = max(1, parse_int_env("NEWS_HISTORY_RETENTION_DAYS", DEFAULT_NEWS_HISTORY_RETENTION_DAYS))
    title_match_days = max(
        0,
        parse_int_env("NEWS_HISTORY_TITLE_MATCH_DAYS", DEFAULT_NEWS_HISTORY_TITLE_MATCH_DAYS)
    )
    cutoff_date = reference_date - timedelta(days=retention_days)
    title_match_enabled = title_match_days > 0
    title_key_cutoff_date = reference_date - timedelta(days=title_match_days) if title_match_enabled else None

    try:
        with open(path, "r", encoding="utf-8") as history_file:
            raw_data = json.load(history_file)
    except FileNotFoundError:
        logging.info(f"   [News History] No history file found. Starting fresh: {path}")
        return build_news_history_state([], path, title_match_enabled=title_match_enabled)
    except (OSError, json.JSONDecodeError) as e:
        logging.warning(f"   [News History] Could not load {path}: {e}. Starting fresh.")
        return build_news_history_state([], path, title_match_enabled=title_match_enabled)

    articles = []
    for article in normalize_history_articles(raw_data):
        collected_date = parse_history_date(article.get("collected_at"))
        if collected_date and collected_date < cutoff_date:
            continue
        articles.append(article)

    logging.info(
        f"   [News History] Loaded {len(articles)} recently collected articles "
        f"(retention={retention_days}d, title_match={title_match_days}d)."
    )
    return build_news_history_state(
        articles,
        path,
        title_key_cutoff_date=title_key_cutoff_date,
        title_match_enabled=title_match_enabled
    )


def save_news_history(history):
    if not history:
        return

    payload = {
        "version": 1,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "articles": history.get("articles", []),
    }
    path = history.get("path") or get_news_history_path()
    try:
        with open(path, "w", encoding="utf-8") as history_file:
            json.dump(payload, history_file, ensure_ascii=False, indent=2)
        logging.info(f"   [News History] Saved {len(payload['articles'])} articles to {path}.")
    except OSError as e:
        logging.error(f"   [News History] Failed to save {path}: {e}")


def should_skip_seen_article(entry, history, seen_title_keys=None):
    if not history:
        title_key = normalize_title_for_dedupe(entry.title)
        if seen_title_keys is not None and title_key in seen_title_keys:
            return True, "title_in_run", title_key
        return False, None, title_key

    title_key = normalize_title_for_dedupe(entry.title)
    if entry.link in history.get("links", set()):
        return True, "link", title_key
    if title_key and title_key in history.get("title_keys", set()):
        return True, "title", title_key
    if seen_title_keys is not None and title_key in seen_title_keys:
        return True, "title_in_run", title_key
    return False, None, title_key


def mark_article_collected(history, entry, target, collected_date=None):
    if not history:
        return

    title_key = normalize_title_for_dedupe(entry.title)
    link = entry.link
    if link in history.get("links", set()) or title_key in history.get("title_keys", set()):
        return

    collected_at = (collected_date or datetime.now().date()).isoformat()
    history["articles"].append({
        "link": link,
        "title": entry.title,
        "title_key": title_key,
        "target": target,
        "collected_at": collected_at,
    })
    if link:
        history["links"].add(link)
    if title_key and history.get("title_match_enabled", True):
        history["title_keys"].add(title_key)


def split_message(message, limit=TELEGRAM_MESSAGE_LIMIT):
    """
    Split a long Telegram message into line-aware chunks that stay within the limit.
    """
    if len(message) <= limit:
        return [message]

    chunks = []
    current = ""

    for line in message.splitlines(keepends=True):
        if len(current) + len(line) <= limit:
            current += line
            continue

        if current.strip():
            chunks.append(current.rstrip())
            current = ""

        while len(line) > limit:
            split_at = line.rfind("\n", 0, limit)
            if split_at <= 0:
                split_at = line.rfind(" ", 0, limit)
            if split_at <= 0:
                split_at = limit

            chunk = line[:split_at].rstrip()
            if chunk:
                chunks.append(chunk)
            line = line[split_at:].lstrip("\n")

        current = line

    if current.strip():
        chunks.append(current.rstrip())

    return chunks


def sanitize_telegram_html(message):
    """
    Escape raw ampersands, which frequently break Telegram HTML parsing.
    """
    return re.sub(r"&(?!#?\w+;)", "&amp;", message)


def convert_html_to_plain_text(message):
    """
    Convert a Telegram HTML message into plain text while preserving links.
    """
    anchor_pattern = re.compile(r"<a\s+href=(['\"])(.*?)\1>(.*?)</a>", re.IGNORECASE | re.DOTALL)

    def replace_anchor(match):
        url = html.unescape(match.group(2).strip())
        label = BeautifulSoup(match.group(3), "html.parser").get_text(" ", strip=True)
        label = html.unescape(label)
        return f"{label} ({url})" if label else url

    plain_message = anchor_pattern.sub(replace_anchor, message)
    plain_message = re.sub(r"</?(b|i|u|s|code|pre)>", "", plain_message, flags=re.IGNORECASE)
    plain_message = BeautifulSoup(plain_message, "html.parser").get_text("\n")
    plain_message = html.unescape(plain_message)
    plain_message = re.sub(r"\n{3,}", "\n\n", plain_message)
    return plain_message.strip()


def send_telegram_chunks(url, chat_id, message, parse_mode=None):
    """
    Send one logical message to Telegram, splitting into multiple chunks if needed.
    """
    chunks = split_message(message)
    total_chunks = len(chunks)

    for idx, chunk in enumerate(chunks, start=1):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        logging.info(
            f"   Sending Telegram chunk {idx}/{total_chunks} "
            f"({len(chunk)} chars, mode={parse_mode or 'PLAIN'})..."
        )
        response = requests.post(url, json=payload, timeout=15)
        if response.ok:
            continue

        return False, response

    return True, None


def build_news_links_message(links, title="🔗 금일 수집된 주요 뉴스 링크"):
    if not links:
        return None

    lines = [f"<b>{html.escape(title)}</b>"]
    for article_title, article_link in links:
        safe_title = html.escape(article_title)
        safe_link = html.escape(article_link, quote=True)
        lines.append(f'- <a href="{safe_link}">{safe_title}</a>')
    return "\n".join(lines)

# --- Data Fetcher Module ---
def fetch_market_data():
    """
    Fetches key market indices and exchange rates, including Philly Semi and Russell 2000.
    """
    tickers = {
        "KOSPI": "^KS11",
        "KOSDAQ": "^KQ11",
        "S&P 500": "^GSPC",
        "NASDAQ": "^IXIC",
        "DOW JONES": "^DJI",
        "RUSSELL 2000": "^RUT",
        "PHILLY SEMI": "^SOX", # Philadelphia Semiconductor
        "USD/KRW": "KRW=X",
        "BTC/USD": "BTC-USD"
    }
    
    data = {}
    data = {}
    logging.info("   Fetching market data...")
    
    for name, symbol in tickers.items():
        try:
            ticker = yf.Ticker(symbol)
            # Get the latest day's data
            history = ticker.history(period="5d")
            
            if not history.empty:
                current_price = history['Close'].iloc[-1]
                prev_close = history['Close'].iloc[-2] if len(history) > 1 else current_price
                change = current_price - prev_close
                pct_change = (change / prev_close) * 100
                
                data[name] = {
                    "price": current_price,
                    "change": change,
                    "pct_change": pct_change
                }
            else:
                data[name] = None
        except Exception as e:
            logging.error(f"   Error fetching {name}: {e}")
            data[name] = None
            
    return data

def scrape_article_content(url):
    """
    Fetches and extracts the main text content from a news article URL.
    """
    try:
        # Google News links are often redirects, requests usually handles them but let's be safe
        response = requests.get(url, headers=HEADERS, timeout=5)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()
            
        # Get text
        text = soup.get_text(separator='\n')
        
        # Break into lines and remove leading/trailing space on each
        lines = (line.strip() for line in text.splitlines())
        # Break multi-headlines into a line each
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        # Drop blank lines
        text = '\n'.join(chunk for chunk in chunks if chunk)
        
        # Limit text length to avoid token limits (approx 800 chars per article is usually enough for summary)
        return text[:800]
        
    except Exception as e:
        logging.error(f"   Failed to scrape {url}: {e}")
        return None

def fetch_news(
    mode="weekday",
    is_us_holiday=False,
    is_kr_holiday=False,
    target="general",
    initial_seen_links=None,
    news_history=None,
    collected_date=None,
):
    """
    Fetches top economic news using Google News RSS with specific search queries
    based on the mode (weekday/saturday/sunday) and holiday status.
    """
    
    if mode == "saturday":
        logging.info("   [Mode] Saturday: Focusing on US Market Close & Global News")
        queries = [
            "미국 증시 마감",     # US Market Close
            "주간 해외 증시",     # Weekly Overseas Market
            "글로벌 경제뉴스"     # Global Economic News
        ]
    elif mode == "sunday":
        logging.info("   [Mode] Sunday: Focusing on Weekly Summary & Next Week Outlook")
        queries = [
            "주간 증시 정리",     # Weekly Market Summary
            "다음주 증시 일정",   # Next Week Market Schedule
            "다음주 경제 캘린더", # Next Week Economic Calendar
            "주간 증시 전망"      # Weekly Market Outlook
        ]
    else: # weekday
        if is_kr_holiday:
            if is_us_holiday:
                logging.info("   [Mode] Weekday (KR & US Holiday): Focusing on Global Economy")
                queries = [
                    "글로벌 경제뉴스",     # Global Economic News
                    "해외 증시 요약",      # Overseas Market Summary
                    "미국 경제 뉴스"       # US Economic News
                ]
            else:
                logging.info("   [Mode] Weekday (KR Holiday): Focusing on US Market & Global News")
                queries = [
                    "미국 증시 마감",     # US Market Close
                    "글로벌 경제뉴스",     # Global Economic News
                    "주요 해외 뉴스"      # Major Overseas News
                ]
        elif is_us_holiday:
            logging.info("   [Mode] Weekday (US Holiday): Focusing on General US Economy")
            queries = [
                "미국 경제 뉴스",     # US Economic News (Generic)
                "특징주",            # Hot Stocks
                "국내 증시 전망"      # Korea Market Outlook
            ]
        else:
            logging.info("   [Mode] Weekday: Focusing on Daily Market Outlook")
            queries = [
                "미국 증시 마감",     # US Market Close
                "특징주",            # Hot Stocks
                "국내 증시 전망"      # Korea Market Outlook
            ]
            
    if target == "pef":
        logging.info("   [Target] PEF: Adding M&A and Private Equity queries")
        queries.extend([
            "사모펀드",
            "M&A 인수합병",
            "PEF 투자",
            "M&A PMI",
            "IT 통합",
            "지분 매각",
            "경영권 매각",
            "인수금융"
        ])
    
    combined_news_context = ""
    seen_links = set(initial_seen_links) if initial_seen_links else set()
    seen_title_keys = set()
    collected_links = [] # List to store (title, link)
    rejected_pef_candidates = []
    
    logging.info("   Fetching news and scraping content...")
    
    for query in queries:
        # Append ' when:1d' to the query to restrict results to the past 24 hours
        time_restricted_query = f"{query} when:1d"
        rss_url = f"https://news.google.com/rss/search?q={time_restricted_query}&hl=ko&gl=KR&ceid=KR%3Ako"
        try:
            response = requests.get(rss_url, timeout=10)
            feed = feedparser.parse(response.content)
            
            # Take top 3 articles per query to get even more diverse news
            for entry in feed.entries[:3]:
                if entry.link in seen_links:
                    continue
                skip_article, skip_reason, title_key = should_skip_seen_article(
                    entry,
                    news_history,
                    seen_title_keys=seen_title_keys
                )
                if skip_article:
                    logging.info(
                        f"   [News History] SKIP already collected ({skip_reason}): {entry.title}"
                    )
                    seen_links.add(entry.link)
                    seen_title_keys.add(title_key)
                    continue

                seen_links.add(entry.link)
                seen_title_keys.add(title_key)
                
                logging.info(f"   - Processing: {entry.title}")
                content = scrape_article_content(entry.link)
                
                if target == "pef":
                    pef_meta = evaluate_pef_article(entry.title, entry.link, content)
                    decision = "ACCEPT" if pef_meta["accepted"] else "REJECT"
                    logging.info(
                        f"   [PEF Filter] {decision} score={pef_meta['score']} "
                        f"source={pef_meta['source']} categories={', '.join(pef_meta['categories']) or 'None'}"
                    )
                    if not pef_meta["accepted"]:
                        logging.info(f"      reasons: {', '.join(pef_meta['reasons'])}")
                        if pef_meta["promotable"]:
                            rejected_pef_candidates.append({
                                "entry": entry,
                                "content": content,
                                "pef_meta": pef_meta
                            })
                        continue

                combined_news_context = append_article_context(
                    combined_news_context,
                    entry,
                    content,
                    target=target,
                    pef_meta=pef_meta if target == "pef" else None
                )
                collected_links.append((entry.title, entry.link))
                mark_article_collected(news_history, entry, target, collected_date=collected_date)
                    
        except Exception as e:
            logging.error(f"   Error fetching RSS for {query}: {e}")

    if target == "pef" and len(collected_links) < PEF_MIN_ACCEPTED_ARTICLES and rejected_pef_candidates:
        needed = PEF_MIN_ACCEPTED_ARTICLES - len(collected_links)
        logging.info(
            f"   [PEF Filter] Accepted {len(collected_links)} articles; "
            f"promoting up to {needed} borderline candidates for coverage."
        )
        rejected_pef_candidates.sort(
            key=lambda candidate: (
                candidate["pef_meta"]["score"],
                1 if candidate["pef_meta"]["trusted_source"] else 0,
                len(candidate["content"] or "")
            ),
            reverse=True
        )

        for candidate in rejected_pef_candidates:
            if needed <= 0:
                break
            if any(link == candidate["entry"].link for _, link in collected_links):
                continue

            pef_meta = candidate["pef_meta"]
            logging.info(
                f"   [PEF Filter] PROMOTE score={pef_meta['score']} "
                f"source={pef_meta['source']} categories={', '.join(pef_meta['categories']) or 'None'}"
            )
            combined_news_context = append_article_context(
                combined_news_context,
                candidate["entry"],
                candidate["content"],
                target="pef",
                pef_meta=pef_meta
            )
            collected_links.append((candidate["entry"].title, candidate["entry"].link))
            mark_article_collected(
                news_history,
                candidate["entry"],
                target,
                collected_date=collected_date
            )
            needed -= 1

    return combined_news_context, collected_links, seen_links


def fetch_firm_mention_news(firm_name, initial_seen_links=None, news_history=None, collected_date=None):
    """
    Fetches recent news that directly mentions the GP name.
    This runs separately from the PEF filter so firm mentions are not lost.
    """
    lookback_days = max(1, parse_int_env("PEF_FIRM_NEWS_LOOKBACK_DAYS", 30))
    queries = build_firm_news_queries(firm_name)
    match_terms = build_firm_match_terms(firm_name)
    seen_links = set(initial_seen_links) if initial_seen_links else set()
    seen_titles = set()
    combined_news_context = ""
    collected_links = []

    if not queries:
        return combined_news_context, collected_links, seen_links

    logging.info(
        f"   [Target] Firm mentions: Searching {firm_name} news "
        f"(lookback={lookback_days}d, queries={', '.join(queries)})"
    )

    for query in queries:
        rss_query = f'"{query}" when:{lookback_days}d'
        try:
            response = requests.get(
                "https://news.google.com/rss/search",
                params={
                    "q": rss_query,
                    "hl": "ko",
                    "gl": "KR",
                    "ceid": "KR:ko",
                },
                timeout=10,
            )
            feed = feedparser.parse(response.content)

            for entry in feed.entries[:5]:
                if len(collected_links) >= PEF_FIRM_MENTION_MAX_ARTICLES:
                    return combined_news_context, collected_links, seen_links

                title_key = normalize_title_for_dedupe(entry.title)
                if entry.link in seen_links or title_key in seen_titles:
                    continue
                skip_article, skip_reason, title_key = should_skip_seen_article(
                    entry,
                    news_history,
                    seen_title_keys=seen_titles
                )
                if skip_article:
                    logging.info(
                        f"      [News History] SKIP already collected ({skip_reason}): {entry.title}"
                    )
                    seen_links.add(entry.link)
                    seen_titles.add(title_key)
                    continue

                logging.info(f"   - Firm mention candidate: {entry.title}")
                content = scrape_article_content(entry.link)
                searchable_text = normalize_text(entry.title, content, entry.link)
                is_match, match_reason = match_firm_mention(searchable_text, match_terms, firm_name)
                if not is_match:
                    logging.info(f"      [Firm Mention] REJECT: {match_reason}")
                    continue

                seen_links.add(entry.link)
                seen_titles.add(title_key)
                logging.info(f"      [Firm Mention] ACCEPT: {match_reason}")

                firm_context = ""
                firm_context += f"\n\n--- FIRM MENTION ARTICLE START ---\n"
                firm_context += f"Target Firm: {firm_name}\n"
                firm_context += f"Title: {entry.title}\n"
                firm_context += f"Link: {entry.link}\n"
                firm_context += f"Date: {getattr(entry, 'published', 'Unknown')}\n"
                firm_context += f"Content:\n{content or '(Content scraping failed)'}\n"
                firm_context += f"--- FIRM MENTION ARTICLE END ---\n"
                combined_news_context += firm_context
                collected_links.append((entry.title, entry.link))
                mark_article_collected(
                    news_history,
                    entry,
                    "firm_mention",
                    collected_date=collected_date
                )

        except Exception as e:
            logging.error(f"   Error fetching firm mention RSS for {query}: {e}")

    return combined_news_context, collected_links, seen_links


def dedupe_links(links):
    deduped = []
    seen = set()
    for title, link in links:
        if link in seen:
            continue
        seen.add(link)
        deduped.append((title, link))
    return deduped


def build_market_snapshot(market_data, max_items=None):
    if not market_data:
        return "- 시장 데이터 없음"

    lines = []
    for name, data in market_data.items():
        if max_items and len(lines) >= max_items:
            break
        if data:
            emoji = "🔺" if data['change'] > 0 else "🔻" if data['change'] < 0 else "➖"
            lines.append(f"- {name}: {data['price']:,.2f} ({emoji} {data['pct_change']:.2f}%)")
        else:
            lines.append(f"- {name}: Data Unavailable")
    return "\n".join(lines) if lines else "- 시장 데이터 없음"


def build_no_new_articles_briefing(market_data, target="general", briefing_date=None, kr_holiday_text=""):
    reference_date = briefing_date or datetime.now().date()
    today = reference_date.strftime("%m/%d(%a)")
    market_snapshot = build_market_snapshot(market_data, max_items=8)

    if target == "pef":
        pef_context = get_pef_persona_config()
        firm_name = pef_context["firm_name"]
        pmi_role = pef_context["pmi_role"]
        return f"""<b>👔 {today} {firm_name} GP & {pmi_role} 인사이트 브리핑{kr_holiday_text}</b>

<b>📭 신규 채택 뉴스 없음</b>
- 중복 제거 및 PEF 필터 적용 결과, 오늘 새로 브리핑할 PEF/{firm_name} 관련 기사는 없습니다.
- 기존 기사 재사용 없이 시장 데이터와 내부 점검 액션만 간단히 확인합니다.

<b>📊 시장 데이터 체크</b>
{market_snapshot}

<b>🎯 오늘/이번 주 핵심 액션</b>
- <b>GP Action</b>: 진행 중인 딜/포트폴리오의 기존 업데이트와 미확인 데이터만 재점검.
- <b>{pmi_role} Action</b>: 신규 기사 기반 이슈는 없으므로, 기존 IT DD/PMI 체크리스트의 미완료 항목만 팔로업."""

    return f"""<b>📊 {today} 시장 브리핑{kr_holiday_text}</b>

<b>📭 신규 채택 뉴스 없음</b>
- 중복 제거 결과, 오늘 새로 브리핑할 뉴스 기사는 없습니다.
- 기존 기사 재사용 없이 시장 데이터만 간단히 확인합니다.

<b>📊 시장 데이터 체크</b>
{market_snapshot}

<b>🎯 대응</b>
- 신규 뉴스 기반 판단은 보류하고, 주요 지수/환율 변동과 기존 체크포인트 중심으로 모니터링합니다."""

# --- Summarizer Module ---
def generate_briefing(market_data, news_context, mode="weekday", is_us_holiday=False, is_kr_holiday=False, holiday_name_kr=None, holiday_name_us=None, target="general", briefing_date=None):
    """
    Generates a daily economic briefing using Gemini 2.0 Flash with a structured analyst persona.
    """
    # Construct the prompt
    reference_date = briefing_date or datetime.now().date()
    today = reference_date.strftime("%m/%d(%a)")
    
    market_summary = "## Market Data Indices\n"
    if market_data:
        for name, data in market_data.items():
            if data:
                emoji = "🔺" if data['change'] > 0 else "🔻" if data['change'] < 0 else "➖"
                market_summary += f"- {name}: {data['price']:,.2f} ({emoji} {data['pct_change']:.2f}%)\n"
            else:
                market_summary += f"- {name}: Data Unavailable\n"
    else:
        market_summary += "Data Unavailable\n"
        
    # Helper to clean up holiday text
    us_holiday_text = f" (미국 휴장: {holiday_name_us})" if is_us_holiday else ""
    kr_holiday_text = f" (국내 휴장: {holiday_name_kr})" if is_kr_holiday else ""

    if not (news_context or "").strip():
        logging.info(f"   [No News] No new articles for target='{target}'. Using fallback briefing.")
        return build_no_new_articles_briefing(
            market_data,
            target=target,
            briefing_date=reference_date,
            kr_holiday_text=kr_holiday_text
        )

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "Error: GEMINI_API_KEY not found in environment variables."

    genai.configure(api_key=api_key)

    # Define Prompt Template based on Mode
    if mode == "saturday":
        # Saturday: Global Market Weekly Summary
        prompt_content = f"""
    <b>📊 {today} 글로벌 증시 주간 요약 보고서</b>
    
    <b>🌍 글로벌 시장 상황 (이번 주 마감)</b>
    <b>지수</b>
    - (List major US indices: Dow, Nasdaq, S&P500, Russell 2000, Philly Semi with % change)
    - (Add a one-line comment on the weekly/daily trend)
    
    <b>핵심 특징</b>
    - (Summarize 2-3 key drivers of the US market this week. Use bolding for keywords.)
    
    ---
    
    <b>🔥 이번 주 글로벌 핫 이슈</b>
    (Identify 3 key themes/events from the US/Global market)
    
    <b>1️⃣ (Theme Title)</b>
    - <b>(Key Point)</b>: (Detail)
    <b>결과 및 영향:</b>
    - (Related stocks or sectors)
    
    <b>2️⃣ (Theme Title)</b>
    ...
    
    ---
    
    <b>💡 다음 주 글로벌 체크 포인트 (미리보기)</b>
    - (Briefly mention 1-2 key events expected next week based on news)
        """
        
    elif mode == "sunday":
        # Sunday: Weekly Summary & Next Week Outlook
        prompt_content = f"""
    <b>📅 {today} 이번 주 증시 정리 및 다음 주 전망</b>
    
    <b>📉 이번 주 시장 요약 (Review)</b>
    <b>시장 동향</b>
    - (Summarize how the Korean and US markets performed this past week)
    - (Mention key indices changes if available in context)
    
    <b>주요 이슈 점검</b>
    - (List 2-3 major economic events or news from the past week)
    
    ---
    
    <b>🗓️ 다음 주 증시 일정 (Preview)</b>
    (Based on news articles about "Next Week Schedule")
    
    <b>주요 경제 지표 발표</b>
    - (List expected events/announcements with dates if possible)
    
    <b>주요 기업 실적 발표</b>
    - (List expected earnings releases)
    
    ---
    
    <b>👀 다음 주 관전 포인트</b>
    <b>1. (Point 1)</b>
    - (Explanation)
    
    <b>2. (Point 2)</b>
    - (Explanation)
    
    ---
    
    <b>🎯 다음 주 대응 전략</b>
    - (General investment strategy advice for the upcoming week)
        """
        
    else:
        # Weekday: Daily Outlook (Original)
        
        # Determine Header
        header = f"<b>📊 {today} 한국 증시 종합 전망 보고서{kr_holiday_text}</b>"
        
        # US Market Section
        if is_us_holiday:
            us_section = f"""
    <b>🌍 글로벌 시장 상황 (미국 휴장: {holiday_name_us})</b>
    - <b>미국 증시는 '{holiday_name_us}'로 인해 휴장했습니다.</b>
    - (Instead, summarize any major European or Global economic news if available, or skip with a brief mention.)
            """
        else:
            us_section = """
    <b>🌍 글로벌 시장 상황 (미 증시)</b>
    <b>지수</b>
    - (List major US indices: Dow, Nasdaq, S&P500, Russell 2000, Philly Semi with % change)
    - (Add a one-line comment on the overall vibe)
    
    <b>핵심 특징</b>
    - (Summarize 2-3 key drivers. Use bolding for keywords.)
            """
            
        # KR Market Section (Outlook)
        if is_kr_holiday:
            kr_section = f"""
    <b>🇰🇷 한국 증시 상황 (휴장: {holiday_name_kr})</b>
    - <b>오늘은 '{holiday_name_kr}'로 인해 한국 증시가 휴장합니다.</b>
    - (Do NOT provide a specific forecast range or hot themes for trading today.)
    - (Instead, briefly summarize the overall sentiment or recent trend leading into the holiday.)
            """
            # Outlook sections (Themes, Strategy) should be minimized or removed for holidays
            extra_section = """
    <b>💡 휴장일 체크 포인트</b>
    - (Any major global events to watch during the holiday)
            """
        else:
            kr_section = """
    <b>🇰🇷 한국 증시 오늘 전망</b>
    <b>예상 범위</b>
    <b>코스피: (Estimate a range)</b>
            """
            extra_section = """
    <b>🚀 오늘의 최강 테마 (우선순위)</b>
    
    <b>🥇 1순위: (Sector Name)</b>
    <b>(Catchy Slogan)</b>
    <b>관련주:</b>
    - (List stocks)
    <b>호재:</b>
    - (Why this sector?)
    
    <b>🥈 2순위: (Sector Name)</b>
    ...
    
    ---
    
    <b>🎯 매매 전략 (종합)</b>
    <b>🟢 공격적 매수</b>
    - (Sectors/Stocks)
    
    <b>🟡 관망/보유</b>
    - (Sectors)
    
    <b>🔴 주의/매도</b>
    - (Sectors)
            """

        prompt_content = f"""
    {header}
    
    {us_section}
    
    ---
    
    {extra_section if not is_kr_holiday else ""}
    
    {kr_section}
    
    ---
    
    {extra_section if is_kr_holiday else ""}
    
    <b>🎬 결론</b>
    (One sentence summary)
        """

    if target == "pef":
        pef_context = get_pef_persona_config()
        firm_name = pef_context["firm_name"]
        pmi_role = pef_context["pmi_role"]
        prompt_content = f"""
    <b>👔 {today} {firm_name} GP & {pmi_role} 인사이트 브리핑{kr_holiday_text}</b>
    
    <b>📊 오늘의 투자위원회 한 줄 판단</b>
    - (딜 환경, 자금 조달 여건, 포트폴리오 운영 환경을 1-2문장으로 압축 요약)
    
    <b>🗞️ 핵심 뉴스와 {firm_name} 시사점</b>
    <b>1. (핵심 테마/기사)</b>
    - <b>사실</b>: (팩트 요약)
    - <b>{firm_name} 시사점</b>: (신규 딜, 밸류에이션, 엑시트, 포트폴리오 영향)
    - <b>추가 확인 데이터</b>: (숫자, 공시, 시장 데이터)
    
    <b>2. (핵심 테마/기사)</b>
    - <b>사실</b>: (팩트 요약)
    - <b>{firm_name} 시사점</b>: (투자 판단과 연결)
    - <b>추가 확인 데이터</b>: (검증 포인트)
    
    ---
    
    <b>💼 GP 관점 핵심 판단</b>
    <b>1. 소싱 및 언더라이팅</b>
    - (어떤 섹터/자산을 더 볼지, 무엇을 조심할지)
    
    <b>2. 자금조달, 밸류에이션, 엑시트</b>
    - (인수금융, 금리, 멀티플, 회수 창구 관점)
    
    <b>3. 포트폴리오 밸류업</b>
    - (원가, 가격, 현금흐름, 조직, 거버넌스 관점)
    
    ---
    
    <b>🧭 {firm_name} 언급 뉴스/회사명 레이더</b>
    - (FIRM MENTION ARTICLE이 있으면, 기사에 등장한 회사/기관/인물을 1-3개만 리스트업하고 {firm_name} 관점의 의미를 한 줄로 정리)
    - (직접 언급 뉴스가 없으면 "금일 수집 기준 직접 언급 뉴스 없음"으로 짧게 처리)
    
    ---
    
    <b>🖥️ {pmi_role} 핵심 체크</b>
    - <b>Day-1/TSA</b>: (분리/통합, 의존 시스템, 서비스 연속성에서 지금 확인할 1가지)
    - <b>보안/데이터</b>: (사이버보안, 개인정보, 데이터 이전/접근권한에서 지금 확인할 1가지)
    - <b>100일 IT 실행</b>: (ERP/앱/인프라/데이터 중 밸류업이나 리스크 완화에 바로 연결되는 1가지)
    
    ---
    
    <b>🎯 오늘/이번 주 핵심 액션 플랜</b>
    <b>GP Action</b>
    - (투자팀이 오늘 확인/실행할 일 1-2개)
    
    <b>{pmi_role} Action</b>
    - (IT PMI 관점에서 바로 점검할 일 1-2개)
        """
        role_description = (
            f"You are the internal morning-briefing writer for {firm_name}, a Korea-focused private equity GP.\n"
            f"Your audience is the deal team, investment committee, operating partners, and an {pmi_role}.\n"
            "Write like an actionable internal memo, not a public newsletter."
        )
        specific_instructions = f"""
    - **Perspective**: Prioritize implications for sourcing, underwriting, financing, exit, and portfolio value creation.
    - **Firm mention radar**: Use only articles marked "FIRM MENTION ARTICLE" for the {firm_name} mention/news radar. Extract concrete company, institution, or person names from those articles. Do not invent names.
    - **IT PMI**: Keep the IT PMI section to exactly 3 bullets. Make each bullet short, concrete, and tied to Day-1/TSA, security/data, or the first 100 days.
    - **Inference**: If a news item is not directly about technology, infer the most plausible IT PMI implications and clearly label that part as inference.
    - **Tone**: Avoid generic consultant language. Be concise, specific, and action-oriented for {firm_name}.
    - **Evidence**: Use actual facts from the articles, and separate confirmed facts from inference when needed.
    - **Length**: Keep the full briefing concise enough for one Telegram message when possible.
    """
    else:
        role_description = "You are a top-tier Financial Analyst.\n    Based on the provided Market Data and News Articles, write a Report."
        specific_instructions = "- **Specifics**: Use ACTUAL numbers from the articles."

    prompt = f"""
    {role_description}
    
    **Format Requirements (Strictly Follow This Structure)**:
    {prompt_content}
    
    **Input Data:**
    {market_summary}
    
    {news_context}
    
    **Instructions:**
    - **Language**: Korean.
    - **Formatting**:
        - Use ONLY these Telegram-supported HTML tags: <b>, <i>, <u>, <s>, <code>, <pre>, <a href="...">.
        - **FORBIDDEN TAGS**: <p>, <ul>, <ol>, <li>, <div>, <span>, <font>, <br>, <h1>..<h6>. DO NOT USE THESE.
        - **Lists**: Use hyphens (-) or emojis for lists. Do NOT use <ul>/<li>.
        - **Newlines**: Use actual newlines instead of <br> or <p>.
        - **Colors**: Do NOT use <font color="...">. Use emojis like 🔴 (Red/Up/Hot) or 🔵 (Blue/Cool/Down) or 🔻/🔺 to represent direction/sentiment.
    {specific_instructions}
    """
    
    # Retry logic with Model Fallback
    models_to_try = ['gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-3.1-flash-lite-preview']
    
    logging.info(f"   [Debug] Generating briefing for mode: {mode}")
    
    for model_name in models_to_try:
        logging.info(f"   Using model: {model_name}...")
        max_retries = 3
        retry_delay = 30
        
        for attempt in range(max_retries):
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(prompt)
                return response.text
            except Exception as e:
                if "429" in str(e):
                    logging.warning(f"   [Rate Limit Hit] Waiting {retry_delay} seconds before retry ({attempt + 1}/{max_retries})...")
                    time.sleep(retry_delay)
                    retry_delay += 30 
                else:
                    logging.error(f"   Error with {model_name}: {e}")
                    break 
        
        logging.warning(f"   Failed with {model_name}, attempting fallback...")
    
    return "Error: Failed to generate briefing with all available models."

# --- Notifier Module ---
def send_telegram_message(message, target="general"):
    """
    Sends a message to a Telegram channel.
    """
    if target == "pef":
        bot_token = os.getenv("TELEGRAM_PEF_BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
        channel_id = os.getenv("TELEGRAM_PEF_CHANNEL_ID")
    else:
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        channel_id = os.getenv("TELEGRAM_CHANNEL_ID")
    
    if not bot_token or not channel_id:
        logging.error(f"Error: Telegram credentials not found for target '{target}'.")
        return False
        
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    sanitized_html_message = sanitize_telegram_html(message)
    logging.info(
        f"   Prepared Telegram message for target='{target}' "
        f"(raw={len(message)} chars, html_sanitized={len(sanitized_html_message)} chars)."
    )

    try:
        success, response = send_telegram_chunks(
            url,
            channel_id,
            sanitized_html_message,
            parse_mode="HTML"
        )
        if success:
            logging.info("Message sent successfully to Telegram.")
            return True

        response_text = response.text[:500] if response is not None else "No response body"
        if response is not None and response.status_code == 400:
            logging.warning(
                f"   [Warning] HTML send failed with 400 Bad Request. "
                f"Telegram response: {response_text}"
            )
            plain_text_message = convert_html_to_plain_text(message)
            logging.info(
                f"   Retrying Telegram send in plain text "
                f"({len(plain_text_message)} chars after HTML stripping)."
            )
            fallback_success, fallback_response = send_telegram_chunks(
                url,
                channel_id,
                plain_text_message
            )
            if fallback_success:
                logging.info("Message sent successfully to Telegram (Plain Text Fallback).")
                return True

            fallback_response_text = (
                fallback_response.text[:500] if fallback_response is not None else "No response body"
            )
            logging.error(
                "   Error sending fallback message: "
                f"{fallback_response.status_code if fallback_response is not None else 'N/A'} "
                f"Telegram response: {fallback_response_text}"
            )
            return False

        logging.error(
            f"Error sending message: status={response.status_code if response is not None else 'N/A'} "
            f"response={response_text}"
        )
        return False
    except requests.exceptions.RequestException as e:
        logging.error(f"Error sending message: {e}")
        return False

# --- Main Execution ---
def main():
    # Load environment variables
    load_dotenv()

    # Setup Logging
    # Note: We must call this before any logging calls
    setup_logging()
    
    # Check for CLI arguments
    # Usage: python main.py --mode saturday
    # Usage: python main.py --date 2023-12-25
    args = sys.argv[1:] if len(sys.argv) > 1 else []
    test_mode = "test" in args or "--test" in args
    news_history_enabled = parse_bool_env("NEWS_HISTORY_ENABLED", True) and "--no-news-history" not in args
    
    # Determine 'today' for holiday checking
    today = datetime.now().date()
    if "--date" in args:
        try:
            idx = args.index("--date")
            date_str = args[idx+1]
            today = datetime.strptime(date_str, "%Y-%m-%d").date()
            logging.info(f"   [Debug] Using custom date: {today}")
        except (IndexError, ValueError) as e:
             logging.error(f"Error: --date requires YYYY-MM-DD format. Using today. {e}")
    
    # Check Holidays
    is_kr_holiday, is_us_holiday_prev_close, holiday_name_kr, holiday_name_us = check_holidays(today)
    
    # Determine Mode
    today_weekday = today.weekday() # Mon=0, Sun=6
    
    mode = "weekday"
    if today_weekday == 5:
        mode = "saturday"
    elif today_weekday == 6:
        mode = "sunday"
        
    # Mode override
    if "--mode" in args:
        try:
            idx = args.index("--mode")
            mode = args[idx+1]
        except IndexError:
            logging.error("Error: --mode requires an argument (weekday/saturday/sunday)")
        
    logging.info(f"--- Daily Economic Briefing Service (Mode: {mode.upper()}) ---")
    if is_kr_holiday:
        logging.info(f"   [Holiday] KR Market Closed: {holiday_name_kr}")
    if is_us_holiday_prev_close and mode == "weekday":
        logging.info(f"   [Holiday] US Market (Prev Close) Closed: {holiday_name_us}")

    news_history = load_news_history(today=today) if news_history_enabled else None
    if not news_history_enabled:
        logging.info("   [News History] Disabled for this run.")
    save_history_after_run = bool(news_history) and not test_mode
    if news_history and test_mode:
        logging.info("   [News History] Test mode: history will be read but not saved.")
    
    # 1. Fetch Data
    logging.info("1. Fetching Market Data...")
    market_data = fetch_market_data()
    
    # Pass US holiday status for news fetching logic
    news_context_general, _, seen_links_general = fetch_news(
        mode=mode,
        is_us_holiday=is_us_holiday_prev_close,
        is_kr_holiday=is_kr_holiday,
        target="general",
        news_history=news_history,
        collected_date=today
    )
    
    # 3. Generate Briefing (Pass Mode & Holiday Context)
    logging.info("3. Generating General Briefing using Gemini...")
    briefing_general = generate_briefing(
        market_data, 
        news_context_general, 
        mode=mode,
        is_us_holiday=is_us_holiday_prev_close,
        is_kr_holiday=is_kr_holiday,
        holiday_name_kr=holiday_name_kr,
        holiday_name_us=holiday_name_us,
        target="general",
        briefing_date=today
    )
    
    # 4. Print to Console
    logging.info("\n" + "="*50)
    # We want this in the log file too, so usage of info is correct
    logging.info(briefing_general) 
    logging.info("="*50 + "\n")
    
    # 5. Send to Telegram
    # Skip if 'test' in args
    if test_mode:
         logging.info("4. Sending General Briefing to Telegram... [SKIPPED] (Test Mode)")
    else:
        logging.info("4. Sending General Briefing to Telegram...")
        send_telegram_message(briefing_general, target="general")
        
    # --- PEF GP Briefing ---
    logging.info("\n--- Starting PEF GP Briefing Sequence ---")
    pef_context = get_pef_persona_config()

    # 6. Fetch firm mention news first so direct mentions are preserved.
    logging.info("5. Fetching & Scraping Firm Mention News...")
    news_context_firm_mentions, firm_mention_links, seen_links_firm_mentions = fetch_firm_mention_news(
        pef_context["firm_name"],
        initial_seen_links=seen_links_general,
        news_history=news_history,
        collected_date=today
    )
    
    # 7. Fetch additional PEF News
    logging.info("6. Fetching & Scraping PEF News...")
    news_context_pef, pef_links, _ = fetch_news(
        mode=mode, 
        is_us_holiday=is_us_holiday_prev_close, 
        is_kr_holiday=is_kr_holiday, 
        target="pef",
        initial_seen_links=seen_links_firm_mentions,
        news_history=news_history,
        collected_date=today
    )
    combined_pef_context = news_context_firm_mentions + news_context_pef
    pef_source_links = dedupe_links(firm_mention_links + pef_links)
    
    # 8. Generate PEF Briefing
    logging.info("7. Generating PEF Briefing using Gemini...")
    briefing_pef = generate_briefing(
        market_data, 
        combined_pef_context,
        mode=mode,
        is_us_holiday=is_us_holiday_prev_close,
        is_kr_holiday=is_kr_holiday,
        holiday_name_kr=holiday_name_kr,
        holiday_name_us=holiday_name_us,
        target="pef",
        briefing_date=today
    )
    
    pef_links_message = build_news_links_message(
        pef_source_links,
        title=f"🔗 PEF 및 {pef_context['firm_name']} 관련 수집 뉴스 링크"
    )
    
    # 9. Print PEF Briefing to Console
    logging.info("\n" + "="*50)
    logging.info(briefing_pef) 
    logging.info("="*50 + "\n")
    
    # 10. Send PEF Briefing to Telegram
    if test_mode:
         logging.info("8. Sending PEF Briefing to Telegram... [SKIPPED] (Test Mode)")
    else:
        logging.info("8. Sending PEF Briefing to Telegram...")
        if os.getenv("TELEGRAM_PEF_CHANNEL_ID"):
            pef_sent = send_telegram_message(briefing_pef, target="pef")
            if pef_sent and pef_links_message:
                logging.info("9. Sending PEF source links to Telegram...")
                send_telegram_message(pef_links_message, target="pef")
        else:
            logging.info("Skipping PEF Telegram send (TELEGRAM_PEF_CHANNEL_ID not found in .env)")

    if save_history_after_run:
        save_news_history(news_history)

if __name__ == "__main__":
    main()
