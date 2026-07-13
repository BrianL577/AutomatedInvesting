import { NextRequest, NextResponse } from "next/server";
import { StrategyConfigSchema } from "../../../lib/strategySchema";
import {
  deleteStrategyForCurrentUser,
  listStrategiesForCurrentUser,
  renameStrategyForCurrentUser,
  saveStrategyForCurrentUser,
  setActiveStrategyForCurrentUser,
} from "../../../lib/strategyStore";

export const dynamic = "force-dynamic";

export async function GET() {
  const { strategies, signedIn } = await listStrategiesForCurrentUser();
  return NextResponse.json({ strategies, signedIn });
}

export async function POST(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const { config, source, prompt } = (body ?? {}) as {
    config?: unknown;
    source?: string;
    prompt?: string;
  };

  const parsed = StrategyConfigSchema.safeParse(config);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Strategy config failed validation", issues: parsed.error.issues.slice(0, 10) },
      { status: 400 }
    );
  }
  if (typeof prompt === "string" && prompt.length > 5000) {
    return NextResponse.json({ error: "Prompt too long" }, { status: 400 });
  }

  const result = await saveStrategyForCurrentUser(parsed.data, source === "ai" ? "ai" : "manual", prompt);
  if (!result.ok) return NextResponse.json({ error: result.error }, { status: result.status });
  return NextResponse.json({ id: result.id });
}

export async function PATCH(req: NextRequest) {
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const { activateId, renameId, name } = (body ?? {}) as {
    activateId?: string | null;
    renameId?: string;
    name?: string;
  };

  if (renameId !== undefined) {
    if (typeof name !== "string") {
      return NextResponse.json({ error: "Missing name (string)" }, { status: 400 });
    }
    const result = await renameStrategyForCurrentUser(renameId, name);
    if (!result.ok) return NextResponse.json({ error: result.error }, { status: result.status });
    return NextResponse.json({ ok: true });
  }

  if (activateId === undefined) {
    return NextResponse.json(
      { error: "Missing activateId (string id, or null for the default) or renameId+name" },
      { status: 400 }
    );
  }

  const result = await setActiveStrategyForCurrentUser(activateId);
  if (!result.ok) return NextResponse.json({ error: result.error }, { status: result.status });
  return NextResponse.json({ ok: true });
}

export async function DELETE(req: NextRequest) {
  const id = req.nextUrl.searchParams.get("id");
  if (!id) return NextResponse.json({ error: "Missing id" }, { status: 400 });
  if (id === "default-jj") return NextResponse.json({ error: "Cannot delete the default strategy" }, { status: 400 });

  const result = await deleteStrategyForCurrentUser(id);
  if (!result.ok) return NextResponse.json({ error: result.error }, { status: result.status });
  return NextResponse.json({ ok: true });
}
