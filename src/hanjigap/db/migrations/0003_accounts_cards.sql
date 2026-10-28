-- 0003: 계좌 · 카드 · 뱅샐 결제수단 연결
--
-- 원칙
--   계좌번호·카드번호는 끝 4자리만 저장한다. 전체 번호, 유효기간, CVC를 넣을 칸은 두지 않는다.
--   자유 입력 칸(memo, number_hint)에는 숫자가 10자리 넘게 이어지면 저장하지 않는다. (실수로 전체 번호를 적는 것 방지)
--   계좌·카드는 지우지 않고 closed_on(해지일)을 채운다. 지난 거래 연결이 끊기지 않게 하려는 것이다.
--   종류 값은 영어 코드로 저장하고 화면에서 한글로 바꿔 보여 준다.
--   color: 화면에 쓰는 대표색 '#rrggbb' (소문자). 비어 있으면 화면이 종류별 기본색을 쓴다.

CREATE TABLE accounts (
    id           INTEGER PRIMARY KEY,
    member_id    INTEGER NOT NULL REFERENCES members (id),   -- 명의자
    institution  TEXT    NOT NULL CHECK (length(institution) > 0),
    name         TEXT    NOT NULL CHECK (length(name) > 0),
    kind         TEXT    NOT NULL
                 CHECK (kind IN ('checking', 'savings', 'deposit', 'parking', 'securities',
                                 'isa', 'pension', 'irp', 'fund', 'other')),
    number_last4 TEXT    CHECK (number_last4 IS NULL OR number_last4 GLOB '[0-9][0-9][0-9][0-9]'),
    opened_on    TEXT    CHECK (opened_on IS NULL
                                OR date(opened_on) IS opened_on
                                OR (opened_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
                                    AND date(opened_on || '-01') IS opened_on || '-01')),
    closed_on    TEXT    CHECK (closed_on IS NULL OR date(closed_on) IS closed_on),
    color        TEXT    CHECK (color IS NULL
                                OR color GLOB '#[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
    memo         TEXT    CHECK (memo IS NULL
                                OR memo NOT GLOB '*[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'),
    number_hint  TEXT    CHECK (number_hint IS NULL
                                OR number_hint NOT GLOB '*[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'),
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (member_id, name),
    UNIQUE (id, member_id),                                   -- instrument_links가 '같은 사람'을 검사하는 데 쓴다
    CHECK (closed_on IS NULL OR opened_on IS NULL OR closed_on >= opened_on)
) STRICT;

CREATE TABLE cards (
    id                  INTEGER PRIMARY KEY,
    member_id           INTEGER NOT NULL REFERENCES members (id),   -- 명의자
    issuer              TEXT    NOT NULL CHECK (length(issuer) > 0),
    name                TEXT    NOT NULL CHECK (length(name) > 0),
    kind                TEXT    NOT NULL CHECK (kind IN ('credit', 'check', 'hipass', 'other')),
    number_last4        TEXT    CHECK (number_last4 IS NULL
                                       OR number_last4 GLOB '[0-9][0-9][0-9][0-9]'),
    pay_day             INTEGER CHECK (pay_day IS NULL OR pay_day BETWEEN 1 AND 31),
    -- 이용기간: (결제월 + period_month_offset)월 period_start_day일부터 한 달.
    -- 예) 전월 1일 ~ 말일 = (1, -1). 둘 다 비어 있으면 '전월 1일 ~ 말일'로 본다.
    period_start_day    INTEGER CHECK (period_start_day IS NULL OR period_start_day BETWEEN 1 AND 31),
    period_month_offset INTEGER CHECK (period_month_offset IS NULL
                                       OR period_month_offset BETWEEN -2 AND 0),
    pay_account_id      INTEGER REFERENCES accounts (id),           -- 다른 사람 계좌도 가능 (가족카드 등)
    annual_fee          INTEGER CHECK (annual_fee IS NULL OR annual_fee >= 0),
    closed_on           TEXT    CHECK (closed_on IS NULL OR date(closed_on) IS closed_on),
    color               TEXT    CHECK (color IS NULL
                                       OR color GLOB '#[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]'),
    memo                TEXT    CHECK (memo IS NULL
                                       OR memo NOT GLOB '*[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'),
    number_hint         TEXT    CHECK (number_hint IS NULL
                                       OR number_hint NOT GLOB '*[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'),
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (member_id, name),
    UNIQUE (id, member_id),
    CHECK ((period_start_day IS NULL) = (period_month_offset IS NULL))
) STRICT;

CREATE INDEX idx_cards_pay_account ON cards (pay_account_id);

-- 뱅샐 가계부의 '결제수단' 이름 → 계좌·카드. 한 번 짝지으면 그 이름의 거래가 모두 이어진다.
-- 거래 표는 고치지 않고 이름으로 이어 붙이므로 다시 정산해도 연결이 유지된다.
-- '연결 안 됨'은 저장하지 않는다. 거래에는 있는데 이 표에 없는 이름이 곧 연결 안 된 결제수단이다.
CREATE TABLE instrument_links (
    member_id  INTEGER NOT NULL REFERENCES members (id),   -- 누구의 뱅샐 파일에 나온 이름인가
    instrument TEXT    NOT NULL CHECK (length(instrument) > 0),
    target     TEXT    NOT NULL CHECK (target IN ('account', 'card', 'easy_pay')),
    account_id INTEGER,
    card_id    INTEGER,
    linked_by  INTEGER REFERENCES members (id),
    linked_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (member_id, instrument),
    -- 같은 사람의 계좌·카드에만 연결된다
    FOREIGN KEY (account_id, member_id) REFERENCES accounts (id, member_id),
    FOREIGN KEY (card_id, member_id) REFERENCES cards (id, member_id),
    CHECK ((target = 'account') = (account_id IS NOT NULL)),
    CHECK ((target = 'card') = (card_id IS NOT NULL))
) STRICT;

CREATE INDEX idx_instrument_links_account ON instrument_links (account_id);
CREATE INDEX idx_instrument_links_card ON instrument_links (card_id);