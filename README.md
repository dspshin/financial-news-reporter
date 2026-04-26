# Financial News & Market Outlook Briefing Service

이 프로젝트는 주요 시장 지수와 경제 뉴스를 자동으로 수집하고, Google Gemini AI를 활용하여 전문적인 시장 전망 보고서를 생성한 후 Telegram 채널로 전송하는 자동화 도구입니다.

## 🚀 주요 기능

- **시장 데이터 수집**: `yfinance`를 사용하여 주요 지수(KOSPI, KOSDAQ, S&P 500, NASDAQ 등)와 환율, 비트코인 시세를 조회합니다.
- **뉴스 크롤링 & 분석**: Google News RSS를 통해 주요 경제 뉴스(미국 증시 마감, 특징주, 국내 증시 전망)를 수집하고, 기사 본문을 스크래핑하여 분석합니다.
- **AI 기반 브리핑 생성**: 수집된 데이터를 바탕으로 Gemini(2.0 Flash 등)가 전문 애널리스트 페르소나로 보고서를 작성합니다.
    - **평일 (월~금)**: 한국 증시 데일리 전망 및 전략
    - **토요일**: 미 증시 마감 기준 글로벌 증시 주간 요약
    - **일요일**: 이번 주 증시 정리 및 다음 주 주요 경제 일정/전망
- **PEF(사모펀드) 전용 브리핑**: M&A 및 PEF 관련 주요 뉴스를 추가 수집하고, 저신뢰 기사 필터링을 거쳐 GP(General Partner) 관점의 심층 인사이트 브리핑을 별도로 생성합니다. (참고 뉴스 원문 링크 포함)
- **Baikal 언급 뉴스 레이더**: `PEF_FIRM_NAME`으로 지정한 운용사명이 직접 언급된 최신 뉴스를 별도 수집하고, 기사에 등장한 회사/기관/인물을 요약합니다.
- **IT PMI 관점 보강**: PEF 브리핑에서 Day-1/TSA, 보안/데이터, 100일 IT 실행 항목을 간단한 체크포인트로 정리합니다.
- **중복 뉴스 방지**: 실전 실행에서 채택된 기사를 히스토리 파일에 기록해 다음 실행부터 같은 링크/제목의 뉴스를 건너뜁니다. (`--test` 실행은 히스토리를 저장하지 않음)
- **신규 뉴스 없음 처리**: 중복 제거 결과 신규 채택 기사가 0건이면 빈 섹션을 만들지 않고, 시장 데이터와 액션 중심의 간단한 fallback 브리핑을 전송합니다.
- **텔레그램 알림**: 생성된 보고서를 지정된 Telegram 채널로 자동 전송합니다. (PEF 브리핑 채널 분리 가능)
- **휴장일 자동 감지**:
    - **한국 증시 휴장일**: "오늘의 증시 전망" 대신 글로벌 시황 위주의 리포트 작성
    - **미국 증시 휴장일**: 전일 마감 데이터 부재 시 일반 미국 경제 뉴스 위주 분석

## 🛠 설치 및 설정 (Installation)

### 1. 요구 사항 (Prerequisites)
- Python 3.8 이상
- Google Gemini API Key
- Telegram Bot Token & Channel ID

### 2. 프로젝트 클론 및 패키지 설치
```bash
git clone <repository-url>
cd financial_news

# 의존성 패키지 설치
pip install -r requirements.txt
```

### 3. 환경 변수 설정 (.env)
프로젝트 루트 디렉토리에 `.env` 파일을 생성하고 아래 내용을 입력하세요.

```ini
# Gemini API Key (https://aistudio.google.com/)
GEMINI_API_KEY=your_gemini_api_key_here

# Telegram 설정 (https://core.telegram.org/bots)
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHANNEL_ID=your_channel_id_here

# PEF 전용 Telegram 설정 (선택 사항)
# TELEGRAM_PEF_BOT_TOKEN 미설정 시 기본 TELEGRAM_BOT_TOKEN 사용
TELEGRAM_PEF_BOT_TOKEN=your_pef_bot_token_here
TELEGRAM_PEF_CHANNEL_ID=your_pef_channel_id_here

# PEF 브리핑 개인화 (선택 사항)
PEF_FIRM_NAME=Baikal Investment
PEF_PMI_ROLE=IT PMI Lead
PEF_FIRM_NEWS_LOOKBACK_DAYS=30
# 쉼표로 구분해 회사명 검색어를 직접 지정할 수 있음
PEF_FIRM_NEWS_QUERIES=바이칼인베스트먼트,바이칼 인베스트먼트,Baikal Investment

# 중복 뉴스 방지 히스토리 (선택 사항)
NEWS_HISTORY_ENABLED=true
NEWS_HISTORY_FILE=.news_history.json
NEWS_HISTORY_RETENTION_DAYS=30
NEWS_HISTORY_TITLE_MATCH_DAYS=7
```

## 📖 사용 방법 (Usage)

### 기본 실행
브리핑을 생성하고 텔레그램으로 전송합니다.
```bash
python main.py
```

### 수동 모드 테스트 (Manual Mode)
특정 요일의 로직을 강제로 테스트하려면 `--mode` 옵션을 사용하세요.
```bash
# 토요일 로직 (글로벌 주간 요약)
python main.py --mode saturday

# 일요일 로직 (주간 정리 & 다음주 전망)
python main.py --mode sunday

# 테스트 모드와 함께 사용 (텔레그램 전송 생략)
python main.py --mode sunday test
```

### 특정 날짜 테스트 (Date Test)
특정 날짜를 기준으로 휴장일 등을 테스트할 수 있습니다.
```bash
# 2024년 크리스마스 기준 실행 (한국 휴장 감지)
python main.py --date 2024-12-25 --test
```

### 테스트 모드 실행
텔레그램 전송을 건너뛰고 콘솔에만 결과를 출력합니다.
```bash
python main.py test
```
`test` 또는 `--test` 모드에서는 기존 히스토리를 읽어 중복 여부는 확인하지만, 새로 수집한 뉴스는 히스토리에 저장하지 않습니다.
링크 기준 중복은 `NEWS_HISTORY_RETENTION_DAYS` 동안 유지되고, 제목 기준 중복은 반복 제목 오탐을 줄이기 위해 `NEWS_HISTORY_TITLE_MATCH_DAYS` 동안만 적용됩니다.

### 중복 뉴스 히스토리 비활성화
특정 실행에서만 이전 수집 이력을 무시하려면 아래 옵션을 사용하세요.
```bash
python main.py --no-news-history
```

### Gemini 모델 조회
사용 가능한 Gemini 모델 목록을 확인합니다.
```bash
python list_models.py
```

## 📂 파일 구조
- `main.py`: 메인 실행 파일 (데이터 수집, AI 분석, 텔레그램 전송)
- `list_models.py`: 사용 가능한 Gemini 모델 확인용 스크립트
- `requirements.txt`: 필요한 Python 라이브러리 목록
- `.env`: API 키 등 보안 정보 저장 (직접 생성 필요)
