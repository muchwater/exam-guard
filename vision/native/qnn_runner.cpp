// Adapted from the team's sd-moe/edge_sd/qnn_runner.cpp (unchanged protocol): persistent HTP context for per-frame pose inference.
// Device study, Part 3 (runs ON the Rubik Pi 3): persistent QNN HTP executor for the on-device drafter.
//
// qnn-net-run reloads its context on every call (0.05-0.9 s), so an end-to-end decode loop needs a process that keeps
// the context binaries resident.  This runner loads every given context binary once (libQnnHtp + libQnnSystem from the
// device's QAIRT 2.46), votes the HTP into the burst corner (DCVS off, max bus/core corners, like
// qnn-net-run --perf_profile burst), and maps each graph input / output tensor to a file in /dev/shm in the tensor's
// native (quantized) layout, which the Python driver fills / reads with numpy.memmap (no copies on either side).
//
// stdout, the line starting with '{': JSON with the graphs and, per tensor, name, QNN data type, dims, scale, offset,
//                     bytes, shm path (real = scale * (q + offset)); the QNN libraries print other lines to stdout too.
// stdin  commands   : "x <graph index>"  -> executes the graph, replies "ok <graph index> <execute us>"
//                     "q"                 -> exits
//
// build: g++ -O2 -std=c++17 -I/usr/include -I/usr/include/QNN -o qnn_runner qnn_runner.cpp -ldl
// run  : ./qnn_runner <shm prefix> ctx_a.bin ctx_b.bin ...
#include <dlfcn.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>

#include <chrono>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#include "QNN/HTP/QnnHtpDevice.h"
#include "QNN/HTP/QnnHtpPerfInfrastructure.h"
#include "QNN/QnnInterface.h"
#include "QNN/System/QnnSystemInterface.h"

struct Tensor {
  std::string name, shm;
  Qnn_Tensor_t t;
  std::vector<uint32_t> dims;
  size_t bytes;
  void* buf;
  float scale;
  int32_t offset;
};

struct Graph {
  std::string name;
  Qnn_GraphHandle_t handle;
  std::vector<Tensor> in, out;
  std::vector<Qnn_Tensor_t> qin, qout;
};

static void die(const std::string& m) {
  std::cerr << "qnn_runner: " << m << std::endl;
  exit(1);
}

static size_t dtype_bytes(Qnn_DataType_t d) {  // 0x0416 -> 16 bit: the low byte reads as the decimal bit width
  uint32_t c = static_cast<uint32_t>(d) & 0xFF;
  uint32_t bits = ((c >> 4) & 0xF) * 10 + (c & 0xF);
  if (bits == 0 || bits % 8) die("unsupported data type " + std::to_string(static_cast<uint32_t>(d)));
  return bits / 8;
}

static Tensor make_tensor(const Qnn_Tensor_t& src, const std::string& shm) {
  Tensor x;
  const Qnn_TensorV1_t& s = src.v1;  // V1 and V2 share this prefix
  x.name = s.name ? s.name : "";
  x.dims.assign(s.dimensions, s.dimensions + s.rank);
  x.t = src;
  x.t.v1.name = strdup(x.name.c_str());
  x.t.v1.dimensions = x.dims.data();
  size_t n = 1;
  for (auto d : x.dims) n *= d;
  x.bytes = n * dtype_bytes(s.dataType);
  x.scale = 1.f;
  x.offset = 0;
  if (s.quantizeParams.encodingDefinition == QNN_DEFINITION_DEFINED &&
      s.quantizeParams.quantizationEncoding == QNN_QUANTIZATION_ENCODING_SCALE_OFFSET) {
    x.scale = s.quantizeParams.scaleOffsetEncoding.scale;
    x.offset = s.quantizeParams.scaleOffsetEncoding.offset;
  }
  x.shm = shm;
  int fd = open(shm.c_str(), O_RDWR | O_CREAT | O_TRUNC, 0644);
  if (fd < 0 || ftruncate(fd, x.bytes) != 0) die("shm " + shm);
  x.buf = mmap(nullptr, x.bytes, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
  close(fd);
  if (x.buf == MAP_FAILED) die("mmap " + shm);
  x.t.v1.memType = QNN_TENSORMEMTYPE_RAW;
  x.t.v1.clientBuf.data = x.buf;
  x.t.v1.clientBuf.dataSize = static_cast<uint32_t>(x.bytes);
  return x;
}

static std::string json_tensor(const Tensor& x) {
  std::string d;
  for (size_t i = 0; i < x.dims.size(); i++) d += (i ? "," : "") + std::to_string(x.dims[i]);
  char b[512];
  snprintf(b, sizeof b, "{\"name\":\"%s\",\"dtype\":%u,\"dims\":[%s],\"scale\":%.9g,\"offset\":%d,\"bytes\":%zu,\"shm\":\"%s\"}",
           x.name.c_str(), static_cast<uint32_t>(x.t.v1.dataType), d.c_str(), x.scale, x.offset, x.bytes, x.shm.c_str());
  return b;
}

int main(int argc, char** argv) {
  if (argc < 3) die("usage: qnn_runner <shm prefix> ctx.bin ...");
  std::string prefix = argv[1];
  void* lib = dlopen("libQnnHtp.so", RTLD_NOW | RTLD_LOCAL);
  void* slib = dlopen("libQnnSystem.so", RTLD_NOW | RTLD_LOCAL);
  if (!lib || !slib) die(std::string("dlopen: ") + dlerror());
  auto getp = reinterpret_cast<Qnn_ErrorHandle_t (*)(const QnnInterface_t***, uint32_t*)>(dlsym(lib, "QnnInterface_getProviders"));
  auto sgetp = reinterpret_cast<Qnn_ErrorHandle_t (*)(const QnnSystemInterface_t***, uint32_t*)>(
      dlsym(slib, "QnnSystemInterface_getProviders"));
  const QnnInterface_t** prov;
  uint32_t np;
  if (!getp || getp(&prov, &np) != QNN_SUCCESS || np == 0) die("QnnInterface_getProviders");
  const QnnInterface_t* qi = nullptr;
  for (uint32_t i = 0; i < np; i++)
    if (prov[i]->apiVersion.coreApiVersion.major == QNN_API_VERSION_MAJOR) qi = prov[i];
  if (!qi) die("no provider with core API major " + std::to_string(QNN_API_VERSION_MAJOR));
  auto& q = qi->QNN_INTERFACE_VER_NAME;
  const QnnSystemInterface_t** sprov;
  if (!sgetp || sgetp(&sprov, &np) != QNN_SUCCESS || np == 0) die("QnnSystemInterface_getProviders");
  auto& sq = sprov[0]->QNN_SYSTEM_INTERFACE_VER_NAME;

  Qnn_BackendHandle_t backend;
  Qnn_DeviceHandle_t device;
  if (q.backendCreate(nullptr, nullptr, &backend) != QNN_SUCCESS) die("backendCreate");
  if (q.deviceCreate(nullptr, nullptr, &device) != QNN_SUCCESS) die("deviceCreate");

  // burst vote (as qnn-net-run --perf_profile burst)
  QnnDevice_Infrastructure_t infra = nullptr;
  if (q.deviceGetInfrastructure(&infra) == QNN_SUCCESS && infra) {
    auto* h = reinterpret_cast<QnnHtpDevice_Infrastructure_t*>(infra);
    uint32_t pid = 0;
    if (h->infraType == QNN_HTP_DEVICE_INFRASTRUCTURE_TYPE_PERF && h->perfInfra.createPowerConfigId(0, 0, &pid) == QNN_SUCCESS) {
      QnnHtpPerfInfrastructure_PowerConfig_t pc;
      memset(&pc, 0, sizeof pc);
      pc.option = QNN_HTP_PERF_INFRASTRUCTURE_POWER_CONFIGOPTION_DCVS_V3;
      auto& d = pc.dcvsV3Config;
      d.contextId = pid;
      d.setDcvsEnable = 1;
      d.dcvsEnable = 0;
      d.powerMode = QNN_HTP_PERF_INFRASTRUCTURE_POWERMODE_PERFORMANCE_MODE;
      d.setSleepLatency = 1;
      d.sleepLatency = 40;
      d.setSleepDisable = 1;
      d.sleepDisable = 1;
      d.setBusParams = 1;
      d.busVoltageCornerMin = d.busVoltageCornerTarget = d.busVoltageCornerMax = DCVS_VOLTAGE_VCORNER_MAX_VOLTAGE_CORNER;
      d.setCoreParams = 1;
      d.coreVoltageCornerMin = d.coreVoltageCornerTarget = d.coreVoltageCornerMax = DCVS_VOLTAGE_VCORNER_MAX_VOLTAGE_CORNER;
      const QnnHtpPerfInfrastructure_PowerConfig_t* cfgs[] = {&pc, nullptr};
      if (h->perfInfra.setPowerConfig(pid, cfgs) != QNN_SUCCESS) std::cerr << "qnn_runner: setPowerConfig failed" << std::endl;
    }
  }

  std::vector<Graph> graphs;
  std::vector<Qnn_ContextHandle_t> contexts;
  for (int a = 2; a < argc; a++) {
    std::ifstream f(argv[a], std::ios::binary | std::ios::ate);
    if (!f) die(std::string("open ") + argv[a]);
    size_t size = f.tellg();
    std::vector<uint8_t> buf(size);
    f.seekg(0);
    f.read(reinterpret_cast<char*>(buf.data()), size);
    QnnSystemContext_Handle_t sys;
    const QnnSystemContext_BinaryInfo_t* info;
    Qnn_ContextBinarySize_t isz;
    if (sq.systemContextCreate(&sys) != QNN_SUCCESS || sq.systemContextGetBinaryInfo(sys, buf.data(), size, &info, &isz) != QNN_SUCCESS)
      die(std::string("binary info ") + argv[a]);
    const QnnSystemContext_GraphInfo_t* gi;
    uint32_t ng;
    if (info->version == QNN_SYSTEM_CONTEXT_BINARY_INFO_VERSION_1) {
      gi = info->contextBinaryInfoV1.graphs;
      ng = info->contextBinaryInfoV1.numGraphs;
    } else if (info->version == QNN_SYSTEM_CONTEXT_BINARY_INFO_VERSION_2) {
      gi = info->contextBinaryInfoV2.graphs;
      ng = info->contextBinaryInfoV2.numGraphs;
    } else {
      gi = info->contextBinaryInfoV3.graphs;
      ng = info->contextBinaryInfoV3.numGraphs;
    }
    Qnn_ContextHandle_t ctx;
    if (q.contextCreateFromBinary(backend, device, nullptr, buf.data(), size, &ctx, nullptr) != QNN_SUCCESS)
      die(std::string("contextCreateFromBinary ") + argv[a]);
    contexts.push_back(ctx);
    for (uint32_t k = 0; k < ng; k++) {
      const char* name;
      const Qnn_Tensor_t *ins, *outs;
      uint32_t ni, no;
      if (gi[k].version == QNN_SYSTEM_CONTEXT_GRAPH_INFO_VERSION_1) {
        name = gi[k].graphInfoV1.graphName;
        ins = gi[k].graphInfoV1.graphInputs;
        ni = gi[k].graphInfoV1.numGraphInputs;
        outs = gi[k].graphInfoV1.graphOutputs;
        no = gi[k].graphInfoV1.numGraphOutputs;
      } else {
        name = gi[k].graphInfoV3.graphName;
        ins = gi[k].graphInfoV3.graphInputs;
        ni = gi[k].graphInfoV3.numGraphInputs;
        outs = gi[k].graphInfoV3.graphOutputs;
        no = gi[k].graphInfoV3.numGraphOutputs;
      }
      Graph g;
      g.name = name;
      if (q.graphRetrieve(ctx, name, &g.handle) != QNN_SUCCESS) die(std::string("graphRetrieve ") + name);
      size_t gidx = graphs.size();
      for (uint32_t i = 0; i < ni; i++)
        g.in.push_back(make_tensor(ins[i], prefix + "_" + std::to_string(gidx) + "_i" + std::to_string(i)));
      for (uint32_t i = 0; i < no; i++)
        g.out.push_back(make_tensor(outs[i], prefix + "_" + std::to_string(gidx) + "_o" + std::to_string(i)));
      for (auto& t : g.in) g.qin.push_back(t.t);
      for (auto& t : g.out) g.qout.push_back(t.t);
      graphs.push_back(std::move(g));
    }
  }

  std::string js = "{\"graphs\":[";
  for (size_t k = 0; k < graphs.size(); k++) {
    js += (k ? "," : "") + std::string("{\"name\":\"") + graphs[k].name + "\",\"inputs\":[";
    for (size_t i = 0; i < graphs[k].in.size(); i++) js += (i ? "," : "") + json_tensor(graphs[k].in[i]);
    js += "],\"outputs\":[";
    for (size_t i = 0; i < graphs[k].out.size(); i++) js += (i ? "," : "") + json_tensor(graphs[k].out[i]);
    js += "]}";
  }
  std::cout << js << "]}" << std::endl;

  std::string line;
  while (std::getline(std::cin, line)) {
    if (line == "q") break;
    if (line.size() > 2 && line[0] == 'x') {
      size_t k = std::stoul(line.substr(2));
      if (k >= graphs.size()) {
        std::cout << "err bad graph index" << std::endl;
        continue;
      }
      auto& g = graphs[k];
      auto t0 = std::chrono::steady_clock::now();
      Qnn_ErrorHandle_t e = q.graphExecute(g.handle, g.qin.data(), g.qin.size(), g.qout.data(), g.qout.size(), nullptr, nullptr);
      auto us = std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now() - t0).count();
      if (e != QNN_SUCCESS)
        std::cout << "err " << k << " " << e << std::endl;
      else
        std::cout << "ok " << k << " " << us << std::endl;
    }
  }
  for (auto c : contexts) q.contextFree(c, nullptr);
  q.deviceFree(device);
  q.backendFree(backend);
  return 0;
}
