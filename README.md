# exam-guard — 시험장 AI 부정행위 탐지 데모

KAIST On-device AI 텀 프로젝트(Rubik Pi 3, QCS6490).

초소형 카메라나 AI 안경으로 시험지를 촬영하고 무선으로 전송해 생성형 AI의 답을 받는 부정행위를 노린다. 두 신호를 같은 시간 창(time window)에서 결합해 **언제**와 **누가**를 함께 특정한다.

| 신호 | 입력 | 알려 주는 것 | 모듈 |
|---|---|---|---|
| Network event | Wi-Fi **monitor mode** 캡처(우리 기기 MAC만), frame 크기와 inter-arrival time | 이미지 업로드형 전송이 **언제** 있었는가 | `capture/`, `net_event/` |
| Abnormal behavior | 카메라 → pose(NPU) → 좌석별 행동 | **누가**(좌석) 이상 행동을 했는가 | `vision/` |
| Time-Window Fusion | 두 이벤트 | 겹칠 때만 alert(시각 + 좌석) | `fusion/` |

- **monitor mode**: NIC가 채널의 모든 802.11 frame을 OS로 올려 주는 모드.
- **radiotap**: monitor mode에서 frame마다 붙는 metadata header(RSSI, rate, channel 등).
- **inter-arrival time(IAT)**: 같은 기기가 보낸 연속 frame 사이의 시간 간격.

선행 연구: Lumos(Sharma et al., USENIX Security 2022). 암호화된 802.11 트래픽의 크기와 시간 정보만으로 숨겨진 IoT 기기를 식별한다. `net_event`의 multi-timescale window는 Lumos Algorithm 1을 따른다.

## 구조
```
configs/    모든 수치 파라미터 (+ "# source:" 근거, 근거가 없으면 TBD)
capture/    monitor mode 설정, live sniffer(tcpdump + BPF 우리 MAC 필터), pcap reader/writer, 캡처 실험 스크립트
net_event/  feature(크기, IAT, burst), rule-based detector, 같은 interface의 window-model detector, 측정·학습
vision/     카메라·녹화(frame timestamp), pose(QNN HTP / ONNX CPU fallback), 좌석 매핑, 행동 인식
fusion/     time-window fusion → alert, 반복 업로드에 걸친 좌석별 증거 누적(seat_evidence)
synth/      측정 분포 기반 합성 pcap / keypoint 생성기
synth/netsim/  명세 기반 802.11 + TCP 시뮬레이터: 이미지 업로드·브라우징 pcap(실측 전 단계, docs/netsim_model.md)
datasets/   공개 데이터 index (ExamCheat-MultiV, CC BY-SA 4.0)
demo/       live 데모(자동 녹화) + 화면(영상 overlay, network timeline, alert log)
replay/     녹화된 pcap + 영상으로 전체 pipeline 재생(오프라인 데모)
tools/      TBD 목록, 좌석 표시, 카메라 지연·시계 오차 측정, 연기 cue, RTT/HTTP·TLS 바이트 측정,
            ExamCheat pose 추출·통계, 대용 페이지 이미지, offline 데모 세션 생성
docs/       조사 결과, 정의 후보, 캡처·녹화 절차, 시간 동기화
common/     공통 event 타입, config(TBD 검사), latency 기록
```
구조는 지시안을 따랐다. 추가한 것은 세 가지다. 모듈들이 같은 event 타입과 config 로더를 써야 해서 `common/`을 만들었고, 측정·보조 스크립트를 `tools/`로 분리했다. 공개 데이터셋 index는 `datasets/`에 두었다.

config는 겹쳐 쓴다(`--config`를 여러 번 주면 뒤의 것이 앞의 것을 덮는다).
- `configs/default.yaml`: 실제 시스템용. 근거가 없는 값은 TBD로 남긴다.
- `configs/netsim_v1_derived.yaml`: 합성 데이터에서 유도한 network detector 값. 잠정값이다.
- `configs/examcheat.yaml`: ExamCheat-MultiV에서 측정한 B1 임계값 제안.

## 설치
```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python numpy pyyaml opencv-python-headless onnxruntime pytest
.venv/bin/python -m pytest -q tests        # 하드웨어 없이 전부 실행됨
```
보드(Ubuntu 24.04)에는 `tcpdump`, `iw`, QAIRT runtime(`/usr/lib/libQnnHtp.so`)이 필요하다. Rubik Pi 3 이미지에는 모두 들어 있다(QAIRT 2.46 apt 패키지). onnxruntime만 따로 설치한다.
```bash
python3 -m venv --system-site-packages .venv && .venv/bin/pip install onnxruntime
```
보드별 설정(실제 MAC, 인터페이스, 채널)은 `configs/site_<이름>.yaml`에 두고 `--config`로 덮어쓴다. 이 파일은 실제 MAC이 들어 있어 git에 올리지 않는다. NPU 경로는 `vision/native/README.md`를 본다.

## 실행
```bash
python -m tools.list_tbd                                   # 근거가 비어 있는 파라미터 목록
python -m capture.monitor_setup --check                    # monitor mode 지원 여부
python -m capture.sniffer --out data/raw/x.pcap            # 단독: 캡처
python -m net_event.detector data/raw/x.pcap               # 단독: network detector
python -m vision.pipeline --video data/raw/a.mp4           # 단독: vision
python -m replay.run --pcap x.pcap --video a.mp4 --out data/runs/r1 --render   # 오프라인 전체
sudo -E .venv/bin/python -m demo.live --run data/runs/live_001                 # 라이브(자동 녹화)
```
### 하드웨어 없이 전체 흐름 (합성 패킷 + ExamCheat 영상)
```bash
python -m synth.netsim.dataset --out data/synth/netsim_v1 --runs-per-preset 8 --browse-duration 300 --workers 24
python -m net_event.calibrate --index data/synth/netsim_v1/index.jsonl --split calib \
    --rule-preset esp32_lan --rule-preset esp32_lan_wifi --rule-preset esp32_wan --rule-preset linux_wan \
    --out-json data/measured/calib_netsim_v1.json --out-config configs/netsim_v1_derived.yaml
python -m net_event.evaluate --index data/synth/netsim_v1/index.jsonl --split eval \
    --config configs/netsim_v1_derived.yaml --detector window_model --out data/measured/eval_window.json
E=data/external/examcheat/V1/V1
python -m tools.make_demo_session --out data/runs/demo_synth --clip $E/nor10.mp4 --clip $E/m16.mp4 \
    --clip $E/n11.mp4 --clip $E/nor11.mp4 --upload-in-class mobile_phone --upload-in-class hidden_notes \
    --preset esp32_lan --mcs 5 --resolution 1280x720
python -m replay.run --pcap data/runs/demo_synth/net.pcap --video data/runs/demo_synth/video.mp4 \
    --out data/runs/demo_synth/replay --config configs/netsim_v1_derived.yaml --config configs/examcheat.yaml \
    --config data/runs/demo_synth/session.yaml --render
```
`session.yaml`에는 데모 전용 값(fusion pre/post 0, min_duration 0)이 들어 있다. 실측값이 아니므로 파일 안에 그렇게 표시해 두었다. ExamCheat 영상을 보여 줄 때는 CC BY-SA 4.0 출처를 함께 표기한다.

각 모듈은 단독 실행할 수 있다. 수치가 `TBD`인 파라미터가 필요한 경로는 `TBDError`로 멈추고 어떤 key인지 알려 준다(임의 값으로 돌지 않음).

## 핵심 설계
- **"언제"는 패킷, "누가"는 vision**: B1(고개 숙임)은 정상 필기와 자세가 겹치지만, 업로드가 없으면 alert를 내지 않는다. 업로드 순간에 여러 좌석이 B1이면, 업로드가 반복될 때 좌석별로 증거를 누적해 한 좌석으로 좁힌다(`fusion/seat_evidence.py`, `docs/seat_evidence.md`). 이 과정에는 조정 상수가 없고, 시험 전체에서 정상 좌석 오지목 확률을 α 이하로 묶는다.
- **replay 우선**: 모든 모듈은 입력 timestamp만 쓰고 wall clock은 쓰지 않는다. 그래서 라이브에서 녹화한 pcap과 영상을 replay하면 같은 결과가 나온다. 동기화 방법은 `docs/time_sync.md`에 있다.
- **detector 교체**: `NetDetector` interface(`update`/`advance`/`flush`) 아래에 `RuleDetector`와 `WindowModelDetector`(logistic 또는 ONNX)가 있다. 바꿀 때는 `net_event.detector` 설정만 고치면 된다.
- **NPU + fallback**: pose는 QNN HTP 상주 runner로 돌린다. 실패하면 ONNX Runtime CPU로 넘어간다. stage별 latency(ms)를 `latency.csv`로 남긴다.
- **개인정보**: kernel BPF(`wlan addr1/addr2 <우리 MAC>`)로 다른 기기의 frame을 수집 단계에서 버리고, 읽을 때 한 번 더 거른다. allowlist가 비어 있으면 캡처를 거부한다.

## 현재 상태 (2026-11-26)
- 이상 행동: **B1 고개 숙임 유지 + B2 안경 만지기**로 확정했다.
  - ExamCheat-MultiV에서 B1 `head_drop`은 정상 필기와 구분되지 않았다(frame AUC 0.506).
  - B2는 데이터셋에 양성 샘플이 없다.
  - 두 행동 모두 팀원 연기 녹화로 다시 측정해야 한다(`docs/examcheat_analysis.md`).
- 좌석 특정: ExamCheat의 정상 행동으로 분석한 결과는 다음과 같다(`docs/seat_evidence.md`).
  - 조건: 30석, 수학(업로드 30회), 촬영 자세 recall 0.8.
  - B1 임계값이 0.227이면 약 6번째 업로드에서 좌석이 특정된다.
  - 정상 좌석 오지목은 0.5% 이하다.
- 네트워크: 합성 데이터(eval)에서 두 detector의 결과가 갈렸다.
  - 업로드 전용 기기: rule과 window model 모두 recall 1.0, 오탐 0.
  - 브라우징 중 업로드: rule은 0/9이고 window model은 9/9.
  - 합성 결과이므로 실제 성능의 상한으로 본다(`docs/netsim_model.md`).
- monitor mode 실측(`docs/monitor_mode_test.md`): RTL8192EU + `rtl8xxxu`는 다른 기기의 **HT 데이터 프레임을 하나도 잡지 못했다**(legacy 속도 프레임만 잡힘).
  - 업로드 feature를 만들려면 동글을 바꿔야 한다. 후보는 `mt76x2u`/`mt7921u`/`ath9k_htc` 칩으로, 보드 커널에 드라이버가 이미 있다.
  - 계획했던 RTL8188FTV도 같은 드라이버라 같은 위험이 있다.
- 실측 캡처(`docs/real_capture_spark3.md`): 연구실 머신 spark3의 내장 Wi-Fi(MT7925)로 실제 업로드 패킷을 잡았다.
  - 합성 보정 그대로 적용한 결과: window model 10/10(오탐 4), rule 5/10(오탐 0).
  - 실측 데이터로 다시 보정해야 한다.
- 보드 측정(카메라 지연, 좌석)은 아직 하지 않았다. `python -m tools.list_tbd`로 남은 항목을 본다.

## 한계
- Wi-Fi로 전송하는 경우만 탐지한다. LTE나 Bluetooth로 휴대폰에 넘긴 뒤 휴대폰이 셀룰러로 업로드하면 sniffer로는 보이지 않는다.
- sniffer 하나로는 위치를 알 수 없다. "누가"는 vision이 맡는다.
- 이상 행동 후보(고개 숙임 유지 등)는 정상 필기 자세와 겹친다. 그래서 단독으로 alert를 내지 않고 network event와 겹칠 때만 낸다.
