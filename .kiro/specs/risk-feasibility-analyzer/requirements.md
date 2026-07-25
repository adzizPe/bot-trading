# Requirements Document

## Introduction

Milestone 9.6 menambahkan **Risk Feasibility Analyzer** sebagai fitur analisis advisory yang dijalankan sebelum pengguna membuat Trade Plan. Analyzer membaca equity akun, konfigurasi risk aktif, stop loss, dan spesifikasi volume/tick broker untuk menjelaskan apakah rumus position sizing yang berlaku dapat menghasilkan lot yang valid.

Feature ini hanya membaca data dan menyajikan diagnosis. Hasil analyzer tidak mengubah keputusan sistem, tidak membuat atau mengubah Trade Plan, dan tidak menjadi pengganti validasi authoritative yang sudah ada pada Risk Management serta Trade Plan creation.

## Glossary

- **Analyzer**: Operasi read-only yang menghitung dan menjelaskan feasibility position sizing.
- **Analysis Result**: Hasil advisory yang tidak dipersist dan tidak memiliki `trade_plan_id`.
- **Authoritative Risk Configuration**: Konfigurasi Risk Management aktif yang sudah ada dan hanya dibaca Analyzer.
- **Risk Base**: Nilai yang dipakai oleh konfigurasi existing untuk menghitung risk amount; equity ketika `use_equity_for_risk` aktif, atau balance ketika nonaktif.
- **Raw Lot**: Volume sebelum batas maksimum dan normalisasi volume step broker.
- **Normalized Lot**: Volume setelah cap `volume_max` dan pembulatan turun ke `volume_step`.
- **Minimum Broker Lot**: Volume minimum executable pada grid volume broker.
- **Stop Distance**: Selisih absolut antara entry price dan stop-loss price dalam unit harga simbol.
- **Feasible**: Input valid dan normalized lot memenuhi minimum broker lot.
- **Infeasible**: Input valid tetapi configured risk tidak menghasilkan lot executable.
- **Unavailable**: Analisis tidak dapat dipercaya karena input wajib tidak tersedia, invalid, stale, atau tidak konsisten.
- **Diagnostic Minimum-Lot Risk**: Estimasi risiko hipotetis bila minimum broker lot digunakan; bukan izin atau instruksi untuk memaksa volume tersebut.

## Requirements

### Requirement 1: Read-only dan advisory-only

**User Story:** Sebagai trader, saya ingin memeriksa feasibility tanpa mengubah state atau keputusan trading.

#### Acceptance Criteria

1. WHEN analysis diminta, THE Analyzer SHALL hanya membaca signal context, account snapshot, risk configuration, tick, dan symbol specification yang sudah tersedia.
2. THE Analyzer SHALL NOT membuat, menyimpan, menyetujui, menolak, memperbarui, atau menghapus Trade Plan.
3. THE Analyzer SHALL NOT mengubah Strategy Engine, signal, Risk Management settings/state, daily risk state, Position Size Calculator, Paper Trading, Backtesting, Demo Execution, Safety Layer, Order Executor, order, position, atau broker state.
4. THE Analyzer SHALL NOT memanggil `order_check`, `order_send`, paper executor, demo executor, atau operasi broker yang bersifat mutation.
5. THE Analyzer SHALL NOT mengubah hasil atau control flow endpoint Trade Plan creation yang sudah ada.
6. WHEN analysis selesai, THE result SHALL tidak memuat `trade_plan_id` atau identifier lain yang menyiratkan Trade Plan telah dibuat.
7. WHEN analysis diulang dengan input yang sama, THE Analyzer SHALL tidak menimbulkan perubahan business state.
8. THE Analyzer SHALL NOT menyimpan Analysis Result ke database atau menambahkan migration khusus hasil feasibility.

### Requirement 2: Sumber input authoritative

**User Story:** Sebagai trader, saya ingin analisis memakai data yang sama jenisnya dengan perhitungan risk existing tanpa mengubah data tersebut.

#### Acceptance Criteria

1. WHEN analysis dimulai, THE Analyzer SHALL membaca Authoritative Risk Configuration yang aktif tanpa membuat default baru atau memperbarui record existing.
2. THE Analyzer SHALL membaca `risk_per_trade_percent` dan `use_equity_for_risk` dari konfigurasi existing dan SHALL NOT menerima override nilai risk dari client.
3. WHEN `use_equity_for_risk` bernilai true, THE Analyzer SHALL menggunakan snapshot equity sebagai Risk Base.
4. WHEN `use_equity_for_risk` bernilai false, THE Analyzer SHALL menggunakan snapshot balance sebagai Risk Base dan SHALL tetap menampilkan equity sebagai konteks akun.
5. THE Analyzer SHALL memperoleh entry price, stop-loss price, direction, dan symbol dari signal/context existing tanpa mengubah Strategy atau signal.
6. WHEN direction adalah `BUY`, THE Analyzer SHALL menggunakan ask sebagai entry price; WHEN direction adalah `SELL`, THE Analyzer SHALL menggunakan bid sebagai entry price, sesuai aturan existing.
7. THE Analyzer SHALL membaca sekurang-kurangnya `volume_min`, `volume_max`, `volume_step`, `trade_tick_value`, `trade_tick_size`, `point`, dan currency dari snapshot broker/account.
8. THE analysis request SHALL NOT menerima override untuk equity, balance, risk percent, risk base, entry, stop loss, volume, tick value, tick size, volume limits, atau symbol specification.
9. IF konfigurasi aktif tidak dapat dibaca tanpa mutation, THEN THE Analyzer SHALL return `UNAVAILABLE` dan SHALL NOT membuat atau memperbaiki konfigurasi.

### Requirement 3: Validasi input dan fail-closed analysis

**User Story:** Sebagai trader, saya ingin hasil hanya diberikan ketika input calculation aman dan dapat dipercaya.

#### Acceptance Criteria

1. IF Risk Base, equity, balance, risk percent, entry, stop loss, tick size, tick value, point, volume minimum, volume maximum, atau volume step bukan angka finite, THEN THE Analyzer SHALL return `UNAVAILABLE`.
2. IF risk percent, entry, stop distance, tick size, tick value, point, volume minimum, volume maximum, atau volume step tidak positif, THEN THE Analyzer SHALL return `UNAVAILABLE`.
3. IF `volume_max < volume_min`, THEN THE Analyzer SHALL return `UNAVAILABLE`.
4. IF direction bukan `BUY` atau `SELL`, THEN THE Analyzer SHALL return `UNAVAILABLE`.
5. IF BUY stop loss tidak berada di bawah entry atau SELL stop loss tidak berada di atas entry, THEN THE Analyzer SHALL return `UNAVAILABLE`.
6. IF account, tick, atau symbol specification tidak tersedia, tidak lengkap, tidak sesuai symbol, stale menurut policy backend existing, atau saling tidak konsisten, THEN THE Analyzer SHALL return `UNAVAILABLE`.
7. IF selected Risk Base finite tetapi nol atau negatif, THEN THE Analyzer SHALL return `INFEASIBLE`, menjelaskan bahwa modal risk base tidak positif, dan SHALL NOT menggantinya dengan nilai lain.
8. WHEN suatu diagnostic tidak dapat dihitung secara aman, THE Analyzer SHALL menandainya unavailable atau null dan SHALL NOT mengganti hasil invalid dengan nol.
9. WHEN beberapa kondisi invalid dan infeasible terjadi bersamaan, THE Analyzer SHALL memberikan precedence kepada `UNAVAILABLE`.

### Requirement 4: Formula yang konsisten dengan Position Size Calculator

**User Story:** Sebagai trader, saya ingin angka advisory konsisten dengan formula position sizing existing tanpa mengubah calculator tersebut.

#### Acceptance Criteria

1. THE Analyzer SHALL NOT mengubah source, interface, validation, rounding, atau behavior `PositionSizeCalculator`.
2. WHEN inputs valid, THE Analyzer SHALL menggunakan string-to-Decimal conversion dan runtime Decimal behavior yang konsisten dengan implementation existing.
3. THE Analyzer SHALL calculate `risk_amount = risk_base * risk_percent / 100`.
4. THE Analyzer SHALL calculate `stop_distance = abs(entry_price - stop_loss_price)`.
5. THE Analyzer SHALL calculate `ticks_at_risk = stop_distance / trade_tick_size`.
6. THE Analyzer SHALL calculate `risk_per_lot = ticks_at_risk * trade_tick_value`.
7. THE Analyzer SHALL calculate `raw_lot = risk_amount / risk_per_lot` tanpa pembulatan naik.
8. THE Analyzer SHALL calculate `capped_lot = min(raw_lot, volume_max)`.
9. THE Analyzer SHALL calculate `normalized_lot = floor(capped_lot / volume_step) * volume_step` menggunakan grid yang berawal dari nol, sama dengan behavior existing.
10. THE Analyzer SHALL NOT ceil, round up, clamp up, atau mengganti normalized lot dengan minimum broker lot.
11. WHEN identical positive inputs diberikan, THE Analyzer calculation SHALL match nilai decision-boundary dari formula existing untuk risk amount, risk per lot, raw lot, capped lot, dan normalized lot.

### Requirement 5: Penentuan feasibility volume

**User Story:** Sebagai trader, saya ingin mengetahui apakah volume hasil risk calculation dapat dieksekusi pada aturan broker.

#### Acceptance Criteria

1. THE Analyzer SHALL calculate `minimum_broker_lot = ceil(volume_min / volume_step) * volume_step` sebagai volume executable terkecil pada grid broker.
2. IF minimum broker lot lebih besar dari `volume_max`, THEN THE Analyzer SHALL return `UNAVAILABLE` karena grid volume broker tidak executable.
3. IF normalized lot lebih besar dari atau sama dengan minimum broker lot, THEN THE Analyzer SHALL return `FEASIBLE`, subject to seluruh input validation.
4. IF normalized lot kurang dari minimum broker lot, termasuk normalized lot nol, THEN THE Analyzer SHALL return `INFEASIBLE`.
5. WHEN raw lot berada tepat pada volume-step boundary, THE Analyzer SHALL mempertahankan boundary tersebut.
6. WHEN raw lot berada di antara dua volume-step boundary, THE Analyzer SHALL membulatkan turun ke boundary terdekat yang lebih rendah.
7. THE Analyzer SHALL display raw lot, capped lot, normalized lot, configured `volume_min`, effective minimum broker lot, `volume_max`, dan `volume_step` dengan label unit lot yang jelas.
8. THE Analyzer SHALL membedakan raw lot sebagai diagnostic value dan normalized lot sebagai volume hasil formula; THE Analyzer SHALL NOT menyatakan raw lot sebagai executable volume.

### Requirement 6: Required minimum equity

**User Story:** Sebagai trader, saya ingin mengetahui equity minimum agar minimum broker lot sesuai dengan risk percent saat ini.

#### Acceptance Criteria

1. WHEN threshold inputs valid, THE Analyzer SHALL calculate `required_minimum_risk_base = minimum_broker_lot * risk_per_lot * 100 / risk_percent`.
2. WHEN Risk Base type adalah `EQUITY`, THE Analyzer SHALL expose `required_minimum_equity` dengan nilai yang sama dengan required minimum risk base.
3. WHEN Risk Base type adalah `BALANCE`, THE Analyzer SHALL expose required minimum risk base sebagai `BALANCE` dan SHALL menandai `required_minimum_equity` sebagai diagnostic hypothetical atau not applicable, bukan sebagai decision input.
4. THE Analyzer SHALL menampilkan account currency untuk equity, Risk Base, risk amount, required minimum risk base, dan required minimum equity yang applicable.
5. THE Analyzer SHALL NOT merekomendasikan perubahan equity, deposit, leverage, atau risk percent sebagai cara otomatis untuk meloloskan trade.
6. THE Analyzer SHALL menjelaskan bahwa nilai minimum tersebut adalah batas matematis advisory berdasarkan snapshot dan bukan jaminan Trade Plan akan disetujui.

### Requirement 7: Maximum stop loss yang feasible

**User Story:** Sebagai trader, saya ingin mengetahui batas stop loss yang masih memungkinkan minimum broker lot tanpa meningkatkan risk.

#### Acceptance Criteria

1. WHEN inputs valid, THE Analyzer SHALL calculate `maximum_stop_distance = risk_amount * trade_tick_size / (minimum_broker_lot * trade_tick_value)`.
2. WHEN direction adalah BUY, THE Analyzer SHALL calculate boundary stop-loss price sebagai `entry_price - maximum_stop_distance`.
3. WHEN direction adalah SELL, THE Analyzer SHALL calculate boundary stop-loss price sebagai `entry_price + maximum_stop_distance`.
4. THE Analyzer SHALL menampilkan maximum stop distance dalam price units dan points ketika `point` valid.
5. THE Analyzer SHALL menampilkan boundary stop-loss price dengan direction dan symbol price units yang jelas.
6. IF actual stop distance lebih besar dari maximum stop distance, THEN THE Analyzer SHALL include alasan bahwa stop terlalu lebar untuk minimum broker lot pada configured risk.
7. IF actual stop distance sama dengan maximum stop distance, THEN THE volume constraint SHALL dianggap berada pada feasible boundary, subject to normalization dan seluruh validation lain.
8. THE Analyzer SHALL NOT mengubah stop loss signal, menyarankan stop loss yang mengabaikan Strategy, atau menerapkan boundary tersebut ke Trade Plan.

### Requirement 8: Estimasi risiko pada minimum lot

**User Story:** Sebagai trader, saya ingin memahami konsekuensi risiko jika minimum broker lot digunakan tanpa benar-benar memaksanya.

#### Acceptance Criteria

1. WHEN inputs valid, THE Analyzer SHALL calculate `minimum_lot_estimated_risk_amount = minimum_broker_lot * ticks_at_risk * trade_tick_value`.
2. WHEN Risk Base positif, THE Analyzer SHALL calculate `minimum_lot_estimated_risk_percent = minimum_lot_estimated_risk_amount / risk_base * 100`.
3. THE Analyzer SHALL display estimated amount dalam account currency dan estimated percent sebagai percentage.
4. THE output SHALL label nilai tersebut `DIAGNOSTIC_ONLY` dan menjelaskan bahwa minimum lot tidak akan dipilih atau dikirim oleh Analyzer.
5. THE Analyzer SHALL NOT menyediakan force, override, round-up, execute, atau create-plan action yang memakai minimum broker lot.
6. IF estimated minimum-lot risk melebihi configured risk, THEN THE Analyzer SHALL include selisih amount dan percentage yang dapat dihitung secara aman.
7. THE Analyzer SHALL NOT menyebut penggunaan minimum lot sebagai rekomendasi aman ketika nilainya melebihi configured risk.

### Requirement 9: Status, alasan penolakan, dan rekomendasi

**User Story:** Sebagai trader, saya ingin hasil yang deterministik dan mudah dipahami.

#### Acceptance Criteria

1. THE Analyzer SHALL return tepat satu status utama: `FEASIBLE`, `INFEASIBLE`, atau `UNAVAILABLE`.
2. WHEN status `FEASIBLE`, THE Analyzer SHALL explain bahwa formula dapat menghasilkan normalized lot yang memenuhi minimum broker, tetapi final Trade Plan tetap ditentukan flow existing.
3. WHEN status `INFEASIBLE`, THE Analyzer SHALL return recommendation `DO_NOT_FORCE_MINIMUM_LOT`.
4. WHEN status `UNAVAILABLE`, THE Analyzer SHALL return recommendation `RETRY_WITH_VALID_FRESH_DATA`.
5. WHEN status bukan `FEASIBLE`, THE Analyzer SHALL return stable reason codes dan sanitized human-readable messages tanpa duplikasi.
6. Reason codes SHALL sekurang-kurangnya membedakan `RISK_BASE_NOT_POSITIVE`, `NORMALIZED_LOT_BELOW_BROKER_MINIMUM`, `STOP_DISTANCE_EXCEEDS_FEASIBLE_MAXIMUM`, `INPUT_INVALID`, `SNAPSHOT_UNAVAILABLE`, `SNAPSHOT_STALE`, `SYMBOL_MISMATCH`, dan `BROKER_VOLUME_GRID_INVALID` ketika applicable.
7. Recommendation SHALL mudah dipahami, SHALL NOT menjanjikan profit atau approval, dan SHALL NOT menyuruh sistem menaikkan risk, volume, atau mengubah Strategy.
8. WHEN lebih dari satu infeasible reason applicable, THE Analyzer SHALL return alasan unik dalam urutan stabil.
9. THE Analyzer SHALL clearly state bahwa hasil bersifat advisory dan tidak mengubah keputusan Risk Management atau Trade Plan creation.

### Requirement 10: Analysis result contract

**User Story:** Sebagai consumer API/dashboard, saya ingin result lengkap dan tidak ambigu.

#### Acceptance Criteria

1. THE system SHALL menyediakan operasi feasibility analysis yang terpisah dari operasi Trade Plan creation.
2. THE analysis request SHALL hanya menerima identifier context yang diperlukan untuk membaca candidate existing dan SHALL menolak unknown fields serta calculation overrides.
3. THE result SHALL include symbol, direction, analysis timestamp, snapshot timestamps, account currency, balance, equity, Risk Base type/value, configured risk percent, entry price, stop-loss price, stop distance, tick size, tick value, point, volume limits, dan volume step.
4. THE result SHALL include risk amount, ticks at risk, risk per lot, raw lot, capped lot, normalized lot, minimum broker lot, required minimum risk base, required minimum equity applicability/value, maximum stop distance, boundary stop-loss price, minimum-lot estimated risk amount/percent, status, reasons, dan recommendation.
5. THE result SHALL preserve Decimal decision values without lossy serialization that can change a volume-step or feasibility boundary.
6. THE result SHALL include explicit units or unit metadata for currency, percent, lot, price, point, dan tick-derived values.
7. THE result SHALL NOT expose account login, credentials, admin token, authorization header, raw upstream exception, stack trace, environment values, atau secret.
8. IF the requested signal/context does not exist, THEN THE operation SHALL return a sanitized not-found response with zero business mutation.
9. IF request validation fails, THEN THE operation SHALL return a bounded sanitized validation response with zero business mutation.
10. Repeated analysis with the same authoritative inputs SHALL return identical calculation, status, reason, dan recommendation values, excluding request timing metadata.

### Requirement 11: Presentation dan stale-result safety

**User Story:** Sebagai trader, saya ingin membaca hasil dengan jelas tanpa menganggapnya sebagai approval.

#### Acceptance Criteria

1. WHEN result tersedia, THE presentation SHALL show raw lot, floor-normalized lot, minimum broker lot, required minimum equity/risk base, maximum stop distance/boundary price, minimum-lot risk estimate, reasons, dan recommendation.
2. THE presentation SHALL label raw lot sebagai non-executable diagnostic dan minimum-lot risk sebagai hypothetical diagnostic.
3. THE presentation SHALL use non-color-only status cues dan programmatically available text.
4. IF result berasal dari signal/context berbeda, superseded request, atau snapshot yang sudah stale, THEN THE presentation SHALL mark or discard result dan SHALL NOT menampilkannya sebagai current.
5. WHILE analysis loading, failed, stale, atau unavailable, THE presentation SHALL show state tersebut tanpa menyimpulkan bahwa trade feasible.
6. THE presentation SHALL NOT menyediakan editable equity, risk, stop, volume, tick, atau broker metadata fields untuk mengubah analyzer input.
7. THE presentation SHALL NOT mengubah enabled/disabled decision dari existing Trade Plan creation berdasarkan result analyzer.
8. THE presentation SHALL state bahwa Trade Plan creation dan Risk Management existing tetap authoritative.

### Requirement 12: Compatibility dan regression

**User Story:** Sebagai maintainer, saya ingin feature ditambahkan tanpa mengubah behavior existing.

#### Acceptance Criteria

1. ALL existing backend and frontend tests SHALL tetap lulus tanpa perubahan expected behavior.
2. THE feature SHALL NOT mengubah Strategy Engine rules, scoring, signal generation, atau signal persistence.
3. THE feature SHALL NOT mengubah Risk Management settings, formulas, limits, lock behavior, daily counters, atau persistence behavior.
4. THE feature SHALL NOT mengubah `PositionSizeCalculator` source, interface, output, exceptions, normalization, atau rounding behavior.
5. THE feature SHALL NOT mengubah Trade Plan creation, approval, rejection, persistence, list/detail contract, atau Trade Plan yang sudah tersimpan.
6. THE feature SHALL NOT mengubah Paper Trading, Backtesting, Demo Execution, Safety Layer, Order Executor, order request, reconciliation, atau position management.
7. THE feature SHALL NOT membuat migration database untuk Analysis Result dan SHALL NOT memerlukan backfill existing data.
8. THE feature SHALL fit existing FastAPI, React/Vite, SQLite/Alembic, dan native VPS deployment tanpa container atau service deployment baru.
9. THE feature SHALL NOT memerlukan perubahan `.env`, production risk configuration, Strategy configuration, executor configuration, atau deployment topology.
10. New tests SHALL verify formula boundaries, invalid inputs, all statuses, output completeness, read-only behavior, zero Trade Plan writes, zero risk-state writes, zero executor calls, sanitization, stale result handling, dan compatibility dengan flow existing.

## Out of Scope

- Mengubah atau mengganti Strategy Engine, Risk Management, Position Size Calculator, Trade Plan flow, atau Order Executor.
- Menjadikan Analyzer sebagai approval, rejection gate, atau sumber keputusan authoritative.
- Membuat, mengubah, menghapus, atau mengeksekusi Trade Plan.
- Memaksa minimum broker lot, membulatkan volume ke atas, menaikkan risk, atau mengubah stop loss.
- Mengubah Paper Trading, Backtesting, Demo Execution, Safety Layer, reconciliation, atau posisi broker.
- Menyimpan Analysis Result atau menambah database migration untuk hasil feasibility.
- Mengubah deployment native VPS atau menambahkan container/virtualization deployment.
