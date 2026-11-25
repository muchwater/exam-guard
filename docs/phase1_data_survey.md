# Phase 1 — 공개 데이터 조사 결과 (2026-11-25)

조사 방법: 각 후보는 데이터셋 페이지(또는 Kaggle API metadata, GitHub repo, 논문 본문)를 직접 열어 확인했다. "검증" 열의 ✔는 이 프로젝트에서 원문을 직접 확인한 항목, △는 조사 agent의 1차 확인만 거친 항목이다.

## (A) 이미지 업로드 시 Wi-Fi 패킷 기록

용어
- **monitor mode**: NIC가 자기 앞으로 온 frame만이 아니라 채널에서 들리는 모든 802.11 frame을 OS로 올려 주는 동작 모드.
- **radiotap**: monitor mode 캡처 시 드라이버가 frame마다 붙이는 metadata header(RSSI, rate, channel, flags 등).

**결론: 우리 용도(monitor mode + radiotap + 이미지 업로드 구간 라벨)에 맞는 공개 데이터는 없다.** 직접 캡처가 필요하다(Phase 2-A).

| 이름 | 출처 | license | 내용(형식, 규모, 라벨) | 우리 용도 적합성 | 문제점 | 검증 |
|---|---|---|---|---|---|---|
| WiFinger (NDSS 2026) | https://huggingface.co/datasets/Gonewinddd/WiFinger | HF tag "mit" (별도 LICENSE 문서 없음) | pcap 약 1.6 GB, 이벤트 trigger 시각 `.txt`(`epoch,label`), `target_mac.json`. agent 확인 결과 DLT 127(radiotap), 암호화된 QoS data frame | 2순위: parser·feature를 실제 radiotap 캡처로 검증하는 데 사용 | **카메라 없음**(플러그·스피커 등 IoT 이벤트). dataset card에는 monitor mode/radiotap 언급이 없고 pcap header 확인은 agent만 함 | card ✔ / pcap header △ |
| Lumos (USENIX Sec 2022) | 논문 본문, https://github.com/rahul-anand/Lumos | 코드 MIT | 논문: "We have released our code on https://bit.ly/lumos-code". repo에는 코드(RPi/, Xcode/)만 있음 | **데이터 없음**. 설계 근거로만 사용(multi-timescale window δ∈{1,2,3,5,10,…}s, Algorithm 1) | 캡처 데이터 미공개 | ✔ |
| Mon(IoT)r (IMC 2019) | moniotrlab.khoury.northeastern.edu/publications/imc19/ | "non-profit research and education" 용도, 신청 후 서명 필요 | gateway의 tcpdump, MAC별 pcap. 카메라·도어벨 15종, "take picture" 등 상호작용 라벨 | 3순위: IP 계층에서 업로드 burst 모양 참고용 | 802.11/radiotap 없음. 이메일 신청과 서명이 필요해 일정 리스크 | △ |
| UNSW IoT traces (TMC 2018) | iotanalytics.unsw.edu.au/iottraces.html | "MIT-0" | Ethernet pcap(DLT 1) 20일치, 카메라 6종 | 3순위 | 활동 라벨 없음, 802.11 아님 | △ |
| D-Link IoT (Data in Brief 2021) | data.mendeley.com/datasets/84cc8grtkt/1 | CC BY 4.0 | 카메라 8종. radiotap 폴더는 probe request뿐이고 data는 Ethernet | 3순위 | 라벨 없음 | △ |
| YourThings, CIC IoT 2022, IoT-23, AWID3, CRAWDAD 등 | 각 공식 페이지 | 다양(라이선스 미기재 다수) | gateway 캡처 또는 공격 데이터 | 부적합 | 업로드 라벨이 없거나 802.11 header가 없음 | △ |
| 숨은 카메라 탐지 논문(DeWiCam, MotionCompass, SnoopDog 등) | 각 논문 본문 | — | — | **데이터 미공개** | — | △ |

참고: WiFinger 논문은 일부 카메라가 사진을 로컬에만 저장해 네트워크 트래픽이 발생하지 않았다고 보고한다(agent 확인 △). 또한 **휴대폰 LTE나 Bluetooth로 전송하는 경우는 Wi-Fi sniffer로 원리상 탐지할 수 없다.** 이 시스템의 탐지 범위는 "Wi-Fi로 업로드하는 경우"로 한정된다.

## (B) 시험 부정행위 이상 행동 영상 / pose keypoint

**결론: 대면 시험의 2D pose 시퀀스를 명확한 license로 공개한 데이터는 없다.** 대면 시험 영상 중 license가 명확한 것은 ExamCheat-MultiV 하나다. 우리 대상 행동(AI 안경 사용 시의 자세)을 담은 데이터는 없다.

| 이름 | 출처 | license | 내용 | 적합성 | 문제점 | 검증 |
|---|---|---|---|---|---|---|
| ExamCheat-MultiV | kaggle.com/datasets/rimmajeed/examcheating-multiv-video-based-dataset | **CC BY-SA 4.0** (Kaggle API `licenseName`) | 영상 637개(설명 기준), 약 65명, 5 class(Normal, Copying, Gesture Sharing, Hidden Notes, Mobile Phone), 다중 시점, 연출 촬영, 25.8 GB | 1순위(대면 영상). 우리 pose 모델로 keypoint를 추출해 normal/phone 비교용으로 사용 | 논문 없음. 설명에 있는 "CSV annotations"가 파일 목록에 없음(agent 확인). 라벨은 일부 파일명 prefix뿐. ShareAlike 조건 | 메타데이터 ✔ / 파일 목록 △ |
| Hashemite Univ. Student cheating behavior | kaggle.com/datasets/fairouzfarouq/student-cheating-behavior-dataset | **"Unknown"** | 37개 영상, 8명, 1920×1080 24 fps, 약 3 m 거리, 5 class | 1순위 후보지만 license 불명 | 저자에게 사용 허락을 받아야 함 | ✔ |
| NTU RGB+D 60/120 | rose1.ntu.edu.sg/dataset/actionRecognition/ | "released for academic research only", 신청 후 Release Agreement. 지도교수 이메일 필요 | 3D skeleton 25 joint. 관련 class: A11 reading, A12 writing, A29 play with phone/tablet, A35 nod head/bow 등 | 4순위(pretraining) | 연출 촬영, 대부분 서 있는 동작. 신청 승인까지 시간 필요 | ✔ |
| OEP (MSU, IEEE TMM 2017) | cvlab.cse.msu.edu/oep-dataset.html (Wayback) | license 없음("please cite") | 24명, 웹캠 + 머리 착용 카메라, 부정행위 구간 gt | 2순위(온라인 시험) | 1인, 상반신, 온라인 환경 | △ |
| SCB-Dataset | github.com/Whiffe/SCB-dataset | "non-commercial use only", 저작권자가 모든 권리 보유 | 이미지 13k장, 19 class(read, write, bow the head, turn the head 등) | 3순위 | 이미지만 있고 keypoint 없음. 원본 영상 저작권 불명 | △ |
| Kaggle/IEEE DataPort 시험장 이미지 세트 다수 | 각 페이지 | CC0, Apache 2.0, 유료 구독 등 | YOLO 형식 이미지 | 낮음 | 시퀀스 없음. 일부는 YouTube·TikTok 스크랩 | △ |
| Cheatomaly(2026), CAN(2025) | 논문 본문 | "upon reasonable request" | 대면 시험 영상 94개 / 이미지 17k장 | 요청 시에만 | 공개 안 됨 | △ |

## 다음 단계로의 함의
1. 패킷: 팀 소유 기기로 직접 캡처한다(docs/capture_protocol.md). WiFinger는 radiotap parser 검증에만 사용한다.
2. 행동: 팀원 연기 녹화가 1순위다(docs/recording_protocol.md). ExamCheat-MultiV(CC BY-SA)는 "normal vs phone" 대비 확인용이다. NTU는 신청 승인을 받으면 pretraining에 쓴다.
