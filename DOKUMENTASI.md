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

### Tahap 2: Home/Standby

CNC kembali ke posisi awal (home) dengan Y-axis ditinggikan (Y-90) untuk clearance.

**Tujuannya:** Agar spindle tidak Membentur PCB saat bergerak

### Tahap 3: Ambil Foto & Deteksi

Kamera mengambil foto PCB, lalu YOLOv7 mendeteksi semua lubang pad.

**Output:** Daftar koordinat lubang dalam piksel (x, y)

### Tahap 4: Transformasi Koordinat

Koordinat piksel diubah ke koordinat millimeter menggunakan matriks affine dari calibration.

```
Koordinat Piksel (dari YOLOv7)
        ↓
Matriks Affine
        ↓
Koordinat MM (untuk CNC)
```

**Kenapa perlu transformasi?**
- Kamara melihat dari atas (bidang 2D)
- CNC mengebor di bidang XY dengan koordinat absolut
- Antara kamera dan CNC ada perbedaan rotasi, skala, dan posisi

**Matriks yang digunakan:**
- Berasal dari `calibration_affine.json`
- Reprojection error: ~1.22mm (tingkat ketelitian)

### Tahap 5: Generate G-Code

Dari koordinat yang sudah ditransformasi, dibuat G-Code untuk drilling.

**Includes:**
- Urutan pengeboran (terdekat dulu untuk efisiensi)
- Kedalaman drilling
- Kecepatan spindle
- Clearance antar lubang

### Tahap 6: Eksekusi Drilling

CNC mengebor lubang satu per satu sesuai G-Code yang sudah生成.

**Selama proses:**
- Operator bisa monitor via Web UI
- Bisa pause/stop jika perlu
- Progress ditampilkan realtime

---

## State Machine

Sistem bekerja dalam beberapa state:

```
IDLE (Siap)
    ↓ Start
HOMING (Ke posisi awal)
    ↓
ACQUIRING (Ambil foto & deteksi)
    ↓
TRANSFORM (Ubah koordinat)
    ↓
READY (Siap drilling)
    ↓ Start Drill
DRILLING (Mengebor)
    ↓
COMPLETE (Selesai)
    ↓
IDLE
```

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
2. Klik "Start" di Web UI
3. CNC home (Y-90)
4. Kamera capture → YOLOv7 detect
5. Koordinat piksel → transform ke mm (affine)
6. Generate G-Code
7. CNC drill satu-satu
8. Selesai → Alert operator
9. Kembali ke IDLE
```

Sistem ini semi-otomatis: operator tetap perlu:
- Place/clamp PCB
- Klik start
- Monitor progress
- Handle error jika ada