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
6. [Chạy Demo thủ công (chi tiết)](#6-chạy-demo-thủ-công-chi-tiết)
7. [Chạy Auto Demo](#7-chạy-auto-demo)
8. [Kiểm tra hệ thống](#8-kiểm-tra-hệ-thống)
9. [REST API](#9-rest-api)
10. [Dashboard](#10-dashboard)
11. [Kết quả mong đợi](#11-kết-quả-mong-đợi)
12. [Câu hỏi thường gặp](#12-câu-hỏi-thường-gặp)

---

## 1. Tổng quan

Dự án xây dựng hệ thống cân bằng tải cho SDN controller theo mô hình **phân tán** — không cần super-controller trung tâm. Hai Ryu controller (A và B) tự trao đổi thông tin qua REST API, tính toán chỉ số tải theo thuật toán DALB và tự động **migrate switch** từ controller bị quá tải sang controller nhàn rỗi hơn mà không làm gián đoạn kết nối của host.

### Tính năng chính

| Tính năng | Mô tả |
|---|---|
| DALB Algorithm | C_Load = w1×N + w2×F + w3×R; ρ = mean/max |
| Adaptive CT | Ngưỡng tự điều chỉnh theo tải thực tế |
| ARP Proxy | Xử lý ARP cross-domain, không flood toàn mạng |
| Switch Migration | OFPRoleRequest SLAVE→MASTER, không ngắt host |
| REST API | GET /load, GET /status, GET /arp, POST /migrate |
| Web Dashboard | Biểu đồ real-time, xử lý được khi 1 controller offline |
| Auto Demo | Script 4-phase tự động, in kết quả PASS/FAIL |
| Test Scenarios | 8 nhóm test API, rho, CORS, switch distribution |
| Unit Tests | 13 test case DALB, tất cả PASS |

---

## 2. Kiến trúc hệ thống

```
         Domain A  ─  Controller A (OpenFlow :6633 | REST :8080)
         ┌──────────────────────────────────────┐
         │   S1 ──── h1 (10.0.1.1)              │
         │    │ ─── h2 (10.0.1.2)              │
         │    │ ─── h3 (10.0.1.3)              │
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

### Chỉ số cân bằng ρ

```
ρ = mean(load_A, load_B) / max(load_A, load_B)
```

| ρ | Ý nghĩa |
|---|---------|
| 1.0 | Hoàn toàn cân bằng |
| ≥ 0.7 | Chấp nhận được |
| < 0.7 | Mất cân bằng → kích hoạt migrate |

### Điều kiện migrate (cần cả 2)

- **C1**: ρ < 0.7 (toàn cụm mất cân bằng)
- **C2**: Controller này có tải lớn nhất

### Chọn switch để migrate

```
Chọn switch có load lớn nhất thỏa:  L_switch ≤ (L_overloaded − L_target) / 2
```

### Adaptive CT

```
CT = mean(loads) nếu mean > ICT(1000),  ngược lại CT = ICT
```

---

## 4. Cấu trúc file

```
project/
├── dalb_module.py      ← Logic DALB thuần + 13 unit test
├── topology.py         ← Mininet: 2 ctrl, 5 switch, 11 host
├── controller_a.py     ← Ryu Controller A | OF :6633 | REST :8080
├── controller_b.py     ← Ryu Controller B | OF :6634 | REST :8081
├── auto_demo.py        ← Script tự động 4-phase demo
├── test_scenarios.py   ← Kiểm tra API, rho, CORS, switch (NEW)
├── traffic_gen.py      ← Hướng dẫn sinh traffic bằng tay
├── dashboard.html      ← Web dashboard real-time (không cần server)
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

### Cài đặt một lệnh

```bash
sudo apt update && sudo apt install -y mininet
pip install ryu "eventlet==0.33.3" "dnspython==2.8.0" requests
```

> **Bắt buộc**: eventlet **0.33.3**. Sai version → lỗi `TypeError: cannot set 'is_timeout'`.

### Kiểm tra cài đặt

```bash
python3 -c "import ryu, eventlet, mininet; print('OK')"
python3 dalb_module.py   # phải in: All tests PASSED ✅
```

### Patch Ryu WSGI (nếu cần, 1 lần duy nhất)

Nếu gặp `ImportError: cannot import name 'ALREADY_HANDLED'`:

```bash
# Tìm file:
python3 -c "import ryu.app.wsgi; print(ryu.app.wsgi.__file__)"

# Sửa dòng:  from eventlet.wsgi import ALREADY_HANDLED
# Thành:
# try:
#     from eventlet.wsgi import ALREADY_HANDLED
# except ImportError:
#     ALREADY_HANDLED = False
```

---

## 6. Chạy Demo thủ công (chi tiết)

Mở **4 terminal riêng biệt** — đặt cạnh nhau để quan sát đồng thời.

---

### Bước 0 — Dọn Mininet cũ (luôn làm đầu tiên)

```bash
sudo mn -c
```

---

### Bước 1 — Terminal 1: Khởi động Controller A

```bash
cd ~/Downloads/SDN_FInal/project
ryu-manager controller_a.py --ofp-tcp-listen-port 6633
```

**Chờ thấy dòng này trước khi tiếp tục:**
```
[A] Controller A ready — OF=6633  REST=http://0.0.0.0:8080
```

> Nếu không thấy, kiểm tra xem port 6633 hoặc 8080 có bị dùng không:
> `sudo fuser 6633/tcp 8080/tcp`

---

### Bước 2 — Terminal 2: Khởi động Controller B

```bash
cd ~/Downloads/SDN_FInal/project
ryu-manager controller_b.py --ofp-tcp-listen-port 6634
```

**Chờ thấy dòng này trước khi tiếp tục:**
```
[B] Controller B ready — OF=6634  REST=http://0.0.0.0:8081
```

---

### Bước 3 — Terminal 3: Khởi động Mininet

```bash
cd ~/Downloads/SDN_FInal/project
sudo python3 topology.py
```

Chờ xuất hiện prompt `mininet>`. Sau đó kiểm tra kết nối:

```
mininet> pingall
```

> **Bình thường**: lần đầu có vài gói lỗi (ARP chưa học). Gõ `pingall` lần 2 → 100%.

Kết quả mong đợi lần 2:
```
*** Results: 0% dropped (110/110 received)
```

---

### Bước 4 — Mở Dashboard

Mở trình duyệt và kéo file `dashboard.html` vào, **hoặc**:

```bash
xdg-open ~/Downloads/SDN_FInal/project/dashboard.html
```

Dashboard tự cập nhật mỗi 3 giây. Kiểm tra:
- Cả 2 controller hiện **ONLINE** (badge xanh)
- 5 switch phân bổ: A quản lý S1, S2 — B quản lý S3, S4, S5
- Biểu đồ ρ (rho) ≈ 1.0 (cân bằng khi chưa có traffic)

---

### Bước 5 — Terminal 4: Kiểm tra API nhanh

```bash
# Xem tải Controller A
curl http://localhost:8080/load | python3 -m json.tool

# Xem tải Controller B
curl http://localhost:8081/load | python3 -m json.tool

# Xem trạng thái đầy đủ (bao gồm rho, peer_status, migration_log)
curl http://localhost:8080/status | python3 -m json.tool
curl http://localhost:8081/status | python3 -m json.tool
```

---

### Bước 6 — Terminal 3: Sinh traffic để kích hoạt migrate

Quay lại Mininet CLI, tạo nhiều luồng iperf vào Domain A:

```
mininet> h1 iperf -s &
mininet> h2 iperf -c 10.0.1.1 -t 120 &
mininet> h3 iperf -c 10.0.1.1 -t 120 &
mininet> h4 iperf -c 10.0.1.1 -t 120 &
mininet> h5 iperf -c 10.0.1.1 -t 120 &
```

Thêm traffic cross-domain để B nhàn hơn:

```
mininet> h6 iperf -s &
mininet> h7 iperf -c 10.0.2.1 -t 120 &
```

**Quan sát sau 10–30 giây:**

- **Terminal 1**: log `[A][DALB] ρ=0.xxx < 0.70 → migrate`  sau đó `[A][MIGRATE] SUCCESS`
- **Dashboard**: thanh load A chuyển đỏ, ρ giảm dưới 0.7, migration log xuất hiện entry mới
- **Terminal 2**: log `[B][MIGRATE] Received SX → MASTER`

---

### Bước 7 — Xác nhận kết quả

```bash
# Kiểm tra phân bổ switch sau migrate
curl http://localhost:8080/status | python3 -m json.tool
curl http://localhost:8081/status | python3 -m json.tool
```

**Kết quả đúng**: A còn ít switch hơn, B nhận thêm, ρ tăng về ≥ 0.7.

---

### Bước 8 — Dọn dẹp

```
mininet> h1 kill %1 2>/dev/null
mininet> h2 kill %1 2>/dev/null
mininet> h3 kill %1 2>/dev/null
mininet> h4 kill %1 2>/dev/null
mininet> exit
```

```bash
sudo mn -c
# Ctrl+C ở Terminal 1 và Terminal 2
```

---

## 7. Chạy Auto Demo

Script `auto_demo.py` tự động toàn bộ quy trình — không cần chạy `topology.py`.

> **Lưu ý**: Không chạy `topology.py` và `auto_demo.py` cùng lúc.

### Bước 1 — Dọn + khởi động 2 controller

```bash
sudo mn -c

# Terminal 1:
cd ~/Downloads/SDN_FInal/project
ryu-manager controller_a.py --ofp-tcp-listen-port 6633

# Terminal 2:
cd ~/Downloads/SDN_FInal/project
ryu-manager controller_b.py --ofp-tcp-listen-port 6634
```

### Bước 2 — (Tuỳ chọn) Mở Dashboard để quan sát

```bash
xdg-open ~/Downloads/SDN_FInal/project/dashboard.html
```

### Bước 3 — Chạy auto demo

```bash
cd ~/Downloads/SDN_FInal/project
sudo python3 auto_demo.py
```

| Phase | Thời gian | Nội dung |
|-------|-----------|---------|
| Phase 1 | ~60s | Baseline ping, đo tải ban đầu |
| Phase 2 | ~60s | Heavy iperf traffic vào Domain A |
| Phase 3 | tự động | Chờ phát hiện migrate event |
| Phase 4 | ngay sau | Kiểm tra ρ, in PASS/FAIL |

**Output cuối:**
```
============================================================
  DEMO KẾT QUẢ
  Controller A: 1 switch(es), load = 89.2
  Controller B: 4 switch(es), load = 95.1
  rho = 0.938 >= 0.70  →  BALANCED ✅
============================================================
```

---

## 8. Kiểm tra hệ thống

### Chạy test API tự động

```bash
cd ~/Downloads/SDN_FInal/project
python3 test_scenarios.py
```

Script kiểm tra **8 nhóm test**:

| Nhóm | Nội dung |
|------|---------|
| TEST 1 | Controller A và B có thể kết nối |
| TEST 2 | Cấu trúc JSON của `/load` đúng |
| TEST 3 | Cấu trúc JSON của `/status` đúng (kể cả migration_log, peer_status) |
| TEST 4 | Giá trị ρ khớp với công thức mean/max |
| TEST 5 | Endpoint `/arp?ip=...` hoạt động |
| TEST 6 | Tổng số switch = 5, phân bổ hợp lý |
| TEST 7 | CORS header có mặt (cần cho dashboard) |
| TEST 8 | `/migrate` validate payload đúng |

**Output mẫu:**
```
── TEST 1: Controller Connectivity ──────────────────────
  [PASS] Controller A reachable (port 8080)
  [PASS] Controller B reachable (port 8081)

── TEST 4: Rho Validation ───────────────────────────────
  [PASS] Controller A rho matches computed value  (ρ=0.823 ≈ 0.821)
  [PASS] Controller B rho matches computed value  (ρ=0.823 ≈ 0.821)
  [PASS] System currently balanced  (ρ=0.821 ≥ 0.7)

── SUMMARY ──────────────────────────────────────────────
  PASS:  24
  FAIL:   0
  SKIP:   2   (skipped = not an error)
  All tests PASSED ✅
```

> `SKIP` là bình thường: nghĩa là test đó cần thêm điều kiện (ví dụ cần chạy `pingall` trước để ARP được học).

### Chạy unit test DALB

```bash
python3 dalb_module.py
# All tests PASSED ✅
```

---

## 9. REST API

| Method | Endpoint | Port A | Port B | Mô tả |
|--------|---------|:------:|:------:|-------|
| GET | `/load` | 8080 | 8081 | Tải từng switch + tổng |
| GET | `/status` | 8080 | 8081 | Trạng thái đầy đủ + migration_log + peer_status |
| GET | `/arp?ip=x.x.x.x` | 8080 | 8081 | Tra MAC của IP trong domain |
| POST | `/migrate` | 8080 | 8081 | Nhận switch (body: `{"dpid":N,"name":"SN"}`) |

### GET /load — ví dụ

```bash
curl http://localhost:8080/load
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

### GET /status — ví dụ

```bash
curl http://localhost:8080/status
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

### Theo dõi tải liên tục

```bash
watch -n 5 'echo "=== A ===" && curl -s http://localhost:8080/load | python3 -m json.tool; echo "=== B ===" && curl -s http://localhost:8081/load | python3 -m json.tool'
```

---

## 10. Dashboard

File `dashboard.html` — không cần web server, kéo vào trình duyệt là dùng được.

### Cách mở

```bash
xdg-open ~/Downloads/SDN_FInal/project/dashboard.html
# Hoặc: kéo file vào Chrome / Firefox
```

### Thành phần giao diện

| Thành phần | Mô tả |
|---|---|
| Status badge | ONLINE (xanh) / OFFLINE (đỏ) — tự detect |
| Load bar | Thanh % load, chuyển đỏ khi vượt 80% CT |
| Managed switches | Danh sách switch đang quản lý |
| Peer status | Trạng thái controller kia (ONLINE/OFFLINE) |
| Per-switch table | Flows, RTT, C_Load từng switch |
| ρ gauge | Chỉ số 0–1, ngưỡng 0.7 đánh dấu đường đỏ |
| Line chart | Lịch sử 60 điểm (3 phút), update mỗi 3s |
| Migration log | Danh sách migrate có timestamp |

> **Khi 1 controller offline**: dashboard vẫn hiển thị controller kia bình thường, controller offline hiện badge đỏ "OFFLINE".

---

## 11. Kết quả mong đợi

### Trạng thái ban đầu (không có traffic)

```
Controller A: S1, S2  — load thấp
Controller B: S3, S4, S5  — load thấp
ρ ≈ 1.0  →  cân bằng
```

### Sau heavy traffic vào Domain A

```
Controller A tải tăng cao
ρ < 0.7  →  C1 thỏa
A là controller có load lớn nhất  →  C2 thỏa
→ A chọn switch thỏa L ≤ Δ/2
→ A gửi OFPRoleRequest SLAVE cho switch đó
→ A gọi POST /migrate đến B
→ B gửi OFPRoleRequest MASTER
→ Switch chuyển sang B (host không bị ngắt)
```

### Sau migrate

```
Controller A: ít switch hơn, load giảm
Controller B: thêm switch, load tăng vừa
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
│ Total: 212.4/s | CT=1000 | NORMAL                              │
└────────────────────────────────────────────────────────────────┘
[A][DALB] Load=1149.9/s >= CT=1000 | EXCEEDED ⚠️
[A][DALB] ρ=0.558 < 0.70 → MIGRATE
[A][MIGRATE] S2 (load=87.1) → Controller B
[A][MIGRATE] ✅ SUCCESS: S2 → B
```

---

## 12. Câu hỏi thường gặp

**Q: `pingall` lần đầu có vài lỗi?**  
A: Bình thường. ARP cross-domain cần học MAC lần đầu. Gõ lại `pingall` lần 2 là 100%.

**Q: `/status` trả về `"controller": "B"` nhưng đang chạy controller_a.py?**  
A: Hai controller đang dùng cùng REST port (mặc định 8080 của Ryu). Giải pháp: đảm bảo **chỉ chạy 1 controller tại một thời điểm** hoặc luôn dùng đúng lệnh `ryu-manager controller_a.py --ofp-tcp-listen-port 6633` và `ryu-manager controller_b.py --ofp-tcp-listen-port 6634`. Code đã tự set port qua `_ryu_cfg.CONF.wsapi_port`.

**Q: Dashboard hiển thị OFFLINE mặc dù controller đang chạy?**  
A: Kiểm tra bằng `curl http://localhost:8080/load`. Nếu trả về JSON thì controller đang chạy nhưng port sai. Nếu lỗi kết nối thì controller chưa start xong, chờ thêm 5 giây.

**Q: Không thấy migrate dù đã sinh nhiều traffic?**  
A: Load cần vượt CT (mặc định 1000). Thêm nhiều luồng iperf đồng thời (5+ luồng). Chờ 2 chu kỳ monitor (~20 giây). Kiểm tra log terminal để xem lý do.

**Q: `test_scenarios.py` báo SKIP?**  
A: SKIP không phải lỗi. Nghĩa là test cần điều kiện chưa có — ví dụ cần chạy `pingall` để ARP test hoạt động. Sau `pingall` chạy lại test là sẽ PASS.

**Q: Lỗi `RTNETLINK answers: File exists`?**  
A: Có Mininet cũ đang chạy. Chạy `sudo mn -c` rồi thử lại.

**Q: Lỗi `TypeError: cannot set 'is_timeout'`?**  
A: Sai eventlet version. Chạy: `pip install "eventlet==0.33.3" "dnspython==2.8.0"`.

**Q: `ryu-manager: command not found`?**  
A: Chạy: `export PATH=$PATH:~/.local/bin` hoặc dùng `python3 -m ryu.cmd.manager controller_a.py --ofp-tcp-listen-port 6633`.

---

## Tài liệu tham khảo

- He, J., et al. *"A Load Balancing Strategy for SDN Controller based on Distributed Decision."* IEEE TrustCom, 2014.
- [Ryu SDN Framework Documentation](https://ryu.readthedocs.io/)
- [OpenFlow 1.3 Specification](https://opennetworking.org/wp-content/uploads/2014/10/openflow-spec-v1.3.0.pdf)
- [Mininet Documentation](http://mininet.org/docs/)
