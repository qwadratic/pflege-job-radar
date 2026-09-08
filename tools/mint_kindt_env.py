"""Mint (or rotate) the agent API key and write a ready-to-use .env.kindt -- gitignored, real values
filled in. Never prints the key: it's written straight to the file so it can't end up in a shell
history, a log, or a chat transcript. Read the file yourself, or hand it to kindt directly.

  python tools/mint_kindt_env.py            # mint (or reuse the existing key's slot) and write
  python tools/mint_kindt_env.py --rotate   # invalidate the previous key, mint a new one
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import settings as ST  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SUPABASE_URL = "https://klkxfvieaxpjlplloljn.supabase.co"
SUPABASE_ANON_KEY = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imtsa3hmdmllYXhwamxwbGxvbGpuIiwicm9sZSI6ImFub24i"
                     "LCJpYXQiOjE3NzM5MjEwOTgsImV4cCI6MjA4OTQ5NzA5OH0.S0ED1qBUyRDP0YSDVBQ0s_L5_tKdu4jsPsLmyUo1YCk")  # public by design, see web/.env.build


def main():
    rotate = "--rotate" in sys.argv
    key = ST.set_agent_key(rotate=rotate)
    tmpl = (ROOT / ".env.kindt.example").read_text(encoding="utf-8")
    out = (tmpl.replace("<paste the value PUT /api/settings/agent-key returned>", key)
                .replace("https://<project-ref>.supabase.co", SUPABASE_URL)
                .replace("<anon key -- public, read-only>", SUPABASE_ANON_KEY))
    (ROOT / ".env.kindt").write_text(out, encoding="utf-8")
    print(f"wrote {ROOT / '.env.kindt'} ({'rotated' if rotate else 'minted'} key) -- key not printed here, read the file")


if __name__ == "__main__":
    main()
