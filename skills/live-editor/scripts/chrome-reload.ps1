# preview.html 을 Chrome 으로 연다. Windows 용 — macOS 의 chrome-reload.sh 와 짝이다.
# 인자: 파일 경로(절대 또는 상대)
#
# 탭 재사용은 하지 않는다(못 한다). macOS 판은 AppleScript 로 Chrome 탭 목록을 읽어
# 같은 URL 을 보고 있는 탭을 그 자리에서 새로고침하지만, Windows 의 Chrome 에는
# 그에 대응하는 자동화 창구가 없다 — 원격 디버깅 포트를 열지 않는 한. 사용자의 평소
# 프로필에 디버깅 포트를 여는 건 미리보기 하나 갱신하자고 치를 대가가 아니다.
# 대신 미리보기가 빌드 시각을 탭 제목에 박아 두므로(`문서명 · 14:22:54`), 탭이 여러 개
# 쌓여도 어느 것이 방금 구운 판인지 제목만 보고 구분할 수 있다. 이미 열려 있는 탭에서
# F5 를 눌러도 같은 결과다.
param([Parameter(Mandatory = $true)][string]$Path)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Path)) {
  Write-Error "없는 파일: $Path"
  exit 1
}
$full = (Resolve-Path -LiteralPath $Path).Path

# file://C:/... 는 C: 를 호스트로 파싱해서 거부당한다. 슬래시 세 개가 필요하다.
$url = 'file:///' + ($full -replace '\\', '/')

$candidates = @(
  (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
  (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
  (Join-Path $env:LocalAppData 'Google\Chrome\Application\chrome.exe')
)
$chrome = $candidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1

if ($chrome) {
  Start-Process -FilePath $chrome -ArgumentList $url | Out-Null
  Write-Output 'opened'
} else {
  # PATH 에 chrome 이 있으면 그걸 쓰고, 없으면 기본 브라우저로 넘긴다.
  # 기본 브라우저가 Edge 면 엔진 기능(저장·rhwp)이 안 도니 그 점을 알린다.
  try {
    Start-Process -FilePath 'chrome' -ArgumentList $url | Out-Null
    Write-Output 'opened'
  } catch {
    Start-Process $url | Out-Null
    Write-Output 'opened (기본 브라우저 — Chrome 이 아니면 저장/rhwp 버튼은 동작하지 않습니다)'
  }
}
