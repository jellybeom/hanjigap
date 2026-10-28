-- 0006: 자동차 (차량 · 직접 입력한 시세) + 고정비에 '관련 차량' 칸
--
-- 원칙
--   소유자는 한 사람 (owner_member_id). 공동명의가 실제로 생기면 그때 지분 표를 추가한다.
--   차량번호는 저장하지 않는다.
--   팔아도 지우지 않고 sold_on(매각일)을 채운다.
--   추정 시세는 저장하지 않고 볼 때마다 계산한다:
--       가장 최근 기준값 × (1 − 감가율)^(지난 개월 / 12)
--       기준값 = 구입가 · 직접 입력한 시세 · 뱅샐 재무현황 '동산' 항목 중 가장 최근 것
--   뱅샐 시세는 잔액 기록(bs_holdings)에서 꺼내 쓴다. 여기에 다시 저장하지 않는다.
--   bs_asset_name: 뱅샐 재무현황 '동산' 묶음의 상품명. 비어 있고 동산 항목이 하나뿐이면 그것을 쓴다.

CREATE TABLE vehicles (
    id               INTEGER PRIMARY KEY,
    owner_member_id  INTEGER NOT NULL REFERENCES members (id),
    name             TEXT    NOT NULL CHECK (length(name) > 0),
    model_year       INTEGER CHECK (model_year IS NULL OR model_year BETWEEN 1980 AND 2100),
    purchased_on     TEXT    NOT NULL
                     CHECK (date(purchased_on) IS purchased_on
                            OR (purchased_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
                                AND date(purchased_on || '-01') IS purchased_on || '-01')),
    purchase_price   INTEGER NOT NULL CHECK (purchase_price > 0),
    depreciation_pct REAL    NOT NULL DEFAULT 15 CHECK (depreciation_pct >= 0 AND depreciation_pct <= 50),
    bs_asset_name    TEXT    CHECK (bs_asset_name IS NULL OR length(bs_asset_name) > 0),
    inspection_due_on TEXT   CHECK (inspection_due_on IS NULL OR date(inspection_due_on) IS inspection_due_on),
    sold_on          TEXT    CHECK (sold_on IS NULL OR date(sold_on) IS sold_on),
    color            TEXT    CHECK (color IS NULL
                                    OR color GLOB '#[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
    memo             TEXT    CHECK (memo IS NULL
                                    OR memo NOT GLOB '*[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'),
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (owner_member_id, name),
    CHECK (sold_on IS NULL OR sold_on >= purchased_on)
) STRICT;

-- 직접 입력한 시세. 한 달에 하나 (다시 넣으면 바뀐다)
CREATE TABLE vehicle_valuations (
    vehicle_id INTEGER NOT NULL REFERENCES vehicles (id),
    year_month TEXT    NOT NULL
               CHECK (year_month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
                      AND date(year_month || '-01') IS year_month || '-01'),
    value      INTEGER NOT NULL CHECK (value > 0),
    memo       TEXT    CHECK (memo IS NULL
                              OR memo NOT GLOB '*[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'),
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (vehicle_id, year_month)
) STRICT;

-- 고정비 → 관련 차량 (자동차 할부, 자동차보험, 자동차세 등). 차량 화면의 할부·일정에 쓴다.
ALTER TABLE fixed_items ADD COLUMN vehicle_id INTEGER REFERENCES vehicles (id);

CREATE INDEX idx_fixed_items_vehicle ON fixed_items (vehicle_id);