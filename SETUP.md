# 새 노트북 세팅

## 선행 조건 (기계당 한 번)

`gh auth login` 후 아래 한 줄. 플러그인 설치기가 `git@github.com:`(SSH)로 클론하는데
SSH 키가 없으면 공개 레포조차 실패한다. https로 바꿔치기하면 gh 토큰이 인증을 처리한다.

```bash
git config --global url."https://github.com/".insteadOf "git@github.com:"
```

## 설치

```bash
claude plugin marketplace add risky-dice/claude-setup && claude plugin install jeongsan@gosu && claude plugin install live-editor@gosu && claude plugin install ponytail@gosu
```

## 윈도우 노트북

위 명령들은 macOS/Linux 기준이다. 윈도우에서는 PowerShell 로 이렇게 한다.

```powershell
git config --global url."https://github.com/".insteadOf "git@github.com:"
claude plugin marketplace add risky-dice/claude-setup
claude plugin install jeongsan@gosu; claude plugin install live-editor@gosu; claude plugin install ponytail@gosu
```

live-editor 실행 환경:

```powershell
New-Item -ItemType Directory -Force "$HOME\live-editor-work" | Out-Null
Copy-Item "<skill-dir>\scripts\*" "$HOME\live-editor-work\" -Force
cd "$HOME\live-editor-work"
npm install
python -m venv .venv; .\.venv\Scripts\pip install pymupdf   # pdf 편집용
```

**`python3` 이 아니라 `python` 이다.** 윈도우에는 `python3` 이 없는 경우가 대부분이고 `python`
또는 `py` 가 있다. 자체 테스트는 알아서 찾지만 손으로 치는 명령은 `python hanvas.py …` 로 쓴다.

**미리보기 띄우기** — macOS 의 `chrome-reload.sh` 대신 `chrome-reload.ps1` 을 쓴다.

```powershell
powershell -ExecutionPolicy Bypass -File "$HOME\live-editor-work\chrome-reload.ps1" C:\전체\경로\preview.html
```

`-ExecutionPolicy Bypass` 가 필요한 이유: 서명 없는 로컬 스크립트는 기본 정책에서 막힌다.
이 플래그는 그 실행 하나에만 적용되고 시스템 정책을 바꾸지 않으므로 관리자 권한이 필요 없다.

**탭 재사용은 윈도우에서 안 된다.** macOS 판은 AppleScript 로 Chrome 탭을 찾아 그 자리에서
새로고침하지만 윈도우 Chrome 에는 대응하는 창구가 없다. 대신 미리보기가 빌드 시각을 탭 제목에
박으므로(`문서명 · 14:22:54`) 어느 탭이 최신인지 제목으로 구분한다. 이미 열린 탭에서 F5 를 눌러도
같다.

**Node·파이썬이 아예 없고 설치 권한도 없다면.** 둘 다 관리자 없이 넣는 길이 있다 — Node 는 공식
**zip** 배포본을 풀어서 그 폴더를 `PATH` 에 추가하면 되고, 파이썬은 python.org 설치 관리자에서
"Install for me only"(사용자 전용)를 고르면 된다. 학교 정책이 실행 파일 자체를 막는 경우라면
그때는 우회로가 없다.

## 자동으로 안 되는 것 두 가지

**1. live-editor 실행 환경.** 스킬 정의는 위 명령으로 오지만 엔진은 안 온다.
첫 사용 때 Claude가 세션당 한 번 자동 설치한다(40~70초).

```bash
mkdir -p ~/live-editor-work && cp <skill-dir>/scripts/* ~/live-editor-work/
cd ~/live-editor-work && npm install
python3 -m venv .venv && .venv/bin/pip install pymupdf   # pdf 편집용
```

**관리자 권한이 필요한 곳은 없다.** npm은 `~/live-editor-work/node_modules` 에,
파이썬 의존성은 venv 안에 깔린다. SKILL.md 의 `pip install --break-system-packages`
는 시스템 파이썬에 직접 쓰는 플래그라 권한이 필요하니 venv 를 쓴다.

**진짜 막히는 경우는 Node.js 나 파이썬 자체가 없을 때다.** 런타임을 새로 까는 것이
관리자 권한이고, 그 경우 live-editor 는 우회로가 없다. 버전 고정 필수 —
`@rhwp/core` 를 `^` 로 풀면 렌더링이 깨진다.

**2. 설치 권한 없는 학교 노트북의 jeongsan.**
플러그인 설치 대신 릴리스에서 단일 파일만 받는다.

```bash
gh release download v0.1.0 -R risky-dice/jeongsan   # 또는 웹에서 직접 다운로드
python3 jeongsan.pyz rules
```

파이썬 3.9+ 외 의존성 없음(xlsx 출력만 openpyxl 필요).

## 앱 스킬로도 쓰려면

live-editor는 Claude 앱에 업로드한 계정 스킬이기도 하다. 앱에서 지웠다면
GitHub 사본이 자동으로 되살리지 않으므로 `skills/live-editor/` 를 `.skill` 로 다시 올린다.
