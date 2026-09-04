// Sends an alert email via Gmail SMTP using an App Password -- same
// mechanism and env var names as jj_bot/alerts.py (the laptop-side crash
// alert used by self_update.ps1), so the same Gmail App Password works for
// both without the user needing to set up two separate things. If
// SMTP_USER/SMTP_PASSWORD aren't configured (as Vercel project env vars --
// these are NOT synced from the laptop's own .env, they have to be added
// separately in the Vercel dashboard), sends nothing and returns false so
// the caller can decide how to handle "alerting isn't configured yet."
import nodemailer from "nodemailer";

export async function sendAlertEmail(subject: string, body: string): Promise<boolean> {
  const user = process.env.SMTP_USER;
  const password = process.env.SMTP_PASSWORD;
  const to = process.env.ALERT_EMAIL_TO || user;

  if (!user || !password) {
    console.warn("SMTP_USER/SMTP_PASSWORD not set -- skipping alert email.");
    return false;
  }

  const transport = nodemailer.createTransport({
    host: "smtp.gmail.com",
    port: 587,
    secure: false,
    auth: { user, pass: password },
  });

  try {
    await transport.sendMail({ from: user, to, subject, text: body });
    return true;
  } catch (err) {
    console.error("Failed to send alert email:", err);
    return false;
  }
}
