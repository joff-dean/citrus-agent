# 권한 및 복구 경계

## 인증과 데이터

이 서비스는 사내 메신저를 통한 원격 명령 실행 기능입니다. 메신저 사용 권한과 PC 조작 권한은
별도입니다. 서버의 인증된 사용자 ID, 장치 토큰, 로컬에 고정된 owner_id를 함께 사용합니다.
Hub는 신뢰하는 사내 제어 서버여야 합니다. 허용된 메신저 채널이라는 이유만으로 실행 권한을
자동 부여하지 않습니다.

장치 토큰은 CLI 인자나 환경변수로 전달하지 않습니다. Windows는 현재 사용자 DPAPI로 암호화하고
Ubuntu는 0700 데이터 디렉터리/0600 토큰 파일을 사용합니다. CLI 모델 인증은 PC에 남습니다.
DPAPI는 같은 사용자의 악성 프로그램으로부터 보호하는 격리 장치가 아닙니다.

PC는 Hub로 outbound HTTPS만 사용합니다. 모바일/Hub에서 PC로 접속할 수신 포트는 없습니다.
모델 제공자 통신은 별도이며 조직의 기존 네트워크·프록시·정보 전송 정책을 따라야 합니다.

## 실행 권한

| 모드 | Codex | Claude |
|---|---|---|
| read-only | read-only sandbox, approvalPolicy never; 권한 확대 거절 | Read/Glob/Grep/AskUserQuestion만 노출, 직접 경로 검사 |
| development | workspace-write sandbox, on-request; CLI가 요구하는 승인을 메신저로 연결 | Read/Glob/Grep/Edit/Write/Bash/AskUserQuestion; 쓰기·셸은 PreToolUse 승인 |

Claude는 사용자/프로젝트 설정과 자동 MCP 구성을 로드하지 않도록 명시합니다. Codex는 조직의
설치/관리 설정을 사용할 수 있으므로 배포 시 활성 MCP·hooks·정책도 검토해야 합니다.

**프로젝트 디렉터리와 프롬프트는 OS 격리 경계가 아닙니다.** 특히 Claude native Windows의
도구 제한은 OS 파일시스템 sandbox가 아니며, 승인된 Bash 명령은 사용자의 OS 권한으로 동작합니다.
읽기 전용 도구의 패턴·심볼릭 링크 및 저장소 내용도 조직 데이터 정책에 맞춰 다뤄야 합니다.
Codex read-only도 해당 프로젝트 밖의 모든 읽기를 금지한다는 뜻은 아닙니다.
강한 격리가 필요하면 별도 사용자/VM/회사 관리 실행기를 사용하고 필요한 데이터만 제공하세요.

CLI 실행은 argv 배열과 로컬 stdio를 사용하며 shell=True나 메시지 문자열 셸 보간을 사용하지 않습니다.
Hub가 실행 파일·로컬 경로·추가 CLI 플래그를 전달할 수 없습니다. 사용자가 runtime set으로 지정하는
실행기는 신뢰하는 로컬 설정입니다.

메신저의 개발 작업 권한은 push·배포·운영 서버 재시작 등의 포괄 승인으로 취급하지 않습니다.
일반 Bash를 허용하는 경우 실제 요청 내용에 따라 사용자가 판단해야 합니다. 기존 사내 운영 서비스는
현재의 서버 API/권한 체계를 유지하세요. 이 PC 클라이언트는 임의 서비스 실행 플러그인 로더를 제공하지 않습니다.

## 승인

- owner/job/request/digest가 모두 일치해야 합니다. 승인·일반 답변 타입도 구분합니다.
- 승인 기본 TTL은 300초입니다. 시간 초과는 거절입니다. 세션 전체 승인과 인자 변경은 받지 않습니다.
- Codex의 동적 추가 파일/네트워크 권한은 거절하며, 지원하지 않는 MCP 입력 요청도 거절합니다.
- 승인 payload가 너무 커 메신저에서 검토하기 어려우면 자동 거절하고 PC에서 확인하도록 합니다.
- 일부 Codex 추가 질문은 설치 버전과 모델의 요청 방식에 따라 발생하지 않을 수 있습니다.
  어댑터는 수신한 `item/tool/requestUserInput`을 처리합니다.

## 출력과 보존

사용자용 assistant 텍스트와 작업 상태를 전송합니다. reasoning과 전체 셸 로그는 전달하지 않습니다.
장치 토큰·Bearer 문자열·일부 API 키 패턴을 마스킹하지만 이것은 완전한 DLP가 아닙니다.
코드·모델 출력에 포함된 기타 민감정보는 회사의 서버 출력 정책으로 추가 필터링해야 합니다.

단일 텍스트 16000자 제한과 `[TRUNCATED]` 표시가 있습니다. 큰 파일·이미지·diff 첨부 전송은
현재 제공하지 않으며 원본은 PC에서 확인합니다. 로컬 이벤트 최대 적재 건수 기본값은 10000이며,
한도에 도달하면 새 작업을 받지 않고 실행 중 작업을 중단합니다. 종료 결과 이벤트는 보존합니다.

완료 작업 ID와 세션 기록은 중복 실행 방지를 위해 자동 정리하지 않습니다. 장기 운영 시 기록 크기를
관찰하고 서버의 재전달 보존기간과 함께 정리 정책을 정하세요. state.sqlite3를 임의 삭제하면 중복 실행
방지 이력이 사라집니다. 전체 백업에는 -wal/-shm이 있을 수 있으므로 서비스를 중지한 뒤 백업합니다.

## 장애 시 동작

| 상황 | 동작 |
|---|---|
| Hub 접속 실패 | 지수 backoff 및 jitter로 재시도; 시작 시 인증 전 작업 수신 금지 |
| heartbeat/lease 실패 | 유효 lease가 끝나면 실행 취소 |
| 이벤트 ACK 유실 | 동일 이벤트 ID로 재전송 |
| claim 응답 유실 | 로컬에 저장한 동일 request_id로 재요청 |
| 프로세스 재시작 | running 작업은 interrupted로 기록; 자동 재실행 없음 |
| 승인 연결 단절 | TTL 이후 거절 |
| 장치 인증 폐기 | 실행 중 작업 중단, 클라이언트 종료 |
| 사용자 취소 | 실행기 interrupt/프로세스 종료, 기존 파일 변경은 유지 |

Ubuntu systemd는 cgroup으로 자식 프로세스를 정리합니다. Windows `run`은 kill-on-close Job Object에
실행 트리를 묶어 에이전트가 종료될 때 자식도 종료되도록 합니다. 이 보호를 설정할 수 없는 Windows
환경에서는 실행을 실패 처리합니다. 회사의 기존 Job Object 정책과 충돌하는지 현장 검증이 필요합니다.

Ubuntu 전경 실행을 SIGKILL한 경우처럼 운영체제 감독을 벗어난 강제 종료에서는 고아 프로세스가 남을
수 있습니다. 이 경우 프로세스·파일 상태를 확인한 뒤 새 작업을 시작하세요. 외부 API 호출 등 이미 발생한
부작용을 프로세스 종료가 되돌리지는 않습니다. 따라서 exactly-once 실행을 보장하지 않습니다.

## 제품별 연결 근거

- [Codex app-server 공식 문서](https://developers.openai.com/codex/app-server): 로컬 stdio,
  thread/start·resume, turn/start·interrupt, 승인 요청 프로토콜. 실험적 기능과 버전 변경에 유의합니다.
- [Claude Python Agent SDK](https://code.claude.com/docs/en/agent-sdk/python): 세션·스트리밍·hooks·권한 콜백.
- [Claude 권한 처리](https://code.claude.com/docs/en/agent-sdk/permissions): can_use_tool보다 앞서 허용되는
  도구가 있을 수 있어 쓰기·셸 확인은 PreToolUse hook에서 수행합니다.

SDK는 0.2.160을 고정했습니다. Codex의 로컬 생성 JSON schema와 설치 버전을 개발 검증에 사용했으며,
배포 환경 버전은 사내 검증 후 고정해서 배포하세요. 프로토콜 변화는 어댑터에서 처리합니다.
