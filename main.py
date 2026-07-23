import os
import sys
import requests
import yfinance as yf
import feedparser
from google import genai
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
    "냉동공조저널", "기계신문", "주달", "ipdaily", "pressclub global", "brunch.co.kr",
    "ai넷",
]

FIRM_SHORT_NAME_CONTEXT_KEYWORDS = [
    "pef", "사모펀드", "m&a", "인수", "매각", "컨소시엄", "트랙레코드",
    "임태호", "애큐온", "캐피탈", "저축은행", "운용사"
]

TELEGRAM_MESSAGE_LIMIT = 3900
PEF_FIRM_MENTION_MAX_ARTICLES = 5
DEFAULT_NEWS_HISTORY_FILE = ".news_history.json"
DEFAULT_NEWS_HISTORY_RETENTION_DAYS = 30
DEFAULT_NEWS_HISTORY_TITLE_MATCH_DAYS = 7
DEFAULT_GEMINI_MODELS = (
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
)

PEF_DIRECT_KEYWORDS = [
    "pef", "private equity", "사모펀드", "사모투자", "바이아웃",
    "블라인드펀드", "프로젝트펀드", "펀드레이징", "gp 모집",
]

PEF_TITLE_DEAL_KEYWORDS = [
    "m&a", "인수", "매각", "인수합병", "우선협상", "우협", "본입찰",
    "예비입찰", "실사", "경영권", "카브아웃", "carve-out", "인수금융",
    "리파이낸싱", "엑시트", "회수", "사모대출", "private credit",
]

PEF_PUBLIC_MARKET_NOISE_KEYWORDS = [
    "장내매도", "장내 매도", "주식 매도", "보유주식 매도", "보유 주식 매도",
    "개인 유동성", "임원 주식", "주주들", "주당", "순매수", "순매도",
    "공매도", "주가 급등", "주가 급락", "대량보유 공시",
]

PEF_FINANCIAL_PRODUCT_NOISE_KEYWORDS = [
    "공모펀드", "재간접", "펀드 판매", "단독 판매", "판매 개시", "가입 이벤트",
]

EVENT_ACTION_ROOTS = (
    "인수", "매각", "합병", "분할", "투자", "유치", "상장", "ipo",
    "회수", "엑시트", "소송", "제재", "승인", "선정", "모집", "통합",
    "인수금융", "리파이낸싱", "증자", "전환",
)

KOREAN_PARTICLE_SUFFIXES = (
    "으로", "에서", "에게", "까지", "부터", "처럼", "보다", "로", "은", "는",
    "이", "가", "을", "를", "의", "에", "와", "과", "도",
)

EVENT_TITLE_STOPWORDS = {
    "관련", "대한", "통해", "위해", "추진", "계획", "전망", "가능성", "논란",
    "단독", "속보", "종합", "포토", "영상", "기자", "오늘", "이번", "최근",
    "시장", "업계", "기업", "회사", "그룹", "회장", "대표", "목적", "확보",
}


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

    Core relevance is anchored in the headline. Full article text is used to enrich
    categories, but incidental body keywords cannot promote an unrelated headline.
    """
    source = extract_article_source(title)
    headline = normalize_text(title.rsplit(" - ", 1)[0])
    text = normalize_text(title, content, source, link)

    hard_noise_hits = sorted({kw for kw in PEF_HARD_EXCLUDE_KEYWORDS if kw in text})
    soft_noise_hits = sorted({kw for kw in PEF_SOFT_EXCLUDE_KEYWORDS if kw in text})
    direct_pe_hits = sorted({kw for kw in PEF_DIRECT_KEYWORDS if kw in headline})
    deal_title_hits = sorted({kw for kw in PEF_TITLE_DEAL_KEYWORDS if kw in headline})
    public_market_noise_hits = sorted({
        kw for kw in PEF_PUBLIC_MARKET_NOISE_KEYWORDS if kw in headline
    })
    financial_product_noise_hits = sorted({
        kw for kw in PEF_FINANCIAL_PRODUCT_NOISE_KEYWORDS if kw in headline
    })
    strong_signal_hits = sorted({kw for kw in PEF_STRONG_SIGNAL_KEYWORDS if kw in headline})
    medium_signal_hits = sorted({kw for kw in PEF_MEDIUM_SIGNAL_KEYWORDS if kw in headline})

    categories = []
    category_reason_samples = []
    score = 0

    for category, keywords in PEF_CATEGORY_KEYWORDS.items():
        hits = [kw for kw in keywords if kw in text]
        if hits:
            categories.append(category)
            score += 2 if category in {"deal_sourcing", "financing_exit"} else 1
            label = PEF_CATEGORY_LABELS.get(category, category)
            category_reason_samples.append(f"{label}:{', '.join(hits[:2])}")

    if direct_pe_hits:
        score += 4
    if deal_title_hits:
        score += 3
    elif strong_signal_hits:
        score += min(len(strong_signal_hits), 2) * 2
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
        score += 1
    if low_signal_source:
        score -= 2
    if hard_noise_hits:
        score -= 6
    if soft_noise_hits:
        score -= 2

    has_core_signal = bool(direct_pe_hits or deal_title_hits)
    has_control_context = any(
        keyword in headline
        for keyword in ("경영권", "최대주주", "인수", "m&a", "사모펀드", "pef")
    )
    is_public_market_noise = bool(public_market_noise_hits) and not has_control_context
    is_financial_product_noise = bool(financial_product_noise_hits) and not deal_title_hits
    accepted = (
        score >= 4
        and has_core_signal
        and not is_public_market_noise
        and not is_financial_product_noise
        and not low_signal_source
    )
    promotable = accepted

    reasons = []
    if direct_pe_hits:
        reasons.append(f"pef_anchor:{', '.join(direct_pe_hits[:3])}")
    if deal_title_hits:
        reasons.append(f"headline_deal:{', '.join(deal_title_hits[:3])}")
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
    if public_market_noise_hits:
        reasons.append(f"public_market_noise:{', '.join(public_market_noise_hits[:2])}")
    if financial_product_noise_hits:
        reasons.append(f"financial_product_noise:{', '.join(financial_product_noise_hits[:2])}")
    if not has_core_signal:
        reasons.append("no_headline_pef_or_deal_anchor")
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


def get_gemini_models():
    configured = os.getenv("GEMINI_MODELS", "")
    if configured.strip():
        models = [model.strip() for model in configured.split(",") if model.strip()]
        if models:
            return models
    return list(DEFAULT_GEMINI_MODELS)


def is_rate_limit_error(error):
    status_code = getattr(error, "status_code", None) or getattr(error, "code", None)
    message = str(error).lower()
    return status_code == 429 or "429" in message or "resource_exhausted" in message


def briefing_generation_succeeded(briefing):
    return bool(briefing and not briefing.lstrip().lower().startswith("error:"))


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


def get_event_title_tokens(title):
    normalized = normalize_title_for_dedupe(title)
    tokens = []
    for raw_token in re.findall(r"[0-9a-zA-Z가-힣]+", normalized):
        token = raw_token.lower()
        if len(token) < 2 or token in EVENT_TITLE_STOPWORDS:
            continue
        for suffix in KOREAN_PARTICLE_SUFFIXES:
            if token.endswith(suffix) and len(token) - len(suffix) >= 2:
                token = token[:-len(suffix)]
                break
        if token in EVENT_TITLE_STOPWORDS:
            continue
        for action_root in sorted(EVENT_ACTION_ROOTS, key=len, reverse=True):
            if token.startswith(action_root):
                token = action_root
                break
        tokens.append(token)
    return set(tokens)


def is_same_news_event(left_title, right_title):
    if normalize_title_for_dedupe(left_title) == normalize_title_for_dedupe(right_title):
        return True

    left_tokens = get_event_title_tokens(left_title)
    right_tokens = get_event_title_tokens(right_title)
    if not left_tokens or not right_tokens:
        return False

    action_tokens = set(EVENT_ACTION_ROOTS)
    common_actions = (left_tokens & right_tokens) & action_tokens
    if not common_actions:
        return False

    common_tokens = left_tokens & right_tokens
    common_entities = common_tokens - action_tokens
    if len(common_entities) < 2:
        return False

    overlap = len(common_tokens) / min(len(left_tokens), len(right_tokens))
    jaccard = len(common_tokens) / len(left_tokens | right_tokens)
    if overlap >= 0.65 and jaccard >= 0.45:
        return True
    return (
        len(common_tokens) >= 3
        and overlap >= 0.4
        and any(len(token) >= 4 for token in common_entities)
    )


def find_duplicate_event_title(title, existing_titles):
    for existing_title in existing_titles or []:
        if is_same_news_event(title, existing_title):
            return existing_title
    return None


def new_fetch_status(source):
    return {
        "source": source,
        "queries_attempted": 0,
        "queries_succeeded": 0,
        "queries_failed": 0,
        "entries_found": 0,
        "errors": [],
    }


def merge_fetch_statuses(*statuses):
    merged = new_fetch_status("combined")
    for status in statuses:
        if not status:
            continue
        for key in (
            "queries_attempted", "queries_succeeded", "queries_failed", "entries_found"
        ):
            merged[key] += status.get(key, 0)
        merged["errors"].extend(status.get("errors", []))
    return merged


def is_fetch_outage(status):
    return bool(
        status
        and status.get("queries_attempted", 0) > 0
        and status.get("queries_succeeded", 0) == 0
    )


def is_partial_fetch_failure(status):
    return bool(
        status
        and status.get("queries_succeeded", 0) > 0
        and status.get("queries_failed", 0) > 0
    )


def log_fetch_status(status, label):
    logging.info(
        f"   [News Fetch] {label}: success={status.get('queries_succeeded', 0)}/"
        f"{status.get('queries_attempted', 0)}, failed={status.get('queries_failed', 0)}, "
        f"entries={status.get('entries_found', 0)}"
    )


def history_scope(target):
    return "pef" if target in {"pef", "firm_mention"} else "general"


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
    links_by_target = {"general": set(), "pef": set()}
    title_keys_by_target = {"general": set(), "pef": set()}
    event_titles_by_target = {"general": [], "pef": []}

    for article in articles:
        scope = history_scope(article.get("target"))
        if article.get("link"):
            links_by_target[scope].add(article["link"])

    if title_match_enabled:
        for article in articles:
            title_key = article.get("title_key")
            if not title_key:
                continue
            collected_date = parse_history_date(article.get("collected_at"))
            if title_key_cutoff_date and collected_date and collected_date < title_key_cutoff_date:
                continue
            title_keys.add(title_key)
            scope = history_scope(article.get("target"))
            title_keys_by_target[scope].add(title_key)
            if article.get("title"):
                event_titles_by_target[scope].append(article["title"])
    return {
        "path": path,
        "articles": articles,
        "links": links,
        "title_keys": title_keys,
        "links_by_target": links_by_target,
        "title_keys_by_target": title_keys_by_target,
        "event_titles_by_target": event_titles_by_target,
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


def should_skip_seen_article(entry, history, target="general", seen_title_keys=None):
    if not history:
        title_key = normalize_title_for_dedupe(entry.title)
        if seen_title_keys is not None and title_key in seen_title_keys:
            return True, "title_in_run", title_key
        return False, None, title_key

    title_key = normalize_title_for_dedupe(entry.title)
    scope = history_scope(target)
    target_links = history.get("links_by_target", {}).get(scope, history.get("links", set()))
    target_title_keys = history.get("title_keys_by_target", {}).get(
        scope, history.get("title_keys", set())
    )
    if entry.link in target_links:
        return True, "link", title_key
    if title_key and title_key in target_title_keys:
        return True, "title", title_key
    duplicate_title = find_duplicate_event_title(
        entry.title,
        history.get("event_titles_by_target", {}).get(scope, []),
    )
    if duplicate_title:
        return True, "same_event", title_key
    if seen_title_keys is not None and title_key in seen_title_keys:
        return True, "title_in_run", title_key
    return False, None, title_key


def stage_article_for_history(pending_articles, entry, target, collected_date=None):
    title_key = normalize_title_for_dedupe(entry.title)
    record = {
        "link": entry.link,
        "title": entry.title,
        "title_key": title_key,
        "target": target,
        "collected_at": (collected_date or datetime.now().date()).isoformat(),
    }
    scope = history_scope(target)
    for pending in pending_articles:
        if history_scope(pending.get("target")) != scope:
            continue
        if entry.link and entry.link == pending.get("link"):
            return
        if title_key and title_key == pending.get("title_key"):
            return
    pending_articles.append(record)


def commit_pending_articles(history, pending_articles):
    if not history or not pending_articles:
        return 0

    committed = 0
    for article in pending_articles:
        scope = history_scope(article.get("target"))
        target_links = history["links_by_target"].setdefault(scope, set())
        target_title_keys = history["title_keys_by_target"].setdefault(scope, set())
        link = article.get("link")
        title_key = article.get("title_key")
        if link and link in target_links:
            continue
        if title_key and title_key in target_title_keys:
            continue

        history["articles"].append(article)
        if link:
            history["links"].add(link)
            target_links.add(link)
        if title_key and history.get("title_match_enabled", True):
            history["title_keys"].add(title_key)
            target_title_keys.add(title_key)
            history["event_titles_by_target"].setdefault(scope, []).append(article.get("title", ""))
        committed += 1

    return committed


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
def calculate_market_performance(history, mode="weekday"):
    if history is None or history.empty or "Close" not in history:
        return None

    closes = history["Close"].dropna()
    if closes.empty:
        return None

    current_price = closes.iloc[-1]
    weekly = mode in {"saturday", "sunday"}
    if len(closes) == 1:
        baseline_price = current_price
    elif weekly:
        target_date = closes.index[-1] - timedelta(days=7)
        eligible = closes[closes.index <= target_date]
        baseline_price = eligible.iloc[-1] if not eligible.empty else closes.iloc[0]
    else:
        baseline_price = closes.iloc[-2]

    change = current_price - baseline_price
    pct_change = (change / baseline_price) * 100 if baseline_price else 0.0
    return {
        "price": current_price,
        "change": change,
        "pct_change": pct_change,
        "period": "weekly" if weekly else "daily",
    }


def fetch_market_data(mode="weekday"):
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
    logging.info("   Fetching market data...")
    
    for name, symbol in tickers.items():
        try:
            ticker = yf.Ticker(symbol)
            history = ticker.history(period="1mo" if mode in {"saturday", "sunday"} else "5d")
            data[name] = calculate_market_performance(history, mode=mode)
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


def parse_google_news_feed(response):
    response.raise_for_status()
    feed = feedparser.parse(response.content)
    if getattr(feed, "bozo", False) and not getattr(feed, "entries", []):
        raise ValueError(f"invalid RSS response: {getattr(feed, 'bozo_exception', 'parse error')}")
    return feed


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
        logging.info("   [Target] PEF: Using dedicated M&A and Private Equity queries")
        queries = [
            "사모펀드",
            "PEF M&A",
            "PEF 투자 규제",
            "사모펀드 운용사 GP",
            "M&A 인수합병",
            "경영권 매각",
            "인수금융 리파이낸싱",
            "M&A IT 통합",
            "카브아웃 TSA",
        ]
    
    combined_news_context = ""
    seen_links = set(initial_seen_links) if initial_seen_links else set()
    seen_title_keys = set()
    accepted_event_titles = []
    collected_links = []
    pending_articles = []
    fetch_status = new_fetch_status(f"{target}_news")
    
    logging.info("   Fetching news and scraping content...")
    
    for query in queries:
        time_restricted_query = f"{query} when:1d"
        fetch_status["queries_attempted"] += 1
        try:
            response = requests.get(
                "https://news.google.com/rss/search",
                params={
                    "q": time_restricted_query,
                    "hl": "ko",
                    "gl": "KR",
                    "ceid": "KR:ko",
                },
                timeout=10,
            )
            feed = parse_google_news_feed(response)
            entries = list(feed.entries[:3])
            fetch_status["queries_succeeded"] += 1
            fetch_status["entries_found"] += len(entries)
            
            for entry in entries:
                if entry.link in seen_links:
                    continue
                skip_article, skip_reason, title_key = should_skip_seen_article(
                    entry,
                    news_history,
                    target=target,
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
                
                pef_meta = None
                if target == "pef":
                    pef_meta = evaluate_pef_article(entry.title, entry.link, content)
                    decision = "ACCEPT" if pef_meta["accepted"] else "REJECT"
                    logging.info(
                        f"   [PEF Filter] {decision} score={pef_meta['score']} "
                        f"source={pef_meta['source']} categories={', '.join(pef_meta['categories']) or 'None'}"
                    )
                    if not pef_meta["accepted"]:
                        logging.info(f"      reasons: {', '.join(pef_meta['reasons'])}")
                        continue

                duplicate_title = find_duplicate_event_title(entry.title, accepted_event_titles)
                if duplicate_title:
                    logging.info(
                        f"   [Event Dedupe] SKIP same event: {entry.title} "
                        f"(matched: {duplicate_title})"
                    )
                    continue

                combined_news_context = append_article_context(
                    combined_news_context,
                    entry,
                    content,
                    target=target,
                    pef_meta=pef_meta if target == "pef" else None
                )
                collected_links.append((entry.title, entry.link))
                accepted_event_titles.append(entry.title)
                stage_article_for_history(
                    pending_articles,
                    entry,
                    target,
                    collected_date=collected_date,
                )
                    
        except Exception as e:
            fetch_status["queries_failed"] += 1
            fetch_status["errors"].append(f"{query}: {type(e).__name__}: {str(e)[:200]}")
            logging.error(f"   Error fetching RSS for {query}: {e}")

    log_fetch_status(fetch_status, f"target={target}")
    return combined_news_context, collected_links, seen_links, pending_articles, fetch_status


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
    accepted_event_titles = []
    combined_news_context = ""
    collected_links = []
    pending_articles = []
    fetch_status = new_fetch_status("firm_mentions")

    if not queries:
        return combined_news_context, collected_links, seen_links, pending_articles, fetch_status

    logging.info(
        f"   [Target] Firm mentions: Searching {firm_name} news "
        f"(lookback={lookback_days}d, queries={', '.join(queries)})"
    )

    for query in queries:
        if len(collected_links) >= PEF_FIRM_MENTION_MAX_ARTICLES:
            break
        rss_query = f'"{query}" when:{lookback_days}d'
        fetch_status["queries_attempted"] += 1
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
            feed = parse_google_news_feed(response)
            entries = list(feed.entries[:5])
            fetch_status["queries_succeeded"] += 1
            fetch_status["entries_found"] += len(entries)

            for entry in entries:
                if len(collected_links) >= PEF_FIRM_MENTION_MAX_ARTICLES:
                    break

                title_key = normalize_title_for_dedupe(entry.title)
                if entry.link in seen_links or title_key in seen_titles:
                    continue
                skip_article, skip_reason, title_key = should_skip_seen_article(
                    entry,
                    news_history,
                    target="firm_mention",
                    seen_title_keys=seen_titles
                )
                if skip_article:
                    logging.info(
                        f"      [News History] SKIP already collected ({skip_reason}): {entry.title}"
                    )
                    seen_links.add(entry.link)
                    seen_titles.add(title_key)
                    continue

                # Mark every attempted candidate so rejected results are not scraped
                # again through another firm-name query in the same run.
                seen_links.add(entry.link)
                seen_titles.add(title_key)
                logging.info(f"   - Firm mention candidate: {entry.title}")
                content = scrape_article_content(entry.link)
                searchable_text = normalize_text(entry.title, content, entry.link)
                is_match, match_reason = match_firm_mention(searchable_text, match_terms, firm_name)
                if not is_match:
                    logging.info(f"      [Firm Mention] REJECT: {match_reason}")
                    continue

                duplicate_title = find_duplicate_event_title(entry.title, accepted_event_titles)
                if duplicate_title:
                    logging.info(
                        f"      [Event Dedupe] SKIP same firm event: {entry.title} "
                        f"(matched: {duplicate_title})"
                    )
                    continue

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
                accepted_event_titles.append(entry.title)
                stage_article_for_history(
                    pending_articles,
                    entry,
                    "firm_mention",
                    collected_date=collected_date
                )

        except Exception as e:
            fetch_status["queries_failed"] += 1
            fetch_status["errors"].append(f"{query}: {type(e).__name__}: {str(e)[:200]}")
            logging.error(f"   Error fetching firm mention RSS for {query}: {e}")

    log_fetch_status(fetch_status, "firm mentions")
    return combined_news_context, collected_links, seen_links, pending_articles, fetch_status


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


def get_market_period_label(market_data):
    for data in (market_data or {}).values():
        if data and data.get("period") == "weekly":
            return "주간 등락률"
    return "전일 대비"


def build_no_new_articles_briefing(
    market_data,
    target="general",
    briefing_date=None,
    kr_holiday_text="",
    fetch_status=None,
):
    reference_date = briefing_date or datetime.now().date()
    today = reference_date.strftime("%m/%d(%a)")
    market_snapshot = build_market_snapshot(market_data, max_items=8)
    partial_warning = ""
    if is_partial_fetch_failure(fetch_status):
        partial_warning = (
            "\n- 일부 뉴스 검색 요청이 실패했습니다. 아래 0건 판단은 성공한 검색 범위에 한정됩니다."
        )

    if target == "pef":
        pef_context = get_pef_persona_config()
        firm_name = pef_context["firm_name"]
        pmi_role = pef_context["pmi_role"]
        return f"""<b>👔 {today} {firm_name} GP & {pmi_role} 인사이트 브리핑{kr_holiday_text}</b>

<b>📭 신규 채택 뉴스 없음</b>
- 중복 제거 및 PEF 필터 적용 결과, 오늘 새로 브리핑할 PEF/{firm_name} 관련 기사는 없습니다.{partial_warning}
- 기존 기사 재사용 없이 시장 데이터와 내부 점검 액션만 간단히 확인합니다.

<b>📊 시장 데이터 체크 ({get_market_period_label(market_data)})</b>
{market_snapshot}

<b>🎯 오늘/이번 주 핵심 액션</b>
- <b>GP Action</b>: 진행 중인 딜/포트폴리오의 기존 업데이트와 미확인 데이터만 재점검.
- <b>{pmi_role} Action</b>: 신규 기사 기반 이슈는 없으므로, 기존 IT DD/PMI 체크리스트의 미완료 항목만 팔로업."""

    return f"""<b>📊 {today} 시장 브리핑{kr_holiday_text}</b>

<b>📭 신규 채택 뉴스 없음</b>
- 중복 제거 결과, 오늘 새로 브리핑할 뉴스 기사는 없습니다.{partial_warning}
- 기존 기사 재사용 없이 시장 데이터만 간단히 확인합니다.

<b>📊 시장 데이터 체크 ({get_market_period_label(market_data)})</b>
{market_snapshot}

<b>🎯 대응</b>
- 신규 뉴스 기반 판단은 보류하고, 주요 지수/환율 변동과 기존 체크포인트 중심으로 모니터링합니다."""


def build_news_collection_failure_briefing(
    market_data,
    target="general",
    briefing_date=None,
    kr_holiday_text="",
):
    reference_date = briefing_date or datetime.now().date()
    today = reference_date.strftime("%m/%d(%a)")
    market_snapshot = build_market_snapshot(market_data, max_items=8)
    if target == "pef":
        persona = get_pef_persona_config()
        header = f"👔 {today} {persona['firm_name']} GP & {persona['pmi_role']} 브리핑"
    else:
        header = f"📊 {today} 시장 브리핑"

    return f"""<b>{header}{kr_holiday_text}</b>

<b>⚠️ 뉴스 수집 장애</b>
- 모든 뉴스 RSS 요청이 실패해 오늘의 뉴스 유무를 확인할 수 없습니다.
- 따라서 "신규 뉴스 없음"으로 판단하지 않으며, 뉴스 기반 분석과 투자 판단은 보류합니다.

<b>📊 시장 데이터 체크 ({get_market_period_label(market_data)})</b>
{market_snapshot}

<b>🎯 대응</b>
- RSS 연결 상태를 확인한 뒤 재실행하고, 복구 전에는 시장 데이터만 참고합니다."""

# --- Summarizer Module ---
def generate_briefing(
    market_data,
    news_context,
    mode="weekday",
    is_us_holiday=False,
    is_kr_holiday=False,
    holiday_name_kr=None,
    holiday_name_us=None,
    target="general",
    briefing_date=None,
    fetch_status=None,
):
    """
    Generates a daily economic briefing with the configured Gemini fallback chain.
    """
    # Construct the prompt
    reference_date = briefing_date or datetime.now().date()
    today = reference_date.strftime("%m/%d(%a)")
    
    market_summary = f"## Market Data Indices ({get_market_period_label(market_data)})\n"
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
        if is_fetch_outage(fetch_status):
            logging.warning(
                f"   [News Fetch] All requests failed for target='{target}'. "
                "Using collection-failure briefing."
            )
            return build_news_collection_failure_briefing(
                market_data,
                target=target,
                briefing_date=reference_date,
                kr_holiday_text=kr_holiday_text,
            )
        logging.info(f"   [No News] No new articles for target='{target}'. Using fallback briefing.")
        return build_no_new_articles_briefing(
            market_data,
            target=target,
            briefing_date=reference_date,
            kr_holiday_text=kr_holiday_text,
            fetch_status=fetch_status,
        )

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "Error: GEMINI_API_KEY not found in environment variables."

    if is_partial_fetch_failure(fetch_status):
        news_context = (
            "## NEWS COLLECTION WARNING\n"
            "Some RSS queries failed. Base the report only on the articles below and explicitly "
            "state that coverage was partial.\n\n"
            + news_context
        )

    # Define Prompt Template based on Mode
    if mode == "saturday":
        # Saturday: Global Market Weekly Summary
        prompt_content = f"""
    <b>📊 {today} 글로벌 증시 주간 요약 보고서</b>
    
    <b>🌍 글로벌 시장 상황 (이번 주 마감)</b>
    <b>지수</b>
    - (List major US indices: Dow, Nasdaq, S&P500, Russell 2000, Philly Semi with % change)
    - (All supplied percentage changes are weekly returns. Add a one-line weekly trend comment.)
    
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
    - (List monitoring priorities and conditions that would change the view; do not give buy/sell instructions.)
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
    - <b>방향성</b>: (상승 우위/중립/하락 우위 중 하나. 입력 근거가 약하면 중립)
    - <b>신뢰도</b>: (낮음/보통/높음 중 하나)
    - <b>근거</b>: (입력 데이터와 기사에서 확인되는 근거 2-3개)
            """
            extra_section = """
    <b>🔎 오늘의 관찰 테마</b>
    - (기사에 직접 근거가 있는 섹터/테마 최대 2개와 확인할 조건)

    <b>🎯 리스크 체크</b>
    - (전망을 무효화할 변수와 오늘 확인할 데이터 2-3개)
            """

        prompt_content = f"""
    {header}
    
    {us_section}
    
    ---
    {kr_section}
    
    ---
    {extra_section}
    
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
    - **Decision discipline**: Do not turn a single article or a daily market move into a firm investment conclusion. State uncertainty and the missing evidence.
    - **Length**: Keep the full briefing concise enough for one Telegram message when possible.
    """
    else:
        role_description = (
            "You are a cautious market-briefing analyst.\n"
            "Based only on the supplied Market Data and News Articles, write an evidence-led report."
        )
        specific_instructions = """
    - **Evidence boundary**: Use only numbers, dates, company names, and events present in the input. Never invent an index range, price target, schedule, or causal explanation.
    - **Market data meaning**: Treat supplied percentages as historical performance, not a forecast. On weekends they are weekly returns; on weekdays they are previous-close returns.
    - **Uncertainty**: Label unsupported interpretation as "(추론)" and use low confidence when evidence is thin or mixed.
    - **No trading directives**: Do not use language such as aggressive buy, sell, must buy, or target price. Provide monitoring priorities and conditions instead.
    - **Article scope**: A single article cannot establish a broad market regime. Separate confirmed facts from a tentative implication.
    """

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

    models_to_try = get_gemini_models()
    max_attempts = max(1, parse_int_env("GEMINI_MAX_ATTEMPTS_PER_MODEL", 1))
    retry_delay = max(1, parse_int_env("GEMINI_RETRY_DELAY_SECONDS", 5))
    client = genai.Client(api_key=api_key)
    
    logging.info(f"   [Debug] Generating briefing for mode: {mode}")
    
    for model_name in models_to_try:
        logging.info(f"   Using model: {model_name}...")

        for attempt in range(max_attempts):
            try:
                response = client.models.generate_content(model=model_name, contents=prompt)
                if not response.text:
                    raise ValueError("Gemini returned an empty response")
                return response.text.strip()
            except Exception as e:
                if is_rate_limit_error(e):
                    logging.warning(
                        f"   [Rate Limit] {model_name} unavailable; switching to the next model."
                    )
                    break

                logging.error(
                    f"   Error with {model_name} (attempt {attempt + 1}/{max_attempts}): {e}"
                )
                if attempt + 1 < max_attempts:
                    time.sleep(retry_delay * (attempt + 1))
        
        logging.warning(f"   Failed with {model_name}, attempting fallback...")
    
    return "Error: Failed to generate briefing with all available models."

# --- Notifier Module ---
def redact_sensitive_text(value, *secrets):
    redacted = str(value)
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "<redacted>")
    return re.sub(r"/bot[^/\s]+/", "/bot<redacted>/", redacted)


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
        logging.error(f"Error sending message: {redact_sensitive_text(e, bot_token)}")
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
    save_history_after_run = news_history is not None and not test_mode
    pending_to_commit = []
    if news_history and test_mode:
        logging.info("   [News History] Test mode: history will be read but not saved.")
    
    # 1. Fetch Data
    logging.info("1. Fetching Market Data...")
    market_data = fetch_market_data(mode=mode)
    
    # Pass US holiday status for news fetching logic
    (
        news_context_general,
        general_links,
        _,
        pending_general,
        general_fetch_status,
    ) = fetch_news(
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
        briefing_date=today,
        fetch_status=general_fetch_status,
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
        if briefing_generation_succeeded(briefing_general):
            general_sent = send_telegram_message(briefing_general, target="general")
            if general_sent:
                pending_to_commit.extend(pending_general)
            elif pending_general:
                logging.warning(
                    f"   [News History] General delivery failed; "
                    f"{len(pending_general)} article(s) remain uncommitted for retry."
                )
        else:
            logging.error("   Skipping General Telegram send because briefing generation failed.")
        
    # --- PEF GP Briefing ---
    logging.info("\n--- Starting PEF GP Briefing Sequence ---")
    pef_context = get_pef_persona_config()

    # 6. Fetch firm mention news first so direct mentions are preserved.
    logging.info("5. Fetching & Scraping Firm Mention News...")
    initial_pef_seen_links = {link for _, link in general_links}
    (
        news_context_firm_mentions,
        firm_mention_links,
        _,
        pending_firm_mentions,
        firm_fetch_status,
    ) = fetch_firm_mention_news(
        pef_context["firm_name"],
        initial_seen_links=initial_pef_seen_links,
        news_history=news_history,
        collected_date=today
    )
    
    # 7. Fetch additional PEF News
    logging.info("6. Fetching & Scraping PEF News...")
    (
        news_context_pef,
        pef_links,
        _,
        pending_pef,
        pef_fetch_status,
    ) = fetch_news(
        mode=mode, 
        is_us_holiday=is_us_holiday_prev_close, 
        is_kr_holiday=is_kr_holiday, 
        target="pef",
        initial_seen_links=initial_pef_seen_links | {link for _, link in firm_mention_links},
        news_history=news_history,
        collected_date=today
    )
    combined_pef_context = news_context_firm_mentions + news_context_pef
    combined_pef_fetch_status = merge_fetch_statuses(firm_fetch_status, pef_fetch_status)
    log_fetch_status(combined_pef_fetch_status, "combined PEF")
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
        briefing_date=today,
        fetch_status=combined_pef_fetch_status,
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
            pef_delivery_complete = False
            if briefing_generation_succeeded(briefing_pef):
                pef_sent = send_telegram_message(briefing_pef, target="pef")
                pef_links_sent = not pef_links_message
                if pef_sent and pef_links_message:
                    logging.info("9. Sending PEF source links to Telegram...")
                    pef_links_sent = send_telegram_message(pef_links_message, target="pef")
                pef_delivery_complete = pef_sent and pef_links_sent
            else:
                logging.error("   Skipping PEF Telegram send because briefing generation failed.")

            pending_pef_delivery = pending_firm_mentions + pending_pef
            if pef_delivery_complete:
                pending_to_commit.extend(pending_pef_delivery)
            elif pending_pef_delivery:
                logging.warning(
                    f"   [News History] PEF delivery incomplete; "
                    f"{len(pending_pef_delivery)} article(s) remain uncommitted for retry."
                )
        else:
            logging.info("Skipping PEF Telegram send (TELEGRAM_PEF_CHANNEL_ID not found in .env)")
            if pending_firm_mentions or pending_pef:
                logging.warning(
                    "   [News History] PEF channel is not configured; collected PEF articles "
                    "remain uncommitted."
                )

    if save_history_after_run and pending_to_commit:
        committed_count = commit_pending_articles(news_history, pending_to_commit)
        if committed_count:
            logging.info(
                f"   [News History] Committed {committed_count} article(s) after successful delivery."
            )
            save_news_history(news_history)

if __name__ == "__main__":
    main()
