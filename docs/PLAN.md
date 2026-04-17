# Auto CNC Drill System - Plan (Historical + Current)

Dokumen ini adalah roadmap historis. Status implementasi saat ini sudah melewati fase foundation dan berada pada fase stabilisasi operasional.

## Current Focus (Aktif)

1. Stabilitas workflow `/` dan `/mapping`.
2. Akurasi mapping + calibrate + refine.
3. Telemetry/metrics untuk evaluasi performa produksi.
4. Hardening recovery flow (`STOP`, `RESET`, `UNLOCK`, `HOME`, `Z-HOME`).

## Backlog Prioritas

1. Penyederhanaan UX wizard operator.
2. Peningkatan observability error code per phase.
3. Profil tuning refine berbasis jenis PCB.
4. Automated regression test untuk command WebSocket inti.

## Milestone Runtime Saat Ini

- Dashboard utama `/` stabil untuk mapping + drill.
- Halaman `/mapping` tersedia untuk mapping-only + calibrate.
- End-of-process behavior konsisten: kembali `HOME` sebelum `IDLE`.
