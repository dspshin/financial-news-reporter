# Financial News & Market Outlook Briefing Service

이 프로젝트는 주요 시장 지수와 경제 뉴스를 자동으로 수집하고, Google Gemini AI를 활용하여 전문적인 시장 전망 보고서를 생성한 후 Telegram 채널로 전송하는 자동화 도구입니다.

## 🚀 주요 기능

- **시장 데이터 수집**: `yfinance`를 사용하여 주요 지수(KOSPI, KOSDAQ, S&P 500, NASDAQ 등)와 환율, 비트코인 시세를 조회합니다.
- **뉴스 크롤링 & 분석**: Google News RSS를 통해 주요 경제 뉴스(미국 증시 마감, 특징주, 국내 증시 전망)를 수집하고, 기사 본문을 스크래핑하여 분석합니다.
- **AI 기반 브리핑 생성**: 수집된 데이터를 바탕으로 Gemini(2.0 Flash 등)가 전문 애널리스트 페르소나로 보고서를 작성합니다.
    - **평일 (월~금)**: 한국 증시 데일리 전망 및 전략
    - **토요일**: 미 증시 마감 기준 글로벌 증시 주간 요약
    - **일요일**: 이번 주 증시 정리 및 다음 주 주요 경제 일정/전망
- **텔레그램 알림**: 생성된 보고서를 지정된 Telegram 채널로 자동 전송합니다.
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

### 특정 날짜 테스트 (Date Test)
특정 날짜를 기준으로 휴장일 등을 테스트할 수 있습니다.
```bash
# 2024년 크리스마스 기준 실행 (한국 휴장 감지)
python main.py --date 2024-12-25 --test
```
```

### 테스트 모드 실행
텔레그램 전송을 건너뛰고 콘솔에만 결과를 출력합니다.
```bash
python main.py test
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
