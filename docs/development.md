# 개발과 검증

## 코드 구성

```text
src/citrus_agent/
  cli.py          설치 후 사용할 명령·등록·진단
  config.py       로컬 프로젝트·CLI·Hub 설정
  secrets.py      DPAPI/파일 권한 기반 장치 토큰 저장
  transport.py    HTTPS JSON 클라이언트
  state.py        SQLite 작업·세션·outbox·멱등 기록
  agent.py        claim / heartbeat / events / lease / 취소 orchestration
  runtime.py      Codex stdio, Claude SDK, 승인·질문 연결
  locking.py      데이터 디렉터리 단일 실행 잠금
  service.py      Ubuntu systemd / Windows 작업 스케줄러
  windows_job.py  Windows 자식 프로세스 수명 관리
scripts/         OS별 가상환경 설치
tests/           모의 Hub·모의 CLI·권한·복구 테스트
```

## 로컬 테스트

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev,claude]'
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m build
```

HTTP 통합 테스트는 loopback에 임시 포트를 열 수 있는 환경이 필요합니다.
모의 CLI는 Python 자식 프로세스로 실행되며 실제 모델 API를 호출하지 않습니다.
Claude 테스트는 실제 SDK 타입과 옵션을 사용하고 모델 클라이언트만 대체합니다.

## 검증 범위

- HTTP Hub → job claim → 실제 자식 프로세스 → 세션 저장 → 이벤트 업로드 → ACK 삭제
- TLS URL 검증, 리다이렉트 거부, 비밀 마스킹, 현재 사용자 자격증명 저장
- 중복 job, claim ID 재사용, 재시작 시 interrupted 처리, 세션 소유권 분리
- 승인·입력의 owner/digest/request/type 검증과 만료
- 작업 취소·timeout·lease 만료·인증 폐기
- Codex 세션 재개와 JSONL 프로토콜 오류
- Claude 도구 노출·hook 경로 검사·추가 승인·세션 재개
- systemd/Task Scheduler의 공백·한글 경로 처리

GitHub Actions는 Ubuntu와 Windows, Python 3.11·3.12 조합에서 실행합니다.
Windows에서는 DPAPI 저장/복구가 실제 OS API를 호출합니다.

## 실제 회사 환경에서 남는 검증

자동 테스트는 회사 메신저·모델 계정·운영 프로젝트 접근 권한을 대체하지 않습니다.
배포 전에 server-integration.md의 인수 테스트와 각 PC의 doctor를 수행하세요.
작업 스케줄러 등록 권한, 사용자 systemd, Windows CLI 설치·Git Bash, 사내 CA/프록시는
조직별로 다릅니다. 실제 모델 작업·PC 재부팅·잠금·절전 복구도 배포 환경에서 확인합니다.

버전 업그레이드 시 SDK 버전과 설치된 CLI schema를 함께 확인하고 테스트를 통과한 조합만 배포합니다.
