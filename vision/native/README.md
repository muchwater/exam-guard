# NPU(HTP) 경로: ONNX → DLC → context binary → qnn_runner

`qnn_runner.cpp`는 팀의 sd-moe 프로젝트에서 쓰던 상주형 runner를 그대로 가져왔다(protocol 동일). context binary를 한 번만 로드하고, 입출력 tensor를 `/dev/shm`에 매핑한다. Python(`vision/backends.py::QnnHtpRunner`)은 numpy memmap으로 값을 읽고 쓴다. `qnn-net-run`을 프레임마다 실행하면 context를 매번 다시 로드하므로 쓰지 않는다.

## 1. PC(컨테이너 `ee416`): ONNX → DLC
QAIRT SDK가 필요하다. **현재 컨테이너에 SDK가 없다**(2026-11-25 확인). 보드 runtime과 같은 QAIRT 2.46 버전을 설치한 뒤 진행한다.
```bash
qairt-converter --input_network models/pose.onnx --output_path models/pose.dlc
# 양자화(HTP int8/int16): calibration 이미지 목록은 우리 카메라 프레임에서 뽑는다
qairt-quantizer --input_dlc models/pose.dlc --input_list calib/list.txt --output_dlc models/pose_q.dlc
```
양자화 설정(w8a8 / w8a16)은 정확도와 latency를 측정한 뒤 정한다(TBD).

## 2. 보드: DLC → context binary, runner 빌드
```bash
qnn-context-binary-generator --backend /usr/lib/libQnnHtp.so --model /usr/lib/libQnnModelDlc.so \
    --dlc_path models/pose_q.dlc --binary_file pose_htp --output_dir models
g++ -O2 -std=c++17 -I/usr/include -I/usr/include/QNN -o vision/native/qnn_runner vision/native/qnn_runner.cpp -ldl
```
(인자 형식은 sd-moe `edge_sd/device_qnn.py::ctxbin`에서 실제로 쓰던 명령과 같다.)

## 3. CPU fallback
`vision.pose.backend: qnn`일 때 runner 실행이 실패하면(파일 없음, HTP 오류 등) 자동으로 ONNX Runtime CPU로 넘어가고 log를 남긴다. latency 기록에는 `vision.inference[qnn-htp]`와 `vision.inference[onnx-cpu]`가 따로 남는다.
