# ExamCheat-MultiV pose feature 분석 (B1·B2 근거)

- 데이터: "ExamCheating _MultiV video-based Dataset"(ExamCheat-MultiV), rimmajeed, Kaggle
  (kaggle.com/datasets/rimmajeed/examcheating-multiv-video-based-dataset), **CC BY-SA 4.0**.
  - 발표 자료나 보고서에 이 데이터의 frame이나 통계를 쓰면 출처와 license를 표기해야 한다. 문구는 `datasets/examcheat.py::ATTRIBUTION`에 있다.
  - 추출한 keypoint와 feature는 adaptation에 해당한다. 공유하려면 CC BY-SA 4.0으로 해야 하므로 `data/`에만 두고 commit하지 않는다.
- 용도: **개발용 통계에만 쓴다.** live 데모 영상은 계속 팀원이 직접 연기한 장면만 사용한다.
- 재현 명령:
  ```bash
  python -m datasets.examcheat --probe                          # index (label, 중복 crop 확인)
  python -m tools.examcheat_extract --workers 12 --threads 4    # pose + feature (모든 frame)
  python -m tools.examcheat_stats                               # → data/measured/examcheat_feature_stats.json
  ```
- pose 설정: `models/pose.onnx`(YOLOv8n-pose, ONNX Runtime CPU)를 썼다. conf 0.25, iou 0.7, kpt_conf 0.5는 configs 값 그대로다. 코드 경로도 live와 같다(`vision.pose.PoseEstimator`, `vision.action.pose_features`).

## 1. label 확인 (2026-11-25, prefix별로 3개 clip × 2 frame을 직접 봄)

| prefix | class | 화면에서 확인한 내용 |
|---|---|---|
| `c` | copying | 옆 사람을 보거나 종이를 건넴 |
| `g` | gesture_sharing | 손으로 신호를 보냄(V 사인 등) |
| `m` | mobile_phone | 무릎이나 손에 휴대폰을 들고 있음 |
| `n` | hidden_notes | 주머니나 신발에서 쪽지를 꺼내며 깊게 숙임 |
| `nor` | normal | 시험지에 필기함 |
| `s` | **미확정** | crop에만 있고 일반 필기처럼 보임 → **제외** |

- **V1**(83개): 1080×1920 세로 영상, 30 fps, 앞쪽 높은 각도에서 앉은 수험생 1명을 찍었다. 표본으로 본 clip이 모두 같은 옷이라 **참가자 1명일 가능성이 높다.** 뒤쪽에 다른 사람이 지나가는 frame이 있다(multi-person frame 40%).
- **crops**(150개): 224×224로 피험자 중심을 잘라낸 clip이며 참가자가 여러 명이다.
  - V1과 이름과 frame 수(±1)가 같은 39개는 V1 clip을 잘라낸 것이라 중복으로 제외했다.
  - `s` 12개도 제외해서 99개가 남았다.
- **V2**(30개): 교실 전체를 찍은 장면이다. 이름에 붙은 단어(copy/gesture/mobile/notes/all)는 장면 단위라 **누가** 해당 행동을 했는지 알 수 없다. 그래서 사람별 통계에서 제외했다.
  - 대신 여러 좌석을 보여 주는 데모 화면이나 pose 다인원 테스트용 후보로는 쓸 수 있다(`--include-scenes`).
- 통계 대상: **182 clip, 50,993 frame**.

| class | V1 | crops | 합계 |
|---|---|---|---|
| normal | 7 | 27 | 34 |
| copying | 23 | 20 | 43 |
| gesture_sharing | 18 | 9 | 27 |
| hidden_notes | 20 | 28 | 48 |
| mobile_phone | 15 | 15 | 30 |

**피험자 선택.** clip마다 frame별로 가장 큰 bbox를 고르고, 그 bbox들의 좌표별 중앙값을 "피험자 영역"으로 정했다. 각 frame에서는 이 영역과 IoU가 가장 큰 사람을 피험자로 보았고, IoU가 0이면 피험자가 없는 것으로 처리했다.
- 이렇게 한 이유는 live 시스템이 고정된 좌석 polygon으로 신원을 정하고 tracker를 쓰지 않기 때문이다. 같은 조건으로 맞추려고 했다.
- frame별 사람 수도 함께 저장했다.

## 2. 결론 요약

1. **B1(head_down_hold): head_drop만으로는 이 데이터에서 판별력이 없다.**
   - 판별 대상은 normal과 "물건을 내려다보는" proxy positive(mobile_phone + hidden_notes)다.
   - frame AUC는 **0.506**(clip bootstrap 95% CI 0.400–0.621)이다.
   - source별로는 V1 0.675(0.562–0.839), crops 0.449다. class별로 보면 mobile_phone 0.649, hidden_notes 0.433이다.
   - 추정: hidden_notes는 상체 전체를 숙이므로 어깨도 함께 내려가고, 어깨 기준으로 정규화한 head_drop이 커지지 않는다(crops의 중앙값은 −0.166으로 normal보다 낮다).
2. **지속 시간을 함께 봐도 분리되지 않는다.** 실제 `TemporalSegmenter`(max_gap 0.847 s)로 계산했다.
   - threshold 0.227에서 normal clip의 최장 event는 **7.08 s**(crops nor25, 고개를 숙이고 필기)다. positive clip의 최장 event(≤ 6.00 s)보다 길다.
   - 단, clip이 짧아서(≤ 11.9 s) 실제 시험에서 오래 유지하는 자세는 이 데이터로 검증할 수 없다.
   - 이 결과는 `docs/behavior_definitions.md`의 주의 사항("B1은 정상 필기와 자세가 겹친다")을 수치로 확인한 것이다. 따라서 B1은 단독 alert가 아니라 network event와 겹칠 때만 의미가 있다.
3. **normal의 상위 꼬리는 viewpoint가 달라도 안정적이다.** normal frame 기준 p95 threshold는 V1 0.220, crops 0.228, 전체 0.227로 비슷하다. 그래서 "정상 필기의 95%보다 더 숙임"이라는 정의는 데이터셋 사이에서 재현된다.
4. **B2(glasses_touch): positive가 없다.**
   - false-alarm baseline만 쟀다. normal frame에서 `min_wrist_to_nose < 0.5` 비율은 0.6%, `< 0.75` 비율은 12.3%다.
   - 높은 각도의 카메라에서는 필기하는 손이 얼굴 근처에 투영되므로 normal 중앙값도 0.96 shoulder width다.
   - gesture_sharing(손을 얼굴 근처로 올림)은 `< 0.5`에서 4.6%로 가장 많이 걸리는 hard negative다.
   - B2 임계값은 팀원 녹화 전까지 **TBD**로 둔다.
5. **max_gap_s 근거:** positive clip에서 head-down frame 사이의 keypoint 결손 중 가장 긴 것은 0.847 s(hidden_notes)이고 p95는 0.394 s다.
6. **fps:** V1은 30.02 fps로 일정하다. crops는 원본에 따라 25, 29, 30, 59 fps가 섞여 있다(측정 중앙값 59). segmenter가 시간 기준으로 동작하므로 frame 수 대신 초 단위를 썼다.

## 3. 사용자가 결정할 것

- **B1 operating point.** `configs/examcheat.yaml`은 normal frame FPR 5%를 목표로 잡아 **0.227**로 두었다(결정 필요). 1%로 하면 0.252, 10%로 하면 0.199다.
  - held-out(clip 5-fold) 결과는 FPR 0.061, TPR 0.073이다.
  - 1% 목표(0.252)와 min_duration 1.75 s를 쓰면 clip 기준 held-out FPR 0.029, TPR 0.139다.
- **min_duration_s는 TBD로 둔다.** 이 데이터에서는 판별에 쓸 값을 정할 수 없다. 팀원이 연기한 AI 안경 "숙이고 유지" 구간 길이로 정해야 한다.
- **B1 정의 보강 여부.** 기사의 "오래 숙임"을 정상 필기와 구분하려면 자세 외의 단서가 필요해 보인다. 예를 들면 손목이 움직이지 않는지(필기하지 않음), 머리가 고정되어 있는지 같은 시간축 feature다.
  - 현재 `vision/action.py` FEATURES에는 이런 단서가 없다.
  - 추가할지는 팀원 녹화로 확인한 뒤 정할 일이다(이번 작업에서는 바꾸지 않음).

## 4. 한계

- 레이블이 clip 단위라서 positive clip에도 고개를 숙이지 않은 frame이 섞여 있다. AUC는 이 label noise만큼 낮게 나온다.
- 참가자 ID가 없어서 subject 단위가 아니라 **clip 단위**로 나눴다. V1은 사실상 1명이라 V1 내부의 held-out은 subject-independent가 아니다. cross-source(V1 ↔ crops)가 일반화에 더 가까운 점검이다.
- crops는 224×224로 확대해 추론했고, 어깨나 코가 잘려 head_drop이 NaN인 비율이 class에 따라 9–29%다(V1은 ≤ 2%).
- 대상 행동(AI 안경 사용)을 직접 담은 데이터가 아니다. mobile_phone과 hidden_notes는 "아래를 보는 행동"의 proxy일 뿐이다.

## 5. 수치 표 (`python -m tools.examcheat_stats`가 생성)

FPR target과 B2의 t 값은 보고용 grid이며 operating point로 고른 값이 아니다.

### head_drop 분위수 (class별, 모든 source)

| class | clips | frames | subject rate | head_drop NaN | p05 | p25 | p50 | p75 | p95 |
|---|---|---|---|---|---|---|---|---|---|
| normal | 34 | 12100 | 0.99 | 0.22 | -0.422 | -0.216 | -0.038 | 0.079 | 0.227 |
| copying | 43 | 8166 | 0.96 | 0.16 | -0.603 | -0.297 | -0.121 | 0.009 | 0.251 |
| gesture_sharing | 27 | 4868 | 1.00 | 0.05 | -0.613 | -0.282 | -0.096 | 0.045 | 0.296 |
| hidden_notes | 48 | 17623 | 0.95 | 0.16 | -0.510 | -0.271 | -0.116 | 0.048 | 0.263 |
| mobile_phone | 30 | 8236 | 1.00 | 0.14 | -0.370 | -0.059 | 0.064 | 0.166 | 0.269 |

### head_drop 분위수 (V1)

| class | clips | p05 | p25 | p50 | p75 | p95 |
|---|---|---|---|---|---|---|
| normal | 7 | -0.385 | -0.271 | -0.121 | 0.029 | 0.220 |
| copying | 23 | -0.642 | -0.417 | -0.202 | 0.042 | 0.323 |
| gesture_sharing | 18 | -0.403 | -0.220 | -0.027 | 0.119 | 0.358 |
| hidden_notes | 20 | -0.414 | -0.122 | 0.035 | 0.194 | 0.687 |
| mobile_phone | 15 | -0.438 | -0.065 | 0.041 | 0.148 | 0.392 |

### head_drop 분위수 (crops)

| class | clips | p05 | p25 | p50 | p75 | p95 |
|---|---|---|---|---|---|---|
| normal | 27 | -0.426 | -0.212 | -0.029 | 0.082 | 0.228 |
| copying | 20 | -0.358 | -0.171 | -0.084 | -0.009 | 0.087 |
| gesture_sharing | 9 | -0.839 | -0.328 | -0.147 | -0.033 | 0.139 |
| hidden_notes | 28 | -0.546 | -0.303 | -0.166 | -0.009 | 0.110 |
| mobile_phone | 15 | -0.290 | -0.055 | 0.081 | 0.171 | 0.231 |

### B1: head_drop AUC (positive = mobile_phone + hidden_notes, negative = normal)

| source | AUC | 95% CI (clip bootstrap) | pos clips/frames | neg clips/frames | NaN pos | NaN neg |
|---|---|---|---|---|---|---|
| all | 0.506 | 0.400–0.621 | 78/21106 | 34/9371 | 0.16 | 0.22 |
| V1 | 0.675 | 0.562–0.839 | 35/6376 | 7/956 | 0.01 | 0.00 |
| crops | 0.449 | 0.324–0.583 | 43/14730 | 27/8415 | 0.21 | 0.24 |

class별 AUC (vs normal): copying 0.402, gesture_sharing 0.441, hidden_notes 0.433, mobile_phone 0.649

### B1: held-out 평가 (clip 단위 5-fold, seed 0)

| target Normal FPR | threshold (전체 normal) | fold thresholds | test FPR mean [min,max] | test TPR mean [min,max] |
|---|---|---|---|---|
| 0.01 | 0.252 | 0.249, 0.242, 0.252, 0.254, 0.252 | 0.018 [0.000, 0.059] | 0.058 [0.012, 0.094] |
| 0.05 | 0.227 | 0.229, 0.201, 0.231, 0.231, 0.229 | 0.061 [0.000, 0.241] | 0.073 [0.016, 0.100] |
| 0.10 | 0.199 | 0.177, 0.151, 0.208, 0.209, 0.205 | 0.096 [0.003, 0.261] | 0.106 [0.024, 0.146] |

### B1: cross-source (한 source의 normal로 threshold → 다른 source에서 평가)

| fit → test | target FPR | threshold | test FPR | test TPR |
|---|---|---|---|---|
| fit_V1_test_crops | 0.01 | 0.264 | 0.004 | 0.003 |
| fit_V1_test_crops | 0.05 | 0.220 | 0.066 | 0.030 |
| fit_V1_test_crops | 0.10 | 0.179 | 0.118 | 0.077 |
| fit_crops_test_V1 | 0.01 | 0.249 | 0.024 | 0.171 |
| fit_crops_test_V1 | 0.05 | 0.228 | 0.043 | 0.189 |
| fit_crops_test_V1 | 0.10 | 0.200 | 0.078 | 0.216 |

### B1: clip별 최장 연속 유지 시간 (head_drop > threshold, gap 허용 없음)

| target FPR | threshold | class | longest hold p50 / p90 / max (s) | clip 길이 p50 / max (s) |
|---|---|---|---|---|
| 0.01 | 0.252 | normal | 0.00 / 0.03 / 0.15 | 6.6 / 11.2 |
| 0.01 | 0.252 | copying | 0.00 / 0.31 / 1.83 | 5.9 / 8.6 |
| 0.01 | 0.252 | gesture_sharing | 0.00 / 0.85 / 1.87 | 4.8 / 6.8 |
| 0.01 | 0.252 | hidden_notes | 0.00 / 1.29 / 3.30 | 7.7 / 11.9 |
| 0.01 | 0.252 | mobile_phone | 0.00 / 0.37 / 4.10 | 6.0 / 8.5 |
| 0.05 | 0.227 | normal | 0.00 / 0.21 / 0.69 | 6.6 / 11.2 |
| 0.05 | 0.227 | copying | 0.00 / 0.59 / 1.90 | 5.9 / 8.6 |
| 0.05 | 0.227 | gesture_sharing | 0.00 / 0.85 / 1.87 | 4.8 / 6.8 |
| 0.05 | 0.227 | hidden_notes | 0.00 / 1.30 / 3.33 | 7.7 / 11.9 |
| 0.05 | 0.227 | mobile_phone | 0.01 / 0.78 / 6.00 | 6.0 / 8.5 |
| 0.10 | 0.199 | normal | 0.00 / 0.28 / 2.97 | 6.6 / 11.2 |
| 0.10 | 0.199 | copying | 0.00 / 0.75 / 1.90 | 5.9 / 8.6 |
| 0.10 | 0.199 | gesture_sharing | 0.00 / 0.85 / 2.73 | 4.8 / 6.8 |
| 0.10 | 0.199 | hidden_notes | 0.00 / 1.37 / 3.36 | 7.7 / 11.9 |
| 0.10 | 0.199 | mobile_phone | 0.03 / 1.40 / 6.13 | 6.0 / 8.5 |

### B1: TemporalSegmenter 기준 clip 단위 (max_gap_s = 0.847, min_duration 후보 = normal clip 최장 event)

| target FPR | threshold | clip AUC (최장 event) | min_duration 후보 (s) | clip TPR @후보 | held-out clip FPR / TPR | 최장 event p50/max: normal | mobile_phone | hidden_notes |
|---|---|---|---|---|---|---|---|---|
| 0.01 | 0.252 | 0.578 | 1.75 | 0.128 | 0.029 / 0.139 | 0.00/1.75 | 0.00/5.93 | 0.00/4.40 |
| 0.05 | 0.227 | 0.594 | 7.08 | 0.000 | 0.029 / 0.000 | 0.00/7.08 | 0.07/6.00 | 0.00/4.73 |
| 0.10 | 0.199 | 0.603 | 7.19 | 0.000 | 0.029 / 0.000 | 0.00/7.19 | 0.19/6.13 | 0.00/4.73 |

### B2: min_wrist_to_nose < t 인 frame 비율 (false-alarm baseline, positive 없음)

| class | subject frames | wrist visible | t=0.25 | t=0.5 | t=0.75 | t=1 |
|---|---|---|---|---|---|---|
| normal | 11994 | 0.92 | 0.001 | 0.006 | 0.123 | 0.401 |
| copying | 7816 | 0.96 | 0.002 | 0.019 | 0.137 | 0.325 |
| gesture_sharing | 4868 | 0.99 | 0.004 | 0.046 | 0.159 | 0.442 |
| hidden_notes | 16748 | 0.83 | 0.000 | 0.006 | 0.047 | 0.297 |
| mobile_phone | 8236 | 0.95 | 0.000 | 0.005 | 0.248 | 0.489 |

### keypoint dropout gap (head_drop NaN 구간, bracketed threshold = 0.227)

| class | 모든 gap n / p50 / p95 / max (s) | head-down 사이 gap n / p50 / p95 / max (s) |
|---|---|---|
| normal | 303 / 0.051 / 0.551 / 4.559 | 2 / 0.059 / 0.067 / 0.068 |
| copying | 260 / 0.100 / 0.655 / 3.448 | 6 / 0.150 / 0.275 / 0.300 |
| gesture_sharing | 55 / 0.051 / 0.273 / 1.576 | 3 / 0.100 / 0.100 / 0.100 |
| hidden_notes | 464 / 0.051 / 0.540 / 3.322 | 15 / 0.067 / 0.394 / 0.847 |
| mobile_phone | 203 / 0.034 / 0.337 / 0.898 | 4 / 0.042 / 0.051 / 0.051 |

### fps (decoded frame timestamp 기준)

| source | sizes | fps measured p05 / p50 / p95 | container fps p50 | multi-person frame 비율 |
|---|---|---|---|---|
| V1 | 1080x1920 | 30.02 / 30.02 / 30.02 | 30.02 | 0.403 |
| crops | 224x224 | 29.00 / 59.00 / 59.00 | 59.00 | 0.262 |
