import Link from "next/link";
import { createClient } from "../lib/supabase/server";
import SignOutButton from "./SignOutButton";
import NavTabs from "./NavTabs";

export default async function NavBar() {
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  return (
    <nav className="navbar">
      <div className="navbar-inner">
        {user ? (
          <NavTabs
            tabs={[
              { href: "/", label: "Dashboard" },
              { href: "/strategies", label: "Strategy Creator" },
              { href: "/optimizations", label: "Optimizations" },
              { href: "/accounts", label: "My Accounts" },
            ]}
          />
        ) : (
          <div className="navbar-links" />
        )}
        <div className="navbar-auth">
          {user ? (
            <>
              <span className="navbar-email">{user.email}</span>
              <SignOutButton />
            </>
          ) : (
            <>
              <Link className="btn" href="/login">
                Sign in
              </Link>
              <Link className="btn btn-primary" href="/signup">
                Sign up
              </Link>
            </>
          )}
        </div>
      </div>
    </nav>
  );
}
