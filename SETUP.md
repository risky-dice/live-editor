# 새 노트북 세팅

한 줄로 끝난다:

```bash
claude plugin marketplace add risky-dice/claude-setup && claude plugin install jeongsan@gosu && claude plugin install live-editor@gosu && claude plugin install ponytail@gosu
```

## 자동으로 안 되는 것 두 가지

**1. live-editor 실행 환경.** 스킬 정의는 위 명령으로 오지만 엔진은 안 온다.
첫 사용 때 Claude가 세션당 한 번 자동 설치한다(40~70초).

```bash
mkdir -p ~/live-editor-work && cp <skill-dir>/scripts/* ~/live-editor-work/
cd ~/live-editor-work && npm install && pip install pymupdf --break-system-packages
```

node·npm·pip가 없거나 설치 권한이 막힌 기계에서는 **스킬은 보이는데 실행이 실패한다.**
버전 고정 필수 — `@rhwp/core`를 `^`로 풀면 렌더링이 깨진다.

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
