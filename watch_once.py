"""
네이버예약 초진 항목 출현 감시 (1회 실행용, GitHub Actions)
- 알림만 보내고 자동 예약은 안 함
- 상태(알림 보냄 여부 등)는 state/state.json 에 저장 -> 워크플로가 cache로 유지
"""
import json
import os
import sys
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright

URL = os.environ.get(
    "TARGET_URL",
    "https://m.booking.naver.com/booking/13/bizes/988532?theme=place&entry=pll&lang=ko",
)
KEYWORDS = [k.strip() for k in os.environ.get("KEYWORDS", "박동수,초진").split(",") if k.strip()]
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "")
HEARTBEAT_HOURS = 24
FAIL_ALERT_AT = 3  # 5분 간격 기준 연속 3회 실패(약 15분)면 경고

STATE_PATH = Path("state/state.json")
UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


def load_state():
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"notified": False, "fails": 0, "last_beat": 0}


def save_state(s):
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(s))


def notify(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("(텔레그램 미설정)", text)
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT_ID, "text": text, "disable_web_page_preview": True},
        timeout=10,
    )
    print("telegram:", r.status_code)


def find_hit(lines):
    for line in lines:
        if all(k in line for k in KEYWORDS):
            return line
    for i in range(len(lines) - 2):
        chunk = " ".join(lines[i : i + 3])
        if all(k in chunk for k in KEYWORDS):
            return chunk
    return None


def fetch_lines():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=UA, viewport={"width": 390, "height": 844}, locale="ko-KR")
        page = ctx.new_page()
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=30000)
            page.wait_for_timeout(1500)
            body = page.inner_text("body")
            lines = [l.strip() for l in body.splitlines() if l.strip()]
            Path("debug").mkdir(exist_ok=True)
            page.screenshot(path="debug/last.png")
            Path("debug/body.txt").write_text(body, encoding="utf-8")
            return lines
        except Exception:
            Path("debug").mkdir(exist_ok=True)
            try:
                page.screenshot(path="debug/error.png")
            except Exception:
                pass
            raise
        finally:
            browser.close()


def main():
    state = load_state()
    now = time.time()

    try:
        lines = fetch_lines()
    except Exception as e:
        state["fails"] = state.get("fails", 0) + 1
        print(f"조회 실패 {state['fails']}회: {e}")
        if state["fails"] == FAIL_ALERT_AT:
            notify("⚠️ 네이버예약 조회가 연속 실패 중. 차단/구조 변경 가능성, Actions 로그 확인 필요")
        save_state(state)
        sys.exit(1)  # Actions에서 실패 표시 + debug 아티팩트 업로드

    state["fails"] = 0
    print(f"줄 수 {len(lines)}")
    if len(lines) < 5:
        print("경고: 페이지 줄 수가 너무 적음 (로딩/차단 의심)")

    hit = find_hit(lines)
    if hit and not state.get("notified"):
        notify(f"🚨 초진 항목 떴음!\n{hit[:100]}\n\n{URL}")
        state["notified"] = True
        print("HIT:", hit[:80])
    elif not hit:
        if state.get("notified"):
            notify("초진 항목이 다시 사라졌어. 다음 오픈 대기 중")
        state["notified"] = False
        print("없음")
    else:
        print("이미 알림 보낸 상태로 유지 중")

    if HEARTBEAT_HOURS and now - state.get("last_beat", 0) > HEARTBEAT_HOURS * 3600:
        notify("✅ 감시 정상 작동 중")
        state["last_beat"] = now

    save_state(state)


if __name__ == "__main__":
    main()
