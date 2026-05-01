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
6. [Chạy Demo thủ công](#6-chạy-demo-thủ-công)
7. [Chạy Auto Demo](#7-chạy-auto-demo)
8. [REST API](#8-rest-api)
9. [Kết quả mong đợi](#9-kết-quả-mong-đợi)
10. [Câu hỏi thường gặp](#10-câu-hỏi-thường-gặp)

---

## 1. Tổng quan

Dự án xây dựng hệ thống cân bằng tải cho SDN controller theo mô hình **phân tán** — không cần super-controller trung tâm. Hai Ryu controller (A và B) tự trao đổi thông tin qua REST API, tính toán chỉ số tải theo thuật toán DALB và tự động **migrate switch** từ controller bị quá tải sang controller nhàn rỗi hơn mà không làm gián đoạn kết nối của host.

### Tính năng chính

| Tính năng | Mô tả |
|---|---|
| DALB Algorithm | Công thức tải C_Load = w1×N + w2×F + w3×R |
| Adaptive CT | Ngưỡng kiểm tra tự điều chỉnh theo tải thực tế |
| ARP Proxy | Controller xử lý ARP cross-domain, không flood toàn mạng |
| Switch Migration | OFPRoleRequest SLAVE→MASTER, không ngắt kết nối host |
| REST API | GET /load, GET /status, GET /arp, POST /migrate |
| Web Dashboard | Biểu đồ real-time Chart.js, cập nhật mỗi 3 giây |
| Auto Demo | Script tự động 4 phase, kiểm tra kết quả PASS/FAIL |
| Unit Tests | 13 test case cho toàn bộ logic DALB, tất cả PASS |

---

## 2. Kiến trúc hệ thống

```
         Domain A  ─  Controller A (OpenFlow :6633 | REST :8080)
         ┌──────────────────────────────────┐
         │   S1 ─────── h1 (10.0.1.1)       │
         │    │   ───── h2 (10.0.1.2)       │
         │    │   ───── h3 (10.0.1.3)       │
         │    │                             │
         │   S2 ─────── h4 (10.0.1.4)       │
         │         ─── h5 (10.0.1.5)        │
         └──────────────┼───────────────────┘
                        │  inter-domain link
                        │  S2:port4 ↔ S4:port4
         ┌──────────────┼───────────────────┐
         │   S4 ─────── h9 (10.0.2.4)       │
         │    │                             │
         │   S3 ─────── h6 (10.0.2.1)       │
         │         ─── h7 (10.0.2.2)        │
         │         ─── h8 (10.0.2.3)        │
         │    │                             │
         │   S5 ─────── h10 (10.0.2.5)      │
         │         ─── h11 (10.0.2.6)       │
         └──────────────────────────────────┘
         Domain B  ─  Controller B (OpenFlow :6634 | REST :8081)
```

### Phân công ban đầu

| Switch | Controller mặc định | Hosts kết nối |
|--------|--------------------|-----------------------------------------|
| S1 | A | h1 (10.0.1.1), h2 (10.0.1.2), h3 (10.0.1.3) |
| S2 | A | h4 (10.0.1.4), h5 (10.0.1.5) — port4: inter-domain |
| S3 | B | h6 (10.0.2.1), h7 (10.0.2.2), h8 (10.0.2.3) |
| S4 | B | h9 (10.0.2.4) — port4: inter-domain |
| S5 | B | h10 (10.0.2.5), h11 (10.0.2.6) |

> Tất cả host dùng subnet `/16` (10.0.0.0/16) để ARP hoạt động cross-domain mà không cần gateway.

---

## 3. Thuật toán DALB

### Công thức tính tải controller

```
C_Load = w1 × N  +  w2 × F  +  w3 × R
```

| Ký hiệu | Ý nghĩa | Trọng số mặc định |
|---------|---------|-------------------|
| N | Số flow entry active trên switch | w1 = 0.1 |
| F | Flow rate — số packet/giây | w2 = 0.8 |
| R | Round-trip time đến controller (ms) | w3 = 0.1 |

### Chỉ số cân bằng ρ (rho)

```
ρ = mean(all controller loads) / max(all controller loads)
```

| Giá trị ρ | Ý nghĩa |
|-----------|---------|
| ρ = 1.0 | Hoàn toàn cân bằng |
| 0.7 ≤ ρ < 1.0 | Tạm chấp nhận |
| ρ < 0.7 | Mất cân bằng → kích hoạt migrate |

### Điều kiện để migrate (phải thỏa đồng thời cả 2)

| Điều kiện | Mô tả |
|-----------|-------|
| C1 | ρ < 0.7 (toàn cụm mất cân bằng) |
| C2 | Controller này đang có tải **lớn nhất** trong cụm |

### Chọn switch để migrate

```
Chọn switch có load lớn nhất thỏa: L_switch ≤ (L_overloaded − L_target) / 2
```

### Adaptive CT

```
δ = mean(loads);   CT = δ nếu δ > ICT,  ngược lại CT = ICT (1000)
```

---

## 4. Cấu trúc file

```
project/
├── dalb_module.py      ← Logic DALB thuần + 13 unit test (python3 dalb_module.py)
├── topology.py         ← Mininet: 2 controller, 5 switch, 11 host
├── controller_a.py     ← Ryu app Controller A  | OF :6633 | REST :8080
├── controller_b.py     ← Ryu app Controller B  | OF :6634 | REST :8081
├── auto_demo.py        ← Script tự động 4-phase demo
├── traffic_gen.py      ← Hướng dẫn sinh traffic bằng tay
├── dashboard.html      ← Web dashboard real-time (không cần web server)
└── README.md           ← File này
```

---

## 5. Cài đặt môi trường

### Yêu cầu hệ thống

| Phần mềm | Phiên bản | Cài đặt |
|----------|-----------|---------|
| Python | 3.9.x / 3.10.x | `sudo apt install python3` |
| Mininet | 2.3+ | `sudo apt install mininet` |
| Open vSwitch | 2.13+ | đi kèm Mininet |
| Ryu | 4.34 | `pip install ryu` |
| eventlet | **0.33.3** | `pip install "eventlet==0.33.3"` |
| dnspython | **2.8.0** | `pip install "dnspython==2.8.0"` |
| requests | bất kỳ | `pip install requests` |

### Cài đặt toàn bộ một lần

```bash
sudo apt update
sudo apt install -y mininet python3-pip

pip install ryu
pip install "eventlet==0.33.3"
pip install "dnspython==2.8.0"
pip install requests
```

> **Bắt buộc**: eventlet phải đúng version **0.33.3**. Sai version sẽ gây lỗi `TypeError: cannot set 'is_timeout' attribute` ngay khi khởi động Ryu.

### Kiểm tra cài đặt

```bash
python3 -c "import ryu; print('Ryu OK')"
python3 -c "import eventlet; print('eventlet', eventlet.__version__)"
python3 -c "import mininet; print('Mininet OK')"
```

### Patch Ryu WSGI (nếu gặp lỗi ALREADY_HANDLED)

```bash
# Bước 1: tìm file cần sửa
python3 -c "import ryu.app.wsgi; print(ryu.app.wsgi.__file__)"
# Ví dụ output: /home/user/.pyenv/versions/3.9.18/lib/python3.9/site-packages/ryu/app/wsgi.py

# Bước 2: mở file đó và tìm dòng:
#   from eventlet.wsgi import ALREADY_HANDLED
# Thay thành:
#   try:
#       from eventlet.wsgi import ALREADY_HANDLED
#   except ImportError:
#       ALREADY_HANDLED = False
```

### Kiểm tra unit test DALB

```bash
cd ~/Downloads/SDN_FInal/project
python3 dalb_module.py
# Output cuối: All tests PASSED ✅
```

---

## 6. Chạy Demo thủ công

Demo thủ công cho phép quan sát toàn bộ quá trình theo thời gian thực. Cần mở **4 terminal** cùng lúc.

---

### Bước 0 — Dọn Mininet cũ (bắt buộc trước mỗi lần chạy)

```bash
sudo mn -c
```

---

### Bước 1 — Khởi động Controller A

> Mở **Terminal 1**, chạy lệnh:

```bash
cd ~/Downloads/SDN_FInal/project
ryu-manager controller_a.py --ofp-tcp-listen-port 6633
```

**Chờ thấy dòng sau rồi mới sang bước tiếp:**

```
[A] Controller A ready — OF=6633  REST=http://0.0.0.0:8080
```

---

### Bước 2 — Khởi động Controller B

> Mở **Terminal 2**, chạy lệnh:

```bash
cd ~/Downloads/SDN_FInal/project
ryu-manager controller_b.py --ofp-tcp-listen-port 6634
```

**Chờ thấy dòng sau rồi mới sang bước tiếp:**

```
[B] Controller B ready — OF=6634  REST=http://0.0.0.0:8081
```

---

### Bước 3 — Khởi động Mininet

> Mở **Terminal 3**, chạy lệnh:

```bash
cd ~/Downloads/SDN_FInal/project
sudo python3 topology.py
```

Chờ xuất hiện prompt `mininet>` (mất khoảng 5–10 giây). Sau đó kiểm tra kết nối:

```
mininet> pingall
```

**Kết quả mong đợi:**
```
*** Ping: testing ping reachability
h1 -> h2 h3 h4 h5 h6 h7 h8 h9 h10 h11
h2 -> h1 h3 h4 ...
...
*** Results: 0% dropped (110/110 received)
```

> Nếu lần đầu có vài gói lỗi (ARP chưa học xong), gõ lại `pingall` lần 2 — sẽ 100%.

---

### Bước 4 — Mở Dashboard

> Mở **Terminal 4**, chạy lệnh:

```bash
xdg-open ~/Downloads/SDN_FInal/project/dashboard.html
```

Hoặc kéo thả file `dashboard.html` vào Chrome / Firefox.

**Giao diện dashboard gồm:**

| Thành phần | Mô tả |
|---|---|
| Status cards | Tải hiện tại, ρ, số switch đang quản lý của từng controller |
| Load bar | Thanh % tải — chuyển đỏ khi vượt 70% |
| Rho gauge | Chỉ số 0–1, đường ngưỡng 0.7 màu đỏ |
| Line chart | Lịch sử 60 điểm (3 phút), cập nhật mỗi 3 giây |
| Switch table | Chi tiết từng switch: flows, flow_rate (p/s), RTT (ms), C_Load |
| Migration log | Danh sách các lần migrate đã xảy ra với timestamp |

> Dashboard tự động cập nhật — không cần refresh tay.

---

### Bước 5 — Sinh traffic để kích hoạt migrate

Quay lại **Terminal 3** (Mininet CLI), gõ các lệnh sau để tạo traffic nặng vào Domain A:

```
mininet> h1 iperf -s &
mininet> h2 iperf -c 10.0.1.1 -t 120 &
mininet> h3 iperf -c 10.0.1.1 -t 120 &
mininet> h4 iperf -c 10.0.1.1 -t 120 &
mininet> h5 iperf -c 10.0.1.1 -t 120 &
```

Thêm traffic cross-domain để Controller B nhàn rỗi hơn:

```
mininet> h6 iperf -s &
mininet> h7 iperf -c 10.0.2.1 -t 120 &
```

**Quan sát:**
- **Terminal 1** (Controller A): sau 10–20 giây sẽ thấy log `[A][MONITOR] ρ=0.xxx < 0.70 → migrate`
- **Dashboard**: thanh load của Controller A chuyển đỏ, ρ giảm xuống dưới 0.7, migration log xuất hiện entry mới
- **Terminal 1**: log `[A][MIGRATE] S2 set to SLAVE — migration complete`

---

### Bước 6 — Xác nhận kết quả migrate

Trong **Terminal 4**, kiểm tra trạng thái sau migrate:

```bash
# Xem switch nào Controller A còn quản lý
curl http://localhost:8080/status | python3 -m json.tool

# Xem Controller B đã nhận switch nào
curl http://localhost:8081/status | python3 -m json.tool

# So sánh tải 2 controller
curl http://localhost:8080/load | python3 -m json.tool
curl http://localhost:8081/load | python3 -m json.tool
```

**Kết quả đúng**: ρ đã tăng về ≥ 0.7, Controller A bớt tải, Controller B nhận thêm switch.

---

### Bước 7 — Dừng traffic và dọn dẹp

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
# Nhấn Ctrl+C ở Terminal 1 và Terminal 2 để tắt controller
```

---

## 7. Chạy Auto Demo

Script `auto_demo.py` tự động thực hiện toàn bộ 4 phase demo, tự tạo Mininet bên trong.

> **Lưu ý**: KHÔNG chạy `topology.py` trước — auto_demo tự quản lý Mininet.

---

### Bước 0 — Dọn Mininet cũ

```bash
sudo mn -c
```

---

### Bước 1 — Khởi động Controller A

> Mở **Terminal 1**:

```bash
cd ~/Downloads/SDN_FInal/project
ryu-manager controller_a.py --ofp-tcp-listen-port 6633
```

Chờ thấy: `[A] Controller A ready — OF=6633  REST=http://0.0.0.0:8080`

---

### Bước 2 — Khởi động Controller B

> Mở **Terminal 2**:

```bash
cd ~/Downloads/SDN_FInal/project
ryu-manager controller_b.py --ofp-tcp-listen-port 6634
```

Chờ thấy: `[B] Controller B ready — OF=6634  REST=http://0.0.0.0:8081`

---

### Bước 3 — Mở Dashboard (tuỳ chọn, nên mở trước để quan sát)

```bash
xdg-open ~/Downloads/SDN_FInal/project/dashboard.html
```

---

### Bước 4 — Chạy Auto Demo

> Mở **Terminal 3**:

```bash
cd ~/Downloads/SDN_FInal/project
sudo python3 auto_demo.py
```

Script sẽ tự động chạy 4 phase:

| Phase | Thời gian | Nội dung |
|-------|-----------|---------|
| Phase 1 | ~60 giây | Baseline — ping kiểm tra, đo tải ban đầu |
| Phase 2 | ~60 giây | Heavy traffic — iperf nhiều luồng vào Domain A |
| Phase 3 | tự động | Chờ và phát hiện migrate switch |
| Phase 4 | ngay sau | Kiểm tra ρ sau migrate, in PASS/FAIL |

**Output mẫu cuối demo:**

```
============================================================
  DEMO KẾT QUẢ
============================================================
  Controller A: 1 switch(es), load = 89.2
  Controller B: 4 switch(es), load = 95.1
  rho = 0.938 >= 0.70  →  BALANCED ✅
============================================================
```

---

## 8. REST API

Cả hai controller đều cung cấp REST API. Hỗ trợ CORS (có thể gọi từ trình duyệt).

| Method | Endpoint | Controller A | Controller B | Mô tả |
|--------|---------|:---:|:---:|-------|
| GET | `/load` | :8080 | :8081 | Tải tóm tắt + ρ |
| GET | `/status` | :8080 | :8081 | Trạng thái đầy đủ + migration log |
| GET | `/arp?ip=10.0.x.x` | :8080 | :8081 | Tra MAC của IP trong domain |
| POST | `/migrate` | :8080 | :8081 | Nhận switch từ controller kia |

### Ví dụ GET /load

```bash
curl http://localhost:8080/load
```
```json
{
  "controller": "A",
  "switches": {
    "1": { "load": 125.3, "flows": 12, "flow_rate": 148.2, "rtt": 0.8 },
    "2": { "load":  87.1, "flows":  8, "flow_rate": 102.4, "rtt": 0.7 }
  },
  "total_load": 212.4,
  "rho": 0.82
}
```

### Ví dụ GET /status

```bash
curl http://localhost:8081/status
```
```json
{
  "controller": "B",
  "my_load": 310.5,
  "rho": 0.68,
  "ct": 1200.0,
  "managed_switches": [3, 4, 5],
  "migration_log": [
    { "time": "14:32:05", "switch": "S2", "from": "A", "to": "B" }
  ]
}
```

### Ví dụ POST /migrate (gọi thủ công)

```bash
# Yêu cầu Controller B nhận switch có dpid=2 (S2)
curl -X POST http://localhost:8081/migrate \
     -H "Content-Type: application/json" \
     -d '{"dpid": 2, "name": "S2"}'
```

### Theo dõi tải liên tục bằng watch

```bash
watch -n 5 'echo "=== Controller A ===" && curl -s http://localhost:8080/load | python3 -m json.tool; echo "=== Controller B ===" && curl -s http://localhost:8081/load | python3 -m json.tool'
```

---

## 9. Kết quả mong đợi

### Trạng thái ban đầu

```
Controller A  →  S1, S2          (tải thấp, cân bằng)
Controller B  →  S3, S4, S5      (tải thấp, cân bằng)
ρ ≈ 1.0
```

### Sau khi sinh traffic nặng vào Domain A

```
Controller A tải tăng cao
ρ < 0.7  →  C1 thỏa (toàn cụm mất cân bằng)
A có tải cao nhất  →  C2 thỏa (A là người overloaded)

Quá trình migrate:
  A chọn switch (load ≤ Δ/2)  →  gửi OFPRoleRequest SLAVE
  A gửi POST /migrate đến B
  B nhận  →  gửi OFPRoleRequest MASTER cho switch đó
  Switch chuyển sang B mà không ngắt host
```

### Sau migrate

```
Controller A  →  S1 (ví dụ)
Controller B  →  S2, S3, S4, S5
ρ tăng về ≥ 0.7  →  cân bằng lại
```

### Log điển hình trên Terminal 1 (Controller A)

```
[A][MONITOR] ── Load Report ────────────────────────────────────────────
[A]  Switch │ Flows │  Flow Rate   │  RTT(ms) │ C_Load
[A] ────────┼───────┼──────────────┼──────────┼──────────────────────
[A]  S1     │    12 │   148.2 p/s  │     0.8  │   125.3
[A]  S2     │     8 │   102.4 p/s  │     0.7  │    87.1
[A] ────────┴───────┴──────────────┴──────────┴──────────────────────
[A][MONITOR] my_total=212.4  peer_total=89.3  ρ=0.558 < 0.70 → migrate
[A][MIGRATE] Candidate: S2 (load=87.1)  Δ=(212.4−89.3)/2=61.5  OK
[A][MIGRATE] Sending S2 to Controller B via POST /migrate
[A][MIGRATE] S2 set to SLAVE — waiting for B to take over
[A][MIGRATE] Migration complete. Now managing: [S1]
```

---

## 10. Câu hỏi thường gặp

**Q: `pingall` lần đầu có vài lỗi?**  
A: Bình thường. ARP cross-domain cần một lần học MAC. Gõ lại `pingall` lần 2 là 100%.

**Q: Dashboard trắng / không hiển thị dữ liệu?**  
A: Kiểm tra 2 controller đang chạy bằng `curl http://localhost:8080/load`. Nếu lỗi kết nối thì controller chưa khởi động xong, chờ thêm vài giây rồi refresh.

**Q: Không thấy migrate xảy ra dù đã sinh traffic?**  
A: Cần đủ luồng iperf để ρ thực sự < 0.7. Thêm nhiều client hơn (h2, h3, h4, h5 cùng gửi đến h1). Chờ ít nhất 2 chu kỳ monitor (~20 giây).

**Q: Lỗi `RTNETLINK answers: File exists` khi chạy auto_demo.py?**  
A: Đang có Mininet instance khác chạy. Gõ `exit` trong Mininet CLI, sau đó `sudo mn -c`, rồi chạy lại.

**Q: `/arp?ip=...` trả về 404?**  
A: Bình thường khi host chưa gửi traffic nào. Sau `pingall` controller sẽ tự học MAC và trả về đúng.

**Q: Lỗi `TypeError: cannot set 'is_timeout' attribute`?**  
A: Sai phiên bản eventlet. Chạy: `pip install "eventlet==0.33.3" "dnspython==2.8.0"` rồi khởi động lại controller.

**Q: `ryu-manager: command not found`?**  
A: Thêm pip bin vào PATH:
```bash
export PATH=$PATH:~/.local/bin
# Hoặc dùng:
python3 -m ryu.cmd.manager controller_a.py --ofp-tcp-listen-port 6633
```

**Q: auto_demo.py và topology.py có thể chạy cùng lúc không?**  
A: Không. Chỉ được có một Mininet instance tại một thời điểm. Chọn một trong hai, thoát và `sudo mn -c` trước khi chạy cái kia.

---

## Tài liệu tham khảo

- He, J., et al. *"A Load Balancing Strategy for SDN Controller based on Distributed Decision."* IEEE TrustCom, 2014.
- [Ryu SDN Framework Documentation](https://ryu.readthedocs.io/)
- [OpenFlow 1.3 Specification](https://opennetworking.org/wp-content/uploads/2014/10/openflow-spec-v1.3.0.pdf)
- [Mininet Documentation](http://mininet.org/docs/)
