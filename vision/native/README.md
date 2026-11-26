# NPU(HTP) 경로: ONNX → DLC → context binary → qnn_runner

`qnn_runner.cpp`는 팀의 sd-moe 프로젝트에서 쓰던 상주형 runner를 그대로 가져왔다(protocol 동일). context binary를 한 번만 로드하고, 입출력 tensor를 `/dev/shm`에 매핑한다. Python(`vision/backends.py::QnnHtpRunner`)은 numpy memmap으로 값을 읽고 쓴다. `qnn-net-run`을 프레임마다 실행하면 context를 매번 다시 로드하므로 쓰지 않는다.

## 1. PC(컨테이너 `ee416`): ONNX → DLC → 양자화
QAIRT SDK 2.46.0.260424는 보드 runtime(`v2.46.0.260424121129`)과 같은 빌드다.
- 설치 위치: `/workspace/sdk/qairt/2.46.0.260424`
- venv: `/workspace/venvs/qairt`
```bash
source /workspace/venvs/qairt/bin/activate
export QAIRT_ROOT=/workspace/sdk/qairt/2.46.0.260424 && source $QAIRT_ROOT/bin/envsetup.sh
cd /workspace/exam-guard
qairt-converter --input_network models/pose.onnx --output_path models/pose.dlc
# 양자화 보정 입력(호스트 .venv에서): python -m tools.pose_quant_data --out data/derived/pose_quant \
#     --n-calib 100 --n-eval 60 --path-prefix /workspace/exam-guard/
qairt-quantizer --input_dlc models/pose.dlc --input_list data/derived/pose_quant/calib/input_list.txt \
    --output_dlc models/pose_w8a16pc.dlc --act_bitwidth 16 --weights_bitwidth 8 --use_per_channel_quantization
```
- **보정 데이터는 임시다.** ExamCheat-MultiV V1 프레임을 썼다(클립 단위로 보정/검증 분리). 팀 카메라 프레임을 녹화하면 그걸로 다시 만든다.
- QCS6490 HTP(v68)는 부동소수점을 지원하지 않으므로 양자화가 필요하다.

## 2. 보드: DLC → context binary, runner 빌드
```bash
qnn-context-binary-generator --backend /usr/lib/libQnnHtp.so --model /usr/lib/libQnnModelDlc.so \
    --dlc_path models/pose_w8a16pc.dlc --binary_file pose_htp --output_dir models     # -> models/pose_htp.bin
g++ -O2 -std=c++17 -I/usr/include -I/usr/include/QNN -o vision/native/qnn_runner vision/native/qnn_runner.cpp -ldl
```
(인자 형식은 sd-moe `edge_sd/device_qnn.py::ctxbin`에서 실제로 쓰던 명령과 같다.)

## 양자화 선택 근거 (보드 실측, `data/measured/pose_backend_compare.json`)
검증 프레임 60장(보정에 안 쓴 클립)으로 측정했다. 기준은 보드 CPU의 ONNX fp32이고, 기준이 검출한 사람은 92명이다.

| 변형 | 사람 검출 (일치/놓침/추가) | 키포인트 오차 중앙값/p90 (박스 대각선 대비) | head_drop 편향 (중앙값) | NPU 실행 | 전체 시간 |
|---|---|---|---|---|---|
| CPU fp32 (기준) | – | – | – | – | 299 ms |
| w8a8 | 0 / 92 / 0 | – | – | 6.0 ms | 17 ms |
| w8a16 (텐서별) | 92 / 0 / 2 | 0.98% / 2.1% | −0.113 | 14.1 ms | 33 ms |
| **w8a16 채널별 (채택)** | 91 / 1 / 0 | **0.39% / 1.1%** | **−0.016** | 14.1 ms | 35 ms |

- **w8a8이 실패한 이유:** 출력 텐서 하나에 박스 좌표(0–640)와 신뢰도(0–1)가 섞여 있다. 8 bit로 양자화하면 한 칸이 약 2.5가 되어 신뢰도가 모두 0이 된다.
  - 고치려면 출력만 16 bit로 두는 mixed precision이 필요하다(TBD).
- **텐서별 w8a16의 문제:** head_drop이 체계적으로 0.11 낮았다. 이 크기면 B1 임계값(0.227)의 판단이 달라진다.
  - 채널별 가중치 양자화 하나만 바꿔서 −0.016으로 줄었다.
- **파이프라인 전체 (보드, 1080×1920 클립 186 프레임, `vision.pipeline --latency-csv`):**
  - NPU 경로: 전처리 16.6, 추론 23.7(NPU 실행 13.9), 후처리 0.5, **합계 41.1 ms**(p90 47.2, 약 24 fps)
  - CPU 경로: 합계 315.5 ms
- **B1 임계값은 배포 백엔드(NPU)로 측정해야 한다.** 같은 클립에서 NPU−CPU의 head_drop 차이는 중앙값 −0.01이지만 |차이|의 p90은 0.17이었다.

## 3. CPU fallback
`vision.pose.backend: qnn`일 때 runner 실행이 실패하면(파일 없음, HTP 오류 등) 자동으로 ONNX Runtime CPU로 넘어가고 log를 남긴다. latency 기록에는 `vision.inference[qnn-htp]`와 `vision.inference[onnx-cpu]`가 따로 남는다.
