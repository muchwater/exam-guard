# 합성 패킷 생성기 (synth/netsim) — 모델과 가정

실제 monitor-mode 캡처를 아직 할 수 없어서(보드 미연결), 이미지 업로드 트래픽을 **프로토콜 명세와 실제 구현 소스의 기본값**으로 시뮬레이션한다. 기존 `synth/pcap_gen.py`(실측 분포에서 합성)는 실측 JSON이 생기면 그대로 쓴다. 이 문서의 생성기는 그 전 단계를 담당한다.

- 모든 상수는 `configs/default.yaml`의 `synth.netsim`에 `# source:`와 함께 있다. 출처는 Linux ath9k/mac80211/TCP 소스, ESP-IDF/lwIP 소스, hostapd.conf, RFC, OpenCV 소스, HTTP Archive, 수능 시행기본계획이다. 소스 파일은 2026-11-25 GitHub master에서 직접 읽었다.
- 측정값(`data/measured/`)은 두 가지다.
  - `app_bytes.json`: 우리 uploader/server 코드를 직접 실행해 잰 HTTP 바이트 수와, 공개 API 호스트에 TLS 1.3 handshake만 해서 잰 flight 크기.
  - `wan_rtt_devpc.json`: 개발 PC에서 측정한 TCP connect RTT.
  - **주의**: 이 RTT는 개발 PC가 연결된 캠퍼스 유선망에서의 값이다. 시험장 망의 값이 아니다.

## 구성
| 파일 | 역할 |
|---|---|
| `phy.py` | 2.4 GHz HT20 1SS air time. ath9k와 같은 공식(`preamble + ceil((8L+22)/N_DBPS)·4µs`), A-MPDU 길이, 제어 응답 속도를 계산한다 |
| `sim.py` | event loop, EDCA 경쟁(AIFS + backoff, 다른 station 전송 중 backoff 동결, 같은 slot이면 충돌 후 CW 2배와 retry bit), A-MPDU/BlockAck, 이상적 sniffer |
| `tcp.py` | handshake, MSS 협상, 초기 cwnd, slow start/CA, 송신 버퍼·수신 창 제한, Nagle, delayed ACK(Linux quickack 16, 40 ms / lwIP 2-segment, 250 ms fast timer) |
| `net.py` | device ↔ AP ↔ server. server는 유선 LAN(1 Gb/s 직렬화), 무선 LAN station, WAN(측정 RTT/2씩) 중 하나 |
| `apps.py` | LAN HTTP 업로드(우리 `scenario_upload.py` → `upload_server.py`), WAN HTTPS 업로드(base64 JSON 본문, TLS record overhead), 웹 페이지 로드 |
| `images.py` | 기준 페이지 이미지를 해상도별로 JPEG 인코딩해 업로드 바이트 수를 계산한다(OpenCV 기본 quality 95) |
| `scenario.py` | run 하나 → `.pcap`, `.labels.jsonl`, `.background.jsonl`, `.meta.json` |
| `dataset.py` | preset × (과목, MCS, 해상도 무작위) run 집합을 calib/eval로 나눠 병렬 생성 |

## 시나리오 변수와 근거
- **업로드 간격과 run 길이**: 수능 시행기본계획(2027학년도) 부록3의 시험 시간을 문항 수로 나눈 문항당 시간이다. 한 문항에 한 번 업로드한다고 가정했다.

  | 과목 | 계산 | 간격 |
  |---|---|---|
  | 국어 | 80분/45문항 | 106.7 s |
  | 수학 | 100분/30문항 | 200 s |
  | 영어 | 70분/45문항 | 93.3 s |
  | 탐구 | 30분/20문항 | 90 s |

- **해상도**: esp32-camera `framesize_t`의 VGA–UXGA. UXGA가 OV2640의 최대 해상도다.
- **MCS**: HT20 1SS MCS0–7 전부. 실측 rate 분포가 없어서 run마다 균등하게 뽑는다.
- **장치 profile**
  - `esp32cam`: lwIP 기본값(MSS 1440, 송신 버퍼 5760 B, BA window 6).
  - `linux_client`: Linux 기본값과 Python `http.client`(TCP_NODELAY, BA window 64).
- **브라우징(오탐 시험용 음성)**: HTTP Archive Web Almanac 2024 mobile page weight 분포(p10–p90)에서 페이지 크기를 뽑는다. 페이지 사이 대기 시간은 **0**(연속 로드)으로 두었다. 근거 있는 대기 시간 분포가 없어서 트래픽이 가장 많은 **최악 조건**을 택한 것이다.

## 구조적 가정 (결과 해석 시 주의)
1. **채널**
   - 채널 오류는 없고, 손실은 충돌로만 생긴다.
   - 우리 장치, AP, (선택) 서버 외에 경쟁하는 기기가 없다.
   - MCS는 run 안에서 고정이고 양방향이 같다.
2. **sniffer**
   - 이상적이라고 가정한다. 성공한 frame은 모두 보고, timestamp는 frame의 마지막 bit가 끝나는 시각이다.
   - 실제 USB 어댑터(RTL8188FTV)는 USB 집적 때문에 timestamp가 몰리거나 frame을 놓칠 수 있다. 실측으로 확인해야 한다.
3. **유선 구간**
   - LAN은 1 Gb/s 직렬화 지연만 넣고, switch와 서버 처리 시간은 0으로 둔다.
   - WAN은 측정한 RTT의 절반을 양방향에 각각 넣는다. jitter는 넣지 않는다.
4. **TCP**
   - 수신 창 autotuning, GRO에 의한 ACK 묶음, pacing, TSQ는 모델링하지 않는다. 손실이 없으므로 TCP 재전송도 없다.
   - FIN은 즉시 ACK한다.
5. **응용 계층**
   - WAN 업로드는 마지막 바이트가 ACK된 시점에서 끝난다. AI 응답 다운로드는 모델링하지 않는다(모델 지연을 알 수 없음).
   - HTTP 헤더와 JSON wrapper는 우리 LAN 요청 헤더 크기로 근사했다.
   - 웹 페이지는 GET 1개와 페이지 전체 크기의 응답 1개로 단순화했다. 하위 요청이 빠지므로 uplink 요청 바이트는 실제보다 적게 잡힌다(하한).
   - TLS ClientHello 크기는 Python/OpenSSL 3.5의 측정값(약 1.5 kB, PQ hybrid key share 포함)이다. 실제 브라우저 값은 다를 수 있다.
6. **이미지**
   - `data/ref/page_proxy.png`는 렌더링한 **대용 페이지**다. 실제 사진은 노이즈와 조명 차이 때문에 JPEG가 더 크다.
   - 그래서 합성 업로드 바이트는 **하한**이다. 탐지에는 불리한 쪽이다.

## 사용법
```bash
python -m tools.make_page_proxy --out data/ref/page_proxy.png
python -m tools.measure_app_bytes --out data/measured/app_bytes.json --tls-host api.openai.com ...
python -m tools.rtt_probe --host api.openai.com ... --n 15 --out data/measured/wan_rtt_devpc.json
python -m synth.netsim.scenario --preset esp32_lan --subject math --mcs 7 --resolution 1600x1200 --seed 0 --out data/synth/x.pcap
python -m synth.netsim.dataset --out data/synth/netsim_v1 --runs-per-preset 8 --browse-duration 300 --workers 24
python -m net_event.calibrate --index data/synth/netsim_v1/index.jsonl --split calib --rule-preset esp32_lan ... \
    --out-json data/measured/calib_netsim_v1.json --out-config configs/netsim_v1_derived.yaml
python -m net_event.evaluate --index data/synth/netsim_v1/index.jsonl --split eval \
    --config configs/netsim_v1_derived.yaml --detector rule --out data/measured/eval_netsim_v1_rule.json
```

## 결과 (netsim_v1: preset 6종 × 8 run, calib 24 / eval 24)
`configs/netsim_v1_derived.yaml`은 calib split에서 `net_event/calibrate.py`의 규칙으로 계산한 값이다. **합성 데이터에서 유도한 잠정값**이며, `default.yaml`의 TBD는 그대로 남겨 두었다.

유도값:

| 파라미터 | 값 | 유도 규칙 |
|---|---|---|
| `large_frame_bytes` | 1530 | lwIP의 full segment 크기 |
| `burst_gap_s` | 0.0737 s | 업로드 내부 IAT의 최댓값. 업로드 사이 최소 간격 89.6 s와 분리된다 |
| `min_burst_bytes` | 130,653 | |
| `min_large_ratio` | 0.924 | 85 / (85 + 7) |
| window model `step_s` | 1 s | |
| window model `l2` | 100 | LOSO log-likelihood 최대 |
| window model `threshold` | 0.306 | LOSO F1 최대 |

eval split 결과 (event 단위):

| preset | 업로드 수 | rule recall | window model recall | 오탐/시간 (둘 다) |
|---|---|---|---|---|
| esp32_lan / esp32_lan_wifi / esp32_wan / linux_wan | 545 | 1.000 | 1.000 | 0 |
| linux_browse (연속 브라우징만) | 0 | – | – | 0 |
| linux_browse_wan (브라우징 중 업로드) | 9 | **0.000** | 0.889 | 0 |

- **rule detector는 업로드 전용 기기에서는 완벽하다.** 하지만 같은 기기가 브라우징을 함께 하면 ACK 흐름 때문에 burst가 끊기지 않는다. 업로드가 큰 burst에 섞이면서 large ratio가 떨어져 모두 놓친다.
- **window model**(Lumos식 multi-timescale)은 이 경우도 대부분 잡는다(8/9).
  - 2026-11-27: detector가 학습 때와 달리 downlink feature를 0으로 넣던 버그를 고친 뒤 다시 평가한 값이다. 고치기 전에는 9/9였다.
  - 같은 수정 뒤 spark3 실측 평가(`docs/real_capture_spark3.md`)는 그대로였다(10/10, 오탐 4).
- 모든 수치는 위 가정 아래의 **합성** 결과이므로 실제 성능의 상한으로 봐야 한다. 실측 캡처로 다시 calibration해야 한다.
