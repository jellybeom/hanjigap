-- 0007: 자동차 유지비 (어떤 거래를 자동차 비용으로 볼지)
--
-- 원칙
--   유지비는 저장하지 않고 볼 때마다 거래에서 모은다. 지출 합계에는 원래대로 들어간다.
--   규칙(vehicle_cost_rules)으로 자동 표시하고, 틀린 것은 거래별로 붙이거나 뺀다(vehicle_tx_tags).
--   거래별 표시는 웹 수정 내용처럼 거래 지문(tx_key)에 붙인다. 다시 정산해도 유지된다.
--   할부 상환은 유지비가 아니다. (기본 규칙에 넣지 않는다)
--
-- 규칙 종류
--   category   뱅샐 분류 (대분류, 소분류는 비우면 대분류 전체)
--   text       거래 내용에 들어가는 글자
--   instrument 결제수단 이름 (사람마다 이름이 달라서 member_id 필수)
--   member_id가 비어 있으면 모든 구성원의 거래에 적용한다. (차는 한 사람 소유여도 함께 탄다)

CREATE TABLE vehicle_cost_rules (
    id          INTEGER PRIMARY KEY,
    vehicle_id  INTEGER NOT NULL REFERENCES vehicles (id),
    kind        TEXT    NOT NULL CHECK (kind IN ('category', 'text', 'instrument')),
    member_id   INTEGER REFERENCES members (id),
    category    TEXT    CHECK (category IS NULL OR length(category) > 0),
    subcategory TEXT    CHECK (subcategory IS NULL OR length(subcategory) > 0),
    pattern     TEXT    CHECK (pattern IS NULL OR length(pattern) BETWEEN 1 AND 50),
    cost_type   TEXT    NOT NULL
                CHECK (cost_type IN ('fuel', 'toll', 'parking', 'wash', 'repair', 'insurance',
                                     'tax', 'fine', 'other')),
    active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK ((kind = 'category') = (category IS NOT NULL)),
    CHECK ((kind = 'category') = (pattern IS NULL)),
    CHECK (subcategory IS NULL OR kind = 'category'),
    CHECK (kind != 'instrument' OR member_id IS NOT NULL)
) STRICT;

CREATE INDEX idx_vehicle_cost_rules_vehicle ON vehicle_cost_rules (vehicle_id);

CREATE TABLE vehicle_tx_tags (
    vehicle_id INTEGER NOT NULL REFERENCES vehicles (id),
    member_id  INTEGER NOT NULL REFERENCES members (id),
    tx_key     TEXT    NOT NULL CHECK (length(tx_key) = 32),
    action     TEXT    NOT NULL CHECK (action IN ('include', 'exclude')),
    cost_type  TEXT    CHECK (cost_type IS NULL
                              OR cost_type IN ('fuel', 'toll', 'parking', 'wash', 'repair',
                                               'insurance', 'tax', 'fine', 'other')),
    tagged_by  INTEGER REFERENCES members (id),
    tagged_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (vehicle_id, member_id, tx_key),
    CHECK (action = 'include' OR cost_type IS NULL)
) STRICT;