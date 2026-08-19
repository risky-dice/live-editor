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
