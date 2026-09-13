# 평안투 US Consumer Credit Stress Dashboard

미국 소비자 신용 스트레스가 경기침체나 금융 스트레스로 번질 가능성을 가능한 한 이르게 보기 위한 공개 모니터입니다. 예쁜 경제 보드가 목적이 아니라, **레벨 → 모멘텀 → 가속 → 전이(transmission)** 순서로 사이클 전환을 추적합니다.

## 공개 산출물

| 파일 | 용도 |
|---|---|
| `dist/index.html` | 공개 대시보드 (GitHub Pages / 블로그 iframe) |
| `dist/ccss_data.json` | 계산 결과 원본 |

방법론 문서: `docs/methodology.md`. 공개 HTML은 각 KPI 카드를 열어 정의·출처를 확인할 수 있습니다.

공개 URL: `https://ryanhwang81.github.io/pyeongantu-us-ccss/`

## 10개 KPI (가중치 합 100%)

1. 신용카드 초기 연체 유입 15% — NY Fed HHDC Page 13 CC (flow 30+)
2. 자동차 초기 연체 유입 10% — NY Fed HHDC Page 13 Auto
3. 가계 전체 연체 잔액 비중 5% — NY Fed HHDC Page 11 `100 − Current` (stock)
4. 신용카드 심각 연체 유입 15% — NY Fed HHDC Page 14 CC (flow 90+)
5. 자동차 심각 연체 유입 10% — NY Fed HHDC Page 14 Auto
6. 카드 상각률 전 은행 10% — Fed CHGDEL all banks / FRED `CORCCACBS`
7. 카드 상각률 기타/소형 은행 10% — Fed CHGDEL Other Banks (`chgothersa.htm`)
8. SLOOS 카드 여신 기준 15% — FRED `DRTSCLCC` (**`DRTSCIS`가 아님** — 그 시리즈는 중소기업 C&I)
9. 회전신용 YoY 5% — FRED `REVOLSL` (낮을수록 스트레스)
10. 카드 한도 YoY 5% — NY Fed HHDC Page 10 Credit Card Limit (낮을수록 스트레스)

Subprime share는 2026 Q1 신용점수 방법론 단절 때문에 CCSS에 넣지 않고 Context만 표시합니다.

## 로컬 빌드

```bash
pip install -r requirements.txt
python3 -m unittest discover -s tests -v
python3 build.py --out dist
```

환경변수 `CCSS_CACHE=/path` 가 있으면 원자료를 캐시합니다.

## 자동 갱신

분기 자료가 대부분이므로 매일 재처리하지 않습니다. GitHub Actions가 **매월 1일·16일 09:00 UTC**에 빌드합니다. 신규 데이터가 없으면 dist 커밋 없이 종료합니다. FRED graph CSV는 GitHub-hosted runner에서 timeout 되는 경우가 있어 `self-hosted, macOS, gl-monitor` runner를 사용합니다.

## 고지

이 모니터는 공개 1차 자료를 이용한 시장 해석 도구입니다. 투자 자문이 아니며, 과거 구간 분류가 미래 성과를 보장하지 않습니다.
