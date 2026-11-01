# 자사 도서 후기 모니터 (YES24 · 교보문고)

10분마다 후기를 수집해 **직전 실행 이후 새로 달린 후기만** 슬랙에 보고하고, 구글시트에 누적합니다.
PC가 꺼져 있어도 GitHub Actions에서 돌아갑니다.

## 동작
- 구글시트(`후기` 탭)가 곧 "이미 보고한 후기" 장부입니다. `review_key` 가 시트에 없는 후기만 신규로 판단합니다.
- 시트가 비어 있는 첫 실행은 현재 후기 전체를 **슬랙 없이** 적재합니다(과거 후기 폭탄 방지).
- 슬랙 전송에 실패한 후기는 시트에 기록하지 않아 다음 실행에서 다시 시도합니다.
- 별점은 5점 만점 기준입니다(YES24 10점 척도는 ÷2).

## 준비 (최초 1회)
1. **슬랙**: 알림 받을 채널에 Incoming Webhook 생성 → URL 복사 → `SLACK_WEBHOOK_URL`
2. **구글시트**: 빈 시트 생성 → URL 중간의 ID → `SHEET_ID`
3. **서비스 계정**: Google Cloud에서 Sheets API 사용 설정 → 서비스 계정 생성 → JSON 키 발급 →
   JSON 전문을 `GOOGLE_SERVICE_ACCOUNT_JSON` 에 저장, 서비스 계정 이메일을 위 시트에 **편집자**로 공유
4. **GitHub**: 이 폴더를 저장소로 올리고 Settings → Secrets and variables → Actions 에 위 3개 등록
5. Actions 탭에서 `review-monitor` 를 수동 실행(첫 실행 = 적재), 이후 자동

## 로컬 확인
```
pip install -r requirements.txt
python monitor.py --dry-run      # 수집만, 도서별 최신 후기 1건 출력
python monitor.py --test-slack   # SLACK_WEBHOOK_URL 필요, 테스트 메시지 1건
```

도서 추가/삭제는 `books.json` 만 수정하면 됩니다.
