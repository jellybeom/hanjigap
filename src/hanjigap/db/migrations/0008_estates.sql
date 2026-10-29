-- 0008: 부동산 (집 · 계약 이력 · 시세 기록) + 고정비에 '관련 집' 칸
--
-- 원칙
--   집(properties)에는 주인이 없다. 주인은 계약마다 정한다 (보증금·매입가를 낸 사람).
--   계약 종류: jeonse(전세) · monthly(월세) · own(자가, 매매)
--       amount = 전세·월세는 보증금, 자가는 매입가
--       monthly_rent = 월세 계약에만
--       end_on(만기일) = 전세·월세만. 자가는 만기가 없다.
--   갱신은 새 줄로 남긴다. 이전 계약에는 ended_on(끝난 날)만 적는다. 지우지 않는다.
--   한 집에 진행 중인 계약(ended_on 없음)은 하나뿐이다.
--   이사 나가면 지우지 않고 moved_out_on을 채운다.
--   자산 가치는 저장하지 않고 볼 때마다 계산한다:
--       전세·월세 = 보증금 (돌려받을 돈), 자가 = 그달까지의 마지막 시세, 없으면 매입가
--   시세 출처: manual(직접 입력) · lookup(실거래가 조회, 나중에)
--   전세대출 연결은 화면 단계에서 뱅샐 대출 항목과 함께 한다.

CREATE TABLE properties (
    id            INTEGER PRIMARY KEY,
    name          TEXT    NOT NULL UNIQUE CHECK (length(name) > 0),
    kind          TEXT    NOT NULL DEFAULT 'apartment'
                  CHECK (kind IN ('apartment', 'villa', 'officetel', 'house', 'other')),
    address_memo  TEXT    CHECK (address_memo IS NULL OR length(address_memo) > 0),
    moved_out_on  TEXT    CHECK (moved_out_on IS NULL OR date(moved_out_on) IS moved_out_on),
    color         TEXT    CHECK (color IS NULL
                                 OR color GLOB '#[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
    memo          TEXT    CHECK (memo IS NULL
                                 OR memo NOT GLOB '*[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'),
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
) STRICT;

CREATE TABLE property_contracts (
    id              INTEGER PRIMARY KEY,
    property_id     INTEGER NOT NULL REFERENCES properties (id),
    owner_member_id INTEGER NOT NULL REFERENCES members (id),
    contract_type   TEXT    NOT NULL CHECK (contract_type IN ('jeonse', 'monthly', 'own')),
    amount          INTEGER NOT NULL CHECK (amount >= 0),
    monthly_rent    INTEGER CHECK (monthly_rent IS NULL OR monthly_rent > 0),
    start_on        TEXT    NOT NULL CHECK (date(start_on) IS start_on),
    end_on          TEXT    CHECK (end_on IS NULL OR date(end_on) IS end_on),
    ended_on        TEXT    CHECK (ended_on IS NULL OR date(ended_on) IS ended_on),
    memo            TEXT    CHECK (memo IS NULL
                                   OR memo NOT GLOB '*[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK ((contract_type = 'monthly') = (monthly_rent IS NOT NULL)),
    CHECK ((contract_type = 'own') = (end_on IS NULL)),
    CHECK (contract_type = 'monthly' OR amount > 0),
    CHECK (end_on IS NULL OR end_on > start_on),
    CHECK (ended_on IS NULL OR ended_on >= start_on)
) STRICT;

CREATE INDEX idx_property_contracts_property ON property_contracts (property_id);
CREATE UNIQUE INDEX idx_property_contracts_one_open
    ON property_contracts (property_id) WHERE ended_on IS NULL;

-- 시세 기록. 한 달에 하나 (다시 넣으면 바뀐다)
CREATE TABLE property_valuations (
    property_id INTEGER NOT NULL REFERENCES properties (id),
    year_month  TEXT    NOT NULL
                CHECK (year_month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
                       AND date(year_month || '-01') IS year_month || '-01'),
    value       INTEGER NOT NULL CHECK (value > 0),
    source      TEXT    NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'lookup')),
    memo        TEXT    CHECK (memo IS NULL
                               OR memo NOT GLOB '*[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (property_id, year_month)
) STRICT;

-- 고정비 → 관련 집 (관리비, 월세 등). 집 화면에 함께 나온다.
ALTER TABLE fixed_items ADD COLUMN property_id INTEGER REFERENCES properties (id);

CREATE INDEX idx_fixed_items_property ON fixed_items (property_id);