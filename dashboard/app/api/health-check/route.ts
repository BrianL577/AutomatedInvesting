import { NextRequest, NextResponse } from "next/server";
import { sendAlertEmail } from "../../../lib/emailAlert";

export const dynamic = "force-dynamic";

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL;
const SUPABASE_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

// If the most recent bar is older than this while the market should be
// open, the bot is presumed down. CONFIRMED LIVE: both processes (real +
// virtual) went completely silent for 1.5+ days with nothing catching it
// until the user happened to notice -- this exists specifically to catch
// that within one cron tick instead.
const STALE_THRESHOLD_MINUTES = 25;
// Don't re-email on every single cron tick during a continued outage.
const ALERT_COOLDOWN_MINUTES = 120;

/** Rough approximation of CME Globex hours for NQ: open Sunday 6pm ET
 * through Friday 5pm ET, with a daily maintenance halt 5-6pm ET every day.
 * Good enough for "should we expect fresh bars right now" -- not meant to
 * be exact to the minute for holidays/early closes. */
function isMarketLikelyOpen(nowEt: Date): boolean {
  const day = nowEt.getDay(); // 0=Sun .. 6=Sat
  const hour = nowEt.getHours();
  if (day === 6) return false; // Saturday: always closed
  if (day === 0 && hour < 18) return false; // Sunday before 6pm ET: closed
  if (day === 5 && hour >= 17) return false; // Friday after 5pm ET: closed
  if (hour >= 17 && hour < 18) return false; // daily maintenance halt, every day
  return true;
}

function nowInEt(): Date {
  return new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
}

async function getLatestBarTime(): Promise<Date | null> {
  if (!SUPABASE_URL || !SUPABASE_KEY) return null;
  const res = await fetch(`${SUPABASE_URL}/rest/v1/bars?select=t&order=t.desc&limit=1`, {
    headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}` },
    cache: "no-store",
  });
  if (!res.ok) return null;
  const rows = (await res.json()) as { t: string }[];
  return rows.length ? new Date(rows[0].t) : null;
}

async function getLastAlertAt(): Promise<Date | null> {
  if (!SUPABASE_URL || !SUPABASE_KEY) return null;
  const res = await fetch(`${SUPABASE_URL}/rest/v1/alert_state?select=last_alert_at&id=eq.heartbeat`, {
    headers: { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}` },
    cache: "no-store",
  });
  if (!res.ok) return null;
  const rows = (await res.json()) as { last_alert_at: string | null }[];
  return rows.length && rows[0].last_alert_at ? new Date(rows[0].last_alert_at) : null;
}

async function recordAlertSent(): Promise<void> {
  if (!SUPABASE_URL || !SUPABASE_KEY) return;
  await fetch(`${SUPABASE_URL}/rest/v1/alert_state`, {
    method: "POST",
    headers: {
      apikey: SUPABASE_KEY,
      Authorization: `Bearer ${SUPABASE_KEY}`,
      "Content-Type": "application/json",
      Prefer: "resolution=merge-duplicates,return=minimal",
    },
    body: JSON.stringify({ id: "heartbeat", last_alert_at: new Date().toISOString() }),
  });
}

export async function GET(request: NextRequest) {
  const cronSecret = process.env.CRON_SECRET;
  if (cronSecret) {
    const auth = request.headers.get("authorization");
    if (auth !== `Bearer ${cronSecret}`) {
      return NextResponse.json({ error: "unauthorized" }, { status: 401 });
    }
  }

  const nowEt = nowInEt();
  const marketOpen = isMarketLikelyOpen(nowEt);
  const latestBar = await getLatestBarTime();
  const minutesSinceLastBar = latestBar ? (Date.now() - latestBar.getTime()) / 60000 : null;
  const stale = marketOpen && (minutesSinceLastBar === null || minutesSinceLastBar > STALE_THRESHOLD_MINUTES);

  let alertSent = false;
  if (stale) {
    const lastAlertAt = await getLastAlertAt();
    const minutesSinceLastAlert = lastAlertAt ? (Date.now() - lastAlertAt.getTime()) / 60000 : Infinity;
    if (minutesSinceLastAlert > ALERT_COOLDOWN_MINUTES) {
      const body = latestBar
        ? `No new market data has arrived in ${minutesSinceLastBar!.toFixed(0)} minutes (last bar: ${latestBar.toISOString()}), but the market should be open right now. The trading bot may be down.`
        : `No market data has ever been recorded, and the market should be open right now. The trading bot may not be running.`;
      alertSent = await sendAlertEmail("[AutomatedInvesting] Bot may be down -- no recent market data", body);
      if (alertSent) await recordAlertSent();
    }
  }

  return NextResponse.json({
    marketOpen,
    latestBar: latestBar?.toISOString() ?? null,
    minutesSinceLastBar,
    stale,
    alertSent,
  });
}
