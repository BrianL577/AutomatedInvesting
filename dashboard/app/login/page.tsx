import Link from "next/link";
import AuthForm from "../../components/AuthForm";

export default function LoginPage() {
  return (
    <div className="container auth-page">
      <div className="test-panel auth-panel">
        <div className="test-panel-header">
          <h2>Sign in</h2>
          <p>Access your saved strategies and Tradovate accounts.</p>
        </div>
        <AuthForm mode="signin" />
        <p className="auth-switch">
          No account? <Link href="/signup">Sign up</Link>
        </p>
      </div>
    </div>
  );
}
