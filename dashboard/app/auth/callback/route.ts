import { NextRequest, NextResponse } from "next/server";
import { createClient } from "../../../lib/supabase/server";

/** Handles the email-confirmation link Supabase sends on sign-up. */
export async function GET(req: NextRequest) {
  const code = req.nextUrl.searchParams.get("code");
  const next = req.nextUrl.searchParams.get("next") || "/";

  if (code) {
    const supabase = createClient();
    await supabase.auth.exchangeCodeForSession(code);
  }

  return NextResponse.redirect(new URL(next, req.url));
}
