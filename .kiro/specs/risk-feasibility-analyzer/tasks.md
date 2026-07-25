# Implementation Plan: Risk Feasibility Analyzer

## Overview

Implementasikan analyzer advisory read-only sebagai vertical slice baru di FastAPI dan React/Vite existing. Urutan kerja menjaga pure Python `Decimal` engine terpisah dari I/O, membatasi adapter ke operasi baca, dan baru kemudian memasang API serta dashboard. Jangan mengubah behavior atau control flow Strategy Engine, Risk Management, `PositionSizeCalculator`, Trade Plan, Paper/Backtest/Demo/Safety, atau Order Executor. Jangan menambah persistence, migration, container, service deployment, `.env`, atau topology baru.

## Tasks

- [ ] 1. Siapkan fondasi domain dan tooling test terisolasi
  - [ ]\* 1.1 Tambahkan dependency property-testing yang pinned dan development-only
    - Pin Hypothesis di `backend/requirements-dev.txt` tanpa memasukkannya ke runtime requirements.
    - Pin fast-check di `frontend/package.json` dan perbarui `frontend/package-lock.json` tanpa mengubah runtime dependency atau deployment.
    - _Requirements: 12.1, 12.8, 12.9_
  - [ ] 1.2 Buat model domain immutable dan katalog hasil analyzer
    - Tambahkan package `backend/app/risk_feasibility/` dengan frozen dataclass/enum untuk input tervalidasi, calculation, status, recommendation, applicability, unit metadata, dan ordered reason.
    - Definisikan allowlist reason/message, stable priority, deduplication, dan batas jumlah/panjang reason; jangan mengimpor service mutasi atau model persistence.
    - _Requirements: 1.2, 1.3, 1.6, 1.8, 9.1, 9.5, 9.6, 9.8, 10.4, 10.7_

- [ ] 2. Implementasikan pure Decimal validation, calculation, dan mapping
  - [ ] 2.1 Implementasikan validator input fail-closed
    - Buat konversi `Decimal(str(value))`, penolakan bool/non-finite, aturan positivity, direction/stop geometry, snapshot completeness/freshness/symbol consistency, dan validasi grid broker.
    - Kumpulkan reason `UNAVAILABLE` secara unik dan stabil; jangan membuat nilai diagnostic invalid menjadi nol.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.8, 3.9, 5.2_
  - [ ] 2.2 Implementasikan pure `RiskFeasibilityEngine`
    - Hitung pipeline risk amount, stop/tick risk, raw/capped/floor-normalized lot, effective broker minimum, threshold risk base/equity, maximum stop, boundary SL, dan diagnostic minimum-lot risk/delta dengan `Decimal` saja.
    - Terapkan status/recommendation dan null diagnostic untuk risk base non-positive; jangan ceil/round/clamp volume ke minimum dan jangan memanggil `PositionSizeCalculator`.
    - _Requirements: 3.7, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 6.1, 6.2, 6.3, 7.1, 7.2, 7.3, 7.4, 7.6, 7.7, 8.1, 8.2, 8.6, 9.1, 9.2, 9.3, 9.4_
  - [ ] 2.3 Implementasikan result mapper dan canonical Decimal serializer
    - Petakan calculation menjadi response-domain lengkap dengan canonical plain decimal strings, `null` untuk unavailable values, units, timestamps, advisory disclaimer, labels, stable reasons, dan recommendation.
    - Pastikan output tidak memiliki `trade_plan_id`, approval, secret, login, header, environment value, exception mentah, atau stack trace.
    - _Requirements: 1.6, 5.7, 5.8, 6.4, 6.5, 6.6, 7.4, 7.5, 7.8, 8.3, 8.4, 8.5, 8.7, 9.2, 9.5, 9.7, 9.9, 10.3, 10.4, 10.5, 10.6, 10.7_
  - [ ]\* 2.4 Tulis unit tests untuk validator, engine, dan mapper
    - Uji seluruh status/reason, BUY/SELL, equality dan between-step boundaries, invalid grids, BALANCE applicability, null diagnostics, unit/currency labels, canonical serialization, serta larangan clamp-up.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.7, 3.8, 4.3, 4.9, 4.10, 5.2, 5.3, 5.4, 6.2, 6.3, 7.7, 8.4, 9.1, 9.6, 10.4, 10.5, 10.6_
  - [ ]\* 2.5 Tulis property test untuk fail-closed validation
    - **Property 3: Invalid or untrustworthy inputs fail closed**
    - Gunakan generator boundary-heavy dan minimum 100 examples dengan tag property desain yang persis.
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.8**
  - [ ]\* 2.6 Tulis property test untuk non-positive selected capital
    - **Property 4: Non-positive selected capital is infeasible without fallback**
    - **Validates: Requirements 3.7**
  - [ ]\* 2.7 Tulis property test untuk precedence unavailable
    - **Property 5: Unavailable conditions dominate infeasible conditions**
    - **Validates: Requirements 3.9, 9.1, 9.4**
  - [ ]\* 2.8 Tulis property test untuk exact Decimal formula pipeline
    - **Property 6: Decimal position-sizing formula pipeline is exact**
    - **Validates: Requirements 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8**
  - [ ]\* 2.9 Tulis property test untuk zero-origin floor normalization
    - **Property 7: Zero-origin normalization always floors to the broker step**
    - **Validates: Requirements 4.9, 4.10, 5.5, 5.6**
  - [ ]\* 2.10 Tulis property test untuk effective broker minimum dan classification
    - **Property 9: Effective broker minimum and feasibility classification are grid-correct**
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.4**
  - [ ]\* 2.11 Tulis property test untuk required capital threshold
    - **Property 10: Required capital threshold respects risk-base mode**
    - **Validates: Requirements 6.1, 6.2, 6.3**
  - [ ]\* 2.12 Tulis property test untuk maximum-stop diagnostics
    - **Property 11: Maximum-stop diagnostics are directionally correct**
    - **Validates: Requirements 7.1, 7.2, 7.3, 7.4, 7.6, 7.7**
  - [ ]\* 2.13 Tulis property test untuk minimum-lot risk diagnostics
    - **Property 12: Minimum-lot risk diagnostics and excess deltas are exact**
    - **Validates: Requirements 8.1, 8.2, 8.6**
  - [ ]\* 2.14 Tulis property test untuk lossless Decimal serialization
    - **Property 14: Decimal API serialization is lossless**
    - **Validates: Requirements 10.5**

- [ ] 3. Implementasikan source adapter read-only dan candidate context
  - [ ] 3.1 Buat `RiskSettingsReader` SELECT-only
    - Baca hanya `RiskSettings/default` melalui session read path dan kembalikan `None` bila tidak ada; jangan memakai `get_or_create_settings`, `update_settings`, `add`, `flush`, `commit`, atau default fallback.
    - _Requirements: 1.1, 1.3, 1.8, 2.1, 2.2, 2.9, 12.3, 12.7_
  - [ ] 3.2 Buat capability-limited `ReadOnlyRiskSnapshotGateway`
    - Ekspos hanya `read(symbol)` yang mendelegasikan ke `risk_snapshot`, membentuk atomic timestamp metadata, dan menerapkan policy freshness 60 detik existing.
    - Jangan mengekspos atau memanggil `order_check`, `order_send`, connect/disconnect mutation, executor, atau state update.
    - _Requirements: 1.1, 1.3, 1.4, 2.7, 3.6, 12.6_
  - [ ] 3.3 Buat `CandidateRiskContextBuilder`
    - Validasi candidate signal dan symbol, pilih ask untuk BUY/bid untuk SELL, pilih equity atau balance dari stored setting, lalu derive stop melalui `StopLossCalculator` existing tanpa mengubah source/interface/behavior-nya.
    - Bentuk immutable feasibility input tanpa target, risk lock, daily state, Trade Plan, client override, atau fallback data.
    - _Requirements: 2.2, 2.3, 2.4, 2.5, 2.6, 2.8, 3.4, 3.5, 4.1, 12.2, 12.3, 12.4, 12.5_
  - [ ]\* 3.4 Tulis unit/adapter tests untuk seluruh read source
    - Uji settings present/missing, snapshot timestamp boundary/missing/future, field incomplete, symbol mismatch, MT5 errors, BUY/SELL entry, dan stop derivation.
    - Gunakan session/manager spies untuk membuktikan tidak ada INSERT/UPDATE/commit/flush atau broker mutation.
    - _Requirements: 1.1, 1.4, 2.1, 2.6, 2.7, 2.9, 3.6, 10.8, 12.10_
  - [ ]\* 3.5 Tulis property test untuk authoritative source selection
    - **Property 2: Authoritative risk-base and market-source selection**
    - **Validates: Requirements 2.2, 2.3, 2.4, 2.5, 2.6**
  - [ ]\* 3.6 Tulis differential property test terhadap calculator unchanged
    - **Property 8: Analyzer matches the unchanged calculator boundary**
    - Bandingkan generated accepted inputs dan below-minimum normalized boundary tanpa memodifikasi `backend/app/risk/calculators.py` atau expected tests existing.
    - **Validates: Requirements 4.11, 12.4**

- [ ] 4. Implementasikan orchestration service advisory
  - [ ] 4.1 Buat stateless `RiskFeasibilityService.analyze`
    - Orkestrasi signal read, active settings read, atomic snapshot, context build, validation, engine, dan mapper dengan injected UTC clock.
    - Map not-found, missing config, source errors, stale/mismatch, dan defects ke contract sanitized; jangan menjangkau Trade Plan/risk-state/paper/demo/backtest/safety/executor mutation.
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.7, 1.8, 2.1, 2.9, 3.6, 9.9, 10.1, 10.8, 10.9, 10.10, 12.2, 12.3, 12.5, 12.6_
  - [ ]\* 4.2 Tulis service tests untuk status dan source failure branches
    - Uji FEASIBLE/INFEASIBLE/UNAVAILABLE, missing signal/config, stale/mismatch, repeated input, source exception sanitization, dan absence of Trade Plan identifiers.
    - _Requirements: 1.6, 1.7, 2.9, 3.9, 9.1, 9.2, 9.3, 9.4, 9.5, 9.9, 10.8, 10.10_
  - [ ]\* 4.3 Tulis property test untuk business-state non-interference
    - **Property 1: Analysis is business-state non-interfering**
    - Gunakan in-memory fakes/spies untuk repetition counts; jangan menjalankan generated broker/database I/O.
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.7**
  - [ ]\* 4.4 Tulis property test untuk deterministic result contract
    - **Property 13: Status, reasons, and recommendation are deterministic**
    - **Validates: Requirements 9.1, 9.3, 9.5, 9.8, 10.10**

- [ ] 5. Tambahkan strict read-only API dalam proses FastAPI existing
  - [ ] 5.1 Definisikan request/response schema feasibility terpisah
    - Buat Pydantic request dengan hanya `signal_id`, `extra="forbid"`, panjang bounded, dan response lengkap menggunakan decimal strings/null, timestamp UTC, nested units, reasons, recommendation, dan advisory disclaimer.
    - Jangan menambahkan override calculation, `trade_plan_id`, approval, force, execute, create-plan, atau secret-bearing field.
    - _Requirements: 2.8, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.9_
  - [ ] 5.2 Wire analyzer ke dependency injection dan app state existing
    - Bangun reader, gateway, service, dan injected clock dalam startup FastAPI existing serta expose dependency getter khusus analyzer.
    - Pertahankan satu process/Uvicorn deployment existing; jangan mengubah `TradePlanService`, router selain wiring minimum, `.env`, port, Nginx upstream, process manager, atau menambah service/container.
    - _Requirements: 1.5, 10.1, 12.5, 12.8, 12.9_
  - [ ] 5.3 Tambahkan `GET /api/v1/risk/feasibility?signal_id=...`
    - Route hanya menerima tepat satu query `signal_id` tanpa request body, memanggil analyzer service, memetakan domain statuses ke 200, signal missing ke sanitized 404, malformed/unknown query ke bounded 422, dan unexpected defect ke generic 500.
    - Pertahankan endpoint `POST /risk/trade-plan` dan seluruh contract risk existing tanpa perubahan behavior/control flow.
    - _Requirements: 1.5, 10.1, 10.2, 10.8, 10.9, 12.1, 12.3, 12.5_
  - [ ]\* 5.4 Tulis API contract tests
    - Uji complete payload untuk tiga status, strict single `signal_id` query, seluruh override/unknown query ditolak, 404/422 bounded, OpenAPI field safety, decimal strings, units, no `trade_plan_id`, dan no sensitive output.
    - _Requirements: 1.6, 2.8, 9.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8, 10.9, 12.10_
  - [ ]\* 5.5 Tulis property test untuk public error sanitization
    - **Property 15: Error sanitization never leaks sensitive input**
    - Generate multiline credential/token/authorization/traceback/login/environment markers dan pastikan hanya allowlisted bounded response yang keluar.
    - **Validates: Requirements 10.7**

- [ ] 6. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Implementasikan frontend API contract dan stale-result state machine
  - [ ] 7.1 Tambahkan TypeScript types untuk feasibility contract
    - Modelkan discriminated status/recommendation, nullable decimal strings, timestamps, reasons, units, account/market/volume/calculation sections tanpa mengubah `TradePlan` atau API types existing.
    - _Requirements: 5.7, 6.3, 8.4, 9.1, 10.3, 10.4, 10.5, 10.6, 11.1, 12.5_
  - [ ] 7.2 Tambahkan API client method read-only analyzer
    - Kirim hanya `{ signal_id }` ke endpoint feasibility dan gunakan sanitization/error handling existing; jangan mengirim risk, equity, entry, stop, lot, tick, atau symbol overrides.
    - Jangan invalidate/mutate cache Trade Plan, risk settings, paper/demo/backtest/safety, order, atau position.
    - _Requirements: 1.2, 1.3, 2.8, 8.5, 10.2, 11.6, 11.7_
  - [ ] 7.3 Buat pure current-result reducer dan Decimal display formatter
    - Kelola monotonically increasing generation, active signal identity, request lifecycle, `fresh_until`, stale/discard behavior, dan AbortController metadata tanpa menganggap abort sebagai satu-satunya race guard.
    - Format decimal strings untuk display tanpa konversi ke JavaScript `number` yang dapat mengubah decision boundary.
    - _Requirements: 10.5, 10.10, 11.4, 11.5_
  - [ ]\* 7.4 Tulis unit tests untuk client, reducer, dan formatter
    - Uji request body minimal, sanitized transport errors, loading/error/unavailable/stale transitions, signal replacement, expiry boundary, discarded completion, dan lossless displayed decimal strings.
    - _Requirements: 2.8, 10.2, 10.5, 11.4, 11.5, 12.10_
  - [ ]\* 7.5 Tulis frontend property test untuk current fresh result
    - **Property 16: UI displays only the current fresh result**
    - Gunakan fast-check minimum 100 runs atas completion schedules, source changes, dan clock advances; state non-current tidak boleh menghasilkan feasible conclusion.
    - **Validates: Requirements 11.4, 11.5**

- [ ] 8. Bangun dashboard advisory yang accessible dan terpisah dari Trade Plan
  - [ ] 8.1 Buat komponen presentasi diagnostics read-only
    - Implementasikan status panel, source/account context, position-sizing diagnostics, threshold diagnostics, `DIAGNOSTIC_ONLY` minimum-lot panel, ordered reason list, dan advisory notice.
    - Gunakan icon + text + programmatic status, unit labels, null/unavailable rendering, dan tanpa editable/force/override/create/execute controls.
    - _Requirements: 5.7, 5.8, 6.3, 6.4, 6.5, 6.6, 7.4, 7.5, 7.8, 8.3, 8.4, 8.5, 8.7, 9.2, 9.7, 9.9, 11.1, 11.2, 11.3, 11.6, 11.8_
  - [ ] 8.2 Implementasikan `RiskFeasibilityPage`
    - Tampilkan latest candidate context secara read-only, tombol analyze tunggal untuk candidate BUY/SELL, dan idle/loading/success/unavailable/error/stale states melalui reducer generation guard.
    - Clear result lama saat request baru, discard superseded/mismatched/expired result, dan jangan membaca/mengubah enabled state atau mutation flow `TradePlansPage`.
    - _Requirements: 1.1, 1.2, 2.5, 10.1, 11.1, 11.4, 11.5, 11.6, 11.7, 11.8_
  - [ ] 8.3 Wire lazy route, navigation, dan responsive styling
    - Tambahkan `/risk-feasibility` setelah Risk Management dan sebelum Trade Plans, plus layout/style responsive melalui aplikasi Vite existing.
    - Jangan menambah port, process, service, WebSocket, Nginx location, container, atau deployment artifact selain build frontend existing.
    - _Requirements: 10.1, 11.3, 12.8, 12.9_
  - [ ]\* 8.4 Tulis dashboard component/integration tests
    - Uji seluruh labels/units/diagnostics, accessible status semantics, idle/loading/error/stale/unavailable states, reason order, disclaimer, responsive content, dan absence protected inputs/actions.
    - Uji out-of-order promises, aborted completion, source replacement, timer expiration, serta bahwa stale/unavailable/error tidak merender kesimpulan FEASIBLE.
    - _Requirements: 8.4, 8.5, 9.9, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8, 12.10_

- [ ] 9. Verifikasi integrasi, zero mutation, regression, dan dokumentasi
  - [ ]\* 9.1 Tambahkan route-to-SQLite zero-mutation integration test
    - Snapshot row counts dan serialized state `signals`, `risk_settings`, `daily_risk_states`, `trade_plans`, serta execution-related tables sebelum/sesudah FEASIBLE, INFEASIBLE, UNAVAILABLE, 404, 422, dan repeated analysis.
    - Assert tidak ada Analysis Result persistence, commit/flush/write, identifier plan, migration, atau backfill.
    - _Requirements: 1.2, 1.3, 1.7, 1.8, 10.8, 10.9, 12.3, 12.5, 12.7, 12.10_
  - [ ]\* 9.2 Tambahkan capability-call zero-mutation integration test
    - Gunakan fake MT5/repository/executor counters untuk membuktikan analyzer hanya memanggil `risk_snapshot` dan SELECT readers.
    - Assert `order_check`, `order_send`, Trade Plan save, settings/daily-state update, paper/demo executor, backtest, safety mutation, reconciliation, order, dan position calls tetap nol.
    - _Requirements: 1.1, 1.3, 1.4, 12.2, 12.3, 12.5, 12.6, 12.10_
  - [ ]\* 9.3 Tambahkan backend compatibility regression tests
    - Tambahkan suite baru yang mengunci source/interface/output/exception/rounding `PositionSizeCalculator`, Strategy/signal behavior, risk settings/state/locks, serta Trade Plan create/list/detail dan persisted records sebelum/sesudah analyzer calls.
    - Pertahankan expected behavior seluruh test analysis/risk/paper/backtest/demo/safety/MT5 existing; jangan mengedit expectation lama agar feature lulus.
    - _Requirements: 1.5, 4.1, 4.11, 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.10_
  - [ ]\* 9.4 Tambahkan frontend decoupling regression tests
    - Buktikan analyzer query/cache/status tidak mengubah create-plan button, Trade Plan mutation/list/detail, atau controls Paper/Demo/Safety existing.
    - Pastikan navigation addition tidak mengubah route behavior halaman lama dan frontend suite existing tetap memakai expected behavior yang sama.
    - _Requirements: 1.5, 11.7, 12.1, 12.5, 12.6, 12.10_
  - [ ]\* 9.5 Tambahkan schema dan deployment-boundary regression checks
    - Verifikasi SQL schema/Alembic heads identik tanpa table/column/index/migration analyzer serta app tetap memakai FastAPI/Uvicorn, Vite `dist`, Nginx `/api/v1`, dan process manager native existing.
    - Assert tidak ada requirement runtime, `.env`, port, upstream, container manifest, daemon, queue, atau service topology baru.
    - _Requirements: 1.8, 12.7, 12.8, 12.9, 12.10_
  - [ ] 9.6 Perbarui dokumentasi API, safety boundary, dan native operation
    - Dokumentasikan request/response/status/reasons/decimal-string units, advisory disclaimer, stale-result behavior, dan fakta bahwa analysis tidak membuat Trade Plan/order atau menyimpan hasil.
    - Tambahkan panduan focused tests, build, native VPS update/rollback/recovery melalui virtual environment FastAPI/Uvicorn, Vite `frontend/dist`, Nginx, dan NSSM/PM2 existing; jangan mendokumentasikan container/service/topology baru.
    - _Requirements: 1.2, 1.3, 1.8, 6.6, 8.4, 8.5, 9.9, 10.1, 10.6, 11.8, 12.7, 12.8, 12.9_

- [ ] 10. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test tasks and can be skipped for a faster MVP; core read-only safety tasks are never optional.
- Setiap property task harus menggunakan tag persis `Feature: risk-feasibility-analyzer, Property N: <property text>` dan minimal 100 generated examples.
- Semua task memakai Python untuk backend dan TypeScript/React untuk frontend; nilai keputusan tetap `Decimal`/decimal string, bukan binary float.
- Scope tetap advisory-only: jangan memodifikasi source atau behavior Strategy Engine, Risk Management, `PositionSizeCalculator`, Trade Plan, Paper/Backtest/Demo/Safety, Order Executor, order, position, atau broker state.
- Tidak ada database migration, result persistence, container, service baru, `.env`, port, reverse-proxy route, atau perubahan deployment topology.
- Checkpoint menjalankan test non-watch/focused dahulu, kemudian full regression, lint/typecheck/build; jangan menjalankan development server atau watcher sebagai bagian task.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2"] },
    { "id": 1, "tasks": ["2.1", "3.1", "3.2", "5.1", "7.1"] },
    { "id": 2, "tasks": ["2.2", "3.3", "7.2", "7.3"] },
    {
      "id": 3,
      "tasks": ["2.3", "3.4", "3.5", "3.6", "4.1", "7.4", "7.5", "8.1"]
    },
    {
      "id": 4,
      "tasks": [
        "2.4",
        "2.5",
        "2.6",
        "2.7",
        "2.8",
        "2.9",
        "2.10",
        "2.11",
        "2.12",
        "2.13",
        "2.14",
        "4.2",
        "4.3",
        "4.4",
        "5.2",
        "8.2"
      ]
    },
    { "id": 5, "tasks": ["5.3", "8.3"] },
    { "id": 6, "tasks": ["5.4", "5.5", "8.4"] },
    { "id": 7, "tasks": ["9.1", "9.2", "9.3", "9.4", "9.5", "9.6"] }
  ]
}
```
