# Shopping Website: Build Spec (MVP)

> **For Claude Code:** This is the source of truth for the project. Build in the milestone order in §11 and stop after each milestone so it can be tested. Do not add features that aren't listed. Do not change the architecture decisions in §3 without asking. When something here is ambiguous, pick the simplest option and leave a `# TODO(spec):` comment.

---

## 1. Context and constraints

- **Deadline:** delivered by tomorrow. The developer has **under 4 hours** of working time, because they have a day job.
- **Audience:** an internship assignment, but the site is **likely to be used by a real business**. Reliability matters where money and data are involved.
- **Honest scope statement:** this delivers a **working, deployed MVP** built on sound patterns: server-verified payments, cloud file storage, and secrets in env vars. It is **not** yet battle-tested production software. See §13 for the hardening still required before real customers use it.

### Priorities, in order
1. Payments are correct: never mark an order paid without server-side verification.
2. Nothing depends on the server's local disk.
3. The full purchase flow works end to end on a phone.
4. The admin can manage products and orders.
5. Polish.

---

## 2. Features (scope)

### Customer
- Sign in / sign out with **Google**. **Every page requires sign-in**: anonymous visitors are sent to `/login` first, then returned to the page they asked for. The first sign-in creates the account (registration).
- Public pages that need no sign-in: a landing page at `/` describing the shop and how Google sign-in data is used, a Privacy Policy (`/privacy`) and Terms of Service (`/terms`). Google's OAuth verification requires these.
- Browse active products (list and detail) at `/shop`
- Session-based cart: add, update quantity, remove
- Checkout that redirects to **Paystack (test mode)**
- Order success page
- "My orders" page
- Emails: welcome email on first sign-in, order confirmation on successful payment

### Admin (minimal)
- **Products CRUD:** list, create, edit, delete or deactivate, with image upload
- **Orders:** list (filterable by status), detail view with line items, status update
- Admin access is controlled by an email allowlist in env

### Non-functional
- **Responsive:** must work well at phone width (360px and up)
- **Host-agnostic:** must run unchanged on Render, Railway, Fly.io, or any container/VM host
- `/health` endpoint for uptime monitoring

---

## 3. Architecture decisions (fixed)

| Concern | Decision | Why |
|---|---|---|
| App shape | **Monolith**: FastAPI + Jinja2 server-rendered templates, **one deployment** | A single deploy means frontend and backend can't be down separately. No CORS and no API contract to maintain. Fastest to build. |
| Hosting | **Render** web service (Railway or Fly.io also fine) | Long-running Python process. **Do NOT target Vercel or Netlify**: they are serverless/static-oriented, with read-only or ephemeral filesystems. |
| Database | **Supabase Postgres** via SQLAlchemy 2.x + psycopg 3 | Managed Postgres. Use the **pooler** connection string. |
| File uploads | **Supabase Storage**, public bucket `ShoppingWebsiteBucket` | The code never writes to local disk, so uploads work on any host. |
| Auth | **Google OAuth** (may require some google API library, do whatever the modern docs suggest) | No password handling. Fewest moving parts in a server-rendered app. |
| Admin authz | `ADMIN_EMAILS` env allowlist (comma-separated) | No roles table needed for the MVP. |
| Payments | **Paystack test mode** | Natural fit for a Nigerian business. |
| Email | **Brevo** HTTP API via `httpx` | Sent in `BackgroundTasks` so email never blocks a request. |
| UI | **Bootstrap 5 via CDN** + small custom CSS | Responsive by default with no build step. |
| Money | Integer **kobo** everywhere (`price_kobo`, `total_kobo`) | Avoids float errors. Format as ₦ only in templates. |

---

## 4. Tech stack and dependencies

```
fastapi
uvicorn[standard]
jinja2
python-multipart        # form + file uploads
itsdangerous            # required by SessionMiddleware
authlib
httpx
sqlalchemy>=2
psycopg[binary]>=3
python-dotenv
```

Python 3.11+. No frontend build tooling.

---

## 5. Project structure

```
app/
  main.py              # app factory, middleware, router includes, /health
  config.py            # Settings loaded from env (fail fast if required vars missing)
  db.py                # engine, SessionLocal, get_db dependency
  models.py            # SQLAlchemy models
  auth.py              # Authlib OAuth setup, current_user / require_user / require_admin deps
  services/
    storage.py         # upload_image(file) -> public_url ; delete_image(url)
    payments.py        # init_transaction, verify_transaction, verify_webhook_signature
    mail.py            # send_email(to, subject, html)
    orders.py          # mark_order_paid(order, db) — idempotent, single source of truth
    cart.py            # session cart helpers
  routes/
    shop.py            # /, /products/{id}, /cart, /checkout, /orders
    auth_routes.py     # /login, /auth/callback, /logout
    payments_routes.py # /payments/callback, /payments/webhook
    admin.py           # /admin/...
  templates/
    base.html          # Bootstrap CDN, responsive navbar, flash messages
    shop/ ...  admin/ ...  emails/ ...
  static/
    styles.css
  tests/
    ...test files
schema.sql             # run once in Supabase SQL editor
.env.example
requirements.txt
render.yaml            # optional
README.md              # setup + deploy steps
```

---

## 6. Environment variables (`.env.example`)
Note: also extend it with whatever else you deem necessary

```
BASE_URL=http://localhost:8000          # production: https://yourapp.onrender.com
SESSION_SECRET=change-me-long-random
ENV=development                          # production enables https_only cookies

DATABASE_URL=postgresql+psycopg://...    # Supabase POOLER connection string

SUPABASE_URL=https://xxxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...            # server-side only, never sent to templates
SUPABASE_BUCKET=product-images

GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...

ADMIN_EMAILS=admin1@example.com,admin2@example.com

PAYSTACK_SECRET_KEY=sk_test_...
PAYSTACK_PUBLIC_KEY=pk_test_...

BREVO_API_KEY=xkeysib-...
MAIL_FROM_EMAIL=you@example.com
MAIL_FROM_NAME=Cornershop                # optional
SUPPORT_EMAIL=                           # optional; contact address on policy pages (defaults to MAIL_FROM_EMAIL)
GOOGLE_SITE_VERIFICATION=                # optional; Search Console HTML-tag token for domain verification
```

`config.py` must raise a clear error at startup if any required variable is missing.

---

## 7. Data model (`schema.sql`)

```sql
create table users (
  id          bigserial primary key,
  email       text unique not null,
  name        text,
  avatar_url  text,
  created_at  timestamptz not null default now()
);

create table products (
  id           bigserial primary key,
  name         text not null,
  description  text not null default '',
  price_kobo   integer not null check (price_kobo >= 0),
  stock        integer not null default 0 check (stock >= 0),
  image_url    text,
  is_active    boolean not null default true,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

create table orders (
  id                  bigserial primary key,
  user_id             bigint not null references users(id),
  total_kobo          integer not null check (total_kobo >= 0),
  status              text not null default 'pending'
                      check (status in ('pending','paid','shipped','delivered','cancelled')),
  paystack_reference  text unique not null,
  shipping_name       text not null,
  shipping_phone      text not null,
  shipping_address    text not null,
  paid_at             timestamptz,
  created_at          timestamptz not null default now()
);

create table order_items (
  id               bigserial primary key,
  order_id         bigint not null references orders(id) on delete cascade,
  product_id       bigint not null references products(id),
  product_name     text not null,          -- snapshot at purchase time
  quantity         integer not null check (quantity > 0),
  unit_price_kobo  integer not null check (unit_price_kobo >= 0)
);

create index on orders (user_id);
create index on orders (status);
create index on order_items (order_id);
```

- Products referenced by orders must not be hard-deleted. Admin "delete" sets `is_active = false`. Hard delete is allowed only if no order_items reference the product.
- Order items snapshot the name and price so that later product edits don't rewrite history.
- The tables are accessed only by the server via `DATABASE_URL`. Do not expose the Supabase anon key to the browser.

---

## 8. Routes

### Customer (login required)
Every route requires a signed-in user except `/`, `/privacy`, `/terms`, `/login`, `/auth/google`, `/auth/callback`, `/health`, `/payments/webhook` and `/static/*`. A global middleware enforces this, so new routes are protected by default.

| Method | Path | Notes |
|---|---|---|
| GET | `/` | **Public.** Landing page: what the shop is, how it works, how Google sign-in data is used |
| GET | `/privacy`, `/terms` | **Public.** Privacy Policy and Terms of Service |
| GET | `/shop` | Grid of active products (responsive cards), welcome banner, placeholders when empty. Default page after sign-in |
| GET | `/products/{id}` | Detail page with quantity selector and "Add to cart" |
| GET | `/cart` | Cart view with prices **looked up from DB** |
| POST | `/cart/add` | `product_id`, `qty`; capped at stock |
| POST | `/cart/update` | Set quantity; 0 removes the item |
| GET | `/checkout` | **Login required.** Shipping form plus order summary |
| POST | `/checkout` | Creates a pending order, then redirects to Paystack (see §9.3) |
| GET | `/payments/callback` | Paystack redirect target; verifies, then shows success or failure |
| POST | `/payments/webhook` | Paystack webhook; signature-verified; CSRF-exempt |
| GET | `/orders` | **Login required.** Current user's orders |
| GET | `/orders/{id}` | **Login required.** Owner only; returns 404 otherwise |
| GET | `/health` | Returns `{"status":"ok"}` after a `select 1` on the DB |

### Auth
| Method | Path | Notes |
|---|---|---|
| GET | `/login` | Sign-in page with a "Sign in with Google" button. Keeps `next` in the session |
| GET | `/auth/google` | Redirects to Google |
| GET | `/auth/callback` | Upserts user; sends welcome email on first creation; redirects to `next` |
| POST | `/logout` | Clears the signed-in user; redirects to `/login` |

### Admin (all require `require_admin`; non-admins get 403)
| Method | Path | Notes |
|---|---|---|
| GET | `/admin` | Redirects to `/admin/products` |
| GET | `/admin/products` | Table with all products, including inactive |
| GET/POST | `/admin/products/new` | Create, with optional image |
| GET/POST | `/admin/products/{id}/edit` | Edit; new image replaces the old one |
| POST | `/admin/products/{id}/delete` | Soft delete (see §7) |
| GET | `/admin/orders?status=` | Newest first, status filter |
| GET | `/admin/orders/{id}` | Customer, shipping details, items, totals |
| POST | `/admin/orders/{id}/status` | Allowed transitions only: paid→shipped→delivered; pending→cancelled; paid→cancelled |

---

## 9. Key flows (implement exactly)

### 9.1 Google sign-in
1. Follow current directives for OAuth on google docs
2. Respond to user based on returned response of auth
3. On callback, read `userinfo`. Reject the sign-in if `email_verified` is false. Upsert by email.
4. If the user was newly created, queue the welcome email.
5. Store `user_id`, `email`, `name` and `avatar_url` in the session. `is_admin` is computed from `ADMIN_EMAILS` on every request, so removing an email takes effect immediately.
6. Session cookie settings: `https_only=True` when `ENV=production`, `same_site="lax"`.

### 9.2 Image upload (host-agnostic)
1. The admin form posts `multipart/form-data` with `image: UploadFile`.
2. Validate the content type (images and videos only) and a size of at most 5 MB. Reject anything else with a form error.
3. Upload the bytes to Supabase Storage with `httpx`:
   `POST {SUPABASE_URL}/storage/v1/object/{BUCKET}/{uuid4}.{ext}`
   Headers: `Authorization: Bearer {SERVICE_ROLE_KEY}`, `Content-Type: <mime>`.
4. Store the public URL in `products.image_url`:
   `{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{filename}`
5. On edit with a new image, upload the new image first, update the DB, then attempt to delete the old object on a best-effort basis (log any failure, never raise).
6. **Never** write uploaded files to the local filesystem, not even temporarily.

### 9.3 Checkout and payment (the critical path)
1. **POST /checkout:** load the cart from the session. **Re-fetch every product from the DB.** Ignore any price from the client. Reject inactive products and quantities above stock, then redirect back to the cart with a message.
2. Create an `orders` row with status `pending`, `paystack_reference = "ord_" + uuid4().hex`, the computed `total_kobo`, and the shipping fields. Create the `order_items` rows. Commit.
3. Call Paystack `POST https://api.paystack.co/transaction/initialize` with `email`, `amount` (kobo), `reference`, and `callback_url = {BASE_URL}/payments/callback`. Use header `Authorization: Bearer {PAYSTACK_SECRET_KEY}`.
4. Redirect the user (303) to `data.authorization_url`. Do **not** clear the cart yet.
5. **GET /payments/callback?reference=...:** call `GET https://api.paystack.co/transaction/verify/{reference}`. If `data.status == "success"` **and** `data.amount == order.total_kobo` **and** `data.currency == "NGN"`, call `mark_order_paid`. Clear the cart and show the success page. Otherwise show a "payment not completed" page with a retry link. Do not mark anything as paid in that case.
6. **POST /payments/webhook:** read the **raw body**. Compute HMAC-SHA512 of it with `PAYSTACK_SECRET_KEY` and compare it to the `x-paystack-signature` header with `hmac.compare_digest`. Return 401 on mismatch. For event `charge.success`, look up the order by reference, re-run the same amount check, and call `mark_order_paid`. Always return 200 quickly for valid signatures.
7. **`mark_order_paid(order)` must be idempotent.** It is called by both the callback and the webhook, possibly at the same time:
   - In a transaction: `UPDATE orders SET status='paid', paid_at=now() WHERE id=:id AND status='pending' RETURNING id`.
   - If no row is returned, the order was already processed, so return without doing anything else.
   - Otherwise, decrement stock for each item: `UPDATE products SET stock = stock - :q WHERE id = :pid AND stock >= :q`. If any decrement affects 0 rows (oversold), **still keep the order paid** (the money was taken), log a warning, and add an admin-visible flag. The simplest version is an `[OVERSOLD]` note shown in the admin order view. **Never** fail the payment record.
   - Queue the order confirmation email.

### 9.4 Email
- `send_email(to, subject, html)` posts JSON to `https://api.brevo.com/v3/smtp/email` with header `api-key: {BREVO_API_KEY}` and sender `{MAIL_FROM_NAME} <{MAIL_FROM_EMAIL}>`. Use a 10 s timeout.
- Always invoke it through FastAPI `BackgroundTasks`. On failure, **log and swallow** the error. Email problems must never break sign-in or checkout.
- Templates go in `templates/emails/`: `welcome.html` and `order_confirmation.html` (order number, items, total, shipping address).

---

## 10. UI / responsiveness requirements

- Bootstrap 5.3 via CDN, with `<meta name="viewport" content="width=device-width, initial-scale=1">`.
- Navbar collapses to a hamburger menu on mobile. It shows a cart count badge, sign-in/avatar, and an Admin link for admins.
- Product grid: `row-cols-1 row-cols-sm-2 row-cols-md-3 row-cols-lg-4`. Images use a fixed aspect ratio (`object-fit: cover`) with a placeholder when `image_url` is null.
- Admin tables are wrapped in `.table-responsive`.
- Flash messages are stored in the session and rendered in `base.html`.
- All prices are displayed as `₦{kobo/100:,.2f}` via a single Jinja filter.
- Must be checked at 360px, 768px and desktop widths.

---

## 11. Build order (milestones)

Stop after each milestone and confirm it runs. The time boxes assume under 4 hours total.

| # | Milestone | Time box | Done when |
|---|---|---|---|
| M0 | Skeleton: config, db, `base.html`, `/health`, `schema.sql`, README. **Deploy to Render immediately.** | 25 min | `/health` is green on the live URL |
| M1 | Products: public list and detail; admin CRUD with Supabase Storage upload | 40 min | Admin creates a product with an image on the **deployed** site and it shows on `/` |
| M2 | Google auth, user upsert, admin guard, welcome email | 25 min | Sign-in works on the live URL; a non-admin gets 403 on `/admin` |
| M3 | Cart, checkout, Paystack init, callback verify, webhook, `mark_order_paid`, confirmation email | 45 min | A test card payment marks the order paid exactly once, stock decrements, and the email arrives |
| M4 | Admin orders (list, detail, status) and customer "My orders" | 25 min | Admin can move an order paid→shipped |
| M5 | Responsive polish, error pages (404/500), end-to-end test on a real phone | 30 min | Full purchase completed on a phone against the live site |
| — | Buffer | 20 min | — |

### Cut list (if behind schedule, drop in this order)
1. Customer "My orders" pages
2. Welcome email (keep the order confirmation)
3. Admin order status editing (keep the orders list and detail)
4. Hard delete (soft delete only)

### Never cut
- Server-side payment verification with the amount check
- Idempotent `mark_order_paid`
- Supabase Storage for images (no local disk)
- Server-side price recomputation at checkout

---

## 12. Known caveats and gotchas

- **Render free tier sleeps** after about 15 minutes idle, so the first request after that takes around 30–60 s. This is acceptable for the demo, **not** for a real business; use a paid instance.
- **Brevo only sends from a verified sender.** `MAIL_FROM_EMAIL` must be verified under Senders, Domains & Dedicated IPs, or every send is rejected (logged, never shown to users). If "Authorized IPs" is enabled under Security, requests from Render's changing IPs are blocked, so turn it off or mail fails silently. The free plan has a daily send limit.
- **Google OAuth redirect URIs** must match exactly, including the scheme and any trailing slash. Register both `http://localhost:8000/auth/callback` and `https://<prod-domain>/auth/callback`. While the consent screen is in "Testing" mode, only listed test users can sign in. Add test users or publish the app.
- **Paystack webhook URL** is set in the Paystack dashboard (Settings → API Keys & Webhooks) and must be the public HTTPS URL. Locally, rely on the callback verification; webhooks need the deployed URL or a tunnel.
- **Supabase pooler:** use the pooler connection string in `DATABASE_URL`. If using the transaction-mode pooler, disable psycopg prepared statements (`prepare_threshold=None` in connect args).
- **Supabase free projects pause** after a period of inactivity. Note this in the handoff.
- **Cart in a signed cookie:** fine for a few dozen items. Cookie contents are visible to the user (signed, not encrypted), so store only IDs and quantities and never prices.
- **CSRF:** `same_site="lax"` cookies plus POST-only mutations give baseline protection for the MVP. Proper CSRF tokens on admin forms are listed under hardening.
- **Oversell race:** two people can pay for the last unit at the same time. The order is kept as paid and flagged for the admin (§9.3). Full stock reservation is out of scope.

---

## 13. Out of scope (MVP) → post-deadline hardening

Not built now. Listed so the handoff is honest:

- Paid hosting (no cold starts) plus an uptime monitor (e.g. UptimeRobot on `/health`)
- Custom Brevo sending domain with SPF/DKIM/DMARC verified (instead of a single verified sender address)
- Paystack **live** keys and a business verification review
- CSRF tokens on all forms; rate limiting on auth and checkout
- Automated tests (at minimum: `mark_order_paid` idempotency, webhook signature, price recomputation)
- Supabase backups / point-in-time recovery
- Structured logging and error tracking (e.g. Sentry)
- Product categories, search, pagination, multiple images, discounts, shipping fees, refunds, inventory reservation

---

## 14. Acceptance checklist (demo-ready)

- [ ] Live URL loads on a phone; layout is usable at 360px
- [ ] `/health` returns ok
- [ ] Google sign-in works on the live URL
- [ ] Non-admin gets 403 on `/admin`
- [ ] An anonymous visitor to any page is sent to sign-in; a first sign-in sends the welcome email
- [ ] Admin creates, edits and deactivates a product with an image; the image survives a redeploy
- [ ] Customer adds to cart, checks out, pays with a Paystack test card, and lands on success
- [ ] Order shows `paid` once; stock decremented once (even if callback and webhook both fire)
- [ ] Tampering with the amount or reference does not mark an order paid
- [ ] Order confirmation email received (authorized sandbox recipient)
- [ ] Admin sees the order and updates its status
- [ ] No secrets in the repo; `.env.example` documents every variable
