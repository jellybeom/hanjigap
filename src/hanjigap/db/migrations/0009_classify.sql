-- 0009: 수입·지출·이체 분류 (한지갑 분류 규칙 · 거래별 직접 지정)
--
-- 한지갑 분류: income(수입) · expense(지출) · saving(저축·투자) · excluded(제외)
--   '확인 필요'는 저장하지 않는다. 아무것도 정해지지 않은 거래가 확인 필요다.
--   지출 차감(점심값 정산 입금 등)은 expense + 들어온 돈이다. 그 분류의 지출에서 빠진다.
--
-- 판단 순서 (위가 먼저). 결과는 저장하지 않고 볼 때마다 계산한다.
--   1. 거래별 직접 지정 (tx_classes) — 거래 지문(tx_key)에 붙어서 다시 정산해도 남는다
--   2. 고정비 '용돈' 항목과 맞춰진 거래 → 지출 › 용돈 (J7b)
--   3. 규칙 (class_rules) — position이 작은 것부터. 새 규칙은 맨 위
--   4. 뱅샐 기본값 (코드에 있음)
--
-- 규칙 조건은 모두 비울 수 있지만 하나는 있어야 한다. 비운 조건은 '상관없음'.
--   member_id: 누구의 거래 (비우면 두 사람 모두)
--   direction: in(들어온 돈) · out(나간 돈) · any
--   amount: 정확히 같은 금액 (부호 없이)
-- 회사·사람 이름 같은 규칙 내용은 DB에만 둔다. (코드 저장소에 넣지 않음)

CREATE TABLE class_rules (
    id              INTEGER PRIMARY KEY,
    position        INTEGER NOT NULL,
    member_id       INTEGER REFERENCES members (id),
    direction       TEXT    NOT NULL DEFAULT 'any' CHECK (direction IN ('in', 'out', 'any')),
    tx_type         TEXT    CHECK (tx_type IS NULL OR tx_type IN ('수입', '지출', '이체')),
    category        TEXT    CHECK (category IS NULL OR length(category) > 0),
    subcategory     TEXT    CHECK (subcategory IS NULL OR length(subcategory) > 0),
    pattern         TEXT    CHECK (pattern IS NULL OR length(pattern) BETWEEN 1 AND 50),
    instrument      TEXT    CHECK (instrument IS NULL OR length(instrument) > 0),
    amount          INTEGER CHECK (amount IS NULL OR amount > 0),
    result          TEXT    NOT NULL CHECK (result IN ('income', 'expense', 'saving', 'excluded')),
    result_category TEXT    CHECK (result_category IS NULL OR length(result_category) BETWEEN 1 AND 30),
    memo            TEXT    CHECK (memo IS NULL
                                   OR memo NOT GLOB '*[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'),
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    created_by      INTEGER REFERENCES members (id),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    CHECK (subcategory IS NULL OR category IS NOT NULL),
    CHECK (direction != 'any' OR tx_type IS NOT NULL OR category IS NOT NULL
           OR pattern IS NOT NULL OR instrument IS NOT NULL OR amount IS NOT NULL)
) STRICT;

CREATE INDEX idx_class_rules_position ON class_rules (position, id);

CREATE TABLE tx_classes (
    member_id       INTEGER NOT NULL REFERENCES members (id),
    tx_key          TEXT    NOT NULL CHECK (length(tx_key) = 32),
    result          TEXT    NOT NULL CHECK (result IN ('income', 'expense', 'saving', 'excluded')),
    result_category TEXT    CHECK (result_category IS NULL OR length(result_category) BETWEEN 1 AND 30),
    classified_by   INTEGER REFERENCES members (id),
    classified_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (member_id, tx_key)
) STRICT;