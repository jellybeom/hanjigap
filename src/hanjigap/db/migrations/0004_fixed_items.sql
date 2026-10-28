-- 0004: 고정비 (매달·매년 고정적으로 빠져나가는 항목)
--
-- 원칙
--   수입·지출 합계와는 별개인 '목록'이다. 이 표가 합계를 바꾸지 않는다.
--   그달 상태(빠져나감 / 금액 다름 / 안 빠져나감 / 예정)는 저장하지 않고 볼 때마다 거래와 맞춰 계산한다.
--   담당(member_id) = 빠져나가는 계좌·카드의 주인. 계좌·카드를 고르면 같은 사람이어야 한다.
--   지우지 않고 end_month(마지막 달)로 마감한다.
--   category가 saving(적금 등)·loan(대출 상환·이자)이면 '저축·상환', 나머지는 '생활 고정비'로 묶어 보여 준다.

CREATE TABLE fixed_items (
    id             INTEGER PRIMARY KEY,
    member_id      INTEGER NOT NULL REFERENCES members (id),
    name           TEXT    NOT NULL CHECK (length(name) > 0),
    category       TEXT    NOT NULL
                   CHECK (category IN ('allowance', 'housing', 'insurance', 'parents', 'telecom',
                                       'saving', 'loan', 'other')),
    amount         INTEGER NOT NULL CHECK (amount > 0),        -- 원. 변동이면 기준(예상) 금액
    amount_mode    TEXT    NOT NULL DEFAULT 'fixed' CHECK (amount_mode IN ('fixed', 'variable')),
    cycle          TEXT    NOT NULL DEFAULT 'monthly' CHECK (cycle IN ('monthly', 'yearly')),
    pay_day        INTEGER NOT NULL CHECK (pay_day BETWEEN 1 AND 31),  -- 그달에 없는 날이면 말일로 본다
    pay_month      INTEGER CHECK (pay_month IS NULL OR pay_month BETWEEN 1 AND 12),  -- 매년일 때만
    method         TEXT    NOT NULL CHECK (method IN ('auto_transfer', 'card_auto', 'manual')),
    pay_account_id INTEGER,
    pay_card_id    INTEGER,
    match_text     TEXT    CHECK (match_text IS NULL OR length(match_text) BETWEEN 1 AND 50),
    match_days     INTEGER NOT NULL DEFAULT 3 CHECK (match_days BETWEEN 0 AND 10),
    start_month    TEXT    NOT NULL
                   CHECK (start_month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
                          AND date(start_month || '-01') IS start_month || '-01'),
    end_month      TEXT    CHECK (end_month IS NULL
                                  OR (end_month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
                                      AND date(end_month || '-01') IS end_month || '-01')),
    memo           TEXT    CHECK (memo IS NULL
                                  OR memo NOT GLOB '*[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'),
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    -- 출금 계좌·카드는 담당자 본인 것
    FOREIGN KEY (pay_account_id, member_id) REFERENCES accounts (id, member_id),
    FOREIGN KEY (pay_card_id, member_id) REFERENCES cards (id, member_id),
    CHECK ((cycle = 'yearly') = (pay_month IS NOT NULL)),
    CHECK (pay_account_id IS NULL OR pay_card_id IS NULL),
    CHECK ((method = 'card_auto') = (pay_card_id IS NOT NULL)),  -- 카드 자동결제 ⇔ 카드 있음
    CHECK (method != 'auto_transfer' OR pay_account_id IS NOT NULL),  -- 자동이체면 계좌 필수
    CHECK (end_month IS NULL OR end_month >= start_month)
) STRICT;

CREATE INDEX idx_fixed_items_member ON fixed_items (member_id);
CREATE INDEX idx_fixed_items_account ON fixed_items (pay_account_id);
CREATE INDEX idx_fixed_items_card ON fixed_items (pay_card_id);