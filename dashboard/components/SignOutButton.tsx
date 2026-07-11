"use client";

import { useRouter } from "next/navigation";
import { createClient } from "../lib/supabase/client";

export default function SignOutButton() {
  const router = useRouter();
  const supabase = createClient();

  async function signOut() {
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <button className="btn" onClick={signOut}>
      Sign out
    </button>
  );
}
