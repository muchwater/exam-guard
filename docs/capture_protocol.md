# 패킷 캡처 절차 (Phase 2-A)

목표: 측정값으로 `net_event.rule.*`, `synth.pcap.*`의 `TBD`를 채운다. 시나리오는 (1) idle, (2) 일반 사용(웹 브라우징), (3) 일정 간격 이미지 업로드 세 가지다.

## 0. 원칙
- **팀 소유 기기만** 다룬다. sniffer는 kernel BPF(`wlan addr1/addr2 <우리 MAC>`)로 다른 기기의 frame을 수집 단계에서 버린다. pcap을 읽을 때 Python에서 한 번 더 거른다(`capture/macfilter.py`).
- AP도 팀 소유를 사용한다(예: 팀원 휴대폰 hotspot 또는 공유기, 2.4 GHz, 채널 고정).
- 업로드 대상은 팀 노트북의 `capture.upload_server`다. 이미지가 외부로 나가지 않는다.

## 1. 장비와 고정 변수

| 항목 | 값 | 비고 |
|---|---|---|
| sniffer | Rubik Pi 3 + RTL8188FTV(USB) | 내장 AP6256은 monitor mode를 지원하지 않음(`iw list` 확인) |
| uploader | 팀 기기 1대("숨긴 카메라" 역할) | Python 3 + OpenCV |
| server | 팀 노트북 | `python -m capture.upload_server` |
| AP / 채널 | 팀 AP, 2.4 GHz 고정 채널 | `capture.channel` |
| 배치 | sniffer–uploader–AP 거리와 위치를 사진으로 기록하고 모든 run에서 동일하게 유지 | |
| 이미지 원본 | 같은 시험지 사진 1장(`data/ref/page.jpg`) | 해상도만 바꿔서 재인코딩 |
| JPEG quality | 한 값으로 고정해 기록 | 실험 변수 아님 |
| 전송 방식 | HTTP/1.1 POST, 업로드마다 새 TCP 연결 | |

**실험 변수는 업로드 간격(`--interval`)과 해상도(`--resolution`) 두 가지뿐이다.** 한 번의 비교에서는 둘 중 하나만 바꾼다.

## 2. 사전 점검 (보드)
```bash
lsusb | grep -i 0bda:f179                      # 어댑터 인식 확인
python -m capture.monitor_setup --check        # 인터페이스별 monitor 지원 여부와 사용 중 여부
python -m capture.monitor_setup --iface <wlanX> --channel <AP 채널>
```
`--check`에서 RTL8188FTV가 `monitor=NO`이면 rtl8xxxu 드라이버로는 진행할 수 없다. 이때는 다른 어댑터를 써야 한다(보고 필요).

## 3. 시계 동기화
uploader가 기록한 업로드 시각을 sniffer 시계로 옮기기 위해 오프셋을 측정한다.
```bash
# 보드
python -m tools.clock_offset serve --port <p>
# uploader
python -m tools.clock_offset probe --host <보드 IP> --port <p> --n <N> --out data/raw/offset_<run>.json
```
`client_minus_server_s`가 `net_event.measure --offset`에 들어간다. 오차 상한은 `error_bound_s`(최소 RTT/2)이며 함께 기록한다. 각 run 직전과 직후에 측정한다.

## 4. 시나리오별 녹화
```bash
# 보드 (모든 시나리오 공통)
sudo -E .venv/bin/python -m capture.sniffer --out data/raw/<run>.pcap --duration <s>
```
1. **idle**: uploader가 AP에 연결만 된 상태로 아무 앱도 쓰지 않는다.
2. **browsing**: 팀원이 uploader로 정해진 사이트 목록을 정해진 순서대로 탐색한다(목록과 시간을 기록).
3. **upload**:
   ```bash
   python -m capture.scenario_upload --image data/ref/page.jpg --resolution <WxH> --quality <q> \
       --interval <s> --count <n> --url http://<server>:<port>/upload --out data/raw/<run>.labels.jsonl
   ```
업로드 간격과 해상도 후보는 실험 설계 시 정하고 사용자가 확정한다(현재 `TBD`).

## 5. 측정 → config
```bash
python -m net_event.measure data/raw/<run>.pcap --device <uploader MAC> \
    --labels data/raw/<run>.labels.jsonl --offset <측정값> --out data/measured/<run>.json
```
`data/measured/*.json`의 다음 항목으로 config를 채우고 `# source:`에 파일명을 적는다.
- `burst_gap_s`: `uplink_iat_in_upload` 상위 꼬리와 `gap_between_uploads_s` 하위 꼬리 사이
- `min_burst_bytes`: `air_bytes_per_upload` 하위 꼬리
- `large_frame_bytes`, `min_large_ratio`: `uplink_size_in_upload` 분포(참고 계산값은 1550 B)
- 합성: `synth.pcap.measured_upload` / `measured_background`에 위 JSON 경로를 넣는다.
