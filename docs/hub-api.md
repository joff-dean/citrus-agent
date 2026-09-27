# Hub HTTP API 계약 — protocol_version 1

이 문서는 **구현된 클라이언트가 사용하는 계약**입니다. 기존 FastAPI에 라우터로 추가하거나
기존 서버 API를 이 형식으로 변환합니다. 기본 주소는 `citrus-agent init --hub <base_url>`로 설정합니다.
`https://host/internal/agent`처럼 경로 접두사를 포함해도 됩니다. 아래 경로가 뒤에 붙습니다.

## 공통 규칙

- 모든 요청은 `POST`, `Content-Type: application/json`, UTF-8입니다.
- 등록 API를 제외하면 `Authorization: Bearer <device_token>`이 필수입니다.
- JSON 응답 최상위는 객체입니다. 리다이렉트는 거절합니다. 응답은 최대 2 MiB입니다.
- 인터넷/사내 주소는 HTTPS만 허용합니다. localhost HTTP는 개발 테스트용입니다.
- `agent_id`, `job_id`, `session_id`, `project_id`, `request_id` 식별자는 경로에 안전한
  ASCII 영숫자·`-_.` 조합(1–128자)을 사용합니다. owner_id는 사내 원본 문자열입니다.
- 시간 `at`, `expires_at`은 UTC Unix epoch 초, duration은 초입니다.
- 200: 성공, 400/422: 계약 오류, 401/403: 인증·권한 오류, 410: 장치 폐기,
  429/5xx: 일시 장애입니다. 401/403/410 수신 시 PC는 실행 중 작업을 중단하고 종료합니다.
- API 요청/응답 로그에서 Authorization, device_code, device_token을 제거합니다.
- API는 영구 저장이 끝난 후 응답합니다. 메신저 전송 완료까지 기다리지 않습니다.

## 1. PC 등록

### POST /v1/enrollments

요청:

```json
{"name":"my-dev-pc","platform":"Windows","protocol_version":1}
```

응답:

```json
{"id":"E1","device_code":"high-entropy-secret","user_code":"ABCD-1234","expires_in":300,"interval_seconds":3}
```

device_code는 충분한 난수 비밀이며 PC에만 반환합니다. 사용자에게는 user_code만 표시합니다.
등록 API에는 rate limit을 적용합니다. 인증된 메신저 사용자만 user_code를 연결할 수 있습니다.

### POST /v1/enrollments/E1/poll

요청: `{"device_code":"high-entropy-secret"}`

아직 메시지가 오지 않은 경우: `{"status":"pending"}`

메신저에서 연결한 경우:

```json
{"status":"matched","owner":{"id":"employee-123","display_name":"홍길동"}}
```

종료된 경우: `{"status":"expired"}` 또는 `{"status":"cancelled"}`.
PC는 matched 응답을 보여주고 로컬 사용자가 `y`로 확인하기 전까지 토큰을 저장하지 않습니다.
한 번 matched 된 사용자 연결은 다른 메시지로 바꾸지 않습니다.

### POST /v1/enrollments/E1/confirm

요청:

```json
{"device_code":"high-entropy-secret","owner_id":"employee-123"}
```

응답:

```json
{"agent_id":"A1","owner_id":"employee-123","token":"long-random-device-token"}
```

matched owner와 확인 요청의 owner가 동일하고, 코드가 유효한 경우에만 발급합니다.
동일 device_code/owner에 대한 confirm은 만료 전 멱등 처리하여 응답 유실을 복구할 수 있게 합니다.
서버는 토큰을 해시로 저장합니다. 등록 단계의 재전송용 응답은 짧게 보존하고 보호해야 합니다.

### POST /v1/enrollments/E1/cancel

요청: `{"device_code":"high-entropy-secret"}` → 응답: `{}`.

## 2. 상태·권한 확인

### POST /v1/agents/A1/check

요청: `{"protocol_version":1}` → 응답: `{}`.
인증과 버전 호환성을 확인하는 읽기 전용 요청입니다. doctor가 사용하며 lease를 갱신하지 않습니다.

### POST /v1/agents/A1/heartbeat

클라이언트는 기본 5초 간격으로 전송합니다.

```json
{
  "version":"0.1.0",
  "platform":"Linux",
  "name":"my-dev-pc",
  "projects":{"payment":{"runtimes":["codex","claude"],"policy":"development"}},
  "capabilities":["resume","cancel","approval","user_input"],
  "active_job":{"id":"J1","lease_token":"opaque-job-lease"}
}
```

작업이 없으면 active_job은 null입니다. projects는 전체 현재 목록이며 로컬 경로는 보내지 않습니다.
capabilities는 클라이언트 기능입니다. 실제 CLI 설치/인증 상태는 PC doctor와 현장 시험으로 확인합니다.

```json
{
  "lease":{"job_id":"J1","valid":true,"seconds":60},
  "controls":[]
}
```

활성 작업이 없으면 lease는 null입니다. 활성 작업의 lease가 누락되거나 valid가 false이면
PC는 작업을 중단합니다. seconds는 30–300 범위입니다. 서버는 owner·agent·job·lease_token을 모두
검증한 뒤 갱신합니다. 이미 만료된 실행을 임의로 다시 허가하지 않습니다.

## 3. 작업 가져오기

### POST /v1/agents/A1/jobs/claim

```json
{"request_id":"23a18901-a81d-47c8-b620-676bfbc7c4db","wait_seconds":20}
```

작업이 없으면 최대 20초 기다린 뒤 `{"job":null}`을 반환합니다. 클라이언트 HTTP 제한은 35초입니다.
request_id별 응답을 저장합니다. 같은 ID의 재요청에는 같은 결과를 반환합니다.
empty 응답도 그 request_id에 대한 결과입니다. 클라이언트가 다음 수신에는 새 ID를 사용합니다.

```json
{
  "job":{
    "id":"J1",
    "agent_id":"A1",
    "owner_id":"employee-123",
    "session_id":"S1",
    "project_id":"payment",
    "runtime":"codex",
    "prompt":"최근 테스트 실패 원인을 찾아줘",
    "lease_token":"opaque-job-lease",
    "lease_seconds":60
  }
}
```

runtime은 codex 또는 claude입니다. prompt는 1–100000자입니다.
PC는 자체 등록된 프로젝트·런타임·소유자만 허용합니다. cwd, 임의 실행 파일, CLI 인자, provider
세션 ID를 서버에서 지정할 수 없습니다. PC 한 대는 작업 하나만 실행합니다.

서버는 lease 만료 작업을 자동 재배정하면 안 됩니다. claim 응답이 유실되어 반환될 기존 작업의
lease가 만료됐다면 서버는 유효한 새 lease를 임의 발급하지 말고 미확정 상태로 관리합니다.

## 4. 이벤트 업로드

### POST /v1/agents/A1/events

배치당 최대 50개 이벤트이며 PC의 기본 발신 주기는 1초입니다.

```json
{
  "events":[{
    "id":"6fbb06d8-071b-4a3e-9e22-ac1bc8c3c1b8",
    "sequence":17,
    "job_id":"J1",
    "type":"completed",
    "at":1790550000.0,
    "data":{"text":"파일 2개 수정, 테스트 18개 통과"}
  }]
}
```

```json
{"acknowledged":["6fbb06d8-071b-4a3e-9e22-ac1bc8c3c1b8"]}
```

ACK한 이벤트만 PC에서 삭제됩니다. 중복 이벤트도 이미 저장됐다면 ACK합니다.
중복 키는 agent_id+event_id입니다. sequence는 로컬 정렬용이고 전역 순서가 아닙니다.
ACK할 이벤트가 없으면 빈 배열을 반환합니다. 알 수 없는 이벤트도 저장·ACK 후 운영 경고를
남기는 편이 전체 outbox를 막는 것보다 낫습니다.

| type | data 주요 내용 |
|---|---|
| started | runtime, project_id |
| session | Hub session_id (provider ID는 PC에만 저장) |
| message | 사용자가 볼 수 있는 text |
| progress | operation, status |
| approval_required | request_id, digest, expires_at, details |
| input_required | request_id, digest, expires_at, details.questions |
| request_closed | request_id; 더 이상 응답을 받지 않음 |
| request_rejected | 승인 요청이 너무 큰 경우 reason |
| control_ack | control_id, accepted |
| completed | text, 선택적 usage |
| failed | error_type |
| cancelled | reason: user_cancelled, job_timeout, lease_expired 등 |
| rejected | reason: invalid_job_or_local_policy |
| interrupted | reason: agent_restarted, retry_safe:false |
| duplicate_ignored | retry_safe:false; 원래 작업 상태를 덮어쓰지 않음 |

단일 텍스트는 최대 16000자 후 `[TRUNCATED]`로 표시될 수 있습니다. 서버는 모바일 메시지 한도에
맞춰 추가 분할합니다. 최종 completed와 앞선 message가 같은 내용이면 한 번만 보여줍니다.

## 5. 제어 입력

별도 PC 인바운드 API는 없습니다. 서버가 heartbeat 응답의 controls 배열에 입력을 넣습니다.
control_ack를 영구 수신할 때까지 동일 control ID로 재전송합니다. batch는 최대 50개로 제한합니다.

승인:

```json
{
  "id":"C1","type":"approval","job_id":"J1","owner_id":"employee-123",
  "request_id":"original-request-uuid","digest":"original-sha256",
  "decision":"allow"
}
```

거절은 decision `deny`입니다. request_id와 digest는 원래 이벤트의 값을 그대로 돌려줍니다.
digest를 메신저의 마스킹된 문자열로 다시 계산하지 않습니다. 명령 인자 변경·권한 영구 승인은
지원하지 않습니다. 서버가 사용자 인증·소유권·승인 만료를 먼저 확인하고 PC도 다시 검증합니다.

추가 질문의 답변:

```json
{
  "id":"C2","type":"input","job_id":"J1","owner_id":"employee-123",
  "request_id":"original-request-uuid","digest":"original-sha256",
  "answers":{"question-id":["개발 환경"]}
}
```

Codex answers는 질문 ID → 문자열 배열입니다. Claude AskUserQuestion은 질문의 원문 → 문자열
형식을 사용합니다. 세션의 runtime과 원래 questions를 보고 변환하세요.
예: `{"answers":{"어느 환경인가요?":"개발 환경"}}`.

취소:

```json
{"id":"C3","type":"cancel","job_id":"J1","owner_id":"employee-123"}
```

취소는 실행 중인 정확한 job만 대상으로 합니다. 새로운 작업에 예전 cancel을 적용하지 않습니다.

## 6. 등록 해제

### POST /v1/agents/A1/revoke

요청 `{}` → 응답 `{}`. 토큰을 폐기하고 이후 요청을 401/403/410으로 거절합니다.
동일 소유자의 메신저 PC 해제 명령으로도 서버 측 폐기를 수행할 수 있어야 합니다.
PC 로컬 unregister는 서비스가 중지된 상태에서 실행합니다.
