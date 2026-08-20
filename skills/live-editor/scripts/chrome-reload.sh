#!/bin/zsh
# 같은 파일을 보고 있는 Chrome 탭을 새로고침한다. 중복 탭은 첫 번째만 남기고 닫는다.
# 없으면 새 탭으로 연다. 인자: 파일 경로(절대)
F="${1:?파일 경로 필요}"
[[ -f "$F" ]] || { echo "없는 파일: $F" >&2; exit 1; }
osascript - "file://$F" <<'AS'
on run argv
  set theURL to item 1 of argv
  tell application "Google Chrome"
    set keeper to missing value
    set closed to 0
    repeat with w in windows
      repeat with i from (count of tabs of w) to 1 by -1
        set t to tab i of w
        if (URL of t) is theURL then
          if keeper is missing value then
            set keeper to t
          else
            close t
            set closed to closed + 1
          end if
        end if
      end repeat
    end repeat
    if keeper is missing value then
      if (count of windows) = 0 then
        make new window
        set URL of active tab of front window to theURL
      else
        tell front window to make new tab with properties {URL:theURL}
      end if
      return "opened"
    end if
    tell keeper to reload
    if closed > 0 then return "reloaded (중복 탭 " & closed & "개 닫음)"
    return "reloaded"
  end tell
end run
AS
