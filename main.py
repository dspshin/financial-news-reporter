import os
import sys
import requests
import yfinance as yf
import feedparser
import google.generativeai as genai
import holidays
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

def fetch_news(mode="weekday", is_us_holiday=False, is_kr_holiday=False, target="general"):
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
            "PEF 투자"
        ])
    
    combined_news_context = ""
    seen_links = set()
    collected_links = [] # List to store (title, link)
    
    logging.info("   Fetching news and scraping content...")
    
    for query in queries:
        rss_url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR%3Ako"
        try:
            response = requests.get(rss_url, timeout=10)
            feed = feedparser.parse(response.content)
            
            # Take top 3 articles per query to get even more diverse news
            for entry in feed.entries[:3]:
                if entry.link in seen_links:
                    continue
                seen_links.add(entry.link)
                
                logging.info(f"   - Processing: {entry.title}")
                content = scrape_article_content(entry.link)
                
                if content:
                    combined_news_context += f"\n\n--- ARTICLE START ---\n"
                    combined_news_context += f"Title: {entry.title}\n"
                    combined_news_context += f"Link: {entry.link}\n"
                    combined_news_context += f"Date: {entry.published}\n"
                    combined_news_context += f"Content:\n{content}\n"
                    combined_news_context += f"--- ARTICLE END ---\n"
                    collected_links.append((entry.title, entry.link))
                else:
                    # Fallback to just title/snippet if scraping fails
                    combined_news_context += f"\nTitle: {entry.title}\nLink: {entry.link}\n(Content scraping failed)\n"
                    collected_links.append((entry.title, entry.link))
                    
        except Exception as e:
            logging.error(f"   Error fetching RSS for {query}: {e}")
            
    return combined_news_context, collected_links

# --- Summarizer Module ---
def generate_briefing(market_data, news_context, mode="weekday", is_us_holiday=False, is_kr_holiday=False, holiday_name_kr=None, holiday_name_us=None, target="general"):
    """
    Generates a daily economic briefing using Gemini 2.0 Flash with a structured analyst persona.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "Error: GEMINI_API_KEY not found in environment variables."
        
    genai.configure(api_key=api_key)
    
    # Construct the prompt
    today = datetime.now().strftime("%m/%d(%a)")
    
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
        today_title = f"<b>👔 {today} PEF GP 인사이트 브리핑{kr_holiday_text}</b>"
        prompt_content = f"""
    {today_title}
    
    <b>📊 매크로 및 딜 환경 점검</b>
    - (주요 경제 지표 및 증시 상황이 출자자(LP) 및 딜 소싱에 미칠 핵심 영향 한 줄 평)
    
    <b>🗞️ 주요 M&A 및 매크로 뉴스</b>
    - (일반 경제 및 사모펀드/M&A 관련 가장 핵심적인 뉴스 3-4개 압축 요약)
    
    ---
    
    <b>💡 PEF GP의 딥다이브 인사이트</b>
    <b>1. 딜 소싱 및 섹터 동향 (Deal Sourcing)</b>
    - (현재 매크로 뉴스를 기반으로 주목해야 할 매물, 유망 섹터, 투자 리스크 분석)
    
    <b>2. 밸류에이션 및 엑시트 환경 (Valuation & Exit)</b>
    - (시장 금리와 유동성이 포트폴리오 기업 가치 및 매각/IPO 환경에 미치는 영향)
    
    ---
    
    <b>🎯 오늘/이번 주 핵심 액션 플랜</b>
    - (신규 딜 검토, 펀드레이징, 포트폴리오 밸류업 등 수익 창출 및 기업 가치 향상 관점에서 집중할 최우선 액션 1가지)
        """
        role_description = "You are a highly experienced Private Equity Fund (PEF) General Partner (GP) and Chief Investment Officer (CIO).\n    Based on the provided Market Data and News Articles (which encompass macro economy, stock market, and M&A/PEF news), write a deep and insightful morning briefing tailored for other GPs and LPs."
        specific_instructions = "- **Specifics**: Combine real data from articles with realistic, high-level PEF GP insights."
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
    models_to_try = ['gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-2.0-flash']
    
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
    
    # Try sending with HTML first
    payload = {
        "chat_id": channel_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        logging.info("Message sent successfully to Telegram.")
        return True
    except requests.exceptions.HTTPError as e:
        if response.status_code == 400:
            logging.warning(f"   [Warning] HTML parse failed (400 Bad Request). Retrying with plain text fallback...")
            # Remove parse_mode to send as plain text
            payload.pop("parse_mode")
            try:
                response = requests.post(url, json=payload)
                response.raise_for_status()
                logging.info("Message sent successfully to Telegram (Plain Text Fallback).")
                return True
            except Exception as e2:
                logging.error(f"   Error sending fallback message: {e2}")
                return False
        else:
            logging.error(f"Error sending message: {e}")
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
    
    # 1. Fetch Data
    logging.info("1. Fetching Market Data...")
    market_data = fetch_market_data()
    
    # Pass US holiday status for news fetching logic
    news_context_general, _ = fetch_news(mode=mode, is_us_holiday=is_us_holiday_prev_close, is_kr_holiday=is_kr_holiday, target="general")
    
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
        target="general"
    )
    
    # 4. Print to Console
    logging.info("\n" + "="*50)
    # We want this in the log file too, so usage of info is correct
    logging.info(briefing_general) 
    logging.info("="*50 + "\n")
    
    # 5. Send to Telegram
    # Skip if 'test' in args
    if "test" in args or "--test" in args:
         logging.info("4. Sending General Briefing to Telegram... [SKIPPED] (Test Mode)")
    else:
        logging.info("4. Sending General Briefing to Telegram...")
        send_telegram_message(briefing_general, target="general")
        
    # --- PEF GP Briefing ---
    logging.info("\n--- Starting PEF GP Briefing Sequence ---")
    
    # 6. Fetch additional PEF News
    logging.info("5. Fetching & Scraping PEF News...")
    news_context_pef, pef_links = fetch_news(mode=mode, is_us_holiday=is_us_holiday_prev_close, is_kr_holiday=is_kr_holiday, target="pef")
    
    # 7. Generate PEF Briefing
    logging.info("6. Generating PEF Briefing using Gemini...")
    briefing_pef = generate_briefing(
        market_data, 
        news_context_pef, 
        mode=mode,
        is_us_holiday=is_us_holiday_prev_close,
        is_kr_holiday=is_kr_holiday,
        holiday_name_kr=holiday_name_kr,
        holiday_name_us=holiday_name_us,
        target="pef"
    )
    
    # Append the source links list for the PEF target
    if pef_links:
        links_section = "\n\n<b>🔗 금일 수집된 주요 뉴스 링크</b>\n"
        for title, link in pef_links:
            # Telegram HTML formatting for links
            links_section += f"- <a href='{link}'>{title}</a>\n"
        briefing_pef += links_section
    
    # 8. Print PEF Briefing to Console
    logging.info("\n" + "="*50)
    logging.info(briefing_pef) 
    logging.info("="*50 + "\n")
    
    # 9. Send PEF Briefing to Telegram
    if "test" in args or "--test" in args:
         logging.info("7. Sending PEF Briefing to Telegram... [SKIPPED] (Test Mode)")
    else:
        logging.info("7. Sending PEF Briefing to Telegram...")
        if os.getenv("TELEGRAM_PEF_CHANNEL_ID"):
            send_telegram_message(briefing_pef, target="pef")
        else:
            logging.info("Skipping PEF Telegram send (TELEGRAM_PEF_CHANNEL_ID not found in .env)")

if __name__ == "__main__":
    main()
