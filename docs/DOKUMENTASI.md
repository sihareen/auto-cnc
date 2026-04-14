# Cara Kerja Sistem Auto CNC Drill

Dokumen ini menjelaskan alur kerja sistem dengan bahasa yang mudah dipahami.

---

## Gambaran Umum

Sistem ini membantuoperator PCB untuk mengebor lubang pad secara otomatis.

**Caranya:**
1. Kamera mengambil foto PCB
2. AI (YOLOv7) mendeteksi где lubang
3. Koordinat lubang diubah dari piksel ke millimeter
4. CNC mengebor lubang satu per satu

---

## Komponen Utama

| Komponen | Fungsi |
|----------|--------|
| **Kamera** | Mengambil foto PCB |
| **YOLOv7** | Mendeteksi lokasi lubang |
| **Kalibrasi** | Mengubah koordinat kamera ke CNC |
| **CNC 3-Axis** | Mengebor lubang di PCB |
| **Web UI** | Interface untuk operator |

---

## Tahap-Tahap Kerja

### Tahap 1: Siapkan PCB

Langkah:
- Place PCB di workspace CNC
- Pastikan PCB sudah di-clamp dengan aman

### Tahap 2: Click 1 - Standby

Klik **START** pertama:
- CNC move ke posisi standby (Z-up dulu, lalu XY standby)
- **Tujuannya:** Spindle naik dulu untuk clearance, baru bergerak ke posisi standby

### Tahap 3: Click 2 - Capture & Pause

Klik **START** kedua:
- Kamera ambil foto PCB
- YOLOv7 detect semua padhole
- Koordinat piksel → transformasi ke koordinat mesin (mm)
- CNC move ke padhole pertama (bbox merah di overlay)
- **PAUSE** di sini

**Output:** Daftar koordinat lubang, CNC berhenti di hole pertama

### Tahap 4: Click 3 - Jog & Drill

Opsi A: **Klik START** (langsung drill semua hole)

Opsi B: **Jog Manual** dulu untuk koreksi:
- Jog X/Y/Z untuk geser posisi drill sampai tepat di target
- Setiap jog offset diakumulasi
- Klik **START** → drill pakai work coordinate (original + offset)

### Tahap 5: Selesai

Setelah drilling selesai:
- CNC return ke HOME
- Status: IDLE

---

## State Machine

Sistem bekerja dalam beberapa state:

```
IDLE
    ↓ Start
STANDBY (Y-up, XY standby)
    ↓ Start
ACQUIRING (capture & detect)
    ↓
TRANSFORM (pixel → mm)
    ↓
PAUSED_AT_PADHOLE (bbox merah, siap koreksi jog)
    ↓ Start/Jog
DRILLING (mengebor satu-satu)
    ↓
HOME (kembali ke origin)
    ↓
IDLE
```

**Jog Offset:** Saat PAUSED_AT_PADHOLE, operator bisa jog x/y/z. Offset diakumulasi dan dipakai saat drilling.

**Error states:**
- ERROR_DETECTION: Tidak ada lubang terdeteksi
- ERROR_HARDWARE: Masalah dengan CNC
- ERROR_CAMERA: Kamera bermasalah

---

## Error Handling

### 1. Tidak Ada Lubang Terdeteksi

**Gejala:** YOLOv7 tidak mendeteksi apapun

**Penanganan:**
- Alert ke operator
- Coba lagi dengan threshold lebih rendah
- Jika tetap gagal, operator harus cek PCB

### 2. Lubang di Luar Workspace

**Gejala:** Koordinat hasil transformasi di luar batas kerja CNC

**Penanaan:**
- Clipping ke batas workspace
- Warning ke operator
- Skip lubang yang out-of-bounds

### 3. CNC Tidak Merespon

**Gejala:** Timeout komunikasi GRBL

**Penanganan:**
- Retry dengan exponential backoff (1s, 2s, 4s...)
- Setelah 3x gagal, alert operator
- Re-home required

### 4. Kamera Terputus

**Gejala:** Tidak bisa dapat frame dari kamera

**Penanganan:**
- Pause proses
- Attempt reconnect
- Alert operator

---

## Interface Web UI

### Halaman Utama

**Tampil:**
- Live video dari kamera
- Status machine (position, state)
- Tombol kontrol

### Kontrol

| Tombol | Fungsi |
|-------|--------|
| **Start** | Mulai proses drilling |
| **Pause** | Jeda sementara |
| **Stop** | Stop total, kembali ke IDLE |
| **Home** | Kembali ke posisi awal |

### Status Display

- **Machine Position:** X, Y, Z realtime
- **Current State:** IDLE/HOMING/ACQUIRING/etc
- **Progress:** drilled/total holes
- **Last Error:**Jika ada error

---

## Sinkronisasi UI dan Hardware

Komunikasi menggunakan WebSocket untuk updates realtime:

```
Web Browser                     Server                        Hardware
    │                            │                             │
    │ ◄───── Position Update ───│                             │
    │ ◄───── State Update ──────│                             │
    │                            │                             │
    │ ──── Control Command ───►│                             │
    │                            │ ──── GRBL Command ──────►│
    │                            │                             │
    │                            │ ◄─── Status Feedback ──────│
    │                            │                             │
```

**Update frequency:**
- Position: 10x per detik
- State change: on-event
- Video stream: MJPEG (HTTP)

---

## Contoh Sederhana

Misalnya PCB punya 9 lubang:

```
Input dari YOLOv7 (piksel):
┌───┬───┬───┐
│ 1 │ 2 │ 3 │
├───┼───┼───┤
│ 4 │ 5 │ 6 │
├───┼───┼───┤
│ 7 │ 8 │ 9 │
└───┴───┴───┘

Output G-Code (mm):
- Urutan: 1→2→3→4→5→6→7→8→9
- Setiap point: G0 X206 Y-119.3 (rapid) → G1 Z-1.5 (drill) → G0 Z5 (up)
- Total waktu: ~30 detik (tergantung speed)
```

---

## Ringkasan Alur

```
1. Operator place PCB → Clamp
2. Klik "START" → CNC move standby
3. Klik "START" → Capture, detect, move ke first padhole, PAUSE
4. [Optional] Jog manual x/y/z untuk koreksi
5. Klik "START" → Drill semua hole (dengan offset jika ada), return HOME
6. Selesai → CNC HOME, kembali ke IDLE
```

**3-Click Workflow:**
| Click | Action |
|-------|--------|
| 1 | Move ke standby |
| 2 | Capture & pause di first padhole |
| 3 | Drill (atau jog dulu baru drill) |

Sistem ini semi-otomatis: operator tetap perlu:
- Place/clamp PCB
- Klik start 3x
- Monitor progress
- Handle error jika ada
- Jog koreksi jika perlu