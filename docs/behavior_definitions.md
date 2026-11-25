# 이상 행동 정의 (Phase 2-B) — **확정: B1 + B2** (사용자 결정, 2026-11-25)

근거는 실제 보도된 사례 중 자세가 기술된 것만 사용했다. 원문은 2026-11-25에 직접 확인했다.
최근 사례의 주된 수법은 **AI 스마트 안경**이다. 안경의 카메라로 문제를 촬영하면 AI 답이 렌즈 화면에 표시된다.

| ID | 정의(pose 관점) | 근거(원문 인용) | pose feature 후보 (vision/action.py) |
|---|---|---|---|
| **B1 head_down_hold** | 고개를 시험지 쪽으로 깊게 숙인 자세를 오래 유지함 (안경 카메라를 문제에 조준) | 聯合報 2026-06-12 "考試開始後，隨即發現該生頭愈來愈低" (udn.com/news/story/6925/9561347); ETtoday 2026-06-11 "維持低頭的姿勢很久" (ettoday.net/news/20260611/3181745.htm); 세계일보 2026-07-14 "주변을 살피고 고개를 숙이는 등 이상한 행동" (segye.com/newsView/20260713523220) | `head_drop`(코-어깨 중점 세로 거리/어깨 폭) 큼 + 지속시간 |
| **B2 glasses_touch** | 손이 안경테·관자놀이 부근으로 반복해서 올라감 (촬영 버튼 조작) | YTN 2026-07-15 "시험 도중 안경테를 부자연스럽게 만지는 행동을 수상히 여겨" (ytn.co.kr/_ln/0103_202607151027296523); 위키트리 2026-11-16 (한국산업인력공단 발표 인용) "지속해서 안경을 매만지는 수험생" (wikitree.co.kr/articles/1175040) | `min_wrist_to_nose` 작음, `max_wrist_height`가 어깨 위 |
| B3 looking_around | 고개를 좌우로 돌려 주변을 살핌 | 세계일보 2026-07-14 "주변을 살피고" (위와 같은 기사) | `head_yaw` 변동, `ear_asym` |
| (참고) B4 hidden_phone_under_paper | 시험지·연습지 아래 휴대폰으로 촬영 | 중국 교육부 통보(2021, Sputnik China 인용, agent 확인만 함 △) "将手机藏于草稿纸下拍题" | 손목이 책상 위 정지. pose만으로 구분하기 어려움 |

**확정안: B1 + B2** (사용자 결정). 둘 다 독립 보도 둘 이상으로 확인되고 pose로 관측할 수 있다. B3는 근거가 1건뿐이라 쓰지 않는다.

ExamCheat-MultiV로 측정한 결과는 `docs/examcheat_analysis.md`에 있다. 요약하면 다음과 같다.
- B1 `head_drop`은 정상 필기와 구분되지 않는다(frame AUC 0.506).
- B2는 데이터셋에 양성 샘플이 없어서 오탐 기준선만 쟀다.

주의
- B1은 정상적인 필기(고개 숙임)와 자세가 겹친다. 그래서 단독 판정이 아니라 **Wi-Fi 업로드 이벤트와 같은 시간 창**에서만 alert를 낸다. 이것이 fusion 설계의 이유다.
- "시험지를 가슴/단추 카메라 쪽으로 들어 올림", "귀 만지기(이어폰)", "손목 화면 보기"는 확인된 원문 근거를 찾지 못했다(조사 결과). 그래서 후보에서 제외했다.
- 임계값(`value`), `min_duration_s`, `max_gap_s`는 모두 `TBD`다. 팀원 연기 녹화에서 `python -m vision.pipeline --dump-features`로 측정한 분포로 정한다.

config 예시(값은 측정 후 기입):
```yaml
vision:
  action:
    behaviors:
      head_down_hold:   # docs/behavior_definitions.md B1
        - {feature: head_drop, op: ">", value: TBD}
      glasses_touch:    # B2
        - {feature: min_wrist_to_nose, op: "<", value: TBD}
```
