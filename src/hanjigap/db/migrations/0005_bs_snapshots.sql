-- 0005: 뱅샐현황의 재무·보험·투자·대출 표 (내보낸 시점의 잔액 기록)
--
-- 원칙
--   업로드 1건(import_batches)에 붙여 저장한다. 업로드 기록을 지우면 함께 지워진다.
--   다시 정산하면 새 기록이 생기고, 화면은 그 달의 가장 최근 기록을 쓴다.
--   as_of: 뱅샐에서 내보낸 날 (파일 이름 기간의 끝 날짜)
--   year_month: 그 업로드로 정산(또는 첫 적재)한 달. '9월 말 자산'처럼 그 달 기준으로 보여 준다.
--   '고객정보' 표는 저장하지 않는다.
--   rate: % 숫자 그대로 (5.55 = 5.55%)
--   amount 칸의 뜻: 자산·부채 금액 / 투자 평가금액 / 대출 잔액 / 보험 총납입금
--   principal 칸의 뜻: 투자원금 / 대출원금 (마이너스 통장은 한도)

CREATE TABLE bs_snapshots (
    id                INTEGER PRIMARY KEY,
    batch_id          INTEGER NOT NULL UNIQUE REFERENCES import_batches (id) ON DELETE CASCADE,
    member_id         INTEGER NOT NULL REFERENCES members (id),
    as_of             TEXT    NOT NULL CHECK (date(as_of) IS as_of),
    year_month        TEXT    NOT NULL
                      CHECK (year_month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
                             AND date(year_month || '-01') IS year_month || '-01'),
    total_assets      INTEGER,
    total_liabilities INTEGER,
    net_worth         INTEGER,
    checks_ok         INTEGER NOT NULL CHECK (checks_ok IN (0, 1)),  -- 합계 검증이 모두 맞았나
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
) STRICT;

CREATE INDEX idx_bs_snapshots_member ON bs_snapshots (member_id, year_month);

CREATE TABLE bs_holdings (
    snapshot_id INTEGER NOT NULL REFERENCES bs_snapshots (id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL CHECK (seq >= 1),   -- 표에 나온 순서
    section     TEXT    NOT NULL
                CHECK (section IN ('asset', 'liability', 'insurance', 'investment', 'loan')),
    group_name  TEXT,                                -- 자산·부채 항목, 투자상품종류, 대출종류
    institution TEXT,
    name        TEXT    NOT NULL CHECK (length(name) > 0),
    amount      INTEGER,
    principal   INTEGER,
    rate        REAL,
    status      TEXT,                                -- 보험 계약상태
    start_date  TEXT    CHECK (start_date IS NULL OR date(start_date) IS start_date),
    end_date    TEXT    CHECK (end_date IS NULL OR date(end_date) IS end_date),
    PRIMARY KEY (snapshot_id, seq)
) STRICT;