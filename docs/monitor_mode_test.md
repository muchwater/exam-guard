# Monitor mode 실측: RTL8192EU + rtl8xxxu (2026-11-26, Rubik Pi 3)

## 결론
- 이 동글은 monitor mode에서 **다른 기기 사이의 HT(802.11n MCS) 프레임을 하나도 잡지 못했다**. legacy 속도(11b 1 Mb/s, 11g 24 Mb/s)로 보낸 프레임만 잡혔다.
- 이미지 업로드는 대부분 HT 데이터 프레임이다. 그래서 이 동글로는 packet 크기·간격 feature를 만들 수 없다.
- 칩 자체는 HT를 받는다. 동글을 AP에 연결하면 자기에게 오는 MCS 2 프레임을 정상 수신했다.
- 계획했던 RTL8188FTV도 같은 `rtl8xxxu` 드라이버를 쓴다. 따라서 같은 문제가 날 위험이 있다(미시험).

## 환경
| 항목 | 값 | 출처 |
|---|---|---|
| 보드 | Ubuntu 24.04.5, kernel 6.8.0-1089-qcom | `/etc/os-release`, `uname -r` |
| 동글 | RTL8192EU (USB `0bda:818b`), 2T2R, driver `rtl8xxxu`, firmware `rtl8192eu_nic.bin` | `lsusb`, `dmesg` |
| 동글 HT 능력 | HT20, RX HT20/HT40 SGI, **No RX STBC**, LDPC 표시 없음 | `iw phy phy1 info` |
| AP | 채널 2 (2417 MHz), HT40(secondary above), RX LDPC, TX STBC | `iw dev wlan0 scan dump` |
| 시험 트래픽 | 보드 wlan0(AP6256, 2.4 GHz caps: HT20, No RX STBC, LDPC 없음) → gateway ping | `iw phy phy0 info` |
| 수신 세기 | 보드 프레임 -15 ~ -21 dBm, AP 프레임 -27 ~ -30 dBm (동글이 보드 바로 옆) | radiotap |

- 개인정보 규칙: 저장 대상은 보드 wlan0 MAC 하나뿐이다(`configs/site_rubik.yaml`, kernel BPF + Python 재확인). 그 외 기기의 프레임은 디스크에 남지 않는다.
- 트래픽: 1400 B ping 20회(0.5 s 간격)와 56 B ping 10회(0.3 s 간격)를 `ping -I wlan0`으로 보냈다. 모두 응답을 받았다(손실 0%).

## 결과
조건마다 바꾼 것은 한 가지뿐이다.

| 조건 | 저장 프레임 | HT 데이터 프레임 | 잡힌 것 |
|---|---|---|---|
| monitor, `set channel 2` (no HT) | 57 | **0** | RTS 19, CTS 20, ACK 7, QoS-null 4, ARP 3, 그 외 mgmt (전부 1 또는 24 Mb/s) |
| monitor, `set channel 2 HT20` | 66 | **0** | 위와 같은 종류 |
| 동글을 AP에 연결 + 같은 phy에 monitor vif 추가 | 68 | **0** | 위와 같은 종류 |
| 동글을 AP에 연결, 동글 자신이 받음 (`station dump`) | – | rx **MCS 2** (19.5 Mb/s), -28 dBm | 1400 B ping 응답 20/20 |

- 수신 세기는 원인으로 볼 수 없다. AP가 보드에 보낸 MCS 2 SGI 프레임(보드 rx bitrate 21.6 Mb/s)도 잡히지 않았다. 그런데 같은 세기(-28 dBm)의 AP 프레임은 CTS(24 Mb/s)로도 잡혔고, 동글에게 직접 온 MCS 2 데이터로도 받았다.
- 드라이버 소스도 원인을 설명하지 못한다. `rtl8xxxu_configure_filter`(v6.8)의 RX filter는 FIF 플래그로만 동작하고, 속도별로 거르는 설정이 없다. RX 경로는 HT 프레임을 `RX_ENC_HT`로 표시해 그대로 올린다. 칩 안의 어디에서 HT 프레임이 버려지는지는 밝히지 못했다.

## RTS duration으로 데이터 형식 역산
RTS는 legacy 24 Mb/s라 항상 잡힌다. RTS의 duration(NAV)은 뒤따르는 교환 전체의 길이다.

NAV = 3·SIFS + CTS + DATA + BA

보드가 보낸 RTS의 duration은 158, 306, 322 µs 세 묶음이었다. 이를 `synth/netsim/phy.py`의 시간 식으로 풀었다.

| 관측 NAV | 데이터 프레임 | 정확히 맞는 후보 |
|---|---|---|
| 322 µs | 1400 B ping (MPDU 1478 B + FCS, A-MPDU 서브프레임) | **MCS7** (데이터 220 µs = 36 + 46×4) |
| 158 µs | 56 B ping (MPDU 134 B + FCS) | MCS6 또는 **MCS7** |

- 이 계산은 CTS와 BA에 ERP-OFDM signal extension 6 µs가 붙는다고 가정했다(IEEE 802.11 ERP, 2.4 GHz).
- 이 가정을 빼면 322 µs에 정확히 맞는 후보가 없다(MCS6과 54 Mb/s는 330 µs, MCS7은 310 µs).
- 따라서 데이터는 HT20 MCS7로 실제로 전송됐고, 동글이 받지 못한 것이다.
- 부수 발견: `synth/netsim/phy.py`에는 2.4 GHz OFDM PPDU의 signal extension 6 µs가 빠져 있다. PPDU당 6 µs이므로 크기·간격 feature에 미치는 영향은 작다(TBD: 반영 여부).

## 대안 1: RTS의 NAV로 업로드 구분 (같은 동글, 2026-11-26)
### 왜 RTS가 보이나
- AP 비콘의 HT Operation이 `HT protection: nonmember`였다(`iw dev wlan0 scan dump`). 이 모드에서는 11n 기기가 HT 전송 앞에 legacy 보호 프레임(RTS/CTS 또는 CTS-to-self)을 보낸다.
- 보호 프레임은 legacy 속도라 이 동글로도 잡힌다. 보드(Broadcom)는 작은 ping까지 모든 HT 전송을 RTS로 보호했다.
- 따라서 이 신호가 있는지는 **AP의 protection 모드와 송신 기기의 구현에 달려 있다**. 주변에 non-HT 기기가 없어 protection이 꺼지면 사라질 수 있다.

### 시험 방법
- **업로드 경로:** 보드의 유선 포트에 macvlan을 만들어 별도 netns에 넣고, 그 안에서 `capture.upload_server --host ::`를 띄웠다. 보드 wlan0에서 IPv6 link-local 주소(`%wlan0`)로 업로드했다.
  - 경로: wlan0 → AP → 유선 → 보드. 우리 기기만 쓴다.
  - RTT는 약 9 ms로, 유선 직결(1 ms 미만)이 아니라 무선 경로임을 확인했다.
- **업로드 내용:** `data/ref/page_proxy.png`를 JPEG(quality 95)로 인코딩했다. 640x480(114,993 B) 5회와 1600x1200(528,833 B) 5회를 4초 간격으로 보냈다.
- **비교군:** 같은 경로로 2 MB 다운로드를 5회 했다.
- **정답 시각:** 업로드 시각과 캡처 시각이 같은 보드 시계라 동기화 오차가 없다.

### 결과
| | 횟수 | 회당 보드 RTS | 회당 NAV 합 | 회당 NAV 최대 |
|---|---|---|---|---|
| 업로드 115 KB | 5 | 4–9 | 2.1–6.1 ms | **906–2730 µs** |
| 업로드 529 KB | 5 | 7–11 | 9.1–23.9 ms | **3770–5514 µs** |
| 다운로드 2 MB | 5 | 42–65 (TCP ACK 송신) | 8.8–14.5 ms | **422–742 µs** |
| 전송 없음 | 72 s | 1 | – | 150 µs |

- **긴 NAV가 기준이 된다:** 0.8 ms 이상의 NAV는 업로드에서만 나왔다. 업로드는 10/10이 해당했고, 다운로드는 0/5였다. 긴 NAV는 큰 업링크 A-MPDU를 뜻한다(최대값 5.5 ms ≈ PPDU 상한 5484 µs).
- **여유가 작다:** 115 KB 업로드의 최소값 906 µs와 다운로드의 최대값 742 µs 차이가 크지 않다. TCP slow start 초기에는 A-MPDU가 작기 때문이다.
- **NAV 합으로는 구분이 안 된다.** 업로드와 다운로드의 범위가 겹친다.
- **포착률이 낮다:** 529 KB 업로드의 데이터 전송 시간은 MCS7로 약 70 ms인데, 잡힌 NAV 합은 9–24 ms였다.
  - RTS 뒤 44 µs(RTS 전송 시간 + SIFS)에 CTS가 오는 쌍이 7–12 ms 간격으로 띄엄띄엄 보였다. 그 사이의 RTS/CTS 쌍은 통째로 놓친 것으로 보인다.
  - 업로드 크기를 이 값으로 추정하기는 어렵다.
- **TSFT는 쓸 수 없었다:** 이 드라이버의 radiotap TSFT는 0.24 s 안에서 수백 초씩 튀었다.

### 판단
- "언제"를 알려 주는 보조 신호로는 쓸 수 있다.
- 다만 세 가지 제약이 있다: protection 모드에 의존하고, 포착률이 낮고, 다운로드와의 여유가 작다.
- 현재 detector(크기와 간격 기반)를 그대로 대체할 수는 없다.

## 대안 2: Realtek vendor 드라이버 → 실패
### 준비
- 보드에 `linux-headers-6.8.0-1089-qcom`을 apt로 설치했다.
- `Mange/rtl8192eu-linux-driver`(commit c629cc9, 2026-09-06, `CONFIG_WIFI_MONITOR = y`)를 `~/drivers`에 받아 빌드했다(사용자가 실행).
- 설치하지 않고 이번 부팅에만 올렸다:
```bash
cd ~/drivers/rtl8192eu-linux-driver
make -j8 CONFIG_PLATFORM_I386_PC=n CONFIG_PLATFORM_ARM_AARCH64=y
sudo rmmod rtl8xxxu && sudo insmod ./8192eu.ko      # 시험 후: sudo rmmod 8192eu && sudo modprobe rtl8xxxu
```

### 결과
- **monitor mode, 2번 채널 20 MHz, 같은 ping 시험:** 저장된 프레임은 58개였고 **HT 데이터 프레임은 0개**였다.
  - 잡힌 것: BA 30, CTS 15, RTS 12, 1 Mb/s 데이터 1. 모두 legacy 속도다.
  - BlockAck가 잡히기 시작한 것만 rtl8xxxu와 다르다.
- **수신 필터 레지스터 (monitor 상태, 드라이버의 `/proc/net/rtl8192eu/<if>/read_reg`):**
  - RCR = `0x9000382f`: 모든 물리 주소 수신, BSSID 검사 없음.
  - RXFLTMAP2(데이터) = `0xffff`: 모든 subtype 허용.
  - 필터 설정만 보면 "모두 받기"다.
- **managed 연결 대조:** 같은 동글이 AP에 연결되면 자기에게 온 프레임을 **MCS10**(2SS)으로 받았다(`rx_signal`).
  - 같은 시점에 보드 wlan0은 부하 시 rx MCS4–7, tx 72.2 Mb/s(MCS7 SGI)였다.
  - 따라서 전파 환경이나 수신 성능의 문제가 아니다.

### 결론
- 드라이버 두 종류(`rtl8xxxu`, vendor)에서 결과가 같았고, 필터 레지스터도 모두 받기로 설정돼 있었다.
- 그런데도 칩은 다른 기기 사이의 HT 데이터(A-MPDU)를 host로 넘기지 않는다. **RTL8192EU로는 HT 데이터 캡처가 불가능**하다고 판단한다.
- 시험 후 원래 드라이버(`rtl8xxxu`)로 되돌렸다.

## 다음 단계
0. **(2026-11-26 추가)** 연구실 머신 spark3의 MT7925로 HT 데이터 캡처에 성공했다(`docs/real_capture_spark3.md`). 오프라인 데모용 패킷은 이 방식으로 기록한다.
1. **동글 교체 (권장)**: 보드 커널에 드라이버와 firmware가 이미 있는 칩을 쓴다. 산 뒤에는 이 문서의 시험을 그대로 반복해 HT 프레임이 잡히는지 확인한다.
   - MediaTek `mt76x2u`(MT7612U, `mt7662u.bin` 있음), `mt7921u`(MT7921AU, `WIFI_RAM_CODE_MT7961_1.bin` 있음)
   - Atheros `ath9k_htc`(AR9271, `htc_9271.fw` 있음)
2. 대안 1(RTS NAV)은 새 동글이 오기 전까지, 또는 새 동글도 실패할 때를 위한 보조 수단으로 남긴다.

## 재현
```bash
python -m capture.monitor_setup --check
python -m capture.monitor_setup --config configs/site_rubik.yaml          # monitor, channel 2
python -m capture.sniffer --config configs/site_rubik.yaml --out data/raw/smoke_monitor.pcap --duration 15 &
ping -I wlan0 -c 20 -i 0.5 -s 1400 192.168.68.1; ping -I wlan0 -c 10 -i 0.3 192.168.68.1
python -m capture.monitor_setup --config configs/site_rubik.yaml --restore
```
