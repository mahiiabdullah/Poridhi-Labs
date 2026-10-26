# Layered Architecture Refactor

**Module 7 — Enterprise Backend Architecture & Microservices Foundations**

A flat Express app mixes routing, business rules, and SQL in one file. That is quick to write and hard to change. This lab restructures a small shop API into controllers, services, and repositories, with each layer receiving its dependencies through its constructor. The API behaves exactly as before, and the business rules can be unit-tested without a database.

## Architecture

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-1/images/architecture.svg" alt="Architecture"></p>

## Concept

| Term | Description |
|------|-------------|
| Controller | Translates HTTP requests into service calls and service results into HTTP responses. Holds no business rules. |
| Service | Holds business rules such as stock checks and order totals. Knows nothing about HTTP or SQL. |
| Repository | Holds the SQL for one table. Receives the database handle as a parameter. |
| Dependency injection | Each layer receives its collaborators through its factory function instead of importing them. |
| Composition root | `src/container.js` is the only file that builds every layer and wires them together. |
| Test double | A plain object that stands in for a repository in a unit test. |

Dependencies point downward only: controller, then service, then repository, then PostgreSQL. No layer imports from a layer above it.

## What You Will Build

A shop API with users, products, and orders on Node.js, Express, and PostgreSQL. You first write the flat version and record its output with a smoke test. You then split it into controller, service, and repository layers wired together in `container.js`. The same smoke test must return the same status codes before and after the refactor.

## Prerequisites

- Docker and Docker Compose v2 on the lab VM. Node.js runs only inside the containers, so the host needs no Node.js.
- Port `3100` free on the VM.

## Step 1: Create the project directory

```bash
mkdir ~/layered-shop && cd ~/layered-shop
mkdir -p src db test docs
```

## Step 2: Confirm Docker and Docker Compose are installed

```bash
docker --version
docker compose version
```

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-1/images/Step%202%20Confirm%20Docker%20and%20Docker%20Compose%20are%20installed.png" alt="Docker and Docker Compose versions"></p>

## Step 3: Create the package file and Dockerfile

Run this command to create `package.json`:

```bash
cat > package.json << 'EOF'
{
  "name": "layered-shop",
  "version": "1.0.0",
  "private": true,
  "scripts": {
    "start": "node src/server.js",
    "test": "node --test test/orderService.test.js"
  },
  "dependencies": {
    "express": "4.19.2",
    "pg": "8.12.0"
  }
}

EOF
```

Run this command to create `Dockerfile`:

```bash
cat > Dockerfile << 'EOF'
FROM node:20-alpine

WORKDIR /app

COPY package.json .
RUN npm install --omit=dev

EOF
```

The image holds only the dependencies. Compose mounts the source code from your project folder, so a code change needs a container restart and no image rebuild.

## Step 4: Create the database schema and seed data

Run this command to create `db/init.sql`:

```bash
cat > db/init.sql << 'EOF'
CREATE TABLE users (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE products (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  price NUMERIC(10,2) NOT NULL CHECK (price > 0),
  stock INTEGER NOT NULL CHECK (stock >= 0)
);

CREATE TABLE orders (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  product_id INTEGER NOT NULL REFERENCES products(id),
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  total NUMERIC(12,2) NOT NULL,
  status TEXT NOT NULL DEFAULT 'CONFIRMED',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO users (name, email) VALUES ('Ada Lovelace', 'ada@example.com');
INSERT INTO products (name, price, stock) VALUES ('Keyboard', 49.50, 100), ('Mouse', 19.99, 50);

EOF
```

## Step 5: Create the docker-compose file

Run this command to create `docker-compose.yml`:

```bash
cat > docker-compose.yml << 'EOF'
services:
  db:
    image: postgres:16-alpine
    container_name: shop-db
    environment:
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: shop
    volumes:
      - ./db/init.sql:/docker-entrypoint-initdb.d/init.sql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U postgres -d shop"]
      interval: 5s
      timeout: 3s
      retries: 10

  app:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: shop-app
    command: node src/server.js
    ports:
      - "3100:3100"
    volumes:
      - ./src:/app/src
      - ./test:/app/test
    working_dir: /app
    environment:
      PORT: "3100"
      DATABASE_URL: postgres://postgres:postgres@db:5432/shop
    depends_on:
      db:
        condition: service_healthy

EOF
```

The health check connects over TCP (`-h 127.0.0.1`). During first start PostgreSQL runs `init.sql` on a temporary socket-only server, and a TCP check passes only after the real server is up.

## Step 6: Write the flat monolith

Run this command to create `src/server.js`. Routing, validation, business rules, and SQL all live in this one file.

```bash
cat > src/server.js << 'EOF'
// src/server.js (monolith: routing, business rules, and SQL in one file)
const express = require("express");
const { Pool, types } = require("pg");

types.setTypeParser(1700, parseFloat);

const pool = new Pool({ connectionString: process.env.DATABASE_URL });
const app = express();
app.use(express.json());

app.get("/health", (req, res) => res.json({ status: "ok" }));

// users
app.post("/users", async (req, res) => {
  const { name, email } = req.body;
  if (!name || !email) return res.status(400).json({ error: "name and email are required" });
  const dup = await pool.query("SELECT 1 FROM users WHERE email = $1", [email]);
  if (dup.rowCount > 0) return res.status(409).json({ error: "email already registered" });
  const { rows } = await pool.query(
    "INSERT INTO users (name, email) VALUES ($1, $2) RETURNING *", [name, email]);
  res.status(201).json(rows[0]);
});

app.get("/users", async (req, res) => {
  const { rows } = await pool.query("SELECT * FROM users ORDER BY id");
  res.json(rows);
});

app.get("/users/:id", async (req, res) => {
  const { rows } = await pool.query("SELECT * FROM users WHERE id = $1", [req.params.id]);
  if (rows.length === 0) return res.status(404).json({ error: "user not found" });
  res.json(rows[0]);
});

// products
app.post("/products", async (req, res) => {
  const { name, price, stock = 0 } = req.body;
  if (!name || !(price > 0) || !Number.isInteger(stock) || stock < 0) {
    return res.status(400).json({ error: "name, a positive price, and a non-negative integer stock are required" });
  }
  const { rows } = await pool.query(
    "INSERT INTO products (name, price, stock) VALUES ($1, $2, $3) RETURNING *", [name, price, stock]);
  res.status(201).json(rows[0]);
});

app.get("/products", async (req, res) => {
  const { rows } = await pool.query("SELECT * FROM products ORDER BY id");
  res.json(rows);
});

app.get("/products/:id", async (req, res) => {
  const { rows } = await pool.query("SELECT * FROM products WHERE id = $1", [req.params.id]);
  if (rows.length === 0) return res.status(404).json({ error: "product not found" });
  res.json(rows[0]);
});

// orders
app.post("/orders", async (req, res) => {
  const { userId, productId, quantity } = req.body;
  if (!userId || !productId || !Number.isInteger(quantity) || quantity < 1) {
    return res.status(400).json({ error: "userId, productId, and a positive integer quantity are required" });
  }
  const user = await pool.query("SELECT id FROM users WHERE id = $1", [userId]);
  if (user.rowCount === 0) return res.status(404).json({ error: "user not found" });
  const product = await pool.query("SELECT * FROM products WHERE id = $1", [productId]);
  if (product.rowCount === 0) return res.status(404).json({ error: "product not found" });
  const stock = await pool.query(
    "UPDATE products SET stock = stock - $2 WHERE id = $1 AND stock >= $2 RETURNING id", [productId, quantity]);
  if (stock.rowCount === 0) return res.status(409).json({ error: "insufficient stock" });
  const total = product.rows[0].price * quantity;
  const { rows } = await pool.query(
    "INSERT INTO orders (user_id, product_id, quantity, total) VALUES ($1, $2, $3, $4) RETURNING *",
    [userId, productId, quantity, total]);
  res.status(201).json(rows[0]);
});

app.get("/orders", async (req, res) => {
  const { rows } = await pool.query("SELECT * FROM orders ORDER BY id");
  res.json(rows);
});

app.listen(3100, () => console.log("monolith listening on 3100"));

EOF
```

## Step 7: Create the smoke test

Run this command to create `smoke.sh`. The same script runs before and after the refactor.

```bash
cat > smoke.sh << 'EOF'
#!/usr/bin/env bash
BASE=${1:-http://localhost:3100}
JSON="Content-Type: application/json"

call() {
  local label=$1; shift
  echo "--- $label"
  curl -s -w "\nHTTP %{http_code}\n" "$@"
  echo
}

call "health"                  "$BASE/health"
call "create user"             -X POST "$BASE/users" -H "$JSON" -d "{\"name\":\"Grace\",\"email\":\"grace$(date +%s)@example.com\"}"
call "create product"          -X POST "$BASE/products" -H "$JSON" -d '{"name":"Monitor","price":199,"stock":10}'
call "list products"           "$BASE/products"
call "create order (qty 2)"    -X POST "$BASE/orders" -H "$JSON" -d '{"userId":1,"productId":1,"quantity":2}'
call "order more than stock"   -X POST "$BASE/orders" -H "$JSON" -d '{"userId":1,"productId":1,"quantity":9999}'
call "order for unknown user"  -X POST "$BASE/orders" -H "$JSON" -d '{"userId":9999,"productId":1,"quantity":1}'
call "user without an email"   -X POST "$BASE/users" -H "$JSON" -d '{"name":"No Email"}'
call "list orders"             "$BASE/orders"

EOF
chmod +x smoke.sh
```

## Step 8: Start the monolith

```bash
docker compose up -d --build
```

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-1/images/Step%208%20Start%20the%20monolith_1.png" alt="docker compose up"></p>

```bash
docker compose ps
```

`shop-db` shows `healthy` and `shop-app` shows `running`.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-1/images/Step%208%20Start%20the%20monolith_2.png" alt="docker compose ps"></p>

## Step 9: Record the baseline behavior

```bash
./smoke.sh
```

Note the status codes in order: `200`, `201`, `201`, `200`, `201`, `409`, `404`, `400`, `200`. The refactor must reproduce them.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-1/images/Step%209%20Record%20the%20baseline%20behavior.png" alt="Smoke test baseline output"></p>

## Step 10: Move the monolith aside

```bash
mkdir -p legacy
mv src/server.js legacy/monolith-server.js
mkdir -p src/repositories src/services src/controllers src/lib
```

## Step 11: Create configuration, database, and shared helpers

Configuration comes only from environment variables, so the same image runs anywhere.

Run this command to create `src/config.js`, `src/db.js`, and the three helpers under `src/lib/`:

```bash
cat > src/config.js << 'EOF'
// src/config.js: configuration comes from the environment (12-factor)
function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

module.exports = {
  port: Number(process.env.PORT || 3100),
  databaseUrl: required("DATABASE_URL"),
};

EOF

cat > src/db.js << 'EOF'
// src/db.js
const { Pool, types } = require("pg");

types.setTypeParser(1700, parseFloat); // NUMERIC columns arrive as JS numbers

function createPool(connectionString) {
  return new Pool({ connectionString });
}

module.exports = { createPool };

EOF

cat > src/lib/errors.js << 'EOF'
// src/lib/errors.js
class AppError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

module.exports = { AppError };

EOF

cat > src/lib/asyncHandler.js << 'EOF'
// src/lib/asyncHandler.js: forwards async errors to the Express error handler
module.exports = (fn) => (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);

EOF

cat > src/lib/validate.js << 'EOF'
// src/lib/validate.js
const { AppError } = require("./errors");

function parseId(value, field = "id") {
  const id = Number(value);
  if (!Number.isInteger(id) || id < 1) throw new AppError(400, `${field} must be a positive integer`);
  return id;
}

function requireString(value, field) {
  if (typeof value !== "string" || value.trim() === "") throw new AppError(400, `${field} is required`);
  return value.trim();
}

module.exports = { parseId, requireString };

EOF
```

## Step 12: Create the repository layer

Repositories contain SQL and nothing else. They receive the database handle as a parameter.

Run this command to create the three repository files:

```bash
cat > src/repositories/userRepository.js << 'EOF'
// src/repositories/userRepository.js
function createUserRepository({ db }) {
  return {
    async create({ name, email }) {
      const { rows } = await db.query(
        "INSERT INTO users (name, email) VALUES ($1, $2) RETURNING *", [name, email]);
      return rows[0];
    },
    async findAll() {
      const { rows } = await db.query("SELECT * FROM users ORDER BY id");
      return rows;
    },
    async findById(id) {
      const { rows } = await db.query("SELECT * FROM users WHERE id = $1", [id]);
      return rows[0] || null;
    },
    async findByEmail(email) {
      const { rows } = await db.query("SELECT * FROM users WHERE email = $1", [email]);
      return rows[0] || null;
    },
  };
}

module.exports = { createUserRepository };

EOF

cat > src/repositories/productRepository.js << 'EOF'
// src/repositories/productRepository.js
function createProductRepository({ db }) {
  return {
    async create({ name, price, stock }) {
      const { rows } = await db.query(
        "INSERT INTO products (name, price, stock) VALUES ($1, $2, $3) RETURNING *", [name, price, stock]);
      return rows[0];
    },
    async findAll() {
      const { rows } = await db.query("SELECT * FROM products ORDER BY id");
      return rows;
    },
    async findById(id) {
      const { rows } = await db.query("SELECT * FROM products WHERE id = $1", [id]);
      return rows[0] || null;
    },
    async decrementStock(id, quantity) {
      const { rows } = await db.query(
        "UPDATE products SET stock = stock - $2 WHERE id = $1 AND stock >= $2 RETURNING *", [id, quantity]);
      return rows[0] || null;
    },
  };
}

module.exports = { createProductRepository };

EOF

cat > src/repositories/orderRepository.js << 'EOF'
// src/repositories/orderRepository.js
function createOrderRepository({ db }) {
  return {
    async create({ userId, productId, quantity, total }) {
      const { rows } = await db.query(
        "INSERT INTO orders (user_id, product_id, quantity, total) VALUES ($1, $2, $3, $4) RETURNING *",
        [userId, productId, quantity, total]);
      return rows[0];
    },
    async findAll() {
      const { rows } = await db.query("SELECT * FROM orders ORDER BY id");
      return rows;
    },
    async findById(id) {
      const { rows } = await db.query("SELECT * FROM orders WHERE id = $1", [id]);
      return rows[0] || null;
    },
  };
}

module.exports = { createOrderRepository };

EOF
```

## Step 13: Create the service layer

Services hold the business rules. They know nothing about HTTP or SQL.

Run this command to create the three service files:

```bash
cat > src/services/userService.js << 'EOF'
// src/services/userService.js
const { AppError } = require("../lib/errors");
const { parseId, requireString } = require("../lib/validate");

function createUserService({ userRepository }) {
  return {
    async create(input) {
      const name = requireString(input.name, "name");
      const email = requireString(input.email, "email");
      if (await userRepository.findByEmail(email)) throw new AppError(409, "email already registered");
      return userRepository.create({ name, email });
    },
    list() {
      return userRepository.findAll();
    },
    async get(id) {
      const user = await userRepository.findById(parseId(id));
      if (!user) throw new AppError(404, "user not found");
      return user;
    },
  };
}

module.exports = { createUserService };

EOF

cat > src/services/productService.js << 'EOF'
// src/services/productService.js
const { AppError } = require("../lib/errors");
const { parseId, requireString } = require("../lib/validate");

function createProductService({ productRepository }) {
  return {
    async create(input) {
      const name = requireString(input.name, "name");
      const price = Number(input.price);
      if (!Number.isFinite(price) || price <= 0) throw new AppError(400, "price must be a positive number");
      const stock = Number(input.stock ?? 0);
      if (!Number.isInteger(stock) || stock < 0) throw new AppError(400, "stock must be a non-negative integer");
      return productRepository.create({ name, price, stock });
    },
    list() {
      return productRepository.findAll();
    },
    async get(id) {
      const product = await productRepository.findById(parseId(id));
      if (!product) throw new AppError(404, "product not found");
      return product;
    },
  };
}

module.exports = { createProductService };

EOF

cat > src/services/orderService.js << 'EOF'
// src/services/orderService.js
const { AppError } = require("../lib/errors");
const { parseId } = require("../lib/validate");

// orderService depends on three repositories. It never sees SQL or HTTP.
function createOrderService({ userRepository, productRepository, orderRepository }) {
  return {
    async create(input) {
      const userId = parseId(input.userId, "userId");
      const productId = parseId(input.productId, "productId");
      const quantity = parseId(input.quantity, "quantity");

      const user = await userRepository.findById(userId);
      if (!user) throw new AppError(404, "user not found");

      const product = await productRepository.findById(productId);
      if (!product) throw new AppError(404, "product not found");

      const reserved = await productRepository.decrementStock(productId, quantity);
      if (!reserved) throw new AppError(409, "insufficient stock");

      return orderRepository.create({ userId, productId, quantity, total: product.price * quantity });
    },
    list() {
      return orderRepository.findAll();
    },
    async get(id) {
      const order = await orderRepository.findById(parseId(id));
      if (!order) throw new AppError(404, "order not found");
      return order;
    },
  };
}

module.exports = { createOrderService };

EOF
```

## Step 14: Create the controller layer

Controllers translate HTTP into service calls and back. They contain no rules.

Run this command to create the three controller files:

```bash
cat > src/controllers/userController.js << 'EOF'
// src/controllers/userController.js
const express = require("express");
const asyncHandler = require("../lib/asyncHandler");

function createUserController({ userService }) {
  const router = express.Router();
  router.post("/", asyncHandler(async (req, res) => res.status(201).json(await userService.create(req.body))));
  router.get("/", asyncHandler(async (req, res) => res.json(await userService.list())));
  router.get("/:id", asyncHandler(async (req, res) => res.json(await userService.get(req.params.id))));
  return router;
}

module.exports = { createUserController };

EOF

cat > src/controllers/productController.js << 'EOF'
// src/controllers/productController.js
const express = require("express");
const asyncHandler = require("../lib/asyncHandler");

function createProductController({ productService }) {
  const router = express.Router();
  router.post("/", asyncHandler(async (req, res) => res.status(201).json(await productService.create(req.body))));
  router.get("/", asyncHandler(async (req, res) => res.json(await productService.list())));
  router.get("/:id", asyncHandler(async (req, res) => res.json(await productService.get(req.params.id))));
  return router;
}

module.exports = { createProductController };

EOF

cat > src/controllers/orderController.js << 'EOF'
// src/controllers/orderController.js
const express = require("express");
const asyncHandler = require("../lib/asyncHandler");

function createOrderController({ orderService }) {
  const router = express.Router();
  router.post("/", asyncHandler(async (req, res) => res.status(201).json(await orderService.create(req.body))));
  router.get("/", asyncHandler(async (req, res) => res.json(await orderService.list())));
  router.get("/:id", asyncHandler(async (req, res) => res.json(await orderService.get(req.params.id))));
  return router;
}

module.exports = { createOrderController };

EOF
```

## Step 15: Create the composition root

`container.js` is the only file that knows which concrete object feeds which layer. This is dependency injection by constructor, with no framework.

Run this command to create `src/app.js`, `src/container.js`, and `src/server.js`:

```bash
cat > src/app.js << 'EOF'
// src/app.js
const express = require("express");
const { AppError } = require("./lib/errors");

function createApp({ mounts }) {
  const app = express();
  app.use(express.json());
  app.get("/health", (req, res) => res.json({ status: "ok" }));

  for (const [path, handler] of mounts) app.use(path, handler);

  app.use((req, res) => res.status(404).json({ error: "route not found" }));
  app.use((err, req, res, next) => {
    if (err instanceof AppError) return res.status(err.status).json({ error: err.message });
    if (err.type === "entity.parse.failed") return res.status(400).json({ error: "invalid JSON body" });
    console.error(err);
    res.status(500).json({ error: "internal server error" });
  });
  return app;
}

module.exports = { createApp };

EOF

cat > src/container.js << 'EOF'
// src/container.js: the composition root
const config = require("./config");
const { createPool } = require("./db");
const { createApp } = require("./app");
const { createUserRepository } = require("./repositories/userRepository");
const { createProductRepository } = require("./repositories/productRepository");
const { createOrderRepository } = require("./repositories/orderRepository");
const { createUserService } = require("./services/userService");
const { createProductService } = require("./services/productService");
const { createOrderService } = require("./services/orderService");
const { createUserController } = require("./controllers/userController");
const { createProductController } = require("./controllers/productController");
const { createOrderController } = require("./controllers/orderController");

function buildContainer() {
  const db = createPool(config.databaseUrl);

  const userRepository = createUserRepository({ db });
  const productRepository = createProductRepository({ db });
  const orderRepository = createOrderRepository({ db });

  const userService = createUserService({ userRepository });
  const productService = createProductService({ productRepository });
  const orderService = createOrderService({ userRepository, productRepository, orderRepository });

  const app = createApp({
    mounts: [
      ["/users", createUserController({ userService })],
      ["/products", createProductController({ productService })],
      ["/orders", createOrderController({ orderService })],
    ],
  });

  return { app, start: async () => {}, close: () => db.end() };
}

module.exports = { buildContainer };

EOF

cat > src/server.js << 'EOF'
// src/server.js: starts the app and shuts down cleanly on SIGTERM
const config = require("./config");
const { buildContainer } = require("./container");

async function main() {
  const { app, start, close } = buildContainer();
  await start();

  const server = app.listen(config.port, () => console.log(`listening on ${config.port}`));

  const shutdown = (signal) => {
    console.log(`${signal} received, shutting down`);
    server.close(async () => {
      await close();
      process.exit(0);
    });
  };
  process.on("SIGTERM", () => shutdown("SIGTERM"));
  process.on("SIGINT", () => shutdown("SIGINT"));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

EOF
```

## Step 16: Create a unit test with fake repositories

Because `orderService` receives its repositories as parameters, a test can pass in plain objects. No database is needed.

Run this command to create `test/orderService.test.js`:

```bash
cat > test/orderService.test.js << 'EOF'
// test/orderService.test.js
const test = require("node:test");
const assert = require("node:assert/strict");
const { createOrderService } = require("../src/services/orderService");

function build({ user = { id: 1 }, product = { id: 1, price: 10, stock: 5 } } = {}) {
  const created = [];
  const userRepository = { findById: async () => user };
  const productRepository = {
    findById: async () => product,
    decrementStock: async (id, qty) => (product.stock >= qty ? { ...product, stock: product.stock - qty } : null),
  };
  const orderRepository = {
    create: async (order) => {
      created.push(order);
      return { id: 1, ...order, status: "CONFIRMED" };
    },
  };
  return { service: createOrderService({ userRepository, productRepository, orderRepository }), created };
}

test("creates an order and computes the total", async () => {
  const { service, created } = build();
  const order = await service.create({ userId: 1, productId: 1, quantity: 2 });
  assert.equal(order.total, 20);
  assert.equal(created.length, 1);
});

test("rejects an order when stock is insufficient", async () => {
  const { service, created } = build();
  await assert.rejects(() => service.create({ userId: 1, productId: 1, quantity: 99 }), { status: 409 });
  assert.equal(created.length, 0);
});

test("rejects an order for an unknown user", async () => {
  const { service } = build({ user: null });
  await assert.rejects(() => service.create({ userId: 1, productId: 1, quantity: 1 }), { status: 404 });
});

EOF
```

## Step 17: Restart the app with the layered code

The new `src/server.js` is already visible inside the container through the mount. Restart the container so Node loads it.

```bash
docker compose restart app
```

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-1/images/Step%2017%20Restart%20the%20app%20with%20the%20layered%20code.png" alt="docker compose restart app"></p>

## Step 18: Run the smoke test again

```bash
./smoke.sh
```

The status codes match Step 9. The structure changed and the behavior did not. The `400` from the user without an email now comes from `requireString` in the service layer instead of an inline check.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-1/images/Step%2018%20Run%20the%20smoke%20test%20again.png" alt="Smoke test after the refactor"></p>

## Step 19: Run the unit tests

```bash
docker compose exec app node --test test/orderService.test.js
```

Three tests pass without touching PostgreSQL.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-1/images/Step%2019%20Run%20the%20unit%20tests.png" alt="Unit test results"></p>

## Step 20: Document the dependency direction

Run this command to create `docs/dependencies.md`:

```bash
cat > docs/dependencies.md << 'EOF'
# Dependency Diagram

Dependencies point downward only. No layer imports from a layer above it.

~~~mermaid
graph TD
  server["server.js"] --> container["container.js (composition root)"]
  container --> userController["userController"]
  container --> productController["productController"]
  container --> orderController["orderController"]
  userController --> userService["userService"]
  productController --> productService["productService"]
  orderController --> orderService["orderService"]
  userService --> userRepository["userRepository"]
  productService --> productRepository["productRepository"]
  orderService --> userRepository
  orderService --> productRepository
  orderService --> orderRepository["orderRepository"]
  userRepository --> db[("PostgreSQL")]
  productRepository --> db
  orderRepository --> db
~~~

EOF
```

## Step 21: Verify the file layout

```bash
find ~/layered-shop -type f | LC_ALL=C sort
```

Expected output:

```
/home/poridhian/layered-shop/Dockerfile
/home/poridhian/layered-shop/db/init.sql
/home/poridhian/layered-shop/docker-compose.yml
/home/poridhian/layered-shop/docs/dependencies.md
/home/poridhian/layered-shop/legacy/monolith-server.js
/home/poridhian/layered-shop/package.json
/home/poridhian/layered-shop/smoke.sh
/home/poridhian/layered-shop/src/app.js
/home/poridhian/layered-shop/src/config.js
/home/poridhian/layered-shop/src/container.js
/home/poridhian/layered-shop/src/controllers/orderController.js
/home/poridhian/layered-shop/src/controllers/productController.js
/home/poridhian/layered-shop/src/controllers/userController.js
/home/poridhian/layered-shop/src/db.js
/home/poridhian/layered-shop/src/lib/asyncHandler.js
/home/poridhian/layered-shop/src/lib/errors.js
/home/poridhian/layered-shop/src/lib/validate.js
/home/poridhian/layered-shop/src/repositories/orderRepository.js
/home/poridhian/layered-shop/src/repositories/productRepository.js
/home/poridhian/layered-shop/src/repositories/userRepository.js
/home/poridhian/layered-shop/src/server.js
/home/poridhian/layered-shop/src/services/orderService.js
/home/poridhian/layered-shop/src/services/productService.js
/home/poridhian/layered-shop/src/services/userService.js
/home/poridhian/layered-shop/test/orderService.test.js
```

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-1/images/Step%2021%20Verify%20the%20file%20layout.png" alt="File layout"></p>

## Step 22: Stop the stack

```bash
docker compose down
```

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-1/images/Step%2022%20Stop%20the%20stack.png" alt="docker compose down"></p>

Add `-v` to also remove the database volumes for a clean slate:

```bash
docker compose down -v
```

## Next Steps

This lab proved that the layers are independent: the repositories hold only SQL, the services hold only rules, and one file wires them together. The next step is to split this codebase into three services, each with its own layer files and its own database.
