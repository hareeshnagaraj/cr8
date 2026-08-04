"use client";

import {useState} from "react";

import {LOGIN_MARK} from "@/lib/loginMark";

const MARK_WIDTH = 72;
const MARK_HEIGHT = 44;
const BAR_GAP = 4;
const BAR_WIDTH = (MARK_WIDTH - BAR_GAP * (LOGIN_MARK.bars.length - 1)) /
  LOGIN_MARK.bars.length;

function serverError(html: string) {
  const document = new DOMParser().parseFromString(html, "text/html");
  return document.querySelector(".error")?.textContent?.trim() || "Login failed.";
}

export default function Login() {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    const form = new FormData(event.currentTarget);
    const body = new URLSearchParams({
      username: String(form.get("username") ?? ""),
      password: String(form.get("password") ?? ""),
    });
    try {
      const response = await fetch("/api/login", {
        method: "POST",
        credentials: "same-origin",
        headers: {"Content-Type": "application/x-www-form-urlencoded"},
        body,
      });
      if (response.ok) {
        window.location.href = "/";
        return;
      }
      setError(serverError(await response.text()));
    } catch {
      setError("Login could not reach the server.");
    }
    setBusy(false);
  }

  return (
    <main className="login-page">
      <div className="login-lockup">
        <div className="login-brand">
          <svg
            className="login-mark"
            viewBox={`0 0 ${MARK_WIDTH} ${MARK_HEIGHT}`}
            aria-hidden="true"
          >
            {LOGIN_MARK.bars.map((level, index) => {
              const height = level * MARK_HEIGHT;
              return (
                <rect
                  key={index}
                  x={index * (BAR_WIDTH + BAR_GAP)}
                  y={MARK_HEIGHT - height}
                  width={BAR_WIDTH}
                  height={height}
                  fill={LOGIN_MARK.hue}
                />
              );
            })}
          </svg>
          <div className="login-wordmark">cr8</div>
        </div>

        <form className="login-form" onSubmit={submit}>
          <label className="login-field" htmlFor="username">
            <span>Username</span>
            <input
              className="login-input"
              id="username"
              name="username"
              type="text"
              autoComplete="username"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              required
              maxLength={40}
            />
          </label>
          <label className="login-field" htmlFor="password">
            <span>Password</span>
            <input
              className="login-input"
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
            />
          </label>
          <div className="login-error" aria-live="polite">
            {error ? <span>{error}</span> : null}
          </div>
          <button className="btn btn-main login-submit" type="submit" disabled={busy}>
            {busy ? "Opening…" : "Open library"}
          </button>
        </form>
      </div>
    </main>
  );
}
