# 아침·마감 시황 WordPress 공개 게시

## 대상과 실행 순서

2026-11-08 사용자가 초안 공개와 이후 아침·마감 자동 게시를 승인했다. 공개 사이트는 `https://financialnews7.wordpress.com`, 사이트 ID는 `258515583`이다. `WORDPRESS_DELIVERY.json`의 `effective_from`인 **2026-11-09 아침판부터** 적용한다. 11/6 마감판은 별도 승인으로 이미 생성한 글 ID 7을 11/8 공개했다. 과거 발행분은 새 글로 소급 생성하지 않는다.

순서는 **이미지 검수 → Telegram → 카카오톡 모든 활성 방 처리 → WordPress → 최종 check → 종료 사용량**이다. 카카오톡의 각 방이 sent·허용된 skipped·근거 있는 deferred로 처리된 뒤 WordPress로 진행한다. 카카오톡 보류가 남으면 WordPress 게시 후에도 전체 결과는 미완료로 보고한다. `channel_targets_handled`는 채널 처리, `all_targets_handled`는 WordPress까지의 처리를 뜻한다.

연결된 **WordPress 플러그인 도구**가 실제 업로드·게시를 수행하고 `wordpress_briefing.py`가 원고 변환·검수·단계 영수증을 관리한다. 추가 API 키나 토큰을 저장하지 않는다. 승인된 공개 게시에는 반복 승인을 요청하지 않는다. 실제 권한 거부·인증 만료는 오류와 재개 위치를 기록한다.

카테고리는 **아침판 → 아침 시황(ID `1120528`), 마감판 → 마감 시황(ID `136183968`)**으로 지정한다. 실제 ID는 `WORDPRESS_DELIVERY.json.category_ids`에서 읽는다. 11/6 마감 글 ID 7도 마감 시황으로 분류했다. 게시 전 `wpcom-mcp-categories-get`으로 해당 ID와 이름을 확인하고, 없거나 다른 카테고리이면 새 카테고리를 임의 생성하거나 Uncategorized로 게시하지 말고 확인 근거와 함께 보류한다. 기본 Uncategorized 자체는 삭제·변경하지 않는다.

## 게시할 자료와 검수

검수한 동일 `briefing.png`·`manuscript.txt`·`sources.md`·`source-checks.json`·`qa.json`을 사용한다. 공개 본문 순서는 **정보 기준시각 / 이미지 / 원고 전문 / 분석 근거와 이슈 선정 이유 / 출처·관측시각**이다. 날짜와 시각, 국내 종가·잠정 수급·월물·지연·비교일은 원고 그대로 유지한다. 사이트 기본 언어는 `ko_KR`, 시간대는 `Asia/Seoul`이다.

당일 수집 기록으로 `blog-analysis.md`를 작성한다. 확인된 사실과 출처 링크, 핵심 이슈 3개를 고른 이유, 해석의 조건과 남은 변수, 확인된 아침 조건과 마감 결과를 정리한다. 실제 기록이 있을 때만 수집·열람 건수를 적는다. 당시 기준시각 이후 결과를 추가하지 않는다. 원문 기사 전문·조회 화면·운영 로그·계정 정보는 본문에 넣지 않으며 독자용 출처와 자체 요약을 사용한다. 제목과 문단은 근거가 확인된 내용으로 작성한다.

카카오톡 처리 후, 아래 항목을 검수하고 `blog-qa.json`을 만든다. `checked_at_kst`는 실제 검수시각, `edition`은 정확한 bundle 이름이다. `checks`는 모두 true여야 한다. SHA-256은 아래 여섯 파일을 실제 계산한다.

```json
{
  "edition": "YYYY-MM-DD-am",
  "status": "passed",
  "checked_at_kst": "실제 검수시각 ISO 8601",
  "checks": {
    "manuscript_and_image_match": true,
    "analysis_supported_by_collected_sources": true,
    "cutoff_observation_times_and_links_checked": true,
    "public_content_and_hangul_checked": true
  },
  "sha256": {
    "briefing.png": "실제 해시",
    "manuscript.txt": "실제 해시",
    "sources.md": "실제 해시",
    "source-checks.json": "실제 해시",
    "qa.json": "실제 해시",
    "blog-analysis.md": "실제 해시"
  }
}
```

WordPress 블록 편집 스킬과 `wpcom-blocks-allowed`로 `core/paragraph`, `heading`, `image`, `list`, `list-item` 지원을 확인한다. 저장 본문은 Block Editor에서 편집 가능한 블록이다. `sources.md`에는 실제 HTTPS 출처와 날짜·관측시각을 적고 로컬 파일명만으로 출처를 대신하지 않는다.

## 최초 게시와 단계별 기록

아래의 `YYYY-MM-DD-am`을 실제 bundle로 바꾼다. 마감판은 모든 명령에 `YYYY-MM-DD-pm`을 쓴다. 영수증은 `.wordpress_image_delivery/YYYY-MM-DD-am.json` 또는 `-pm.json`에 분리되며 Git에서 제외한다. 설정 파일에는 사이트 ID·공개 주소·적용일·판별 카테고리 ID를 둔다.

1. `morning_delivery_status.py sync`로 카카오톡 전체 처리 여부와 다음 작업을 확인하고 `wordpress_briefing.py status`로 원래 영수증을 확인한다. WordPress `wpcom-manage-site(action=get_status)`에서 사이트 공개 상태를 확인한다. 예상과 다른 사이트·private·coming_soon이면 운영 설정을 자동 변경하지 않고 근거와 함께 보류한다.
2. 같은 사이트의 `wpcom-mcp-posts-list`에 정확한 slug `market-YYYY-MM-DD-am`과 `status=["any"]`를 지정한다. 기존 글이 있으면 원래 글을 읽고 아래 재개 절차를 적용한다. 같은 날짜 제목만 보고 새 글을 만들지 않는다.
3. WordPress 영수증이 `not_started`이고 기존 글이 없을 때만 준비한다.

```bash
python3 wordpress_briefing.py prepare --bundle output/YYYY-MM-DD-am
```

준비 단계는 당일 QA·별도 블로그 QA·입력 해시·카카오톡 처리 여부를 검사하고 `wordpress-upload.json`을 생성한다. `prepare`를 중복 실행해 기존 기록을 초기화하지 않는다.

4. 업로드 직전에 아래 기록을 남긴 뒤, `wordpress-upload.json`의 인자를 그대로 `wpcom-media-create`에 넘긴다. `file`은 준비된 동일 PNG의 절대 경로다. 호출 전 파일명과 대상 사이트를 간결히 알린다.

```bash
python3 wordpress_briefing.py begin-media --bundle output/YYYY-MM-DD-am
```

5. 실제 업로드 응답의 JSON(`data`를 포함한 응답)을 `wordpress-media-result.json`에 저장한다. CLI는 이미지 URL에서 원본과 픽셀을 대조하고 본문·대표 이미지·이메일/소셜 전송 해제 설정을 담은 `wordpress-post.json`을 만든다.

```bash
python3 wordpress_briefing.py media --bundle output/YYYY-MM-DD-am --result output/YYYY-MM-DD-am/wordpress-media-result.json
```

6. 동일 slug의 기존 글이 없는지 확인하고, 아래 기록 뒤 `wordpress-post.json`의 인자를 그대로 `wpcom-mcp-posts-create`에 넘긴다. `categories` 배열에는 준비된 판의 카테고리 ID 하나만 지정한다. `status=publish`로 공개하며 `_jetpack_dont_email_post_to_subs=true`, `jetpack_publicize_feature_enabled=false`를 유지한다. 대표 이미지 ID는 실제 업로드 응답의 ID다. 호출 전 제목과 즉시 웹 공개를 간결히 알린다.

```bash
python3 wordpress_briefing.py begin-post --bundle output/YYYY-MM-DD-am
```

7. 생성된 ID로 `wpcom-mcp-posts-get(context=edit)`을 호출한다. `id`, `status`, `slug`, `link`, `content`, `featured_media`, `categories`, `meta`를 포함해 실제 응답을 `wordpress-post-result.json`에 저장한다. `_content_warnings`가 있으면 원고·숫자·이미지·링크에 미친 영향을 대조한다.

```bash
python3 wordpress_briefing.py published --bundle output/YYYY-MM-DD-am --result output/YYYY-MM-DD-am/wordpress-post-result.json
python3 morning_delivery_status.py check --bundle output/YYYY-MM-DD-am
```

`published`는 실제 상태 publish, 정확한 slug·대표 이미지·판별 카테고리, 준비 원고의 텍스트·출처 링크·이미지·제목 구조, 이메일/소셜 설정을 대조한다. 로그인 없는 공개 페이지에서 원고가 보이는지까지 확인한 후 URL·글 ID·이미지 ID·해시를 영수증에 기록한다. 이 검증을 통과해야 최종 검사에서 WordPress가 완료로 집계된다. 게시 URL도 최종 응답에 제공한다.

## 중단·재개와 보류

| 영수증 상태 | 다음 동작 |
| --- | --- |
| not_started | 카카오톡 전체 처리·기존 글 검색 후 prepare |
| prepared | 기존 미디어가 없는지 확인 후 begin-media와 단 한 번 업로드 |
| media_requested | 정확한 파일명으로 미디어 목록을 읽고 원본 픽셀 일치를 확인해 기존 응답으로 media 기록 |
| media_uploaded | 기존 slug 검색 후 begin-post와 단 한 번 생성 |
| post_requested | 정확한 slug를 읽고 원래 글의 ID로 get 후 published 검증 |
| published | 동일 글 URL을 재사용하며 업로드·생성을 반복하지 않음 |
| deferred | 원래 기록·단계·오류를 보존하고 보류 보고. 이미 요청한 미디어/글의 읽기 전용 확인은 가능 |
| invalid | 원본 보존 후 불일치 원인 확인. 새 업로드·글 생성 금지 |

`media_requested`·`post_requested` 뒤 도구 시간초과·연결 오류·응답 유실은 실제 성공했을 가능성이 있다. 보내기·글 생성·업로드를 반복하지 않는다. 읽기 전용 결과 확인을 재시도하기 전에 `python3 wordpress_briefing.py retry --bundle output/YYYY-MM-DD-am --evidence '실제 오류·재개 단계·KST 확인시각'`을 실행한다. 영수증의 `read_retries`에 단계별 최대 3회로 누적하며 재개 시 초기화하지 않는다. 기존 결과를 확인할 수 없으면 다음처럼 보류한다.

```bash
python3 wordpress_briefing.py defer --bundle output/YYYY-MM-DD-am --evidence '실제 오류·KST 조회시각·마지막 단계·확인한 slug/미디어 ID·재개 위치'
```

WordPress 오류 때문에 Telegram·카카오톡 성공 영수증을 다시 쓰거나 전송하지 않는다. 최종 검사에서 WordPress가 미처리·보류이면 전체 완료라고 보고하지 않는다. 보류된 카카오톡 방이 있어도 나머지 방을 처리한 뒤 WordPress를 진행하며 각 미완료를 구분한다. 정상 휴장이나 이미 전 대상과 공개 게시가 완료된 경우는 기존 조용한 종료 기준을 적용한다.
