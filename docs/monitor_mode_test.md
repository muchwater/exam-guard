# Monitor mode 실측: RTL8192EU + rtl8xxxu (2026-11-26, Rubik Pi 3)

## 결론 (2026-11-27 갱신)
- 이 동글은 monitor mode에서 **다른 기기 사이의 HT(802.11n MCS) 데이터 프레임을 하나도 host로 넘기지 못한다**. legacy 속도(11b 1 Mb/s, 11g 24 Mb/s)로 보낸 프레임만 잡힌다.
- **원인은 LDPC다** (아래 "원인 규명").
  - 보드와 AP가 둘 다 HT Capabilities에 LDPC를 광고하므로, 둘 사이의 HT 데이터는 LDPC로 부호화된다.
  - RTL8192E에는 LDPC 복호기가 없다.
  - 칩은 HT-SIG까지는 읽지만 데이터 CRC가 모두 실패한다. 칩 카운터로 확인했다: 업로드 중 HT CRC 오류 134건/s, HT 정상 0건.
- 동글에게 직접 온 HT 프레임은 받았다. AP가 LDPC를 못 받는 동글에게는 BCC로 보내기 때문이다.
- 데이터 내용은 어떤 드라이버로도 받을 수 없다. 복호기가 하드웨어에 없기 때문이다.
- **대신 같은 동글로 업로드를 잡는 방법을 찾았다 (대안 3).**
  - 데이터마다 돌아오는 BlockAck는 legacy 속도라 잡힌다. BlockAck의 순번(SSN + bitmap)으로 기기가 보내고 받은 MPDU 수를 정확히 복원한다.
  - 코드: `capture/ba_recon.py`
- 계획했던 RTL8188FTV도 LDPC가 없는 칩이다(미시험). 연결 상대가 LDPC를 쓰면 같은 문제가 난다.

## 원인 규명 (2026-11-27)
### 1. 칩 카운터: HT 프레임은 도착하지만 CRC가 모두 실패한다
- vendor 드라이버(`8192eu`)의 `/proc/net/rtl8192eu/<if>/read_reg`·`write_reg`로 수신 카운터를 0.2 s마다 읽었다.
  - PHY 카운터: BB `0xF90`(HT CRC 정상/오류), `0xF94`(legacy OFDM).
  - MAC 카운터: `0x664` RXERR_RPT(종류 선택 후 읽기).
  - 레지스터 위치는 vendor 드라이버 `hal/hal_com.c`의 `rtw_dump_phy_rx_counters`·`rtw_dump_mac_rx_counters`(N 계열)를 따랐다.
- 시험 내용: 보드가 15 s 쉬고, UXGA JPEG(499,952 B)를 6회 연속 업로드하고, 15 s 쉬고, 2 MB를 3회 다운로드했다. 경로는 대안 1과 같은 무선 경로다.
- 카운터는 채널의 모든 기기를 센다. 프레임 내용은 저장하지 않았다.

| 구간 | 길이 | PHY HT 정상 | PHY HT CRC 오류 | MAC HT 정상 | MAC HT 오류 | PHY legacy OFDM 정상 |
|---|---|---|---|---|---|---|
| 유휴 | 39.8 s | 0.0/s | 0.5/s | 0.2/s | 0.5/s | 73.6/s |
| 업로드 | 1.3 s | **0.0/s** | **134.3/s** | 0.0/s | 141.8/s | 574.1/s |
| 다운로드 | 3.1 s | **0.0/s** | **97.4/s** | 0.0/s | 100.3/s | 321.9/s |

- HT 프레임은 칩에 도착하고, HT-SIG를 읽은 뒤 데이터에서 실패한다. 필터 문제가 아니라 **복호 실패**다.
- 데이터: `data/raw/rtk_exp/base/` (보드, git 제외).

### 2. 왜 복호에 실패하나: LDPC
| 기기 | HT Capabilities | LDPC | RX STBC | 출처 |
|---|---|---|---|---|
| 보드 wlan0 (AP6256) | `0x0121` | **1** | 1 | 보드가 보낸 probe request (spark3·동글 캡처) |
| AP | `0x19ef` | **1** | 1 (TX STBC 1) | AP의 probe response |
| RTL8192EU | – | **없음** | 없음 | vendor `hal/rtl8192e/rtl8192e_hal_init.c`: `HAL_DEF_RX_LDPC` = FALSE, `iw phy` |

- 송신 측은 상대가 LDPC를 받을 수 있으면 LDPC로 보낼 수 있다. 보드와 AP는 서로 LDPC를 받는다.
- spark3(MT7925) 캡처에서 AP→보드 프레임은 STBC로 표시됐다. 이 radiotap은 FEC 종류를 표시하지 않는다.
- 앞서 `iw phy phy0 info`는 보드에 LDPC가 "없다"고 보여 줬다. 그러나 brcmfmac은 FullMAC이라 연결 IE를 firmware가 직접 만든다. 실제로 보낸 probe request에는 LDPC가 켜져 있었다.
- 동글이 AP에 연결됐을 때 자기에게 온 MCS 2·MCS 10 프레임은 받았다. AP는 LDPC를 못 받는 상대에게 BCC로 보낸다.
- 확인할 것(TBD): 드라이버 패치(`drivers/rtl8192eu_monitor_meta.patch`)로 RX descriptor의 LDPC 비트를 radiotap에 실어 직접 확인한다.

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

## 대안 3: BlockAck 순번으로 MPDU 수 복원 (같은 동글, 2026-11-27) → 채택
### 원리
- A-MPDU를 받은 쪽은 곧바로 BlockAck(BA)를 보낸다. BA는 legacy 속도(24 Mb/s)라 이 동글도 받는다.
- compressed BA에는 받는 쪽의 수신 기록이 들어 있다: 시작 순번(SSN)과 64-bit bitmap. bit i는 순번 SSN+i인 MPDU를 받았다는 뜻이다(IEEE 802.11-2016 9.3.1.9.3).
- 송신 기기는 TID마다 QoS 데이터 MPDU에 순번을 하나씩 늘려 붙인다. 그래서 두 BA 사이에 "확인된 최대 순번"이 늘어난 양이 그동안 보낸 새 MPDU 수다.
  - 중간의 BA를 놓쳐도 다음 BA에서 누적값이 맞는다. 단, 그 사이에 2048개(순번 공간의 절반)보다 적게 지나가야 한다.
- 방향:
  - RA = 팀 기기인 BA → 기기가 **보낸**(uplink) MPDU
  - TA = 팀 기기인 BA → 기기가 **받은**(downlink) MPDU
- 얻는 것은 MPDU **수와 시각**이다. MPDU **크기**는 알 수 없다.
- `capture/ba_recon.py`가 새 MPDU마다 가짜 QoS 데이터 프레임(헤더만, 크기 정보 없음)을 만든다. 이 프레임은 앞 A-MPDU 구간(최대 aPPDUMaxTime 5.484 ms)에 고르게 놓인다.
  - 원래 프레임도 그대로 남긴다. 그래서 기존 도구(`tools.real_session`, `net_event.*`, `replay`)가 그대로 동작한다.

### 검증: spark3 캡처 (데이터도 복호됨, 같은 캡처 안의 BA와 비교)
| 활동 | 횟수 | 실제 uplink MPDU(고유 순번) | BA 복원 uplink | 실제 downlink | BA 복원 downlink |
|---|---|---|---|---|---|
| 업로드 115 KB | 5 | 86–88 | 86–88 | 50–58 | 50–58 |
| 업로드 529 KB | 5 | 303–372 | **376–378** | 139–172 | 157–172 |
| 다운로드 2 MB | 5 | 634–1053 | 643–1053 | 1362–1404 | **1405–1409** |

- 차이가 난 곳은 spark3가 데이터 프레임을 놓친 경우다. BA 복원값이 더 일정하고 크기와 맞는다: 529 KB ÷ 1428 B(MSS) ≈ 370.
- 업로드 중 uplink MPDU의 실제 크기 중앙값은 1546 B, 다운로드 중(TCP ACK)은 118 B였다.

### 크기 추정은 실패: Broadcom frameburst
- RTS의 NAV로 MPDU 크기를 추정해 보았다. NAV에서 응답 시간을 뺀 데이터 시간을 MPDU 수로 나누는 방식이다. 실제 크기와 상관계수가 0.16에 그쳤다.
- 원인: 보드는 RTS/CTS 한 번 뒤에 A-MPDU를 여러 개 연달아 보낸다. 예: 13개 → BA → 30개 → BA.
  - RTS의 NAV는 첫 A-MPDU만 덮는다.
  - spark3 TSFT로 확인했다.
- vendor 드라이버의 radiotap에는 TSFT가 없다. host 시각은 USB 지연이 섞여 µs 단위 간격을 잴 수 없다.

### 실측: 보드 동글로 보정 세션 녹화 (`dongle_calib1`, seed 1)
- 보드 혼자 수행했다: 동글(vendor 드라이버) 캡처와 `tools/ota_session.py --plan calib`을 동시에 돌렸다.
  - 업로더와 sniffer가 같은 보드 시계를 쓰므로 시계 차이는 0이다(`tools.real_session --offset 0`).
  - 캡처 21,576 프레임, kernel drop 0
  - 복원 결과: uplink 49,489 MPDU, downlink 118,328 MPDU

| 활동 | 횟수 | uplink MPDU | downlink MPDU | uplink 비율 |
|---|---|---|---|---|
| 업로드 640x480 (122 KB) | 4 | 90–95 | 48–64 | 0.59–0.65 |
| 업로드 800x600 (183 KB) | 4 | 134–138 | 71–78 | 0.64–0.66 |
| 업로드 1600x1200 (500 KB) | 4 | 356–361 | 135–167 | 0.68–0.73 |
| 웹 페이지 로드 (Web Almanac p10–p90) | 48 | 211–2548 | 345–6340 | **0.23–0.38** |
| 큰 ping (1400 B, 0.5 s, 20 s) | 4 | 40–43 | 39–61 | 0.41–0.51 |
| 브라우징 중 업로드 | 4 | 645–1581 | 1410–2790 | **0.30–0.36** |

- 업로드의 uplink MPDU 수는 크기에 비례한다(약 1400 B당 1개).
- TCP는 데이터 두 개마다 ACK 하나를 보낸다. 그래서 업로드는 uplink가 많고(비율 0.59 이상), 다운로드는 downlink가 많다(0.38 이하).
- 브라우징 중 업로드는 페이지의 downlink에 묻혀 비율이 다운로드와 겹친다.
  - RTS NAV 최대값도 페이지 로드(270–5142 µs)와 겹쳐 구분하지 못한다.

### count rule detector
- `net_event.detector.CountRuleDetector`, 이름 `count_rule`. 크기 없이 수만 쓴다.
- 판정: uplink burst 안에서 uplink MPDU 수가 `min_up_mpdus` 이상이고, uplink 비율 n_up / (n_up + n_down)이 `min_up_share` 이상이면 업로드 event다.
- 보정 규칙(`net_event.calibrate`):
  - `min_up_mpdus`: 보정 업로드를 모두 지키는 최소값
  - `min_up_share`: 후보 burst에서 F1이 최대가 되는 값

### 평가: 세션 단위 교차 (보정 세션 ≠ 평가 세션)
- 같은 계획(`--plan calib`)으로 seed만 바꿔 세션을 두 번 녹화했다: `dongle_calib1`(seed 1), `dongle_calib2`(seed 2).
- 세션마다 업로드는 28회다. 해상도 6종 × 4회에 브라우징 중 업로드 4회가 더해진다.
- 한쪽 세션으로 보정하고 다른 쪽 세션으로 평가했다. 방향을 바꿔 두 번 했다.
- 결과 파일:
  - 세션 1 → 2: `data/measured/eval_real_dongle_v1_*.json`, `configs/real_dongle_v1_derived.yaml`
  - 세션 2 → 1: 같은 명령에 split만 바꿨다.

| 보정 → 평가 | detector | 일반 업로드 | 브라우징 중 업로드 | 오탐 | 보정값 |
|---|---|---|---|---|---|
| 1 → 2 | **count_rule** | **24/24** | 0/4 | **0** | MPDU ≥ 90, 비율 ≥ 0.592 |
| 2 → 1 | **count_rule** | 22/24 (VGA 2회 놓침) | 0/4 | 1 (약 3.7/h) | MPDU ≥ 83, 비율 ≥ 0.626 |
| 1 → 2 | window_model | recall 0.929 (전체 28회) | | 44/h | |
| 2 → 1 | window_model | recall 0.786 (전체 28회) | | 33/h | |

- **count rule이 일반 업로드를 46/48 잡았고 오탐은 32분에 1건이었다.**
  - 이 오탐은 페이지 로드 중 downlink BA를 놓친 구간이다. uplink 84 MPDU, downlink 0으로 집계됐다.
  - 놓친 2회는 가장 작은 VGA(122 KB) 업로드다. 그 세션의 VGA 비율이 0.59–0.65여서 0.626 아래로 내려갔다.
- **브라우징 중 업로드(0/8)는 수만으로는 구분할 수 없다.** 크기 정보가 필요하다(다음 단계 2).
- window model은 페이지 로드에서 오탐이 많았다. 수만 들어간 가짜 프레임에서는 크기 feature가 의미가 없기 때문이다.
- rule detector(크기 기반)는 이 스트림에 쓰지 않는다. 가짜 프레임의 크기가 모두 같아서다.

## 드라이버 패치: 동글이 LDPC를 직접 보고 (2026-11-27)
- `drivers/rtl8192eu_monitor_meta.patch`: vendor 드라이버(Mange/rtl8192eu-linux-driver c629cc9)에 7줄을 더했다.
  - radiotap TSFT: RX descriptor의 TSFL
  - monitor mode에서 CRC 오류 프레임을 버리지 않음
  - radiotap MCS 필드에 FEC(LDPC) 표시
- 보드에서 빌드했다: `~/drivers/rtl8192eu-eg`, `make CONFIG_PLATFORM_I386_PC=n CONFIG_PLATFORM_ARM_AARCH64=y`.
- 시험: `REG_RCR`에 ACRC32를 켜고(`0x9000382f → 0x9000392f`), 위 counter 시험과 같은 트래픽을 보냈다.
  - 개인정보: CRC 오류 프레임은 바로 뒤의 정상 프레임이 팀 기기의 BlockAck일 때만(6 ms 이내) 메타데이터를 남겼다. 나머지는 개수만 셌다.

| 귀속 | 수 | MCS | LDPC | SGI | STBC |
|---|---|---|---|---|---|
| 보드 → AP (uplink) | 4 | 7, 4 | **1** | 1 | 0 |
| AP → 보드 (downlink) | 2 | 6 | **1** | 1 | 1 |

- **원인 확정:** 동글이 HT-SIG에서 읽은 LDPC 비트가 1이었다. 보드와 AP 사이의 HT 데이터는 LDPC다.
- **PPDU별 크기는 얻을 수 없다.**
  - CRC 오류 프레임 6,556개가 올라왔지만 HT는 11개뿐이었다. 업로드 중 HT PPDU는 초당 약 130개다.
  - 칩은 대부분의 LDPC PPDU를 host로 넘기지 않는다. 넘긴 경우에도 길이가 1.9–8.1 KB로 PPDU 전체(약 5 ms × 72 Mb/s ≈ 44 KB)보다 훨씬 짧았다.
- **TSFT는 쓸 수 있다:** 모든 프레임에 붙었고 단조 증가했다. host 시각과의 차이는 중앙값 169 µs로, USB 지연이다.
  - 같은 TXOP 안 제어 프레임 사이 간격(CTS→BA, BA→BA)에서 A-MPDU의 공중 시간을 구할 수 있다.
  - 이를 BA의 새 MPDU 수로 나누면, MPDU당 공중 시간 중앙값이 업로드 179 µs, 페이지 로드 32 µs였다(`data/raw/rtk_exp/probe1`).
  - 179 µs는 1546 B를 MCS7 SGI로 보낸 시간(약 171 µs)과 맞는다.
- 결과 파일: `data/measured/rtl8192eu_ldpc_diagnosis.json`

## 다음 단계
1. **데모 네트워크 경로를 보드 동글로 바꿀 수 있다:** 동글 캡처 → `capture.ba_recon` → `count_rule`.
   - 녹화 절차: `docs/real_capture_runbook.md`의 "보드 동글만으로 녹화하는 경로"
   - spark3는 크기까지 필요할 때만 쓴다.
2. **브라우징 중 업로드:** 패치 드라이버의 TSFT로 MPDU당 공중 시간을 구하면 데이터 MPDU(약 180 µs)와 ACK MPDU(약 30 µs)를 나눌 수 있다.
   - 할 일: 패치 드라이버로 보정 세션을 다시 녹화하고, `ba_recon`이 이 값을 가짜 프레임 크기에 반영하게 한다.
3. **일반화 조건 (TBD):**
   - 상대 AP가 BlockAck를 legacy 속도로 보내야 한다. 이 AP는 24 Mb/s였다.
   - 기기가 A-MPDU를 써야 한다(BA 합의).
   - 다른 AP와 휴대폰에서 확인이 필요하다.
4. **동글 교체가 필요하다면** LDPC를 받는 칩이어야 한다: MT7612U(`mt76x2u`), MT7921AU(`mt7921u`).
   - AR9271(`ath9k_htc`)은 LDPC가 없어 같은 문제가 난다. 앞선 권장에서 뺐다.


## 재현
```bash
python -m capture.monitor_setup --check
python -m capture.monitor_setup --config configs/site_rubik.yaml          # monitor, channel 2
python -m capture.sniffer --config configs/site_rubik.yaml --out data/raw/smoke_monitor.pcap --duration 15 &
ping -I wlan0 -c 20 -i 0.5 -s 1400 192.168.68.1; ping -I wlan0 -c 10 -i 0.3 192.168.68.1
python -m capture.monitor_setup --config configs/site_rubik.yaml --restore
```
