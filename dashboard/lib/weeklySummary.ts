/**
 * Compact weekly digest of the historical bars for the AI strategy chat:
 * the FIRST and LAST trading day of each week (~104 entries/year), each
 * reduced to one line of session stats. This is what Claude actually
 * reasons over when discussing patterns — a few KB of aggregated summaries
 * it can genuinely compare, not 484k raw bars it can't.
 */
import type { Bar } from "./backtester";

const ET_FMT = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  weekday: "short",
  hour12: false,
});

type EtInfo = { dateKey: string; weekday: string; hm: string };

function etInfo(iso: string): EtInfo {
  const parts = ET_FMT.formatToParts(new Date(iso));
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? "";
  const hour = get("hour") === "24" ? "00" : get("hour");
  return {
    dateKey: `${get("year")}-${get("month")}-${get("day")}`,
    weekday: get("weekday"),
    hm: `${hour}:${get("minute")}`,
  };
}

/** ISO week key (Monday-based) for a YYYY-MM-DD date string. */
function isoWeekKey(dateKey: string): string {
  const d = new Date(`${dateKey}T12:00:00Z`);
  const day = (d.getUTCDay() + 6) % 7; // Mon=0
  const thursday = new Date(d);
  thursday.setUTCDate(d.getUTCDate() - day + 3);
  const firstThursday = new Date(Date.UTC(thursday.getUTCFullYear(), 0, 4));
  const fd = (firstThursday.getUTCDay() + 6) % 7;
  firstThursday.setUTCDate(firstThursday.getUTCDate() - fd + 3);
  const week = 1 + Math.round((thursday.getTime() - firstThursday.getTime()) / (7 * 86400000));
  return `${thursday.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

type DaySummary = {
  dateKey: string;
  weekday: string;
  open: number;
  close: number;
  high: number;
  low: number;
  sessionNet: number | null; // 09:30 -> 11:00 ET move, if bars exist there
};

function summarizeDay(dateKey: string, weekday: string, dayBars: Bar[]): DaySummary {
  let high = -Infinity;
  let low = Infinity;
  let sessionOpen: number | null = null;
  let sessionClose: number | null = null;
  for (const b of dayBars) {
    if (b.h > high) high = b.h;
    if (b.l < low) low = b.l;
    const { hm } = etInfo(b.t);
    if (hm >= "09:30" && hm <= "11:00") {
      if (sessionOpen === null) sessionOpen = b.o;
      sessionClose = b.c;
    }
  }
  return {
    dateKey,
    weekday,
    open: dayBars[0].o,
    close: dayBars[dayBars.length - 1].c,
    high,
    low,
    sessionNet: sessionOpen !== null && sessionClose !== null ? sessionClose - sessionOpen : null,
  };
}

/** One line per selected day, newest weeks last. Caps at `maxWeeks` most
 * recent weeks so the prompt stays small no matter how much history grows. */
export function buildWeeklyEdgeSummary(bars: Bar[], maxWeeks = 60): string {
  const byDay = new Map<string, { weekday: string; bars: Bar[] }>();
  for (const b of bars) {
    const { dateKey, weekday } = etInfo(b.t);
    if (!byDay.has(dateKey)) byDay.set(dateKey, { weekday, bars: [] });
    byDay.get(dateKey)!.bars.push(b);
  }

  const byWeek = new Map<string, string[]>(); // weekKey -> sorted dateKeys
  for (const dateKey of [...byDay.keys()].sort()) {
    const wk = isoWeekKey(dateKey);
    if (!byWeek.has(wk)) byWeek.set(wk, []);
    byWeek.get(wk)!.push(dateKey);
  }

  const weeks = [...byWeek.keys()].sort().slice(-maxWeeks);
  const lines: string[] = [];
  for (const wk of weeks) {
    const dayKeys = byWeek.get(wk)!;
    const picks = dayKeys.length === 1 ? [dayKeys[0]] : [dayKeys[0], dayKeys[dayKeys.length - 1]];
    for (const dateKey of picks) {
      const { weekday, bars: dayBars } = byDay.get(dateKey)!;
      dayBars.sort((a, b) => a.t.localeCompare(b.t));
      const s = summarizeDay(dateKey, weekday, dayBars);
      const net = s.close - s.open;
      const label = dateKey === dayKeys[0] ? "week-open " : "week-close";
      lines.push(
        `${s.dateKey} ${s.weekday} ${label} | day ${net >= 0 ? "+" : ""}${net.toFixed(1)} pts (O ${s.open.toFixed(1)} -> C ${s.close.toFixed(1)}, range ${(s.high - s.low).toFixed(1)})` +
          (s.sessionNet !== null ? ` | 09:30-11:00 ET ${s.sessionNet >= 0 ? "+" : ""}${s.sessionNet.toFixed(1)} pts` : " | no 09:30-11:00 data")
      );
    }
  }
  return lines.join("\n");
}
