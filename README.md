# Citrus Agent

사내 메신저에서 Ubuntu·Windows PC의 Codex / Claude Code를 사용하는 클라이언트입니다.
PC가 사내 Hub에 HTTPS로 접속합니다. PC의 인바운드 포트나 외부 원격 접속 서비스가 필요하지 않습니다.

이 저장소는 **PC 클라이언트 전체와 서버 추가 작업 문서**를 제공합니다.
기존 사내 FastAPI 서비스는 [Hub API 계약](docs/hub-api.md)에 맞춰 연결해야 합니다.
서버를 구현하거나 배포하지 않으며, 기존 서버에 자동 연결되는 것으로 가정하지 않습니다.

## 설치 및 시작

Python 3.11 이상과 회사에서 허용한 모델 계정/네트워크 환경이 필요합니다.

```bash
# Ubuntu: 저장소를 받은 뒤
bash scripts/install-linux.sh

# Windows PowerShell: 조직에서 허용한 스크립트 실행 정책으로 실행
.\scripts\install-windows.ps1
```

사내 Python 패키지 저장소는 pip의 `PIP_INDEX_URL` / `PIP_CERT` 설정을 사용합니다.
외부 인터넷이 없는 PC에는 사내에서 준비한 wheel과 의존성을 배포하세요.

```bash
citrus-agent init --hub https://messenger-hub.company.example --name my-dev-pc
citrus-agent enroll
# 표시된 코드를 사내 봇에 /PC등록 <코드>로 전송하고 PC에서 사용자 확인
citrus-agent project add payment /home/me/projects/payment --runtime codex --policy development
citrus-agent doctor
citrus-agent run
```

Windows 프로젝트 등록 예시:

```powershell
citrus-agent project add payment "C:\work\payment" --runtime claude --policy development
```

전경 실행을 종료한 뒤 자동 실행을 등록합니다.

```bash
citrus-agent service install
citrus-agent service status
citrus-agent status
```

설정 변경과 등록 해제 전에 `citrus-agent service stop`을 실행하세요.
설정 변경 후에는 `citrus-agent service start`로 다시 시작합니다.

## 구현 범위

- 등록 코드와 PC의 소유자 확인을 사용하는 장치 등록, 원격 토큰 폐기
- Ubuntu systemd 사용자 서비스 / Windows 로그인 시 작업 스케줄러 실행
- Codex app-server의 로컬 stdio 연동, Claude Agent SDK 연동
- 프로젝트별 읽기 전용·개발 정책, 세션 재개, 승인·질문 왕복, 작업 취소
- PC당 한 작업씩 실행, 로컬 세션 소유권 검증
- SQLite 작업 기록·이벤트 outbox, 중복 작업 방지, 재시작 시 중단 기록
- 실행 lease 갱신 실패 시 작업 중단, 서버 인증 폐기 시 에이전트 중지
- HTTPS/사내 CA, HTTP 리다이렉트 거부, Ubuntu 0600 / Windows DPAPI 토큰 보관

## 문서

- [사용자 설치·운영 안내](docs/client-guide.md)
- [기존 서버에 추가할 작업](docs/server-integration.md)
- [클라이언트가 사용하는 HTTP/JSON 계약](docs/hub-api.md)
- [권한·복구·운영 제한](docs/security-and-recovery.md)
- [개발 및 검증](docs/development.md)

## 개발

```bash
python -m venv .venv
# Ubuntu
.venv/bin/python -m pip install -e '.[dev,claude]'
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
```

Windows에서는 `.venv\Scripts\python.exe`를 사용합니다.
GitHub Actions에 Ubuntu / Windows, Python 3.11 / 3.12 테스트를 구성했습니다.
테스트는 가짜 Hub·CLI를 사용하며 실제 모델 API를 호출하거나 과금하지 않습니다.
