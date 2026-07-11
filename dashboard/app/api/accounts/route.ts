import { NextRequest, NextResponse } from "next/server";
import {
  addAccountForCurrentUser,
  deleteAccountForCurrentUser,
  listAccountsForCurrentUser,
} from "../../../lib/accountsStore";

export const dynamic = "force-dynamic";

const NAME_RE = /^[A-Za-z0-9_-]{1,64}$/;

export async function GET() {
  const result = await listAccountsForCurrentUser();
  if (!result.ok) return NextResponse.json({ error: result.error }, { status: result.status });
  return NextResponse.json({ accounts: result.accounts });
}

export async function POST(req: NextRequest) {
  let body: { accountName?: unknown; label?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const accountName = typeof body.accountName === "string" ? body.accountName.trim() : "";
  const label = typeof body.label === "string" ? body.label.trim().slice(0, 100) : undefined;

  if (!NAME_RE.test(accountName)) {
    return NextResponse.json(
      { error: "Account name must be 1-64 characters: letters, numbers, underscore, hyphen." },
      { status: 400 }
    );
  }

  const result = await addAccountForCurrentUser(accountName, label);
  if (!result.ok) return NextResponse.json({ error: result.error }, { status: result.status });
  return NextResponse.json({ account: result.account });
}

export async function DELETE(req: NextRequest) {
  const id = req.nextUrl.searchParams.get("id");
  if (!id) return NextResponse.json({ error: "Missing id" }, { status: 400 });

  const result = await deleteAccountForCurrentUser(id);
  if (!result.ok) return NextResponse.json({ error: result.error }, { status: result.status });
  return NextResponse.json({ ok: true });
}
