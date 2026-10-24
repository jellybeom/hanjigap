-- 0002: 뱅샐 현금흐름 참고값에 수입·지출 구분 추가
--   income  = 현금흐름현황 표에서 '월수입 총계' 위의 항목
--   expense = '월수입 총계'와 '월지출 총계' 사이의 항목

ALTER TABLE bs_cashflow
    ADD COLUMN kind TEXT NOT NULL DEFAULT 'expense' CHECK (kind IN ('income', 'expense'));