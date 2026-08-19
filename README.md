# live-editor

한글(HWP/HWPX)과 텍스트 기반 PDF를 **대화창 안에서 실시간으로 고치는** Claude 스킬입니다.
문서를 올리면 미리보기가 뜨고, "금액 3,000,000원으로 바꿔줘" 같은 평범한 말로 수정하면
바뀐 자리에 형광펜이 칠해진 미리보기가 즉시 갱신되며, 원하면 편집된 파일을 그대로 받습니다.

번역·재작성·외부 API 없이 Claude 자신이 편집 주체이고, 문서 해석과 렌더링은 번들된 엔진
(kordoc + rhwp WASM, PDF는 PyMuPDF)이 로컬에서 처리합니다. 파일이 밖으로 나가지 않습니다.

## 설치

Claude 앱에서 `live-editor.skill` 파일을 스킬로 추가하면 끝입니다. 실제 실행 환경 준비는
Claude가 대화 중 자동으로 합니다(세션당 한 번, 40~70초):

```bash
mkdir -p ~/live-editor-work && cp <skill-dir>/scripts/* ~/live-editor-work/
cd ~/live-editor-work
npm install                                    # kordoc 4.2.0 + @rhwp/core 0.7.19 + cfb 1.2.2 (hwp/hwpx용)
pip install pymupdf --break-system-packages    # pdf용
```

세 패키지는 **정확한 버전으로 고정**돼 있습니다. 특히 `@rhwp/core`는 렌더링이 자주 바뀌기 때문에
`^`로 풀지 마세요. 다른 버전이 깔려 있으면 도구가 실패하는 대신 결과 JSON의 `warnings`에
"검증본과 다름"을 적어 보냅니다.

## `.skill` 다시 포장하기

앱에 올릴 패키지를 만들 때. **절대경로로 실행할 것** — 상대경로로 하면 다른 레포의
`skills/` 안에서 돌아 `Nothing to do!` 가 난다.

```bash
cd ~/Projects/apps/live-editor/skills && zip -qr ~/Desktop/live-editor.skill live-editor -x "*.DS_Store"
```

zip 루트에 `live-editor/` 폴더가 오고 그 안에 `SKILL.md` 와 `scripts/` 가 들어간다.
`node_modules` 와 `.venv` 는 `skills/` 밖에 두므로 자동으로 빠진다.

## 설치 확인

```bash
cd ~/live-editor-work && node hwpedit.mjs test
```

샘플 문서를 즉석에서 만들어 `blocks → render → edit → apply → svg → hanvas → prerender → hanvas_full`까지
전 과정과 오류 경로 9종을 한 번에 돌리고 26개 항목의 통과/실패 표를 찍습니다. 엔진(WASM)이 막힌
환경에서도 미리보기 파일 하나로 두 뷰가 다 열리는지까지 확인합니다. 별도 샘플 파일이 필요 없고 약
30초면 끝납니다. 낯선 환경에서 "환경 문제인지 문서 문제인지"를 가르는 가장 빠른 방법입니다.

## 무엇을 할 수 있나

문단·표 셀 단위 찾아바꾸기, 여러 건 한 번에 일괄 수정, 그리고 **미리보기 파일 하나**로 끝나는
확인 화면입니다. 이 파일(`hanvas_full.py` 생성, 17쪽 문서 기준 약 4MB HTML)은 한컴 레이아웃 그대로의
원본 뷰와 깔끔 뷰를 한 번에 담고, 크롬에서 열면 그 자리에서 글자를 고쳐 hwpx로 저장까지 됩니다.
엔진(WASM)이 막히는 아티팩트 창에서는 보기 전용으로 조용히 내려가되 두 뷰와 형광펜은 그대로
보입니다 — 문서 크기와 환경에 상관없이 사용자에게 보여주는 화면은 언제나 이 하나입니다.

크롬에서 열면 툴바 버튼 네 개가 살아납니다 — 📁 **작업 폴더 지정**(최초 1회, 이후 저장은 대화상자
없이 그 폴더로), 💾 **저장**, 🔗 **rhwp 확장으로 열기**(1차 클릭 저장 → 2차 클릭 열기), 🗑 **수정본
정리**(쌓인 `문서_143022.hwpx`들을 시각순으로 보여주고 최종본만 남기고 삭제). 작업 폴더는 반드시
다운로드 폴더의 **하위** 폴더로 잡으세요 — 크롬이 다운로드·바탕화면·문서 폴더 자체는 막습니다.
PDF는 글자 span을 찾아 같은 위치·폰트·크기로 다시 심는 방식이라 주변 레이아웃이 흐트러지지 않습니다.

## 한계

표 셀 안에 줄바꿈이 있으면 원본 뷰(SVG)에서 그 줄들이 같은 자리에 겹쳐 찍힙니다 — 렌더 엔진(rhwp) 쪽
한계라 문서와 편집 결과에는 이상이 없고, 도구가 겹침을 감지해 경고를 붙입니다(깔끔 뷰나 내려받은 파일로 확인).
암호가 걸린 문서는 열지 않습니다(암호를 묻지도 않습니다 — 한/글에서 암호를 풀어 저장한 뒤
다시 올려야 합니다). 스캔본처럼 글자가 이미지인 PDF는 바꿀 텍스트 자체가 없어 OCR이 먼저 필요하고,
그건 일반 `pdf` 스킬의 몫입니다. 아주 긴 문서는 기본 300쪽까지만 렌더하며(`HWPEDIT_PAGE_CAP`으로 조절),
잘린 사실은 항상 `warnings`로 알립니다. `.docx`/`.xlsx`는 각각 `docx`/`xlsx` 스킬을 쓰세요.

## 구성 파일

| 파일 | 역할 |
|---|---|
| `SKILL.md` | Claude가 읽는 본체 — 편집 루프, 명령 레퍼런스, 실패 시 대응 |
| `scripts/hwpedit.mjs` | HWP/HWPX 엔진 (blocks/render/edit/apply/svg/hanvas/prerender/hwp5patch/test) |
| `scripts/pdfedit.py` | PDF 제자리 편집 엔진 (PyMuPDF) |
| `scripts/hanvas_full.py` | 사용자에게 보여주는 **단 하나의 미리보기 파일** 조립 (뷰어 겸 편집기) |
| `scripts/package.json`, `requirements.txt` | 고정 버전 의존성 |

버전 이력은 `CHANGELOG.md`를 보세요.
