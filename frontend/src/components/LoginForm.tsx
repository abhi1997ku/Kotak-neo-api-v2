import { useState } from "react";
import { useAuth } from "../contexts/AuthContext";

export function LoginForm() {
  const { login, isLoading, error } = useAuth();
  const [totp, setTotp] = useState("");
  const [mpin, setMpin] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (totp.length !== 6) {
      alert("TOTP must be 6 digits");
      return;
    }

    if (mpin.length < 4) {
      alert("MPIN must be entered");
      return;
    }

    try {
      await login(totp, mpin);
      setTotp("");
      setMpin("");
    } catch (err) {
      // Error is already set in context
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-950">
      <div className="w-full max-w-md rounded-xl border border-slate-800 bg-slate-900 p-8">
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-bold text-slate-100">Kotak Neo Terminal</h1>
          <p className="mt-2 text-sm text-slate-400">Personal Trading Dashboard</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-slate-300">TOTP Code</label>
            <input
              type="text"
              value={totp}
              onChange={(e) => setTotp(e.target.value.replace(/\D/g, "").slice(0, 6))}
              placeholder="000000"
              maxLength={6}
              className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2 text-center text-2xl font-mono tracking-widest text-slate-100 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
              disabled={isLoading}
            />
            <p className="mt-1 text-xs text-slate-400">Enter your 6-digit TOTP code</p>
          </div>

          <div>
            <label className="block text-sm font-medium text-slate-300">MPIN</label>
            <input
              type="password"
              value={mpin}
              onChange={(e) => setMpin(e.target.value.replace(/\D/g, "").slice(0, 8))}
              placeholder="Enter MPIN"
              maxLength={8}
              className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-800 px-4 py-2 text-base text-slate-100 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/20"
              disabled={isLoading}
            />
            <p className="mt-1 text-xs text-slate-400">Your broker MPIN is required to complete the session</p>
          </div>

          {error && <div className="rounded-lg border border-red-500/50 bg-red-500/10 p-3 text-sm text-red-300">{error}</div>}

          <button
            type="submit"
            disabled={isLoading || totp.length !== 6 || mpin.length < 4}
            className="w-full rounded-lg bg-blue-600 px-4 py-2 font-medium text-white disabled:opacity-50 hover:bg-blue-700"
          >
            {isLoading ? "Logging in..." : "Login"}
          </button>
        </form>

        <div className="mt-6 border-t border-slate-800 pt-6">
          <p className="text-center text-xs text-slate-400">
            This is a personal trading terminal for your Kotak Neo account.
            <br />
            Enter both TOTP and MPIN to start the live session.
          </p>
        </div>
      </div>
    </div>
  );
}
