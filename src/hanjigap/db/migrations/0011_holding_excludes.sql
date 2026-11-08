-- 0011: 한지갑 집계에서 뺄 뱅샐현황 항목 (각자 용돈 통장·마이너스 통장 등)
--
-- 뱅샐 가계부에서는 뺐지만 '뱅샐현황'(재무현황·대출현황 등)에는 남는 개인 영역을 한지갑에서 뺀다.
-- 원본(bs_holdings)은 그대로 두고, 보여 줄 때 이 규칙에 맞는 항목을 빼고 합계를 다시 낸다.
--   → 지난 달 기록에도 바로 적용되고, 규칙을 지우면 다시 나타난다.
-- 규칙은 본인만 보고 바꾼다 (부부가 서로 터치하지 않는 영역). 화면·API는 뺀 항목을 보내지 않는다.
-- 맞추는 기준: 사람 + 표(section) + 항목(group_name) + 금융사(institution) + 상품명(name). 빈 값은 ''.
-- 상품명은 DB에만 둔다. (로그·코드 저장소에 넣지 않음)

CREATE TABLE holding_excludes (
    id          INTEGER PRIMARY KEY,
    member_id   INTEGER NOT NULL REFERENCES members (id),
    section     TEXT    NOT NULL
                CHECK (section IN ('asset', 'liability', 'investment', 'loan', 'insurance')),
    group_name  TEXT    NOT NULL DEFAULT '',
    institution TEXT    NOT NULL DEFAULT '',
    name        TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (member_id, section, group_name, institution, name)
) STRICT;