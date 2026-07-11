"use client";

import { useEffect, useState } from "react";
import type { SavedAccount } from "../../lib/accountsStore";

export default function AccountsPage() {
  const [accounts, setAccounts] = useState<SavedAccount[]>([]);
  const [accountName, setAccountName] = useState("");
  const [label, setLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "error" | "ok"; text: string } | null>(null);
  const [loaded, setLoaded] = useState(false);

  async function refresh() {
    const res = await fetch("/api/accounts");
    const data = await res.json();
    if (res.ok) setAccounts(data.accounts ?? []);
    setLoaded(true);
  }

  useEffect(() => {
    refresh();
  }, []);

  async function addAccount(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const res = await fetch("/api/accounts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accountName, label }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to add account");
      setAccountName("");
      setLabel("");
      await refresh();
    } catch (err: any) {
      setMessage({ kind: "error", text: err.message });
    } finally {
      setBusy(false);
    }
  }

  async function removeAccount(id: string) {
    setBusy(true);
    setMessage(null);
    try {
      const res = await fetch(`/api/accounts?id=${encodeURIComponent(id)}`, { method: "DELETE" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Failed to remove account");
      await refresh();
    } catch (err: any) {
      setMessage({ kind: "error", text: err.message });
    } finally {
      setBusy(false);
    }
  }

  const namesForEnv = accounts.map((a) => a.account_name).join(",");

  return (
    <div className="container">
      <div className="header">
        <div>
          <h1>My Tradovate Accounts</h1>
          <p>
            Save the Tradovate account name(s) you trade — private to your account, nothing here is shared with
            other users. Only the account <em>name</em> is stored; your Tradovate login, password, CID, and SEC
            still live only in your bot host&apos;s environment variables, never in this database.
          </p>
        </div>
      </div>

      <div className="test-panel">
        <div className="test-panel-header">
          <h2>Add an account</h2>
          <p>
            Enter each Tradovate account name exactly as it appears in Tradovate (e.g. <code>DEMO12345</code>). One
            Tradovate login can have several accounts — add all of them here.
          </p>
        </div>
        <form onSubmit={addAccount} className="test-panel-row">
          <input
            className="test-input"
            placeholder="Account name, e.g. DEMO12345"
            value={accountName}
            onChange={(e) => setAccountName(e.target.value)}
            required
            maxLength={64}
          />
          <input
            className="test-input"
            placeholder="Optional label, e.g. Eval #2"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            maxLength={100}
          />
          <button className="btn btn-primary" type="submit" disabled={busy || !accountName.trim()}>
            Add Account
          </button>
        </form>
        {message && <div className={`test-status test-status-${message.kind}`}>{message.text}</div>}
      </div>

      {loaded && accounts.length === 0 ? (
        <div className="empty-state">No accounts saved yet. Add one above.</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Account Name</th>
                <th>Label</th>
                <th>Added</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((a) => (
                <tr key={a.id}>
                  <td>{a.account_name}</td>
                  <td>{a.label || "—"}</td>
                  <td>{new Date(a.created_at).toLocaleDateString()}</td>
                  <td>
                    <button className="btn" onClick={() => removeAccount(a.id)} disabled={busy}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {accounts.length > 0 && (
        <div className="test-panel" style={{ marginTop: 20 }}>
          <div className="test-panel-header">
            <h2>Use these in your bot host</h2>
            <p>
              Copy this into your Railway (or wherever <code>scripts/run_live.py</code> runs) environment variables
              as <code>TRADOVATE_ACCOUNT_NAMES</code>:
            </p>
          </div>
          <code className="env-copy">{namesForEnv}</code>
        </div>
      )}
    </div>
  );
}
