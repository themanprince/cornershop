-- Run once in the Supabase SQL editor.

create table if not exists users (
  id          bigserial primary key,
  email       text unique not null,
  name        text,
  avatar_url  text,
  created_at  timestamptz not null default now()
);

create table if not exists products (
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

create table if not exists orders (
  id                  bigserial primary key,
  user_id             bigint not null references users(id),
  total_kobo          integer not null check (total_kobo >= 0),
  status              text not null default 'pending'
                      check (status in ('pending','paid','shipped','delivered','cancelled')),
  paystack_reference  text unique not null,
  shipping_name       text not null,
  shipping_phone      text not null,
  shipping_address    text not null,
  admin_note          text,                -- e.g. [OVERSOLD] flag (spec §9.3)
  paid_at             timestamptz,
  created_at          timestamptz not null default now()
);

create table if not exists order_items (
  id               bigserial primary key,
  order_id         bigint not null references orders(id) on delete cascade,
  product_id       bigint not null references products(id),
  product_name     text not null,          -- snapshot at purchase time
  quantity         integer not null check (quantity > 0),
  unit_price_kobo  integer not null check (unit_price_kobo >= 0)
);

create index if not exists orders_user_id_idx on orders (user_id);
create index if not exists orders_status_idx on orders (status);
create index if not exists order_items_order_id_idx on order_items (order_id);

-- Tables are only accessed by the server via DATABASE_URL. Enable RLS with no
-- policies so the public anon key can't read or write them via the Supabase API.
alter table users enable row level security;
alter table products enable row level security;
alter table orders enable row level security;
alter table order_items enable row level security;
