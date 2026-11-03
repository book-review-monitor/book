"""YES24 / 교보문고 자사 도서 후기 모니터.

신규 후기(구글시트에 아직 없는 후기)만 슬랙으로 보고하고 시트에 누적한다.
시트가 비어 있으면 현재 후기 전체를 조용히 적재(baseline)하고 슬랙은 보내지 않는다.

환경변수
  SLACK_WEBHOOK_URL            슬랙 Incoming Webhook URL
  GOOGLE_SERVICE_ACCOUNT_JSON  서비스 계정 키 JSON 전문
  SHEET_ID                     구글시트 ID (서비스 계정에 편집자로 공유)
  SHEET_TAB                    워크시트 이름 (기본 '후기')

사용법
  python monitor.py              정상 실행 (신규만 슬랙 + 시트)
  python monitor.py --dry-run    수집만 하고 도서별 최신 후기 1건을 출력 (쓰기 없음)
  python monitor.py --test-slack 슬랙 테스트 메시지 1건 전송
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).parent
KST = timezone(timedelta(hours=9))
HEADERS = ["review_key", "구분", "채널", "도서명", "작성일", "별점", "내용", "수집시각"]
RECENT_LIMIT = 20  # 평시에는 도서·채널당 최신 20건만 확인 (10분 사이 20건 이상 달리지 않는다는 전제)

session = requests.Session()
session.headers["User-Agent"] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
session.headers["Accept-Language"] = "ko-KR,ko;q=0.9"


def get(url, **kw):
    last = None
    for i in range(3):
        try:
            r = session.get(url, timeout=20, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


# ---------------------------------------------------------------- 수집
def fetch_yes24(book, full):
    """YES24 리뷰. Sort=1 이 최신순. 별점은 10점 만점 → 5점 만점으로 환산."""
    out, page = [], 1
    while True:
        r = get(
            f"https://www.yes24.com/Product/communityModules/GoodsReviewList/{book['id']}",
            params=dict(Sort=1, PageNumber=page, Type="ALL", GoodsSetYn="N", InitYn="Y"),
        )
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.select("div.reviewInfoGrp")
        if not rows:
            break
        for row in rows:
            m = re.search(r"OpenReviewReport\((\d+)", str(row))
            if not m:
                continue
            rating = row.select_one(".total_rating")
            score10 = int(re.search(r"total_rating_(\d+)", " ".join(rating["class"])).group(1)) if rating else 0
            body = row.select_one(".reviewInfoBot.origin .review_cont") or row.select_one(".review_cont")
            for br in row.select("br"):
                br.replace_with("\n")
            date = row.select_one(".txt_date").get_text(strip=True).rstrip(".").replace(".", "-")
            text = re.sub(r"\n{3,}", "\n\n", body.get_text().strip()) if body else ""
            out.append(dict(
                key=f"YES24:{m.group(1)}", channel="YES24 리뷰", title=book["title"], date=date,
                rating=score10 / 2, content=text,
            ))
        if not full or not soup.select_one("a.bgYUI.next:not(.dim)"):
            break
        page += 1
        time.sleep(0.5)
    return out if full else out[:RECENT_LIMIT]


def fetch_kyobo(book, full):
    """교보문고 후기. reviewSort=002 가 최신순."""
    out, page = [], 1
    while True:
        r = get(
            "https://product.kyobobook.co.kr/api/gw/shrd/review/list",
            params=dict(
                page=page, pageLimit=RECENT_LIMIT if not full else 50, reviewSort="002", revwPatrCode="002",
                saleCmdtid=book["id"], saleCmdtids=book["id"], webToonYsno="N", allYsno="N",
                revwSummeryYn="Y", revwPatrCodes=["002", "003", "004"],
            ),
        )
        rows = r.json()["data"]["reviewList"]
        for x in rows:
            out.append(dict(
                key=f"KYOBO:{x['revwNum']}", channel="교보문고 후기", title=book["title"],
                date=x["cretDttm"][:10], rating=x["revwRvgr"], content=(x["revwCntt"] or "").strip(),
            ))
        if not full or not rows:
            break
        page += 1
        time.sleep(0.5)
    return out


def collect(books, full):
    reviews, failed = [], 0
    for b in books:
        try:
            fn = fetch_yes24 if b["channel"] == "YES24" else fetch_kyobo
            reviews += fn(b, full)
        except Exception as e:  # 한 곳이 실패해도 나머지는 진행
            failed += 1
            print(f"[수집 실패] {b['channel']} {b['id']}: {e}", file=sys.stderr)
    return reviews, failed


# ---------------------------------------------------------------- 보고
def fmt_rating(v):
    return f"{int(v) if float(v).is_integer() else v} / 5"


LOW_RATING_MAX = 2  # 이 점수 이하(5점 만점)는 저별점으로 강조


def format_message(r, header="[신규 후기 알림]"):
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    rating = fmt_rating(r["rating"])
    if header == "[신규 후기 알림]" and r["rating"] <= LOW_RATING_MAX:
        # 멘션 대상은 환경변수로 지정: 예) <!channel>, <!here>, <@U0123ABCD>
        mention = os.environ.get("LOW_RATING_MENTION", "").strip()
        header = f"🚨 [저별점 후기 알림] {mention}".rstrip()
        rating = f"*{rating}* ⚠️"
    return (
        f"{header}\n\n"
        f"* 구분: 자사\n"
        f"* 채널: {r['channel']}\n"
        f"* 도서명: {esc(r['title'])}\n"
        f"* 작성일: {r['date']}\n"
        f"* 별점: {rating}\n"
        f"* 내용: {esc(r['content'])}"
    )


def send_slack(text):
    url = os.environ["SLACK_WEBHOOK_URL"]
    r = requests.post(url, json={"text": text}, timeout=20)
    r.raise_for_status()
    time.sleep(1.1)  # 웹훅 초당 1건 제한


# ---------------------------------------------------------------- 구글시트
def open_sheet():
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_info(
        json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    sh = gspread.authorize(creds).open_by_key(os.environ["SHEET_ID"])
    tab = os.environ.get("SHEET_TAB", "후기")
    try:
        ws = sh.worksheet(tab)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(tab, rows=1000, cols=len(HEADERS))
    if ws.row_values(1) != HEADERS:
        ws.update(range_name="A1", values=[HEADERS])
        ws.freeze(rows=1)
    return ws


def to_row(r, now):
    return [r["key"], "자사", r["channel"], r["title"], r["date"], r["rating"], r["content"][:49000], now]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--test-slack", action="store_true")
    ap.add_argument("--loop-minutes", type=int, default=0,
                    help="이 시간(분) 동안 --interval-minutes 간격으로 반복 실행. 0이면 1회만 실행")
    ap.add_argument("--interval-minutes", type=int, default=30)
    args = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)

    if args.test_slack:
        send_slack(format_message(
            dict(channel="YES24 리뷰", title="테스트도서", date=datetime.now(KST).strftime("%Y-%m-%d"),
                 rating=5, content="테스트 후기 내용 전문이 여기에 들어갑니다"),
            header="[후기 알림 테스트 · 실제 후기 아님]"))
        print("테스트 메시지 전송 완료")
        return 0

    books = json.loads((HERE / "books.json").read_text(encoding="utf-8"))

    if args.dry_run:
        reviews, failed = collect(books, full=False)
        seen_books = set()
        for r in reviews:
            if r["title"] + r["channel"] not in seen_books:
                seen_books.add(r["title"] + r["channel"])
                print(format_message(r), "\n" + "-" * 40)
        print(f"수집 {len(reviews)}건 / 실패 {failed}곳")
        return 0

    if not args.loop_minutes:
        return run_once(books)

    # 반복 모드: GitHub 예약 실행이 불규칙하므로, 한 번 시작되면 정해진 간격으로 계속 수집한다.
    end = time.monotonic() + args.loop_minutes * 60
    results = []
    while True:
        stamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        print(f"=== {stamp} KST ===")
        try:
            results.append(run_once(books))
        except Exception as e:  # 일시 장애로 반복이 끊기지 않게 한다
            print(f"[실행 오류] {type(e).__name__}: {e}", file=sys.stderr)
            results.append(1)
        wait = args.interval_minutes * 60
        if time.monotonic() + wait >= end:
            break
        time.sleep(wait)
    return 0 if 0 in results else 1  # 한 번도 성공 못 했을 때만 실패 처리


def run_once(books):
    ws = open_sheet()
    seen = set(ws.col_values(1)[1:])
    baseline = len(seen) == 0
    reviews, failed = collect(books, full=baseline)
    if failed == len(books):
        print("모든 수집이 실패했습니다.", file=sys.stderr)
        return 1

    new = [r for r in reviews if r["key"] not in seen]
    # 같은 후기가 두 번 잡히는 경우 방지 + 오래된 것부터 보고
    new = list({r["key"]: r for r in new}.values())
    new.sort(key=lambda r: (r["date"], r["key"]))

    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    if baseline:
        print(f"[baseline] 시트가 비어 있어 {len(new)}건을 슬랙 없이 적재합니다.")
        done = new
    else:
        done = []
        for r in new:
            try:
                send_slack(format_message(r))
                done.append(r)
            except Exception as e:  # 전송 실패 건은 시트에 안 넣어 다음 실행에서 재시도
                print(f"[슬랙 실패] {r['key']}: {e}", file=sys.stderr)
        print(f"신규 {len(new)}건 중 {len(done)}건 보고")

    if done:
        ws.append_rows([to_row(r, now) for r in done], value_input_option="RAW")
    return 0 if len(done) == len(new) else 1


if __name__ == "__main__":
    sys.exit(main())
