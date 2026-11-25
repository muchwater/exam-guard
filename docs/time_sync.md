# 두 입력(pcap, 영상)의 timestamp 동기화

## 기본: 같은 host, 같은 시계
- **pcap**: libpcap이 kernel 수신 시각을 Unix epoch(초+마이크로초)로 기록한다(`--time-stamp-precision=micro`). 이 값은 host의 CLOCK_REALTIME이다.
- **영상**: `vision.record`와 `demo.live`가 프레임을 받은 직후 `time.time()`(같은 CLOCK_REALTIME)을 `<video>.frames.csv`에 기록한다.
- sniffer와 카메라가 **둘 다 Rubik Pi 3에서** 돌면 변환 없이 같은 시간축에 놓인다. replay는 두 스트림을 timestamp 순서로 병합한다(`replay.run.merged_stream`, 같은 시각이면 패킷을 먼저). 그래서 모든 모듈이 라이브 때와 같은 순서로 입력을 받는다.
- detector, segmenter, fusion은 **입력 timestamp만** 쓰고 wall clock은 쓰지 않는다. 그래서 같은 녹화를 replay하면 같은 결과가 나온다.

## 보정 1: 카메라 파이프라인 지연
`time.time()`은 노출 시점보다 늦다. `tools/sync_check.py`로 측정한 중앙값을 `vision.camera.pipeline_delay_s`에 넣으면 녹화할 때 빼고 기록한다. 녹화 당시 값이 `TBD`였다면 `<video>.meta.json`에 "빼지 않음"이 남는다. 이 경우 replay가 config 값을 대신 적용한다.
측정값에는 모니터 표시 지연도 포함되므로 실제 지연보다 크게 나올 수 있다. 측정값의 p10–p90 폭은 `fusion.pre_s`의 근거가 된다.

## 보정 2: 다른 host에서 녹화한 경우
`tools/clock_offset.py`로 오프셋을 측정한다(최소 RTT 표본, 오차 ≤ RTT/2). 이 값을 `replay.run --video-offset <s>`로 영상 timestamp에 더한다.

## 보정 3: 업로드 정답 라벨
uploader 기기 시계로 기록된 라벨은 `net_event.measure --offset`(uploader − sniffer)으로 sniffer 시계로 옮긴다.
