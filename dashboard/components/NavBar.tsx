import Link from "next/link";
import { createClient } from "../lib/supabase/server";
import SignOutButton from "./SignOutButton";

export default async function NavBar() {
  const supabase = createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  return (
    <nav className="navbar">
      <div className="navbar-inner">
        <div className="navbar-links">
          <Link href="/">Dashboard</Link>
          <Link href="/strategies">Strategy Creator</Link>
          {user && <Link href="/accounts">My Accounts</Link>}
        </div>
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
