# PC 설치와 운영

## 지원 환경

- Ubuntu: Python 3.11 이상, 사용자 systemd, 회사에서 허용한 Codex/Claude 인증 환경.
- Windows 10/11: Python 3.11 이상, PowerShell, 현재 사용자 계정의 작업 스케줄러.
- Claude Code는 해당 CLI가 요구하는 Windows Git Bash 등 실행 환경도 갖춰야 합니다.
- macOS에서는 개발·테스트 및 `run`이 가능하지만 서비스 설치 대상에는 포함하지 않았습니다.

클라이언트는 기본 Python 표준 라이브러리만 사용합니다. Claude 연동은 검증 버전을 고정한
`claude-agent-sdk` 추가 패키지를 사용합니다. 설치 스크립트는 Claude 지원까지 함께 설치합니다.

## 설치

회사 Git 저장소에서 소스를 받은 뒤 Ubuntu에서는:

```bash
bash scripts/install-linux.sh
export PATH="$HOME/.local/bin:$PATH"
```

Windows PowerShell에서는:

```powershell
.\scripts\install-windows.ps1
```

Windows 설치 스크립트는 사용자 PATH에 가상환경 Scripts 경로를 추가합니다. 다른 터미널은 다시
열어야 반영됩니다. 조직 정책이 스크립트를 차단한다면 IT 부서가 승인한 배포 수단으로 실행합니다.
실행 정책이나 관리자 권한을 자동 변경하지 않습니다.

수동 설치도 가능합니다.

```text
python -m venv .venv
# Ubuntu: .venv/bin/python
# Windows: .venv\Scripts\python.exe
<venv-python> -m pip install ".[claude]"
<venv-python> -m citrus_agent --help
```

사내 wheel 저장소와 CA는 표준 pip 설정을 사용합니다. 완전 오프라인 환경은 승인된 빌드 PC에서
대상 OS·Python 버전의 wheel을 수집한 뒤 `pip install --no-index --find-links <wheelhouse>`로 설치합니다.
Claude SDK wheel은 OS별 바이너리를 포함하므로 Ubuntu용과 Windows용을 따로 준비합니다.

## 등록

```text
citrus-agent init --hub https://messenger.company.example/agent --name 개발PC
citrus-agent enroll
```

출력된 `/PC등록 코드`를 사내 봇의 개인 대화로 보냅니다. PC에서 표시된 사내 사용자 정보가
본인인지 확인하고 `y`를 입력합니다. 짧은 코드는 메신저 연결용이고 실제 장치 토큰은 별도로 발급됩니다.

사내 인증서가 기본 신뢰 저장소에 없으면:

```text
citrus-agent init --hub https://messenger.company.example/agent --ca-file /path/company-ca.pem
```

Windows에서는 `C:\certs\company-ca.pem` 같은 경로를 사용합니다. TLS 검증을 끄는 옵션은 없습니다.
프록시는 Python urllib의 표준 HTTPS_PROXY/NO_PROXY 설정을 사용합니다.

## 프로젝트와 CLI 등록

```bash
# Ubuntu
citrus-agent project add payment /home/me/work/payment --runtime codex --policy development
citrus-agent project add docs /home/me/work/docs --runtime claude --policy read-only
```

```powershell
# Windows
citrus-agent project add payment "C:\work\결제 API" --runtime codex --runtime claude --policy development
```

`--runtime`을 생략하면 두 CLI를 등록합니다. `--policy` 기본값은 read-only입니다.
프로젝트 ID는 메신저에서 선택하는 고정 이름이며 서버는 로컬 경로를 지정할 수 없습니다.

Codex는 설치된 `codex` 실행 파일을 사용합니다. Claude는 기본적으로 SDK가 제공하는 CLI를
사용합니다. 기존 Claude 설치를 정확히 지정하려면 다음처럼 등록하세요.

```bash
citrus-agent runtime set codex -- /home/me/.local/bin/codex
citrus-agent runtime set claude -- /home/me/.local/bin/claude
```

```powershell
citrus-agent runtime set claude -- "C:\Users\me\.local\bin\claude.exe"
```

Windows npm의 Codex `.cmd`가 발견되면 알려진 `node_modules/@openai/codex/bin/codex.js`와
`node.exe`를 직접 실행합니다. 다른 배치 래퍼는 실행하지 않습니다. 필요한 경우 명시적으로:

```powershell
citrus-agent runtime set codex -- "C:\Program Files\nodejs\node.exe" "C:\path\codex.js"
```

CLI 모델 인증은 동일한 사용자 계정에서 수행합니다. Hub에 API 키나 CLI 인증 파일을 업로드하지
않습니다. 기본 SDK CLI와 기존 CLI가 다르면 SDK가 선택한 실행 파일에서도 인증 상태를 확인하세요.

## 실행과 점검

```text
citrus-agent doctor
citrus-agent run
```

doctor는 프로젝트 경로, 실행 파일 버전, SDK 설치, Hub 인증을 점검합니다. 모델 요청을 보내지
않으므로 실제 모델 계정·할당량 검증은 수행하지 않습니다. 첫 등록 후 메신저에서 작은 조회 작업으로
실제 실행을 확인하세요.

`run`은 전경 실행이며 Ctrl+C로 종료합니다. 진단 로그는 데이터 디렉터리의 agent.log입니다.
전경 실행 종료 후 다음 명령으로 자동 실행을 설정합니다.

```text
citrus-agent service install
citrus-agent service status
citrus-agent service stop
citrus-agent service start
citrus-agent service uninstall
```

Ubuntu는 `systemctl --user`를 사용합니다. 로그아웃 후 실행이 필요하면 회사 정책에 따라
사용자 lingering을 설정해야 합니다. 설치 과정에서 자동으로 권한을 상승시키지 않습니다.

Windows는 **사용자가 로그인한 동안** 실행하는 작업을 등록합니다. 화면 잠금 상태에서는 계속
실행되지만 로그아웃 후 무인 실행은 지원하지 않습니다. DPAPI·CLI 인증과 동일한 사용자로 실행합니다.
SYSTEM 계정이나 로그인 전 서비스가 필요하면 조직 자격증명 배포 방식을 포함한 별도 구성이 필요합니다.
PC 절전 중에는 작업을 수행하지 않습니다.

서비스 설치 시 실행 파일 경로를 고정하여 systemd와 터미널의 PATH 차이를 줄입니다.
CLI를 다른 위치에 재설치하면 서비스를 중지하고 runtime set 후 다시 시작하세요.

## 설정과 기록

| OS | 기본 데이터 디렉터리 |
|---|---|
| Ubuntu | `~/.local/state/citrus-agent` (XDG_STATE_HOME 지원) |
| Windows | `%LOCALAPPDATA%\CitrusAgent` |

`--home <경로>` 또는 `CITRUS_AGENT_HOME`으로 변경할 수 있습니다. 서비스에는 등록 시의 절대경로가
저장됩니다. 서로 다른 home으로 같은 작업 디렉터리를 병렬 제어하지 마세요.

- config.json: Hub, 프로젝트, CLI 경로, timeout 등의 설정
- credentials.json: Hub 장치 토큰 (Ubuntu 0600, Windows DPAPI)
- enrollments/<등록범위 해시>/state.sqlite3: 작업 ID, 세션 연결, 전송 대기 이벤트
- agent.log: 회전 로그(2 MiB × 최대 4파일), 원본 프롬프트·토큰·모델 stderr 제외

설정 변경은 `service stop` 후 수행합니다. `status`는 로컬 기록 조회이며 프로세스 생존 여부는
`service status` 또는 Hub의 마지막 heartbeat를 확인하세요.

작업·세션·outbox는 Hub URL, 장치 ID, 소유자별로 분리됩니다. 다른 Hub나 새 등록으로 전환해도
이전 등록의 대화·전송 대기 메시지가 새 서버로 전달되지 않습니다. `status`는 현재 등록의 기록만
표시합니다. 등록 해제 후에는 보존된 DB를 별도로 관리합니다.

```text
citrus-agent project list
citrus-agent status
citrus-agent service stop
citrus-agent project remove docs
citrus-agent service start
```

## 업데이트와 제거

서비스를 중지하고 설치 스크립트를 같은 경로에 다시 실행한 뒤 doctor와 작은 작업을 확인합니다.
등록과 작업 기록은 유지됩니다. DB·등록 정보를 다른 PC에 복사해 복제 실행하지 않습니다.

제거 순서:

```text
citrus-agent service stop
citrus-agent unregister
citrus-agent service uninstall
```

unregister는 서버에서 토큰 폐기를 확인한 뒤 로컬 토큰을 지웁니다. Hub 장애 때문에 폐기할 수
없으면 메신저 관리자에게 서버 측 폐기를 요청하세요. 로컬 작업 기록은 자동 삭제하지 않습니다.
