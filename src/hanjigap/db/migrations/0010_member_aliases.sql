-- 0010: 구성원 별칭 (부부 이체 자동 인식용)
--
-- 이체 내용에 다른 구성원의 이름이 들어 있으면 부부 이체로 보고 제외한다.
-- 찾는 이름 = 구성원 이름(display_name, 2자 이상) + 여기 적은 별칭 (실명·애칭 등)
-- 이름은 DB에만 둔다. (코드 저장소에 넣지 않음)
-- 한 별칭은 한 사람에게만. (두 사람 이름이 겹치면 누구에게 보낸 건지 알 수 없어서)

CREATE TABLE member_aliases (
    member_id  INTEGER NOT NULL REFERENCES members (id),
    alias      TEXT    NOT NULL CHECK (length(alias) BETWEEN 2 AND 20),
    created_at TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (member_id, alias)
) STRICT;