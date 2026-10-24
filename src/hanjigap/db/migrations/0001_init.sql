-- 0001: 기본 표 (구성원, 업로드 이력, 월 상태, 거래, 웹 수정, 뱅샐 현금흐름 참고값)
--
-- 원칙
--   금액: 원 단위 정수
--   날짜: 'YYYY-MM-DD', 시간: 'HH:MM:SS' (한국 시간), 연월: 'YYYY-MM'
--   기록 시각(…_at): UTC ISO 8601 ('2026-10-24T00:35:00Z')
--   이미 적용된 이 파일은 고치지 않는다. 바꿀 내용은 새 번호 파일로 추가한다.

CREATE TABLE members (
    id           INTEGER PRIMARY KEY,
    slug         TEXT    NOT NULL UNIQUE
                 CHECK (length(slug) BETWEEN 2 AND 32 AND slug NOT GLOB '*[^a-z0-9_-]*'),
    display_name TEXT    NOT NULL CHECK (length(display_name) > 0),
    email        TEXT    UNIQUE,
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
) STRICT;

CREATE TABLE import_batches (
    id            INTEGER PRIMARY KEY,
    member_id     INTEGER NOT NULL REFERENCES members (id),   -- 누구의 뱅샐 파일인가
    uploaded_by   INTEGER REFERENCES members (id),            -- 누가 올렸나
    uploaded_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    file_sha256   TEXT    NOT NULL CHECK (length(file_sha256) = 64),
    source_name   TEXT,                                       -- zip 안의 파일 이름 등
    container     TEXT    NOT NULL CHECK (container IN ('zip', 'xlsx')),
    period_start  TEXT    CHECK (period_start IS NULL OR date(period_start) IS period_start),
    period_end    TEXT    CHECK (period_end IS NULL OR date(period_end) IS period_end),
    period_source TEXT    CHECK (period_source IS NULL OR period_source IN ('filename', 'dates')),
    row_count     INTEGER NOT NULL CHECK (row_count >= 0),
    status        TEXT    NOT NULL CHECK (status IN ('applied', 'failed')),
    note          TEXT,
    CHECK (period_start IS NULL OR period_end IS NULL OR period_start <= period_end)
) STRICT;

CREATE INDEX idx_import_batches_member ON import_batches (member_id, uploaded_at);

-- 소유자 × 월 상태. baseline = 첫 적재 때 들어온 과거 달, settled = 정산 완료
CREATE TABLE month_settlements (
    member_id  INTEGER NOT NULL REFERENCES members (id),
    year_month TEXT    NOT NULL
               CHECK (year_month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
                      AND date(year_month || '-01') IS year_month || '-01'),
    status     TEXT    NOT NULL CHECK (status IN ('baseline', 'settled')),
    batch_id   INTEGER NOT NULL REFERENCES import_batches (id),
    settled_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    tx_count   INTEGER NOT NULL CHECK (tx_count >= 0),
    PRIMARY KEY (member_id, year_month)
) STRICT;

-- 뱅샐 원본 거래. (소유자, 지문)이 고유하다.
CREATE TABLE transactions (
    id          INTEGER PRIMARY KEY,
    member_id   INTEGER NOT NULL REFERENCES members (id),
    tx_key      TEXT    NOT NULL CHECK (length(tx_key) = 32 AND tx_key NOT GLOB '*[^0-9a-f]*'),
    seq         INTEGER NOT NULL CHECK (seq >= 1),
    base_key    TEXT    NOT NULL,
    year_month  TEXT    NOT NULL,
    tx_date     TEXT    NOT NULL CHECK (date(tx_date) IS tx_date),
    tx_time     TEXT    NOT NULL
                CHECK (tx_time GLOB '[0-2][0-9]:[0-5][0-9]:[0-5][0-9]' AND time(tx_time) IS tx_time),
    tx_type     TEXT    NOT NULL CHECK (tx_type IN ('수입', '지출', '이체')),
    category    TEXT    NOT NULL DEFAULT '',
    subcategory TEXT    NOT NULL DEFAULT '',
    content     TEXT    NOT NULL DEFAULT '',
    amount      INTEGER NOT NULL,
    currency    TEXT    NOT NULL DEFAULT 'KRW' CHECK (currency = 'KRW'),
    instrument  TEXT    NOT NULL DEFAULT '',
    memo        TEXT,
    batch_id    INTEGER NOT NULL REFERENCES import_batches (id),
    row_no      INTEGER NOT NULL CHECK (row_no >= 1),
    UNIQUE (member_id, tx_key),
    CHECK (substr(tx_date, 1, 7) = year_month)
) STRICT;

CREATE INDEX idx_transactions_month ON transactions (member_id, year_month);
CREATE INDEX idx_transactions_batch ON transactions (batch_id);

-- 웹에서 고친 내용. 거래가 재정산으로 지워졌다 다시 들어와도 남도록 거래 id가 아니라 지문으로 연결한다.
-- NULL인 칸은 "고치지 않음"(뱅샐 값 사용).
CREATE TABLE tx_overrides (
    member_id   INTEGER NOT NULL REFERENCES members (id),
    tx_key      TEXT    NOT NULL CHECK (length(tx_key) = 32),
    tx_type     TEXT    CHECK (tx_type IS NULL OR tx_type IN ('수입', '지출', '이체')),
    category    TEXT,
    subcategory TEXT,
    content     TEXT,
    memo        TEXT,
    note        TEXT,
    edited_by   INTEGER REFERENCES members (id),
    edited_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (member_id, tx_key)
) STRICT;

-- 뱅샐현황 시트의 현금흐름현황(뱅샐이 계산한 월별·항목별 합계). 참고·검증용.
CREATE TABLE bs_cashflow (
    batch_id   INTEGER NOT NULL REFERENCES import_batches (id) ON DELETE CASCADE,
    member_id  INTEGER NOT NULL REFERENCES members (id),
    year_month TEXT    NOT NULL
               CHECK (year_month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
                      AND date(year_month || '-01') IS year_month || '-01'),
    item       TEXT    NOT NULL CHECK (length(item) > 0),  -- 급여, 식비, 월수입 총계 등
    amount     INTEGER NOT NULL,
    PRIMARY KEY (batch_id, year_month, item)
) STRICT;

CREATE INDEX idx_bs_cashflow_member ON bs_cashflow (member_id, year_month);