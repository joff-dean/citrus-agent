# 기존 FastAPI 서비스에 추가할 작업

## 전제와 범위

이미 구현된 사내 메신저 송수신 FastAPI 서비스를 유지합니다. 이 저장소에는 해당 서버의
소스나 API 명세가 제공되지 않았으므로 기존 엔드포인트를 추측하여 호출하지 않습니다.
아래 Hub API와 메신저 라우팅을 기존 서비스에 추가하면 PC 클라이언트가 연결됩니다.
새 외부 서버, Telegram, 모바일→PC 직접 접속은 필요하지 않습니다.

```text
모바일 사내 메신저
    ↕ 기존 송수신 API
기존 FastAPI + 추가 Hub 라우터 + 발신 worker + 영구 DB
    ↕ PC가 시작하는 HTTPS 연결
Ubuntu / Windows citrus-agent
    ↕ 로컬 파이프
Codex app-server / Claude Agent SDK
```

PC 클라이언트 구현은 `src/citrus_agent/`, 서버의 정확한 인터페이스는 [hub-api.md](hub-api.md)입니다.
기존 서비스에 동일한 기능이 있으면 재사용하고 아래 계약으로 변환하는 얇은 어댑터만 추가합니다.

## 추가 구현 항목

| 항목 | 서버에서 해야 할 일 | 완료 조건 |
|---|---|---|
| 메시지 어댑터 | 인증된 발신자 ID, 대화방, 메시지 ID를 공통 이벤트로 변환 | 위조된 발신자·봇 자신의 메시지 제외 |
| PC 등록 | 코드 발급·메신저 사용자 연결·PC 확인·장치 토큰 발급 | 짧은 코드만으로 장치 토큰을 획득할 수 없음 |
| 장치 인증 | 토큰 해시 보관, 장치와 소유자 범위 확인, 폐기 | 다른 PC·사용자 자원 접근 시 403 |
| 프로젝트 목록 | heartbeat의 프로젝트·CLI·권한 목록 보관 | 메신저에서 대상 선택 가능 |
| 세션 라우팅 | 사용자/대화/스레드에서 PC·프로젝트·CLI 세션 선택 | 다른 사용자의 세션을 이어갈 수 없음 |
| 작업 저장·수신 | 영구 DB 큐, 원자적 claim, request_id 멱등 처리 | 응답 유실 시 같은 작업 반환 |
| 실행 lease | heartbeat에서 작업 점유 갱신 | 연결 단절 후 자동 재실행 금지 |
| 이벤트 수신 | 이벤트 ID 중복 제거, 영구 저장 후 ACK | 재전송해도 같은 결과를 다시 발송하지 않음 |
| 승인·질문·취소 | control 저장, 사용자 검증, heartbeat 응답으로 전달 | 실제 처리 여부를 control_ack로 확인 |
| 메신저 발신 | DB outbox를 읽는 별도 worker | 메신저 장애 후 결과 발신 재시도 |
| 감사·보관 | 요청자·대상·승인·결과 기록, 회사 보존정책 적용 | 민감한 원문과 인증 토큰을 일반 로그에 남기지 않음 |

## 추천 DB 구조

기존 PostgreSQL 등을 재사용합니다. 아래는 논리 모델이며 그대로 실행할 마이그레이션은 아닙니다.

| 테이블 | 핵심 필드·제약 |
|---|---|
| enrollments | id, device_code_hash, user_code_hash, expires_at, matched_owner_id, status |
| agents | id, owner_id, token_hash, name, last_seen_at, revoked_at |
| agent_projects | agent_id + project_id PK, runtimes, policy |
| sessions | id, owner_id, conversation_id, thread_id, agent_id, project_id, runtime |
| jobs | id, session_id, prompt, status, lease_token_hash, lease_expires_at |
| claim_receipts | agent_id + request_id UNIQUE, job_id 또는 empty 결과, created_at |
| job_events | agent_id + event_id UNIQUE, job_id, sequence, type, payload |
| controls | id, job_id, owner_id, request_id, digest, type, payload, expires_at, acknowledged |
| messenger_outbox | id, event_id, recipient, payload, status, retry_at |

핵심 트랜잭션은 다음 두 가지입니다.

1. claim: 요청 ID 확인 → 대기 작업 잠금 → 장치/사용자/프로젝트 재검증 → lease 발급 →
   claim 결과를 함께 커밋 → 응답. 동일 request_id는 동일 결과를 재전송합니다.
2. events: 이벤트 중복 확인 → 작업 상태 갱신 → 발신 outbox 생성 → 함께 커밋 → ACK.

클라이언트 outbox의 ACK와 메신저 전송 성공은 다른 단계입니다. ACK는 서버의 영구 수신을
뜻합니다. 그 뒤 메신저 전송 실패는 서버가 복구해야 합니다.

## 메신저 명령 연결 예시

| 메시지 | 서버 동작 |
|---|---|
| `/PC등록 ABCD-1234` | 개인 대화에서 인증된 사용자와 enrollment 연결 |
| `/pc 목록` | 내 PC와 마지막 heartbeat 시간 표시 |
| `/프로젝트 개발PC` | 해당 PC가 등록한 프로젝트·CLI 목록 표시 |
| `/새작업 개발PC payment codex` | 사용자에게 묶인 세션 생성 |
| 일반 문장 또는 `/이어서 S42 ...` | 선택한 세션에 job 생성 |
| `/상태 J103` | 마지막 수신 이벤트와 마지막 확인 시각 표시 |
| `/취소 J103` | 소유자 확인 후 cancel control 저장 |
| `/승인 <요청코드>` / `/거절 <요청코드>` | 대기 중 request_id·digest에 묶인 일회용 control 저장 |
| `/답변 <요청코드> ...` | 원래 질문 형식에 맞춰 input control 저장 |
| `/PC해제 개발PC` | 장치 토큰 폐기, 새 작업 차단 |

사내 메시지 길이 제한에 맞춰 결과를 분할하고 여러 progress 이벤트는 합쳐서 보냅니다.
원본 reasoning, 셸 stdout 전체, 토큰, 인증 파일을 모바일로 전송하지 않습니다.
승인 카드에는 대상 PC·프로젝트·작업·실행 내용·만료 시각을 함께 표시합니다.
버튼이 없는 메신저는 서버가 만든 짧은 요청코드를 사용합니다. 단순한 “네”를 승인으로 해석하지 않습니다.

## 세션과 권한

- 대화방의 표시 이름이나 메시지 본문의 사번을 인증 수단으로 사용하지 않습니다.
- 초기 구현은 PC 소유자 한 명만 해당 PC를 조작합니다. 공유 실행기는 별도 기능입니다.
- 단체방에서 결과를 공개하려면 사용자 소유권과 별도로 해당 방에 대한 출력 허용정책이 필요합니다.
  첫 배포는 봇과의 개인 대화를 권장합니다.
- 서버가 허용하는 권한과 PC에 등록된 권한의 교집합만 제공합니다.
- Hub는 명령 문자열, 로컬 경로, CLI 플래그, provider session ID를 job에 넣지 않습니다.
  실행 경로와 provider 세션 연결은 PC가 관리합니다.
- PC당 동시에 한 작업만 실행합니다. 같은 세션의 후속 작업은 현재 작업이 끝난 뒤 배정합니다.

## 장애 복구 규칙

- PC 미접속은 대기 상태로 표현합니다. 상태 메시지에는 마지막 확인 시각을 표시합니다.
- lease 만료는 `unknown/interrupted` 처리합니다. 다른 PC나 같은 PC에 자동 재배정하지 않습니다.
- 사용자가 확인한 후 새 job ID로 재시도할 수 있지만, 이미 발생한 파일 변경·외부 부작용을 먼저 확인합니다.
- 네트워크 단절 중 승인 시간이 만료되면 거절합니다. 오래된 승인을 새로운 작업에 재사용하지 않습니다.
- `completed`가 lease 만료 뒤 늦게 도착할 수 있습니다. 원래 agent/job에 결합된 결과를 감사 기록으로
  보존하고 충돌 상태를 표시합니다. 뒤늦게 성공했다고 이미 별도로 시작한 작업을 성공 처리하지 않습니다.
- `control_ack.accepted=true`는 제어 입력을 수락했다는 의미입니다. 취소 완료는 `cancelled` 이벤트로 확인합니다.
- 봇 webhook 중복은 회사 메시지 ID의 UNIQUE 제약으로 막습니다.

## 서버 인수 테스트

1. 두 사용자의 PC·세션을 등록하고 상호 접근이 차단되는지 확인합니다.
2. enrollment의 사용자 확인 전에는 장치 토큰을 발급하지 않는지 확인합니다.
3. claim 응답을 강제로 유실한 뒤 같은 request_id로 같은 job이 반환되는지 확인합니다.
4. 같은 이벤트 배치를 두 번 보내고 메신저 답장이 중복되지 않는지 확인합니다.
5. 승인 만료·다른 사용자 승인·다른 digest·이미 종료된 요청을 거절하는지 확인합니다.
6. 실행 중 PC 네트워크를 끊고 lease 만료가 자동 재실행으로 이어지지 않는지 확인합니다.
7. 메신저 전송만 실패시킨 뒤 서버 outbox가 결과를 복구하는지 확인합니다.
8. PC 토큰을 폐기하고 heartbeat 401/403 이후 클라이언트가 작업을 중단하는지 확인합니다.
9. Ubuntu와 Windows에서 각 CLI로 실제 회사 테스트 프로젝트를 실행합니다.

실제 서버 연동 시험은 서버 구현과 조직 인증정보가 준비된 환경에서 수행해야 합니다.
