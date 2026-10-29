// 숫자·날짜를 화면 글자로.

const NUM = new Intl.NumberFormat("ko-KR");
export const MINUS = "−"; // 목업과 같은 긴 빼기 기호

export function won(n) {
  return NUM.format(Math.abs(Math.round(n)));
}

export function signedWon(n) {
  if (n === 0) return "0";
  return (n > 0 ? "+" : MINUS) + won(n);
}

export function txAmount(n) {
  return (n > 0 ? "+" : MINUS) + won(n);
}

export function manWon(n) {
  return NUM.format(Math.round(n / 10000)) + "만";
}

export function monthLabel(ym) {
  const [y, m] = ym.split("-");
  return `${y}년 ${Number(m)}월`;
}

export function monthShort(ym) {
  return `${Number(ym.slice(5, 7))}월`;
}

export function monthDot(ym) {
  return ym.replace("-", ".");
}

export function dayLabel(date) {
  return `${Number(date.slice(5, 7))}월 ${Number(date.slice(8, 10))}일`;
}

export function shiftMonth(ym, delta) {
  const total = Number(ym.slice(0, 4)) * 12 + Number(ym.slice(5, 7)) - 1 + delta;
  const y = Math.floor(total / 12);
  const m = (total % 12) + 1;
  return `${y}-${String(m).padStart(2, "0")}`;
}

export function pct(part, whole) {
  if (!whole) return 0;
  return Math.round((part / whole) * 100);
}