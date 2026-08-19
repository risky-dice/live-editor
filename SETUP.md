# 새 노트북 세팅 (내가 만든 것 + 쓰는 것)

## 1. live-editor (이 레포)
Claude 앱 스킬. 계정에 업로드돼 있으면 앱 목록엔 자동으로 뜨지만 **실행 환경은 안 따라온다.**

```bash
mkdir -p ~/live-editor-work && cp ~/Projects/apps/live-editor/scripts/* ~/live-editor-work/
cd ~/live-editor-work && npm install && pip install pymupdf --break-system-packages
node hwpedit.mjs test   # 설치 확인
```

버전 고정 필수. `@rhwp/core`를 `^`로 풀면 렌더링이 깨진다.
계정에 안 뜨면 이 폴더를 `.skill`로 다시 업로드.

## 2. jeongsan (학교회계 정산 검증)
`~/Projects/school/jeongsan` — 별도 레포. clone 후 플러그인 등록.
설치 권한 없는 학교 노트북이면 `jeongsan.pyz` 하나만 복사해서 `python3 jeongsan.pyz`.

## 3. ponytail (남의 스킬, 재설치)
복사하지 말 것. `~/.claude/settings.json` 훅에 `/Users/gosu/...` 절대경로가 박혀 있어
사용자명이 다른 기계에선 조용히 실패한다. 원본에서 새로 설치:
https://github.com/DietrichGebert/ponytail
