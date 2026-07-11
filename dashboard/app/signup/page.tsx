import Link from "next/link";
import AuthForm from "../../components/AuthForm";

export default function SignupPage() {
  return (
    <div className="container auth-page">
      <div className="test-panel auth-panel">
        <div className="test-panel-header">
          <h2>Create your account</h2>
          <p>Your strategies and Tradovate account names are private to you.</p>
        </div>
        <AuthForm mode="signup" />
        <p className="auth-switch">
          Already have an account? <Link href="/login">Sign in</Link>
        </p>
      </div>
    </div>
  );
}
