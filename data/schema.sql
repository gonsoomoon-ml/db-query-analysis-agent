-- 샘플 e-commerce 스키마 (TABLE_META 와 동일 구조). 향후 sample.db / EXPLAIN 용.
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL,
  name TEXT,
  created_at TEXT
);
CREATE UNIQUE INDEX uq_users_email ON users(email);

CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  status TEXT,
  total REAL,
  created_at TEXT
);
CREATE INDEX idx_orders_user_id ON orders(user_id);
CREATE INDEX idx_orders_created_at ON orders(created_at);

CREATE TABLE products (
  id INTEGER PRIMARY KEY,
  sku TEXT NOT NULL,
  name TEXT,
  price REAL,
  category TEXT
);
CREATE UNIQUE INDEX uq_products_sku ON products(sku);
CREATE INDEX idx_products_category ON products(category);

CREATE TABLE order_items (
  id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  qty INTEGER
);
CREATE INDEX idx_order_items_order_id ON order_items(order_id);
CREATE INDEX idx_order_items_product_id ON order_items(product_id);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY,
  actor TEXT,
  action TEXT,
  payload TEXT,
  created_at TEXT
);
