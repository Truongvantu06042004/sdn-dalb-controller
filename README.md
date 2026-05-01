# SDN Load Balancing — DALB Controller

**Đồ án cuối kỳ — Software Defined Networking**  
Thuật toán: *Dynamic and Adaptive Load Balancing (DALB)*  
Tham khảo: *"A Load Balancing Strategy for SDN Controller based on Distributed Decision"* — Beihang University, IEEE TrustCom 2014

---

## Mục lục

1. [Tổng quan](#1-tổng-quan)
2. [Kiến trúc hệ thống](#2-kiến-trúc-hệ-thống)
3. [Thuật toán DALB](#3-thuật-toán-dalb)
4. [Cấu trúc file](#4-cấu-trúc-file)
5. [Cài đặt môi trường](#5-cài-đặt-môi-trường)
6. [Chạy Demo thủ công (từng bước chi tiết)](#6-chạy-demo-thủ-công-từng-bước-chi-tiết)
7. [Chạy Auto Demo](#7-chạy-auto-demo)
8. [Visualization — Biểu đồ trực quan](#8-visualization--biểu-đồ-trực-quan)
9. [Dashboard Web](#9-dashboard-web)
10. [Kiểm tra hệ thống](#10-kiểm-tra-hệ-thống)
11. [REST API](#11-rest-api)
12. [Kết quả mong đợi](#12-kết-quả-mong-đợi)
13. [Câu hỏi thường gặp](#13-câu-hỏi-thường-gặp)

---

## 1. Tổng quan

Dự án xây dựng hệ thống cân bằng tải cho SDN controller theo mô hình **phân tán** — không cần super-controller trung tâm. Hai Ryu controller (A và B) tự trao đổi thông tin qua REST API, tính toán chỉ số tải theo thuật toán DALB và tự động **migrate switch** từ controller bị quá tải sang controller nhàn rỗi hơn, không làm gián đoạn kết nối host.

### Tính năng chính

| Tính năng | Mô tả |
|---|---|
| DALB Algorithm | C_Load = w1×N + w2×F + w3×R; ρ = mean/max |
| Adaptive CT | Ngưỡng kiểm tra tự điều chỉnh theo tải thực tế |
| ARP Proxy | Xử lý ARP cross-domain, không flood toàn mạng |
| Switch Migration | OFPRoleRequest SLAVE→MASTER, không ngắt host |
| REST API | GET /load, GET /status, GET /arp, POST /migrate |
| Visualization | Biểu đồ matplotlib 6 panel cập nhật thời gian thực |
| Web Dashboard | Chart.js dashboard, xử lý được khi 1 controller offline |
| Auto Demo | Script 4-phase tự động, in kết quả PASS/FAIL |
| Test Scenarios | 8 nhóm kiểm tra API, rho, CORS, switch |
| Unit Tests | 13 test case DALB, tất cả PASS |

---

## 2. Kiến trúc hệ thống

```
         Domain A  ─  Controller A (OpenFlow :6633 | REST :8080)
         ┌──────────────────────────────────────┐
         │   S1 ──── h1 (10.0.1.1)              │
         │    │ ──── h2 (10.0.1.2)              │
         │    │ ──── h3 (10.0.1.3)              │
         │    │                                 │
         │   S2 ──── h4 (10.0.1.4)              │
         │      ──── h5 (10.0.1.5)              │
         └──────────────┬──────────────────────┘
                        │  inter-domain link
                        │  S2:port4 ↔ S4:port4
         ┌──────────────┴──────────────────────┐
         │   S4 ──── h9 (10.0.2.4)              │
         │    │                                 │
         │   S3 ──── h6 (10.0.2.1)              │
         │      ──── h7 (10.0.2.2)              │
         │      ──── h8 (10.0.2.3)              │
         │    │                                 │
         │   S5 ──── h10 (10.0.2.5)             │
         │      ──── h11 (10.0.2.6)             │
         └──────────────────────────────────────┘
         Domain B  ─  Controller B (OpenFlow :6634 | REST :8081)
```

### Phân công ban đầu

| Switch | Controller | Hosts |
|--------|------------|-------|
| S1 | A | h1 (10.0.1.1), h2 (10.0.1.2), h3 (10.0.1.3) |
| S2 | A | h4 (10.0.1.4), h5 (10.0.1.5) — port4: inter-domain |
| S3 | B | h6 (10.0.2.1), h7 (10.0.2.2), h8 (10.0.2.3) |
| S4 | B | h9 (10.0.2.4) — port4: inter-domain |
| S5 | B | h10 (10.0.2.5), h11 (10.0.2.6) |

> Tất cả host dùng subnet `/16` (10.0.0.0/16) để ARP cross-domain không cần gateway.

---

## 3. Thuật toán DALB

### Công thức tính tải switch

```
C_Load = w1 × N  +  w2 × F  +  w3 × R
```

| Ký hiệu | Ý nghĩa | Trọng số |
|---------|---------|---------|
| N | Số flow entry active | w1 = 0.1 |
| F | Flow rate (packets/s) | w2 = 0.8 |
| R | RTT đến controller (ms) | w3 = 0.1 |

### Chỉ số cân bằng ρ (rho)

```
ρ = mean(load_A, load_B) / max(load_A, load_B)
```

| ρ | Ý nghĩa |
|---|---------|
| 1.0 | Hoàn toàn cân bằng |
| ≥ 0.7 | Chấp nhận được |
| < 0.7 | Mất cân bằng → kích hoạt migrate |

### Điều kiện migrate (cần cả 2)

- **C1**: ρ < 0.7 — toàn cụm mất cân bằng
- **C2**: Controller này có tải **lớn nhất** trong cụm

### Chọn switch để migrate

```
Chọn switch có load lớn nhất thỏa: L_switch ≤ (L_overloaded − L_target) / 2
```

### Adaptive CT

```
δ = mean(loads);   CT = δ nếu δ > ICT(1000),  ngược lại CT = ICT
```

---

## 4. Cấu trúc file

```
project/
├── dalb_module.py      ← Logic DALB thuần + 13 unit test
├── topology.py         ← Mininet: 2 ctrl, 5 switch, 11 host
├── controller_a.py     ← Ryu Controller A | OF :6633 | REST :8080
├── controller_b.py     ← Ryu Controller B | OF :6634 | REST :8081
├── visualize.py        ← Biểu đồ matplotlib 6 panel real-time (NEW)
├── dashboard.html      ← Web dashboard Chart.js (không cần server)
├── auto_demo.py        ← Script tự động 4-phase demo
├── test_scenarios.py   ← Kiểm tra API, rho, CORS, switch
├── traffic_gen.py      ← Hướng dẫn sinh traffic bằng tay
└── README.md           ← File này
```

---

## 5. Cài đặt môi trường

### Yêu cầu phần mềm

| Phần mềm | Phiên bản | Cài đặt |
|----------|-----------|---------|
| Python | 3.9.x / 3.10.x | `sudo apt install python3` |
| Mininet | 2.3+ | `sudo apt install mininet` |
| Ryu | 4.34 | `pip install ryu` |
| eventlet | **0.33.3** | `pip install "eventlet==0.33.3"` |
| dnspython | **2.8.0** | `pip install "dnspython==2.8.0"` |
| requests | bất kỳ | `pip install requests` |
| matplotlib | bất kỳ | `pip install matplotlib` |
| numpy | bất kỳ | `pip install numpy` |

### Cài đặt tất cả một lệnh

```bash
sudo apt update && sudo apt install -y mininet
pip install ryu "eventlet==0.33.3" "dnspython==2.8.0" requests matplotlib numpy
```

### Kiểm tra cài đặt

```bash
python3 -c "import ryu, eventlet, matplotlib, numpy; print('All OK')"
python3 dalb_module.py     # All tests PASSED ✅
```

### Patch Ryu WSGI (nếu cần — 1 lần duy nhất)

Nếu gặp `ImportError: cannot import name 'ALREADY_HANDLED'`:

```bash
python3 -c "import ryu.app.wsgi; print(ryu.app.wsgi.__file__)"
# Mở file đó, tìm dòng: from eventlet.wsgi import ALREADY_HANDLED
# Thay bằng:
# try:
#     from eventlet.wsgi import ALREADY_HANDLED
# except ImportError:
#     ALREADY_HANDLED = False
```

---

## 6. Chạy Demo thủ công (từng bước chi tiết)

Mở **5 terminal** — đặt cạnh nhau để quan sát đồng thời.

---

### Bước 0 — Dọn Mininet cũ (LUÔN làm trước tiên)

```bash
sudo mn -c
```

---

### Bước 1 — Terminal 1: Khởi động Controller A

```bash
cd ~/Downloads/SDN_FInal/project
ryu-manager controller_a.py --ofp-tcp-listen-port 6633
```

**Chờ thấy dòng sau rồi mới tiếp tục:**

```
[A] Controller A ready — OF=6633  REST=http://0.0.0.0:8080
```

> Nếu không thấy sau 10 giây: `sudo fuser -k 6633/tcp 8080/tcp` rồi thử lại.

---

### Bước 2 — Terminal 2: Khởi động Controller B

```bash
cd ~/Downloads/SDN_FInal/project
ryu-manager controller_b.py --ofp-tcp-listen-port 6634
```

**Chờ thấy dòng sau rồi mới tiếp tục:**

```
[B] Controller B ready — OF=6634  REST=http://0.0.0.0:8081
```

---

### Bước 3 — Terminal 3: Khởi động Mininet

```bash
cd ~/Downloads/SDN_FInal/project
sudo python3 topology.py
```

Chờ prompt `mininet>`, sau đó kiểm tra kết nối:

```
mininet> pingall
```

> **Bình thường**: lần đầu có vài gói lỗi (ARP chưa học). Gõ `pingall` lần 2 → 100%.

Kết quả mong đợi:
```
*** Results: 0% dropped (110/110 received)
```

---

### Bước 4 — Terminal 4: Mở Visualization (biểu đồ matplotlib)

```bash
cd ~/Downloads/SDN_FInal/project
python3 visualize.py
```

Cửa sổ biểu đồ hiện ra với **6 panel**:

| Panel | Nội dung |
|-------|---------|
| **Controller Total Load** | Bar chart so sánh tổng tải A và B, đường CT threshold màu vàng |
| **Per-Switch Load** | Bar chart tải từng switch, xanh dương = Domain A, xanh lá = Domain B |
| **ρ Gauge** | Thanh chỉ số cân bằng 0–1, sparkline lịch sử mini, đường ngưỡng 0.7 |
| **Load History** | Line chart lịch sử 3 phút, dấu vạch đỏ khi ρ < 0.7 |
| **Switch Distribution** | Donut chart phân bổ switch theo controller |
| **Per-Switch Horizontal Bar** | Chi tiết flows, RTT, C_Load từng switch dạng ngang |
| **System Status** | Text overlay: trạng thái, uptime, migration count, log |

> Tất cả cập nhật tự động mỗi **3 giây**. Không cần click gì.

---

### Bước 5 — (Tuỳ chọn) Mở Dashboard Web

```bash
xdg-open ~/Downloads/SDN_FInal/project/dashboard.html
# hoặc kéo file vào Chrome/Firefox
```

Dashboard web có cùng thông tin nhưng dạng web, chạy song song với visualize.py.

---

### Bước 6 — Terminal 5: Kiểm tra API nhanh

```bash
# Tải Controller A
curl http://localhost:8080/load | python3 -m json.tool

# Tải Controller B
curl http://localhost:8081/load | python3 -m json.tool

# Trạng thái đầy đủ (bao gồm rho, peer_status, migration_log)
curl http://localhost:8080/status | python3 -m json.tool
curl http://localhost:8081/status | python3 -m json.tool
```

---

### Bước 7 — Terminal 3: Sinh traffic để kích hoạt migrate

Trong Mininet CLI, tạo nhiều luồng iperf đồng thời vào Domain A:

```
mininet> h1 iperf -s &
mininet> h2 iperf -c 10.0.1.1 -t 120 &
mininet> h3 iperf -c 10.0.1.1 -t 120 &
mininet> h4 iperf -c 10.0.1.1 -t 120 &
mininet> h5 iperf -c 10.0.1.1 -t 120 &
```

Thêm traffic cross-domain (để B nhàn hơn A):

```
mininet> h6 iperf -s &
mininet> h7 iperf -c 10.0.2.1 -t 120 &
```

**Quan sát sau 10–30 giây:**

- **Terminal 1**: `[A][DALB] ρ=0.xxx < 0.70 → migrate` → `[A][MIGRATE] ✅ SUCCESS`
- **Terminal 4** (visualize.py): thanh load A chuyển đỏ/cam → sau migrate load A giảm, ρ tăng về xanh
- **Dashboard web**: migration log xuất hiện entry mới
- **Terminal 2**: `[B][MIGRATE] Received SX → MASTER on Controller B`

---

### Bước 8 — Xác nhận kết quả

```bash
# So sánh switch trước và sau migrate
curl http://localhost:8080/status | python3 -m json.tool
curl http://localhost:8081/status | python3 -m json.tool
```

Kết quả đúng: A có ít switch hơn, B có thêm switch, ρ ≥ 0.7.

---

### Bước 9 — Dọn dẹp

```
mininet> h1 kill %1 2>/dev/null
mininet> h2 kill %1 2>/dev/null
mininet> h3 kill %1 2>/dev/null
mininet> h4 kill %1 2>/dev/null
mininet> h5 kill %1 2>/dev/null
mininet> h6 kill %1 2>/dev/null
mininet> exit
```

```bash
sudo mn -c
# Ctrl+C ở Terminal 1, 2, 4
```

---

## 7. Chạy Auto Demo

Script `auto_demo.py` tự động toàn bộ — không cần chạy `topology.py`.

> **Lưu ý**: Không chạy `topology.py` và `auto_demo.py` cùng lúc.

### Các bước

```bash
# Bước 0: Dọn
sudo mn -c

# Terminal 1: Controller A
cd ~/Downloads/SDN_FInal/project
ryu-manager controller_a.py --ofp-tcp-listen-port 6633

# Terminal 2: Controller B
cd ~/Downloads/SDN_FInal/project
ryu-manager controller_b.py --ofp-tcp-listen-port 6634

# Terminal 3: Visualization (tuỳ chọn, mở trước để quan sát)
python3 visualize.py

# Terminal 4: Auto Demo
cd ~/Downloads/SDN_FInal/project
sudo python3 auto_demo.py
```

### 4 Phase tự động

| Phase | Thời gian | Nội dung |
|-------|-----------|---------|
| Phase 1 | ~60s | Baseline ping, đo tải ban đầu |
| Phase 2 | ~60s | Heavy iperf traffic vào Domain A |
| Phase 3 | tự động | Chờ phát hiện migrate event |
| Phase 4 | ngay sau | Kiểm tra ρ, in PASS/FAIL |

**Output cuối mong đợi:**

```
============================================================
  DEMO KẾT QUẢ
  Controller A: 1 switch(es), load = 89.2
  Controller B: 4 switch(es), load = 95.1
  rho = 0.938 >= 0.70  →  BALANCED ✅
============================================================
```

---

## 8. Visualization — Biểu đồ trực quan

File `visualize.py` tạo cửa sổ biểu đồ matplotlib với **6 panel** cập nhật thời gian thực.

### Cài đặt

```bash
pip install matplotlib numpy
```

### Chạy

```bash
cd ~/Downloads/SDN_FInal/project
python3 visualize.py
```

> Yêu cầu: ít nhất 1 controller đang chạy. Nếu 1 controller offline, panel đó hiện "OFFLINE" và các panel khác vẫn hoạt động bình thường.

### Mô tả 6 panel

```
┌─────────────────┬─────────────────┬─────────────────┐
│  Controller     │  Per-Switch     │   ρ Gauge       │
│  Total Load     │  Load (bar)     │  + sparkline    │
│  (bar chart)    │                 │                 │
├─────────────────┴─────────────────┴─────────────────┤
│              Load History (line chart)               │
│         Controller A (xanh dương) vs B (xanh lá)    │
│         Dấu vạch đỏ khi ρ < 0.7                     │
├─────────────────┬─────────────────┬─────────────────┤
│  Switch         │  Per-Switch     │  System         │
│  Distribution   │  Horizontal     │  Status Text    │
│  (donut pie)    │  Bar (detail)   │  (info panel)   │
└─────────────────┴─────────────────┴─────────────────┘
```

### Màu sắc

| Màu | Ý nghĩa |
|-----|---------|
| Xanh lá | Load bình thường (< 50% CT) |
| Cam | Load trung bình (50–80% CT) |
| Đỏ | Load cao (> 80% CT) |
| Vàng | Đường CT threshold |
| Xanh dương | Controller A / Domain A |
| Xanh lá đậm | Controller B / Domain B |

### Nếu màn hình không hiện (lỗi display)

```bash
# Thử backend khác:
MPLBACKEND=Qt5Agg python3 visualize.py
# hoặc:
MPLBACKEND=GTK3Agg python3 visualize.py
```

---

## 9. Dashboard Web

File `dashboard.html` — không cần web server, kéo vào Chrome/Firefox là dùng.

### Mở

```bash
xdg-open ~/Downloads/SDN_FInal/project/dashboard.html
```

### Thành phần

| Thành phần | Mô tả |
|---|---|
| Status badge | ONLINE (xanh) / OFFLINE (đỏ) — tự detect khi controller tắt |
| Load bar | Thanh % load, chuyển đỏ khi > 80% CT |
| ρ gauge | Chỉ số 0–1, đường ngưỡng 0.7 màu đỏ |
| Per-switch table | Flows, RTT (ms), C_Load từng switch |
| Load history chart | Line chart 60 điểm (3 phút), cập nhật mỗi 3s |
| Migration log | Danh sách migrate có timestamp |

> Nếu 1 controller offline: dashboard tự hiện OFFLINE và vẫn hiển thị controller còn lại bình thường.

---

## 10. Kiểm tra hệ thống

### Test API tự động (8 nhóm)

```bash
cd ~/Downloads/SDN_FInal/project
python3 test_scenarios.py
```

| Nhóm Test | Nội dung |
|-----------|---------|
| TEST 1 | Controller A và B kết nối được |
| TEST 2 | Cấu trúc JSON `/load` đúng (controller name, total_load, switches, ct) |
| TEST 3 | Cấu trúc JSON `/status` đúng (rho, peer_status, migration_log) |
| TEST 4 | Giá trị ρ khớp công thức mean/max (±0.05) |
| TEST 5 | `/arp?ip=...` hoạt động, IPs học được sau pingall |
| TEST 6 | Tổng 5 switch phân bổ đúng giữa 2 controller |
| TEST 7 | CORS header `*` có mặt (bắt buộc cho dashboard) |
| TEST 8 | `/migrate` từ chối payload không hợp lệ (400/503) |

**Output mẫu:**

```
── TEST 1: Controller Connectivity ──────────────────────────────────────
  [PASS] Controller A reachable (port 8080)
  [PASS] Controller B reachable (port 8081)

── TEST 4: Rho Validation ───────────────────────────────────────────────
  [PASS] Controller A rho matches computed value  (ρ=0.823 ≈ 0.821)
  [PASS] Controller B rho matches computed value  (ρ=0.823 ≈ 0.821)
  [PASS] System currently balanced  (ρ=0.821 ≥ 0.7)

── SUMMARY ──────────────────────────────────────────────────────────────
  PASS:  24
  FAIL:   0
  SKIP:   2   (SKIP không phải lỗi — cần pingall trước)

  All tests PASSED ✅
```

### Unit test DALB

```bash
python3 dalb_module.py
```

```
[1] calculate_switch_load: 0.1*10 + 0.8*100 + 0.1*5 = 81.5  ✅
[2] controller_load([97.3, 65.0]) = 162.3  ✅
[3] rho([580, 1820]) = 0.659  (below 0.7 → migrate)  ✅
...
All tests PASSED ✅ (13/13)
```

---

## 11. REST API

| Method | Endpoint | Port A | Port B | Mô tả |
|--------|---------|:------:|:------:|-------|
| GET | `/load` | 8080 | 8081 | Tải từng switch + tổng |
| GET | `/status` | 8080 | 8081 | Trạng thái đầy đủ + migration_log + peer_status |
| GET | `/arp?ip=x.x.x.x` | 8080 | 8081 | Tra MAC của IP trong domain |
| POST | `/migrate` | 8080 | 8081 | Nhận switch `{"dpid":N,"name":"SN"}` |

### GET /load

```bash
curl http://localhost:8080/load | python3 -m json.tool
```

```json
{
  "controller": "A",
  "total_load": 212.4,
  "ct": 1000.0,
  "switches": [
    { "name": "S1", "dpid": 1, "load": 125.3, "flows": 12, "rtt_ms": 0.8 },
    { "name": "S2", "dpid": 2, "load":  87.1, "flows":  8, "rtt_ms": 0.7 }
  ],
  "timestamp": "21:55:33"
}
```

### GET /status

```bash
curl http://localhost:8080/status | python3 -m json.tool
```

```json
{
  "controller": "A",
  "total_load": 212.4,
  "peer_load": 89.3,
  "peer_status": "ONLINE",
  "ct": 1000.0,
  "rho": 0.823,
  "status": "NORMAL",
  "managed_switches": ["S1", "S2"],
  "migration_count": 0,
  "migration_log": [],
  "uptime_seconds": 339
}
```

### POST /migrate (thủ công)

```bash
curl -X POST http://localhost:8081/migrate \
     -H "Content-Type: application/json" \
     -d '{"dpid": 2, "name": "S2"}'
```

### Theo dõi tải liên tục

```bash
watch -n 5 'echo "=== A ===" && curl -s http://localhost:8080/load | python3 -m json.tool; echo "=== B ===" && curl -s http://localhost:8081/load | python3 -m json.tool'
```

---

## 12. Kết quả mong đợi

### Trạng thái ban đầu

```
Controller A  →  S1, S2          load thấp ≈ 0–50
Controller B  →  S3, S4, S5      load thấp ≈ 0–50
ρ ≈ 1.0  →  cân bằng
```

### Sau heavy traffic vào Domain A (iperf 5 luồng)

```
Controller A load tăng  >1000  (vượt CT)
Controller B load thấp  ~50–200
ρ < 0.7  →  C1 thỏa (mất cân bằng)
A có load lớn nhất  →  C2 thỏa
→ DALB chọn switch thoả L ≤ (L_over − L_target)/2
→ A gửi OFPRoleRequest SLAVE → gọi POST /migrate đến B
→ B gửi OFPRoleRequest MASTER → switch chuyển sang B
→ Host không bị ngắt kết nối
```

### Sau migrate

```
Controller A  →  S1 (ví dụ)       load giảm
Controller B  →  S2, S3, S4, S5   load tăng vừa
ρ → ≥ 0.7  →  cân bằng lại
```

### Log điển hình Terminal 1 (Controller A)

```
┌────────────────────────────────────────────────────────────────┐
│ [A][LOAD] Controller A | 21:55:33                              │
├──────────┬───────┬──────────────┬─────────┬────────────────────┤
│ Switch   │ Flows │  Pkt-in/s    │   RTT   │ C_Load             │
├──────────┼───────┼──────────────┼─────────┼────────────────────┤
│ S1       │   12  │   148.2/s    │ 0.8ms   │       125.3        │
│ S2       │    8  │   102.4/s    │ 0.7ms   │        87.1        │
├──────────┴───────┴──────────────┴─────────┴────────────────────┤
│ Total: 1149.9/s | CT=1000 | EXCEEDED                           │
└────────────────────────────────────────────────────────────────┘
[A][DALB] Load=1149.9/s >= CT=1000 | EXCEEDED ⚠️
[A][DALB] Peer Controller B Load: 89.3/s
[A][DALB] ρ=0.558 < 0.70 | MIGRATE → A is MAX [C1✅ C2✅]
[A][DALB] → MIGRATION TRIGGERED!
[A][MIGRATE] S2 (load=87.1) Controller A → B
[A][MIGRATE] ✅ SUCCESS: S2 → B
[A][MIGRATE] New CT=619.6 | Expected load ~1062/s | ρ→0.792
```

---

## 13. Câu hỏi thường gặp

**Q: `pingall` lần đầu có vài lỗi?**  
A: Bình thường. ARP cross-domain cần học MAC lần đầu. Gõ lại `pingall` lần 2 là 100%.

**Q: `visualize.py` báo lỗi display hoặc không hiện cửa sổ?**  
A: Thử đổi backend: `MPLBACKEND=Qt5Agg python3 visualize.py` hoặc `MPLBACKEND=GTK3Agg python3 visualize.py`. Nếu chạy SSH không có GUI thì visualize.py không dùng được — dùng dashboard.html thay thế.

**Q: `/status` trả về `"controller": "B"` nhưng port là 8080?**  
A: Đang chạy nhầm file `controller_b.py` thay vì `controller_a.py`. Code đã tự set WSGI port qua `_ryu_cfg.CONF.wsapi_port` nhưng cần đúng file. Dừng lại, `sudo fuser -k 8080/tcp`, chạy đúng file.

**Q: Dashboard hoặc visualize.py hiện OFFLINE dù controller đang chạy?**  
A: Kiểm tra: `curl http://localhost:8080/load`. Nếu trả về JSON thì controller OK, có thể browser/display đang bị firewall. Nếu lỗi thì controller chưa khởi động xong — chờ thêm 5 giây.

**Q: Không thấy migrate dù đã sinh traffic?**  
A: Load cần vượt CT (1000). Cần ít nhất 5 luồng iperf đồng thời. Chờ ít nhất 2 chu kỳ monitor (~20 giây). Kiểm tra log terminal xem DALB báo lý do gì.

**Q: `test_scenarios.py` báo SKIP?**  
A: SKIP không phải lỗi — nghĩa là test cần điều kiện thêm. Ví dụ: TEST 5 (ARP) cần chạy `pingall` trong Mininet trước. Sau `pingall` chạy lại test sẽ PASS.

**Q: Lỗi `RTNETLINK answers: File exists`?**  
A: Có Mininet cũ đang chạy. Chạy `sudo mn -c` rồi thử lại.

**Q: Lỗi `TypeError: cannot set 'is_timeout'`?**  
A: Sai eventlet version. Chạy: `pip install "eventlet==0.33.3" "dnspython==2.8.0"` rồi restart controller.

**Q: `ryu-manager: command not found`?**  
A: Chạy `export PATH=$PATH:~/.local/bin` hoặc dùng `python3 -m ryu.cmd.manager controller_a.py --ofp-tcp-listen-port 6633`.

**Q: auto_demo.py và topology.py có thể chạy cùng lúc không?**  
A: Không — chỉ được có một Mininet instance. Thoát cái đang chạy, `sudo mn -c`, rồi khởi động cái kia.

---

## Tài liệu tham khảo

- He, J., et al. *"A Load Balancing Strategy for SDN Controller based on Distributed Decision."* IEEE TrustCom, 2014.
- [Ryu SDN Framework Documentation](https://ryu.readthedocs.io/)
- [OpenFlow 1.3 Specification](https://opennetworking.org/wp-content/uploads/2014/10/openflow-spec-v1.3.0.pdf)
- [Mininet Documentation](http://mininet.org/docs/)
- [Matplotlib Documentation](https://matplotlib.org/stable/contents.html)
