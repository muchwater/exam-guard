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

## 다음 단계
1. **동글 교체 (권장)**: 보드 커널에 드라이버와 firmware가 이미 있는 칩을 쓴다. 산 뒤에는 이 문서의 시험을 그대로 반복해 HT 프레임이 잡히는지 확인한다.
   - MediaTek `mt76x2u`(MT7612U), `mt7921u`(MT7921AU, firmware `WIFI_RAM_CODE_MT7961_1.bin` 있음)
   - Atheros `ath9k_htc`(AR9271, firmware `htc_9271.fw` 있음)
2. **대안 (부분적)**: RTS/CTS의 NAV로 데이터 크기를 추정한다. legacy 속도라 이 동글로도 잡힌다. 단, 송신 기기가 RTS/CTS를 쓸 때만 가능하다.
3. **비권장**: 보드에 Realtek vendor 드라이버(out-of-tree, DKMS)를 설치한다. 외부 커널 코드이고, 이 문제가 해결된다는 근거도 아직 없다.

## 재현
```bash
python -m capture.monitor_setup --check
python -m capture.monitor_setup --config configs/site_rubik.yaml          # monitor, channel 2
python -m capture.sniffer --config configs/site_rubik.yaml --out data/raw/smoke_monitor.pcap --duration 15 &
ping -I wlan0 -c 20 -i 0.5 -s 1400 192.168.68.1; ping -I wlan0 -c 10 -i 0.3 192.168.68.1
python -m capture.monitor_setup --config configs/site_rubik.yaml --restore
```
