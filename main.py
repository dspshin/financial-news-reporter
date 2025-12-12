import os
import sys
import requests
import yfinance as yf
import feedparser
import google.generativeai as genai
from datetime import datetime
from dotenv import load_dotenv
from bs4 import BeautifulSoup
import time

# --- Configuration ---
# User-Agent to avoid being blocked by some news sites
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

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
    print("   Fetching market data...")
    
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
            print(f"   Error fetching {name}: {e}")
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
        print(f"   Failed to scrape {url}: {e}")
        return None

def fetch_news(mode="weekday"):
    """
    Fetches top economic news using Google News RSS with specific search queries
    based on the mode (weekday/saturday/sunday).
    """
    
    if mode == "saturday":
        print("   [Mode] Saturday: Focusing on US Market Close & Global News")
        queries = [
            "미국 증시 마감",     # US Market Close
            "주간 해외 증시",     # Weekly Overseas Market
            "글로벌 경제뉴스"     # Global Economic News
        ]
    elif mode == "sunday":
        print("   [Mode] Sunday: Focusing on Weekly Summary & Next Week Outlook")
        queries = [
            "주간 증시 정리",     # Weekly Market Summary
            "다음주 증시 일정",   # Next Week Market Schedule
            "다음주 경제 캘린더", # Next Week Economic Calendar
            "주간 증시 전망"      # Weekly Market Outlook
        ]
    else: # weekday
        print("   [Mode] Weekday: Focusing on Daily Market Outlook")
        queries = [
            "미국 증시 마감",     # US Market Close
            "특징주",            # Hot Stocks
            "국내 증시 전망"      # Korea Market Outlook
        ]
    
    combined_news_context = ""
    seen_links = set()
    
    print("   Fetching news and scraping content...")
    
    for query in queries:
        rss_url = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR%3Ako"
        try:
            response = requests.get(rss_url, timeout=10)
            feed = feedparser.parse(response.content)
            
            # Take top 1 article per query to keep it focused and avoid token limits
            for entry in feed.entries[:1]:
                if entry.link in seen_links:
                    continue
                seen_links.add(entry.link)
                
                print(f"   - Processing: {entry.title}")
                content = scrape_article_content(entry.link)
                
                if content:
                    combined_news_context += f"\n\n--- ARTICLE START ---\n"
                    combined_news_context += f"Title: {entry.title}\n"
                    combined_news_context += f"Link: {entry.link}\n"
                    combined_news_context += f"Date: {entry.published}\n"
                    combined_news_context += f"Content:\n{content}\n"
                    combined_news_context += f"--- ARTICLE END ---\n"
                else:
                    # Fallback to just title/snippet if scraping fails
                    combined_news_context += f"\nTitle: {entry.title}\nLink: {entry.link}\n(Content scraping failed)\n"
                    
        except Exception as e:
            print(f"   Error fetching RSS for {query}: {e}")
            
    return combined_news_context

# --- Summarizer Module ---
def generate_briefing(market_data, news_context, mode="weekday"):
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
        prompt_content = f"""
    <b>📊 {today} 한국 증시 종합 전망 보고서</b>
    
    <b>🌍 글로벌 시장 상황 (미 증시)</b>
    <b>지수</b>
    - (List major US indices: Dow, Nasdaq, S&P500, Russell 2000, Philly Semi with % change)
    - (Add a one-line comment on the overall vibe)
    
    <b>핵심 특징</b>
    - (Summarize 2-3 key drivers. Use bolding for keywords.)
    
    ---
    
    <b>🔥 미 증시 핵심 모멘텀 (국내 영향)</b>
    (Identify 3-4 key themes/events)
    
    <b>1️⃣ (Theme Title)</b>
    - <b>(Key Point)</b>: (Detail)
    - <b>(Key Point)</b>: (Detail)
    <b>결과:</b>
    - (List related US stocks with specific % rise/fall)
    
    <b>2️⃣ (Theme Title)</b>
    ...
    
    ---
    
    <b>🇰🇷 한국 증시 오늘 전망</b>
    <b>예상 범위</b>
    <b>코스피: (Estimate a range)</b>
    
    ---
    
    <b>🚀 오늘의 최강 테마 (우선순위)</b>
    
    <b>🥇 1순위: (Sector Name)</b>
    <b>"(Catchy Slogan)"</b>
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
    
    ---
    
    <b>🎬 결론</b>
    (One sentence summary)
        """

    prompt = f"""
    You are a top-tier Financial Analyst.
    Based on the provided Market Data and News Articles, write a Report.
    
    **Format Requirements (Strictly Follow This Structure)**:
    {prompt_content}
    
    **Input Data:**
    {market_summary}
    
    {news_context}
    
    **Instructions:**
    - **Language**: Korean.
    - **Formatting**: Use HTML tags (`<b>`, etc.). NO Markdown (`*`, `#`).
    - **Specifics**: Use ACTUAL numbers from the articles.
    """
    
    # Retry logic with Model Fallback
    models_to_try = ['gemini-2.5-flash', 'gemini-2.0-flash-001', 'gemini-flash-latest']
    
    print(f"   [Debug] Generating briefing for mode: {mode}")
    
    for model_name in models_to_try:
        print(f"   Using model: {model_name}...")
        max_retries = 3
        retry_delay = 30
        
        for attempt in range(max_retries):
            try:
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(prompt)
                return response.text
            except Exception as e:
                if "429" in str(e):
                    print(f"   [Rate Limit Hit] Waiting {retry_delay} seconds before retry ({attempt + 1}/{max_retries})...")
                    time.sleep(retry_delay)
                    retry_delay += 30 
                else:
                    print(f"   Error with {model_name}: {e}")
                    break 
        
        print(f"   Failed with {model_name}, attempting fallback...")
    
    return "Error: Failed to generate briefing with all available models."

# --- Notifier Module ---
def send_telegram_message(message):
    """
    Sends a message to a Telegram channel.
    """
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    channel_id = os.getenv("TELEGRAM_CHANNEL_ID")
    
    if not bot_token or not channel_id:
        print("Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID not found.")
        return False
        
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    
    payload = {
        "chat_id": channel_id,
        "text": message,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        print("Message sent successfully to Telegram.")
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error sending message: {e}")
        return False

# --- Main Execution ---
def main():
    # Load environment variables
    load_dotenv()
    
    # Determine Mode
    # Default is based on today's weekday
    today_weekday = datetime.now().weekday() # Mon=0, Sun=6
    
    mode = "weekday"
    if today_weekday == 5:
        mode = "saturday"
    elif today_weekday == 6:
        mode = "sunday"
        
    # Check for CLI arguments override
    # Usage: python main.py --mode saturday
    # Usage: python main.py --date 2023-12-25
    
    if len(sys.argv) > 1:
        args = sys.argv[1:]
        if "--mode" in args:
            try:
                idx = args.index("--mode")
                mode = args[idx+1]
            except IndexError:
                print("Error: --mode requires an argument (weekday/saturday/sunday)")
        
    print(f"--- Daily Economic Briefing Service (Mode: {mode.upper()}) ---")
    
    # 1. Fetch Data
    print("1. Fetching Market Data...")
    market_data = fetch_market_data()
    
    # 2. Fetch & Scrape News (Pass Mode)
    print("2. Fetching & Scraping News...")
    news_context = fetch_news(mode=mode)
    
    # 3. Generate Briefing (Pass Mode)
    print("3. Generating Briefing using Gemini...")
    briefing = generate_briefing(market_data, news_context, mode=mode)
    
    # 4. Print to Console
    print("\n" + "="*50)
    print(briefing)
    print("="*50 + "\n")
    
    # 5. Send to Telegram
    # Skip if 'test' in args
    if "test" in args:
         print("4. Sending to Telegram... [SKIPPED] (Test Mode)")
    else:
        print("4. Sending to Telegram...")
        if os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHANNEL_ID"):
            send_telegram_message(briefing)
        else:
            print("Skipping Telegram send (Credentials not found in .env)")

if __name__ == "__main__":
    main()
