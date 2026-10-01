# Cornershop

Server-rendered FastAPI + Jinja2 shop. Supabase Postgres + Storage, Google sign-in,
Paystack (test mode), Brevo email. See `SPEC.md` for the full spec.

## Local setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # fill in every value
.venv/bin/python main.py
```

Open http://localhost:8000/health. It should return `{"status":"ok"}`.

## One-time service setup

1. **Supabase DB:** open the SQL editor and run `schema.sql`. For `DATABASE_URL`, use
   **Connect → Transaction pooler** (port 6543) and put your DB password into it.
2. **Supabase Storage:** the bucket named in `SUPABASE_BUCKET` must be **public**.
3. **Google OAuth** (Cloud Console → Credentials → your OAuth client): add these
   *Authorized redirect URIs* exactly as written:
   - `http://localhost:8000/auth/callback`
   - `https://<your-app>.onrender.com/auth/callback`

   While the consent screen is in *Testing*, add every tester's email as a test user.
4. **Paystack:** in Settings → API Keys & Webhooks, set the webhook URL to
   `https://<your-app>.onrender.com/payments/webhook`.
5. **Brevo:** verify the `MAIL_FROM_EMAIL` sender, then create an API key.

## Deploy (Render)

1. Push this repo to GitHub.
2. In Render, go to **New → Blueprint** and pick the repo. It reads `render.yaml`.
3. Fill in the env vars it prompts for. Set `BASE_URL` to the `https://….onrender.com`
   URL with no trailing slash.
4. Once it's live, check that `https://<your-app>.onrender.com/health` returns ok.

## Caveats

- Render's free tier sleeps after about 15 minutes idle, so the first request then takes 30–60 s. Use a paid instance for real customers.
- Supabase free projects pause when inactive.
- See SPEC.md §13 for the hardening still needed before real customers use the site.
