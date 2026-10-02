# Cornershop

Server-rendered FastAPI + Jinja2 shop. Supabase Postgres + Storage, Google sign-in,
Paystack (test mode), Brevo email. See `SPEC.md` for the full spec.

Every page requires Google sign-in except the landing page (`/`), the Privacy Policy
(`/privacy`) and the Terms of Service (`/terms`). A person's first sign-in creates their
account and sends them a welcome email. People whose email is in `ADMIN_EMAILS` also see
the Admin pages, where they add products with an image or video.

## Local setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # fill in every value
.venv/bin/python -m app.main
```

Open http://localhost:8000/health. It should return `{"status":"ok"}`. Then open
http://localhost:8000/ for the landing page, and sign in to reach the shop.

## Tests

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q app/tests
```

The tests use dummy settings and never call Google, Paystack, Brevo or Supabase, so they
need no `.env` and no network. Tests that need Postgres (orders, checkout, payments) are
skipped unless you point `TEST_DATABASE_URL` at an **empty, disposable** database; they
drop and recreate the tables in it:

```bash
TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/shop_test .venv/bin/python -m pytest -q app/tests
```

## One-time service setup

1. **Supabase DB:** open the SQL editor and run `schema.sql`. For `DATABASE_URL`, use
   **Connect → Transaction pooler** (port 6543) and put your DB password into it.
2. **Supabase Storage:** the bucket named in `SUPABASE_BUCKET` must be **public**.
3. **Google OAuth** (Google Cloud Console → *Google Auth Platform*):
   1. **Branding:** set the app name and support email. **Audience:** choose *External*.
   2. **Data access:** the default `openid`, `email` and `profile` scopes are all the app needs.
   3. **Clients → Create client → Web application.** Add these *Authorized redirect URIs*
      exactly as written:
      - `http://localhost:8000/auth/callback`
      - `https://<your-app>.onrender.com/auth/callback`
   4. Copy the client ID and secret into `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

   While the app's publishing status is *Testing*, only the test users you list under
   **Audience** can sign in (up to 100). Add yourself and every tester, or click
   **Publish app** so anyone with a Google account can sign in.
4. **Paystack:** use your **test** keys (`sk_test_…`, `pk_test_…`). In Settings → API Keys & Webhooks, set the webhook URL to
   `https://<your-app>.onrender.com/payments/webhook`.
5. **Brevo:** under *Senders, Domains & Dedicated IPs*, add and verify the address you'll use
   as `MAIL_FROM_EMAIL`. Then create an API key under *SMTP & API → API keys* and put it in
   `BREVO_API_KEY`. If *Security → Authorized IPs* is on, turn it off; otherwise Brevo
   blocks requests from Render's changing IPs.
6. **Admins:** put the Google email of every admin in `ADMIN_EMAILS`, comma-separated.

## Google OAuth verification

In *Google Auth Platform → Branding*, fill in:

- **Application home page:** `https://<your-app>.onrender.com/`
- **Privacy policy link:** `https://<your-app>.onrender.com/privacy`
- **Terms of service link:** `https://<your-app>.onrender.com/terms`
- **Authorized domains:** your app's domain (e.g. `<your-app>.onrender.com`, or your own domain)

All three pages are public. Before submitting, read `/privacy` and `/terms` and adjust them to
match how the business really operates (especially returns and refunds). They are in
`app/templates/pages/`. Set `SUPPORT_EMAIL` to the address customers should write to.

If Google asks you to prove you own the domain, add it in
[Search Console](https://search.google.com/search-console) as a *URL prefix* property, choose
the **HTML tag** method, copy the `content="…"` value into `GOOGLE_SITE_VERIFICATION`,
redeploy, then click *Verify*.

The app only asks for the `openid`, `email` and `profile` scopes, which are non-sensitive, so
review is usually light.

## Using the admin dashboard

1. Put your Google email in `ADMIN_EMAILS` (on Render: your service → *Environment*), comma-separated
   for several admins, and save. Render restarts the app.
2. Sign in and click the yellow **Admin dashboard** button in the navbar (or go to `/admin`).
   Everyone signed in sees the button; people not in `ADMIN_EMAILS` get a page explaining how to get access.
3. **Overview** shows orders to ship, money received, low stock and orders needing attention.
   **Products** is where you add products with images. **Orders** is where you ship, deliver or cancel
   orders; each change emails the customer.

## Trying a test payment

With Paystack test keys no real money moves. At checkout, on Paystack's page, use Paystack's
test card: `4084 0840 8408 4081`, any future expiry, CVV `408` (if asked, PIN `0000` and OTP `123456`).
Paystack lists more test cards, including ones that fail, at
<https://paystack.com/docs/payments/test-payments/>.

After paying you land on the success page, the order shows as *Paid* under **My orders** and in the
admin dashboard, stock goes down, and a confirmation email is sent. The webhook needs the deployed
HTTPS URL; locally, the redirect back from Paystack confirms the payment on its own.

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
