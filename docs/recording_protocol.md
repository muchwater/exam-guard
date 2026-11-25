# 행동 영상 녹화 절차 (Phase 2-B)

대상: 팀원 본인이 연기한 장면만 찍는다. 다른 사람이 화면에 들어오지 않게 한다.

## 1. 고정 변수
- 카메라: Rubik Pi 3에 연결된 카메라 1대. 위치·높이·각도를 사진과 줄자로 기록하고 모든 세션에서 같게 유지한다(실제 시험장 CCTV처럼 앞쪽 위에서 내려다봄).
- 좌석 배치: 책상과 의자 위치를 바닥 테이프로 표시한다. `tools/seat_annotator.py`로 좌석 polygon을 만든다.
- 소품: 시험지, 필기구, (B1·B2용) 안경 형태 소품. 실제 촬영 기능은 없어도 된다.
- 조명: 같은 강의실, 같은 조명 설정을 쓴다.

## 2. 녹화
```bash
python -m vision.camera --list                       # 카메라 장치 확인 → vision.camera.source
python -m vision.camera --source <src> --measure-fps <N>   # 실제 전달 fps 측정값 기록
python -m tools.sync_check --source <src> --blinks <N> --period <s> --out data/measured/camera_delay.json
python -m vision.record --out data/raw/act_<n>.mp4 --duration <s>
python -m tools.cue_player --schedule docs/schedules/<session>.yaml --seat <S1> --out data/raw/act_<n>.labels.jsonl
```
- `cue_player`는 화면에 "GO: head_down_hold" 같은 지시를 띄운다. 지시 시작과 끝 시각(host 시계)을 라벨로 저장한다. 순서는 seed로 섞는다.
- 동작 하나의 길이, 반복 횟수, 휴식 시간은 schedule 파일에 적는다. 이 값은 **프로토콜 선택값**이며 데이터와 함께 보관한다.
- 정상 동작(필기, 문제 읽기, 기지개 등)도 같은 방식으로 녹화한다. 오탐률 측정에 쓴다.

## 3. 측정 → config
```bash
python -m vision.pipeline --video data/raw/act_<n>.mp4 --dump-features data/measured/act_<n>.features.csv
python -m vision.pipeline --video data/raw/act_<n>.mp4 --dump-keypoints data/raw/act_<n>.kpts.jsonl
python -m synth.pose_stats --keypoints data/raw/act_<n>.kpts.jsonl --labels data/raw/act_<n>.labels.jsonl \
    --kpt-conf 0.5 --out data/measured/pose_stats.json
```
- 행동별 feature 분포(라벨 구간 안과 밖)를 비교해 `vision.action.behaviors`의 임계값을 정한다.
- `min_duration_s`: 라벨 구간 길이 분포에서 정한다. `max_gap_s`: 라벨 구간 안에서 keypoint가 끊긴 최장 길이로 정한다.
- 필요하면 `python -m synth.keypoint_gen`으로 측정 통계 기반의 합성 시퀀스를 만든다.
