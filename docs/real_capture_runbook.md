# 실측 세션 녹화 절차 (spark3 sniffer + 보드 업로더)

목적: 실제 패킷으로 network detector를 다시 보정하고 평가한다. 합성 보정값은 실측에서 VGA를 놓쳤고, window model은 오탐을 냈다(`docs/real_capture_spark3.md`).

## 역할
| 누가 | 어디서 | 무엇을 |
|---|---|---|
| 사용자 | 본인 터미널 → spark3 | 캡처 시작. `sudo` 비밀번호가 필요한 유일한 단계다 |
| Claude | 개발 PC → 보드 | 트래픽 실행 → 캡처 정지 → 분석 |

## 0. 준비 (Claude)
- spark3의 `/tmp`는 재부팅하면 지워진다. 캡처 스크립트를 다시 복사한다:
  `scp tools/linux_monitor_capture.sh spark3:/tmp/eg_capture/`
- 보드 저장소를 최신으로 맞춘다: `ssh rubik 'cd ~/exam-guard && git pull --ff-only'`

## 1. 캡처 시작 (사용자, spark3)
```bash
ssh -t spark3 'sudo bash /tmp/eg_capture/linux_monitor_capture.sh wlP9s9 2 /tmp/eg_capture/calib1.pcap /tmp/eg_capture/stop 2400 <보드 wlan0 MAC>'
```
- `capturing on wlP9s9 ...`가 뜨면 Claude에게 알린다.
- 최대 40분 뒤에는 자동으로 끝난다. 끝나면 Wi-Fi를 원래 상태(managed)로 되돌린다.
- spark3의 Wi-Fi가 IP나 기본 경로를 갖고 있으면 스크립트가 거부한다. 지금은 미사용이다.

## 2. 트래픽 실행 (Claude, 보드, 약 16분)
```bash
cd ~/exam-guard
SRV=$(bash tools/ota_netns.sh up enxf074e4884cd9 $(.venv/bin/python -m tools.ota_session --print-blob-sizes) | tail -1)
.venv/bin/python -m tools.ota_session --server "$SRV" --plan calib --seed 1 --out data/raw/real/calib1
bash tools/ota_netns.sh down
```
- 세션 구성(`calib` plan):
  - 시계 표식
  - 해상도 6종 × 4회 = 업로드 24회(간격 12–25 s)
  - 배경 블록 4회: 웹 페이지 로드(Web Almanac p10–p90 크기), 드문 큰 ping, 작은 주기 ping, 유휴
  - 다운로드 중 업로드 4회
  - 시계 표식
- 설정을 확인할 때는 `--plan smoke`(약 30초)를 먼저 돌린다.

## 3. 정지와 수집 (Claude)
```bash
ssh spark3 'touch /tmp/eg_capture/stop'      # 사용자 터미널의 캡처가 끝나고 Wi-Fi가 복원된다
mkdir -p data/raw/real/calib1
scp spark3:/tmp/eg_capture/calib1.pcap data/raw/real/calib1/capture.pcap
scp 'rubik:exam-guard/data/raw/real/calib1/*' data/raw/real/calib1/
```

## 4. 보정과 평가 (Claude, 개발 PC)
```bash
python -m tools.real_session index --session data/raw/real/calib1 --device <MAC> --preset real_board \
    --segments 4 --split alternate --out data/raw/real/index.jsonl
python -m net_event.calibrate --index data/raw/real/index.jsonl --rule-preset real_board \
    --out-json data/measured/calib_real_v1.json --out-config configs/real_v1_derived.yaml \
    --model-out data/models/net_logreg_real.npz
python -m net_event.evaluate --index data/raw/real/index.jsonl --split eval --detector rule \
    --config configs/real_v1_derived.yaml --out data/measured/eval_real_v1_rule.json
python -m net_event.evaluate --index data/raw/real/index.jsonl --split eval --detector window_model \
    --config configs/real_v1_derived.yaml --out data/measured/eval_real_v1_window_model.json
```
- **시계:** `tools.real_session`이 시계 표식으로 업로더와 sniffer의 시계 차이를 구해 정답 구간을 sniffer 시계로 옮긴다. spark3는 NTP 동기화가 안 된다.
- **구간 분할:** 세션을 활동 사이의 큰 빈 구간에서 4개로 자르고, calib과 eval을 번갈아 배정한다. 보정은 세션 단위 교차검증(LOSO)이다.
- **개인정보:** index와 구간 pcap은 `data/raw`(git 제외)에 둔다. 실제 MAC이 들어 있기 때문이다.

## 5. 데모 세션 (연기 영상 + 캡처 동시 녹화)
- 연기를 녹화하는 동안 1번의 캡처를 켜 둔다.
- 업로더(보드 또는 팀원 휴대폰)는 `tools.ota_session`의 시계 표식으로 시작하고 끝낸다. 휴대폰이면 표식을 대신할 방법이 필요하다(TBD).
- 영상, pcap, 시계 차이를 `replay.run`에 넣는다(`docs/time_sync.md`).
- 그 전까지는 실측 업로드 버스트를 클립 시점에 맞춘 composite로 데모를 만든다:
```bash
python -m tools.make_demo_session --out data/runs/demo_real --clip ... --upload-in-class mobile_phone \
    --upload-in-class hidden_notes --real-session data/raw/real/spark3_20261126 --real-device <MAC> \
    --real-uploads s3_uploads.labels.jsonl --real-markers s3_markers.jsonl --real-resolution 1600x1200
python -m replay.run --pcap data/runs/demo_real/net.pcap --video data/runs/demo_real/video.mp4 \
    --out data/runs/demo_real/replay --config configs/netsim_v1_derived.yaml --config configs/examcheat.yaml \
    --config data/runs/demo_real/session.yaml --render
```
- 2026-11-26 결과: 실제 UXGA 업로드 2회가 모두 net event가 됐다. B1과 겹친 n11 구간에서 alert 1건(S1)이 났다.
