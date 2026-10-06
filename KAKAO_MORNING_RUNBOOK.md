# 카카오톡 순차 직접 전송 런북

## 대상과 전송 설정 확인

2026-10-07 이후 새 발행은 **Telegram → 금복회 장자풍도 60대下 → x삼성 투자방 → 송골매 허리 → 사랑해요♡♥** 순서로 동일 PNG를 각 방에 직접 첨부한다. 한 방을 sent·허용된 skipped 또는 근거 있는 deferred로 처리한 뒤 다음 방으로 진행한다. 실패한 방 때문에 나머지 방을 중단하지 않으며 **공유 기능·동시 선택·share-begin·share-checkpoint를 사용하지 않는다.** 제작·Telegram·완료 판정은 [발행 런북](MORNING_IMAGE_RUNBOOK.md), 폐기한 방식의 근거는 [과거 이력](MORNING_WORKFLOW_HISTORY.md)에 있다.

`.kakao_morning.json`의 `direct_delivery`는 적용일 2026-10-07, 위 네 방 순서, `max_retries: 3`, `continue_on_room_failure: true`로 설정한다. 기존 `room_names`·`share_delivery`·`room_start_dates`는 과거 묶음 판정을 위해 보존한다. 적용일 이후에는 코드가 direct_delivery를 우선하므로 공유 설정을 삭제하거나 매일 다시 쓰지 않는다. 설정·영수증은 Git에서 제외하며 인증정보를 추가하지 않는다. 과거 묶음은 자동 재개·소급 전송하지 않는다.

## 공통 원칙

- Telegram sent·message_id, 당일 검수·이미지 해시와 방별 영수증을 확인한다. sync 결과와 실제 화면을 대조하고 sent/skipped인 방은 건너뛴다. pending·uncertain·rejected·손상 기록은 새 begin이나 삭제로 재시작하지 않는다.
- **이미 로그인된 카카오톡만 Computer Use로 조작한다.** 로그인 시도·요청·대기, 비밀번호·QR·인증번호 입력, 잠금 해제·권한 변경은 하지 않는다.
- 방 전환·검색·파일 선택·첨부 전환 뒤 최신 접근성 항목을 다시 읽는다. 좌표는 현재 스크린샷 기준으로만 사용하며 이전 창의 번호·좌표를 재사용하지 않는다. 열린 창의 정확한 방 이름으로 확인하고 참여자 이름·검색 결과만으로 판단하지 않는다. 동명이방·대상 불명확은 해당 방에 보내지 않고 보류한 뒤 다음 방을 확인한다.
- 명령은 상태만 기록하고 전송은 UI가 수행한다. 명령 성공 응답 확인 후 다음 UI 동작을 한다. 전송 버튼은 한 번만 누르며 선택·첨부·창 닫힘은 발신 성공 근거가 아니다.

## 방마다 수행할 직접 전송

예시의 방 이름을 현재 대상의 정확한 이름으로 바꾼다. 각 명령은 해당 화면을 실제 확인한 뒤 따로 실행하며 예시를 일괄 실행하지 않는다.

1. 정확한 방·로그인 상태를 확인하고 미시작일 때만 begin을 기록한다.

```bash
python3 kakao_morning_state.py begin --bundle output/YYYY-MM-DD-am --room '금복회 장자풍도 60대下' --observed-room '금복회 장자풍도 60대下'
```

2. `파일전송 ⌘O` 후 실제 파일 선택창, `⌘⇧G` 후 실제 경로 입력창을 확인한다. 당일 briefing.png의 절대 경로를 입력하고 날짜·파일명·선택 상태를 확인한 뒤 기록한다.

```bash
python3 kakao_morning_state.py checkpoint --bundle output/YYYY-MM-DD-am --room '금복회 장자풍도 60대下' --observed-room '금복회 장자풍도 60대下' --phase file_selected --evidence '실제 당일 PNG 선택과 확인시각'
```

3. 최신 열기 버튼으로 첨부 미리보기를 열어 동일 이미지 1개·방 이름을 확인한다.

```bash
python3 kakao_morning_state.py checkpoint --bundle output/YYYY-MM-DD-am --room '금복회 장자풍도 60대下' --observed-room '금복회 장자풍도 60대下' --phase attachment_ready --evidence '실제 동일 PNG 1개 첨부 미리보기'
```

4. 다음 UI 동작이 ‘1개 전송’ 클릭일 때 send_requested를 저장하고 **한 번만** 클릭한다.

```bash
python3 kakao_morning_state.py checkpoint --bundle output/YYYY-MM-DD-am --room '금복회 장자풍도 60대下' --observed-room '금복회 장자풍도 60대下' --phase send_requested --evidence '정확한 방·동일 이미지 1개·현재 전송 버튼 확인'
```

5. 정확한 방의 당일 동일 이미지·새 발신시각·실패/전송 중 표시 없음을 확인한 뒤 sent를 기록한다. 확인 불가는 uncertain으로 남기고 재전송하지 않는다.

```bash
python3 kakao_morning_state.py sent --bundle output/YYYY-MM-DD-am --room '금복회 장자풍도 60대下' --evidence '실제 새 발신 이미지·시각·실패 표시 없음 확인'
```

begin / checkpoint / sent / uncertain / skip / retry / defer는 기록 저장 후 run-status.json을 자동 갱신한다. 방별 성공 또는 아래 실패 처리를 기록한 뒤 다음 방에서 1~5단계를 반복한다. 명령 오류가 나면 화면과 기록부터 확인하고 전송 동작을 반복하지 않는다.

## 인증 안내·생략·도구 오류

| 실제 관측 | 처리 |
| --- | --- |
| 로그인 상태에서 ‘My비밀번호 없이 이용 가능’ 등 선택 안내 | 최신 ‘다음에하기/다음에 하기/나중에’로 닫고 방·로그인을 재확인; 반복 안내는 optional_notice 재시도 한도 적용 |
| 로그인 화면 / 필수 재인증 / 실제 OS 잠금 / 명시적 권한 거부 | 각각 not_logged_in / reauthentication / os_locked / permission_unavailable로 남은 방 생략 |
| 읽을 수 있는 화면에서도 로그인 여부 불명확 | login_unknown으로 생략 |
| AX·창 위치 오류 또는 도구의 잠금 오류 문구만 있음 | 실제 잠금으로 단정하지 않고 아래 제한된 읽기 전용 확인 |

비밀번호 입력·필수 인증 우회·보안 설정 변경은 하지 않는다. 필수 여부가 불명확한 안내에는 입력을 보내지 않고 상태만 확인한다. 읽을 수 있는 화면에서도 인증 여부가 불명확하면 login_unknown을 적용한다.

## 재시도 3회와 다음 방 진행

모든 카카오톡 복구는 **같은 방·같은 단계·같은 작업에서 최초 시도 외 추가 재시도 최대 3회**다. 입력 없는 상태 조회(state_read: getAXState/getScreenshot 합산), 방 탐색(navigate), 미전송 파일 선택(file_select), 미전송 첨부(attachment), 명시적 선택 안내 닫기(optional_notice)에 동일 한도를 적용한다. 재시도 **전에** 아래 명령을 실행하고 성공한 경우에만 UI 동작을 한다. 단계는 기존 영수증에서 읽으며 횟수는 `.morning_kakao_delivery/YYYY-MM-DD-am-recovery.json`에 누적된다. 문맥 복구·재실행·오류 문구 변경으로 초기화하지 않고 4번째 재시도나 새로운 복구 루프를 만들지 않는다.

```bash
python3 kakao_morning_state.py retry --bundle output/YYYY-MM-DD-am --room '금복회 장자풍도 60대下' --operation navigate --evidence '실제 오류·현재 단계·KST 확인시각'
```

해당 작업의 3회 재시도가 실패하면 원래 영수증을 보존하고 아래처럼 defer를 기록한다. **deferred는 미완료이며 성공이나 허용된 생략이 아니다.** sync가 다음 미처리 방을 제시하면 같은 PNG의 직접 전송을 계속한다. 첫 금복회 방이 실패해도 나머지 방은 독립 전송하므로 계속할 수 있다. 전송 전 첨부창이 남았으면 정확히 미전송인 해당 창의 취소만 수행해 닫고 새 방을 확인한다. 창 상태가 불명확하면 입력하지 않고 아래 공통 장애 처리로 넘긴다.

```bash
python3 kakao_morning_state.py defer --bundle output/YYYY-MM-DD-am --room '금복회 장자풍도 60대下' --reason retry_exhausted --operation navigate --evidence '재시도 3회 실패·실제 관측과 KST 시각'
```

| 복구 결과 | 방별 기록과 다음 행동 |
| --- | --- |
| 전송 전 작업이 재시도 3회 후 실패 | retry_exhausted로 보류 → 다음 방 |
| 클릭했거나 send_requested인데 결과 확인 불가 | 전송·재첨부 금지; 결과 조회만 최대 3회 재시도. pending이면 uncertain 기록 후 delivery_unconfirmed로 보류 → 다음 방 |
| 손상된 영수증 | 원본 보존, 읽기 전용 확인 후 receipt_invalid로 보류 → 다음 방 |
| 정확한 방 정체성을 확인할 수 없음 | room_unavailable로 보류 → 다음 방 |
| 조회 재시도 후에도 UI 전체를 읽을 수 없음 | tool_unavailable로 현재 방·남은 방 각각 보류; 입력을 강행하지 않고 부분 결과 보고 |

표의 보류 명령은 위 defer 예시의 --reason·--evidence를 실제 상황에 맞춰 사용한다(retry_exhausted만 --operation 필수). 보류된 방은 같은 실행 재개에서 다시 begin·첨부·전송하지 않는다. 보류 후 실제 발신이 읽기 전용으로 확인된 기존 pending만 sent로 해결할 수 있다. 전송 여부가 불명확한 시도는 skipped로 바꾸지 않는다.

AX·창 위치 오류·도구 잠금 오류 문구만으로 실제 OS 잠금으로 단정하지 않는다. 실제 로그인·잠금 화면·명시적 권한 거부가 확인되면 즉시 아래 생략 규칙을 적용하며 로그인·잠금 해제·권한 변경을 재시도하지 않는다. 공통 장애로 남은 방에도 진행이 불가능하면 각 방에 사유를 기록하고 모두 처리한다. delivery-diagnostics.json에 오류 종류·단계·KST 시각·재시도 및 확인 결과·보류/미완료 방을 남기되 전체 대화·스크린샷·인증정보는 저장하지 않는다.

생략은 남은 방마다 실제 관측 근거로 기록한다. 미시작 또는 미전송이 확인된 전송 전 pending에만 skip을 적용한다. 앞선 성공은 유지하며 send_requested·uncertain·손상된 기록을 skipped로 바꾸지 않는다.

```bash
python3 kakao_morning_state.py skip --bundle output/YYYY-MM-DD-am --room '금복회 장자풍도 60대下' --reason not_logged_in --evidence '실제 로그인 화면과 관측시각'
```

## 중단 복구

| 기록 | 허용되는 다음 행동 |
| --- | --- |
| not_started | 정확한 방·로그인과 앞선 방의 sent/skipped/유효한 deferred 확인 후 시작 |
| room_verified / file_selected / attachment_ready의 pending | 같은 방·당일 같은 파일 선택/첨부 화면과 미전송 실행 이력이 일치할 때 기존 시도만 이어감 |
| send_requested·단계 없는 pending·uncertain·rejected·손상 | 재클릭·재첨부·새 begin 없이 발신 여부만 읽기 전용 확인 |
| sent | 성공 보존·재전송 없음 |
| 실제 근거가 있는 skipped | 생략 보존; 남은 방에도 동일 장애가 적용되면 각각 생략 기록 |
| 유효한 deferred | 원본 영수증·누적 횟수 보존; 재전송 없이 다음 미처리 방으로 진행 |

전송 요청 후 실제 발신 확인은 해당 방만 sent, 판단 불가는 uncertain과 deferred로 남겨 다음 방으로 진행한다. 보류 기록 손상·이미지 해시 변경·QA/Telegram 불일치처럼 모든 전송의 근거가 깨진 경우에는 전송을 강행하지 않고 미완료를 보고한다. 과거 공유 기록은 직접 전송으로 변환하지 않고 보존한다. 최종 완료 검사는 발행 런북의 morning_delivery_status.py check를 따르며 보류가 남으면 종료코드 2와 대상별 사유를 보고한다.
