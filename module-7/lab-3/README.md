# Inter-Service Communication

**Module 7 — Enterprise Backend Architecture & Microservices Foundations**

Services that call each other directly fail together. This lab connects three shop services, user, product, and order, with two communication styles. The user lookup is synchronous HTTP, hardened with a timeout and retries. Stock reservation is an asynchronous RabbitMQ message path, so an order can be accepted while the product service is down and completed after it returns.

## Architecture

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-3/images/architecture.svg" alt="Architecture"></p>

## Concept

| Term | Description |
|------|-------------|
| Synchronous call | The caller waits for the answer. The order service uses it for the user lookup because it needs the result before accepting an order. |
| Asynchronous message | The sender publishes and moves on. The receiver handles the message whenever it is running. |
| `order.created` / `stock.result` | The two queues. The first carries a new order to the product service. The second carries the reservation outcome back. |
| Durable queue, persistent message | The queue and its messages survive a RabbitMQ restart. |
| Manual ack / `nack` | A consumer acknowledges a message only after it has handled it. A failed handler requeues the message. |
| Idempotent consumer | Handling the same message twice has the same effect as handling it once. The `stock_reservations` table guarantees this. |
| Timeout and exponential backoff | A request is abandoned after 1.5 seconds. Each retry waits twice as long as the previous one: 200 ms, 400 ms, 800 ms. |
| `202 Accepted` / `PENDING` | The order is stored but not yet confirmed. It becomes `CONFIRMED` or `REJECTED` when the stock result arrives. |

## What You Will Build

The order service validates the user over HTTP, stores the order as `PENDING`, and publishes an `order.created` message. The product service consumes the message, reserves stock exactly once, and publishes a `stock.result` message. The order service consumes that result and moves the order to `CONFIRMED` or `REJECTED`. You then demonstrate a retried call and a service outage.

## Prerequisites

- Docker and Docker Compose v2 on the lab VM. Node.js runs only inside the containers, so the host needs no Node.js.
- Ports `3001`, `3002`, `3003`, and `15672` free on the VM.

## Step 1: Create the project directory

```bash
mkdir ~/shop-messaging && cd ~/shop-messaging
for svc in user product order; do
  mkdir -p $svc-service/db $svc-service/src/lib $svc-service/src/repositories $svc-service/src/services $svc-service/src/controllers
done
mkdir -p order-service/src/clients
```

## Step 2: Confirm Docker and Docker Compose are installed

```bash
docker --version
docker compose version
```

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-3/images/Step%202%20Confirm%20Docker%20and%20Docker%20Compose%20are%20installed.png" alt="Docker and Docker Compose versions"></p>

## Step 3: Create the common files

Every service is built from controller, service, and repository layers on the same base: a Dockerfile that installs the dependencies, a database pool, an Express app with one error handler, a server that shuts down cleanly, and three small helpers. You write them once in the user service and copy them to the other two.

Run this command to create the common files in `user-service`:

```bash
cat > user-service/Dockerfile << 'EOF'
FROM node:20-alpine

WORKDIR /app

COPY package.json .
RUN npm install --omit=dev

EOF

cat > user-service/src/db.js << 'EOF'
// src/db.js
const { Pool, types } = require("pg");

types.setTypeParser(1700, parseFloat); // NUMERIC columns arrive as JS numbers

function createPool(connectionString) {
  return new Pool({ connectionString });
}

module.exports = { createPool };

EOF

cat > user-service/src/app.js << 'EOF'
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

cat > user-service/src/server.js << 'EOF'
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

cat > user-service/src/lib/errors.js << 'EOF'
// src/lib/errors.js
class AppError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

module.exports = { AppError };

EOF

cat > user-service/src/lib/asyncHandler.js << 'EOF'
// src/lib/asyncHandler.js: forwards async errors to the Express error handler
module.exports = (fn) => (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);

EOF

cat > user-service/src/lib/validate.js << 'EOF'
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

`server.js` awaits `start()` before it listens. The product and order services use `start()` to connect to RabbitMQ.

Run this command to copy the common files into the product and order services:

```bash
for svc in product order; do
  cp user-service/Dockerfile $svc-service/
  cp user-service/src/db.js user-service/src/app.js user-service/src/server.js $svc-service/src/
  cp user-service/src/lib/*.js $svc-service/src/lib/
done
```

## Step 4: Create the message contract and helpers

The order service defines the shared messaging pieces first. The product service gets copies of the same two files, so both sides agree on the queue names and the delivery settings.

Run this command to create `order-service/src/lib/events.js` and `order-service/src/lib/rabbit.js`:

```bash
cat > order-service/src/lib/events.js << 'EOF'
// src/lib/events.js: the message contract between order-service and product-service
//   order.created : { orderId, productId, quantity }
//   stock.result  : { orderId, status: "RESERVED" | "REJECTED", total }
module.exports = {
  QUEUES: { ORDER_CREATED: "order.created", STOCK_RESULT: "stock.result" },
};

EOF

cat > order-service/src/lib/rabbit.js << 'EOF'
// src/lib/rabbit.js: durable queues, persistent messages, publisher confirms, manual acks
const amqp = require("amqplib");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function connectWithRetry(url, attempts = 20, delayMs = 2000) {
  for (let i = 1; i <= attempts; i++) {
    try {
      return await amqp.connect(url);
    } catch (err) {
      console.log(`[rabbit] connect attempt ${i}/${attempts} failed: ${err.message}`);
      await sleep(delayMs);
    }
  }
  throw new Error("could not connect to RabbitMQ");
}

async function createMessaging(url) {
  const connection = await connectWithRetry(url);
  let closing = false;
  connection.on("error", (err) => console.error(`[rabbit] connection error: ${err.message}`));
  connection.on("close", () => {
    if (closing) return;
    console.error("[rabbit] connection lost, exiting so the container restarts");
    process.exit(1);
  });

  const channel = await connection.createConfirmChannel();
  await channel.prefetch(1);

  return {
    async publish(queue, message) {
      await channel.assertQueue(queue, { durable: true });
      await new Promise((resolve, reject) => {
        channel.sendToQueue(queue, Buffer.from(JSON.stringify(message)), { persistent: true },
          (err) => (err ? reject(err) : resolve()));
      });
    },
    async consume(queue, handler) {
      await channel.assertQueue(queue, { durable: true });
      await channel.consume(queue, async (msg) => {
        if (!msg) return;
        try {
          await handler(JSON.parse(msg.content.toString()));
          channel.ack(msg);
        } catch (err) {
          console.error(`[rabbit] ${queue} handler failed, requeueing in 2s: ${err.message}`);
          setTimeout(() => channel.nack(msg, false, true), 2000);
        }
      });
    },
    async close() {
      closing = true;
      await channel.close();
      await connection.close();
    },
  };
}

module.exports = { createMessaging };

EOF
```

Run this command to copy both files into the product service:

```bash
cp order-service/src/lib/events.js order-service/src/lib/rabbit.js product-service/src/lib/
```

## Step 5: Add the HTTP client with timeout and retry

Only idempotent reads are retried. Each retry waits twice as long as the previous one.

Run this command to create `order-service/src/lib/httpClient.js`:

```bash
cat > order-service/src/lib/httpClient.js << 'EOF'
// src/lib/httpClient.js: GET with a timeout and exponential backoff
const { AppError } = require("./errors");

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function getJson(url, { service, timeoutMs = 1500, retries = 3, baseDelayMs = 200 }) {
  for (let attempt = 1; attempt <= retries + 1; attempt++) {
    try {
      const res = await fetch(url, { signal: AbortSignal.timeout(timeoutMs) });
      if (res.status >= 500) throw new Error(`${service} responded ${res.status}`);
      return { status: res.status, body: res.status === 404 ? null : await res.json() };
    } catch (err) {
      if (attempt > retries) throw new AppError(503, `${service} is unavailable`);
      const delay = baseDelayMs * 2 ** (attempt - 1);
      console.log(`[retry] ${service} attempt ${attempt}/${retries + 1} failed (${err.message}); retrying in ${delay}ms`);
      await sleep(delay);
    }
  }
}

module.exports = { getJson };

EOF
```

## Step 6: Create the package files and database schemas

The two services that use RabbitMQ depend on `amqplib`.

Run this command to create `package.json` for each service:

```bash
cat > user-service/package.json << 'EOF'
{
  "name": "user-service",
  "version": "1.0.0",
  "private": true,
  "dependencies": {
    "express": "4.19.2",
    "pg": "8.12.0"
  }
}

EOF

cat > product-service/package.json << 'EOF'
{
  "name": "product-service",
  "version": "1.0.0",
  "private": true,
  "dependencies": {
    "amqplib": "0.10.4",
    "express": "4.19.2",
    "pg": "8.12.0"
  }
}

EOF

cat > order-service/package.json << 'EOF'
{
  "name": "order-service",
  "version": "1.0.0",
  "private": true,
  "dependencies": {
    "amqplib": "0.10.4",
    "express": "4.19.2",
    "pg": "8.12.0"
  }
}

EOF
```

Each service owns one database. The `orders` table has no foreign keys, starts every order as `PENDING`, and leaves `total` empty until stock is reserved. The `stock_reservations` table records one outcome per order.

Run this command to create `db/init.sql` for each service:

```bash
cat > user-service/db/init.sql << 'EOF'
CREATE TABLE users (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO users (name, email) VALUES ('Ada Lovelace', 'ada@example.com');

EOF

cat > product-service/db/init.sql << 'EOF'
CREATE TABLE products (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  price NUMERIC(10,2) NOT NULL CHECK (price > 0),
  stock INTEGER NOT NULL CHECK (stock >= 0)
);

CREATE TABLE stock_reservations (
  order_id INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL,
  quantity INTEGER NOT NULL,
  status TEXT NOT NULL,
  total NUMERIC(12,2),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO products (name, price, stock) VALUES ('Keyboard', 49.50, 100), ('Mouse', 19.99, 50);

EOF

cat > order-service/db/init.sql << 'EOF'
CREATE TABLE orders (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  total NUMERIC(12,2),
  status TEXT NOT NULL DEFAULT 'PENDING',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

EOF
```

## Step 7: Write the user service with failure injection

The user service owns the `users` table. `FAIL_FIRST=N` makes it answer the first N lookups with `503`. This gives you a repeatable way to trigger the retry.

Run this command to create the user service files:

```bash
cat > user-service/src/config.js << 'EOF'
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

cat > user-service/src/repositories/userRepository.js << 'EOF'
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

cat > user-service/src/services/userService.js << 'EOF'
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

cat > user-service/src/controllers/userController.js << 'EOF'
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

cat > user-service/src/container.js << 'EOF'
// user-service/src/container.js
const config = require("./config");
const { createPool } = require("./db");
const { createApp } = require("./app");
const { createUserRepository } = require("./repositories/userRepository");
const { createUserService } = require("./services/userService");
const { createUserController } = require("./controllers/userController");

const failFirst = Number(process.env.FAIL_FIRST || 0);
let injected = 0;

// Fails the first N lookups of GET /users/:id with 503. Used only for the retry demo.
function chaos(req, res, next) {
  if (req.method === "GET" && /^\/\d+$/.test(req.path) && injected < failFirst) {
    injected += 1;
    console.log(`[chaos] injected failure ${injected}/${failFirst}`);
    return res.status(503).json({ error: "injected failure" });
  }
  next();
}

function buildContainer() {
  const db = createPool(config.databaseUrl);
  const userRepository = createUserRepository({ db });
  const userService = createUserService({ userRepository });

  const app = createApp({
    mounts: [
      ["/users", chaos],
      ["/users", createUserController({ userService })],
    ],
  });
  return { app, start: async () => {}, close: () => db.end() };
}

module.exports = { buildContainer };

EOF
```

## Step 8: Write the product service

Stock changes only through messages. The reservation runs in one transaction and records its outcome per order, so a redelivered message cannot reserve twice.

Run this command to create the product service files:

```bash
cat > product-service/src/config.js << 'EOF'
// product-service/src/config.js
function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

module.exports = {
  port: Number(process.env.PORT || 3002),
  databaseUrl: required("DATABASE_URL"),
  rabbitmqUrl: required("RABBITMQ_URL"),
};

EOF

cat > product-service/src/repositories/productRepository.js << 'EOF'
// product-service/src/repositories/productRepository.js
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
    // Idempotent: a second call for the same order returns the stored outcome.
    async reserveForOrder({ orderId, productId, quantity }) {
      const client = await db.connect();
      try {
        await client.query("BEGIN");
        const existing = await client.query(
          "SELECT status, total FROM stock_reservations WHERE order_id = $1", [orderId]);
        if (existing.rowCount > 0) {
          await client.query("COMMIT");
          return existing.rows[0];
        }
        const updated = await client.query(
          "UPDATE products SET stock = stock - $2 WHERE id = $1 AND stock >= $2 RETURNING price",
          [productId, quantity]);
        const reserved = updated.rowCount > 0;
        const status = reserved ? "RESERVED" : "REJECTED";
        const total = reserved ? updated.rows[0].price * quantity : null;
        await client.query(
          "INSERT INTO stock_reservations (order_id, product_id, quantity, status, total) VALUES ($1, $2, $3, $4, $5)",
          [orderId, productId, quantity, status, total]);
        await client.query("COMMIT");
        return { status, total };
      } catch (err) {
        await client.query("ROLLBACK");
        throw err;
      } finally {
        client.release();
      }
    },
  };
}

module.exports = { createProductRepository };

EOF

cat > product-service/src/services/productService.js << 'EOF'
// product-service/src/services/productService.js
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
    async reserveForOrder({ orderId, productId, quantity }) {
      const outcome = await productRepository.reserveForOrder({ orderId, productId, quantity });
      return { orderId, status: outcome.status, total: outcome.total };
    },
  };
}

module.exports = { createProductService };

EOF

cat > product-service/src/controllers/productController.js << 'EOF'
// product-service/src/controllers/productController.js
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

cat > product-service/src/container.js << 'EOF'
// product-service/src/container.js
const config = require("./config");
const { createPool } = require("./db");
const { createApp } = require("./app");
const { createMessaging } = require("./lib/rabbit");
const { QUEUES } = require("./lib/events");
const { createProductRepository } = require("./repositories/productRepository");
const { createProductService } = require("./services/productService");
const { createProductController } = require("./controllers/productController");

function buildContainer() {
  const db = createPool(config.databaseUrl);
  const productRepository = createProductRepository({ db });
  const productService = createProductService({ productRepository });
  const app = createApp({ mounts: [["/products", createProductController({ productService })]] });

  let messaging; // connected in start()

  return {
    app,
    async start() {
      messaging = await createMessaging(config.rabbitmqUrl);
      await messaging.consume(QUEUES.ORDER_CREATED, async (event) => {
        const result = await productService.reserveForOrder(event);
        await messaging.publish(QUEUES.STOCK_RESULT, result);
        console.log(`[product-service] order ${result.orderId} -> ${result.status}`);
      });
    },
    async close() {
      if (messaging) await messaging.close();
      await db.end();
    },
  };
}

module.exports = { buildContainer };

EOF
```

## Step 9: Write the order service

The order service never touches stock. It validates the user over HTTP, stores the order as `PENDING`, publishes a message, and applies the result when it arrives.

Run this command to create the order service files:

```bash
cat > order-service/src/config.js << 'EOF'
// order-service/src/config.js
function required(name) {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required`);
  return value;
}

module.exports = {
  port: Number(process.env.PORT || 3003),
  databaseUrl: required("DATABASE_URL"),
  userServiceUrl: required("USER_SERVICE_URL"),
  rabbitmqUrl: required("RABBITMQ_URL"),
};

EOF

cat > order-service/src/clients/userClient.js << 'EOF'
// order-service/src/clients/userClient.js
const { getJson } = require("../lib/httpClient");

function createUserClient({ baseUrl }) {
  return {
    async findById(id) {
      const { body } = await getJson(`${baseUrl}/users/${id}`, { service: "user-service" });
      return body; // null when the user does not exist
    },
  };
}

module.exports = { createUserClient };

EOF

cat > order-service/src/repositories/orderRepository.js << 'EOF'
// order-service/src/repositories/orderRepository.js
function createOrderRepository({ db }) {
  return {
    async create({ userId, productId, quantity }) {
      const { rows } = await db.query(
        "INSERT INTO orders (user_id, product_id, quantity, status) VALUES ($1, $2, $3, 'PENDING') RETURNING *",
        [userId, productId, quantity]);
      return rows[0];
    },
    // Only a PENDING order can change, so a redelivered result is a harmless no-op.
    async applyStockResult({ orderId, status, total }) {
      const { rows } = await db.query(
        "UPDATE orders SET status = $2, total = $3 WHERE id = $1 AND status = 'PENDING' RETURNING *",
        [orderId, status, total]);
      return rows[0] || null;
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

cat > order-service/src/services/orderService.js << 'EOF'
// order-service/src/services/orderService.js
const { AppError } = require("../lib/errors");
const { parseId } = require("../lib/validate");
const { QUEUES } = require("../lib/events");

function createOrderService({ userRepository, orderRepository, messaging }) {
  return {
    async create(input) {
      const userId = parseId(input.userId, "userId");
      const productId = parseId(input.productId, "productId");
      const quantity = parseId(input.quantity, "quantity");

      // Synchronous call: the user must exist before the order is accepted.
      const user = await userRepository.findById(userId);
      if (!user) throw new AppError(404, "user not found");

      const order = await orderRepository.create({ userId, productId, quantity });

      // Asynchronous call: the product service reserves stock later.
      try {
        await messaging.publish(QUEUES.ORDER_CREATED, { orderId: order.id, productId, quantity });
      } catch (err) {
        await orderRepository.applyStockResult({ orderId: order.id, status: "FAILED", total: null });
        throw new AppError(503, "order could not be queued");
      }
      return order;
    },
    async applyStockResult({ orderId, status, total = null }) {
      const orderStatus = status === "RESERVED" ? "CONFIRMED" : "REJECTED";
      await orderRepository.applyStockResult({ orderId, status: orderStatus, total });
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

cat > order-service/src/controllers/orderController.js << 'EOF'
// order-service/src/controllers/orderController.js
const express = require("express");
const asyncHandler = require("../lib/asyncHandler");

function createOrderController({ orderService }) {
  const router = express.Router();
  // 202 Accepted: the order is stored but not yet confirmed.
  router.post("/", asyncHandler(async (req, res) => res.status(202).json(await orderService.create(req.body))));
  router.get("/", asyncHandler(async (req, res) => res.json(await orderService.list())));
  router.get("/:id", asyncHandler(async (req, res) => res.json(await orderService.get(req.params.id))));
  return router;
}

module.exports = { createOrderController };

EOF

cat > order-service/src/container.js << 'EOF'
// order-service/src/container.js
const config = require("./config");
const { createPool } = require("./db");
const { createApp } = require("./app");
const { createMessaging } = require("./lib/rabbit");
const { QUEUES } = require("./lib/events");
const { createUserClient } = require("./clients/userClient");
const { createOrderRepository } = require("./repositories/orderRepository");
const { createOrderService } = require("./services/orderService");
const { createOrderController } = require("./controllers/orderController");

function buildContainer() {
  const db = createPool(config.databaseUrl);
  const userRepository = createUserClient({ baseUrl: config.userServiceUrl });
  const orderRepository = createOrderRepository({ db });

  let messaging; // connected in start()
  const publisher = { publish: (queue, message) => messaging.publish(queue, message) };

  const orderService = createOrderService({ userRepository, orderRepository, messaging: publisher });
  const app = createApp({ mounts: [["/orders", createOrderController({ orderService })]] });

  return {
    app,
    async start() {
      messaging = await createMessaging(config.rabbitmqUrl);
      await messaging.consume(QUEUES.STOCK_RESULT, async (result) => {
        await orderService.applyStockResult(result);
        console.log(`[order-service] order ${result.orderId} -> ${result.status}`);
      });
    },
    async close() {
      if (messaging) await messaging.close();
      await db.end();
    },
  };
}

module.exports = { buildContainer };

EOF
```

## Step 10: Create the docker-compose file

Run this command to create `docker-compose.yml`. `FAIL_FIRST` is read from your shell, so you can switch failure injection on for a single command in Step 16.

```bash
cat > docker-compose.yml << 'EOF'
x-postgres: &postgres
  image: postgres:16-alpine
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U postgres"]
    interval: 5s
    timeout: 3s
    retries: 10

services:
  rabbitmq:
    image: rabbitmq:3-management
    container_name: rabbitmq
    ports:
      - "15672:15672"
    environment:
      RABBITMQ_DEFAULT_USER: guest
      RABBITMQ_DEFAULT_PASS: guest
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "-q", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  user-db:
    <<: *postgres
    container_name: user-db
    environment:
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: users
    volumes:
      - ./user-service/db/init.sql:/docker-entrypoint-initdb.d/init.sql

  product-db:
    <<: *postgres
    container_name: product-db
    environment:
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: products
    volumes:
      - ./product-service/db/init.sql:/docker-entrypoint-initdb.d/init.sql

  order-db:
    <<: *postgres
    container_name: order-db
    environment:
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: orders
    volumes:
      - ./order-service/db/init.sql:/docker-entrypoint-initdb.d/init.sql

  user-service:
    build:
      context: ./user-service
      dockerfile: Dockerfile
    container_name: user-service
    command: node src/server.js
    restart: unless-stopped
    ports:
      - "3001:3001"
    volumes:
      - ./user-service/src:/app/src
    working_dir: /app
    environment:
      PORT: "3001"
      DATABASE_URL: postgres://postgres:postgres@user-db:5432/users
      FAIL_FIRST: "${FAIL_FIRST:-0}"
    depends_on:
      user-db:
        condition: service_healthy

  product-service:
    build:
      context: ./product-service
      dockerfile: Dockerfile
    container_name: product-service
    command: node src/server.js
    restart: unless-stopped
    ports:
      - "3002:3002"
    volumes:
      - ./product-service/src:/app/src
    working_dir: /app
    environment:
      PORT: "3002"
      DATABASE_URL: postgres://postgres:postgres@product-db:5432/products
      RABBITMQ_URL: amqp://guest:guest@rabbitmq:5672
    depends_on:
      product-db:
        condition: service_healthy
      rabbitmq:
        condition: service_healthy

  order-service:
    build:
      context: ./order-service
      dockerfile: Dockerfile
    container_name: order-service
    command: node src/server.js
    restart: unless-stopped
    ports:
      - "3003:3003"
    volumes:
      - ./order-service/src:/app/src
    working_dir: /app
    environment:
      PORT: "3003"
      DATABASE_URL: postgres://postgres:postgres@order-db:5432/orders
      USER_SERVICE_URL: http://user-service:3001
      RABBITMQ_URL: amqp://guest:guest@rabbitmq:5672
    depends_on:
      order-db:
        condition: service_healthy
      rabbitmq:
        condition: service_healthy

EOF
```

If the heredoc gets mangled by your terminal, run `wc -l docker-compose.yml` afterwards. It prints `115 docker-compose.yml`.

## Step 11: Verify the file layout

```bash
find ~/shop-messaging -type f | LC_ALL=C sort
```

Expected output:

```
/home/poridhian/shop-messaging/docker-compose.yml
/home/poridhian/shop-messaging/order-service/Dockerfile
/home/poridhian/shop-messaging/order-service/db/init.sql
/home/poridhian/shop-messaging/order-service/package.json
/home/poridhian/shop-messaging/order-service/src/app.js
/home/poridhian/shop-messaging/order-service/src/clients/userClient.js
/home/poridhian/shop-messaging/order-service/src/config.js
/home/poridhian/shop-messaging/order-service/src/container.js
/home/poridhian/shop-messaging/order-service/src/controllers/orderController.js
/home/poridhian/shop-messaging/order-service/src/db.js
/home/poridhian/shop-messaging/order-service/src/lib/asyncHandler.js
/home/poridhian/shop-messaging/order-service/src/lib/errors.js
/home/poridhian/shop-messaging/order-service/src/lib/events.js
/home/poridhian/shop-messaging/order-service/src/lib/httpClient.js
/home/poridhian/shop-messaging/order-service/src/lib/rabbit.js
/home/poridhian/shop-messaging/order-service/src/lib/validate.js
/home/poridhian/shop-messaging/order-service/src/repositories/orderRepository.js
/home/poridhian/shop-messaging/order-service/src/server.js
/home/poridhian/shop-messaging/order-service/src/services/orderService.js
/home/poridhian/shop-messaging/product-service/Dockerfile
/home/poridhian/shop-messaging/product-service/db/init.sql
/home/poridhian/shop-messaging/product-service/package.json
/home/poridhian/shop-messaging/product-service/src/app.js
/home/poridhian/shop-messaging/product-service/src/config.js
/home/poridhian/shop-messaging/product-service/src/container.js
/home/poridhian/shop-messaging/product-service/src/controllers/productController.js
/home/poridhian/shop-messaging/product-service/src/db.js
/home/poridhian/shop-messaging/product-service/src/lib/asyncHandler.js
/home/poridhian/shop-messaging/product-service/src/lib/errors.js
/home/poridhian/shop-messaging/product-service/src/lib/events.js
/home/poridhian/shop-messaging/product-service/src/lib/rabbit.js
/home/poridhian/shop-messaging/product-service/src/lib/validate.js
/home/poridhian/shop-messaging/product-service/src/repositories/productRepository.js
/home/poridhian/shop-messaging/product-service/src/server.js
/home/poridhian/shop-messaging/product-service/src/services/productService.js
/home/poridhian/shop-messaging/user-service/Dockerfile
/home/poridhian/shop-messaging/user-service/db/init.sql
/home/poridhian/shop-messaging/user-service/package.json
/home/poridhian/shop-messaging/user-service/src/app.js
/home/poridhian/shop-messaging/user-service/src/config.js
/home/poridhian/shop-messaging/user-service/src/container.js
/home/poridhian/shop-messaging/user-service/src/controllers/userController.js
/home/poridhian/shop-messaging/user-service/src/db.js
/home/poridhian/shop-messaging/user-service/src/lib/asyncHandler.js
/home/poridhian/shop-messaging/user-service/src/lib/errors.js
/home/poridhian/shop-messaging/user-service/src/lib/validate.js
/home/poridhian/shop-messaging/user-service/src/repositories/userRepository.js
/home/poridhian/shop-messaging/user-service/src/server.js
/home/poridhian/shop-messaging/user-service/src/services/userService.js
```

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-3/images/Step%2011%20Verify%20the%20file%20layout.png" alt="File layout"></p>

## Step 12: Build and start the stack

```bash
docker compose up -d --build
```

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-3/images/Step%2012%20Build%20and%20start%20the%20stack_1.png" alt="docker compose up"></p>

```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Seven containers run: RabbitMQ, three databases, and three services. RabbitMQ and the databases show `healthy`.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-3/images/Step%2012%20Build%20and%20start%20the%20stack_2.png" alt="Container status table"></p>

## Step 13: Open the Load Balancer modal in the lab UI

Open the **Load Balancer** modal in the lab UI (top-right). Run this once to find the IP to enter:

```bash
hostname -I
```

Sample output:

```
10.61.7.107 172.17.0.1 100.80.176.159 172.18.0.1
```

Use the first IP printed as `LB_IP`. Open the Load Balancer modal.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-3/images/Step%2013%20Open%20the%20Load%20Balancer%20modal%20in%20the%20lab%20UI_1.png" alt="hostname -I"></p>

Expose one port:

| Enter IP | Enter Port |
|----------|------------|
| `LB_IP` | `15672` (RabbitMQ Management UI) |

Click **Expose**. Click the generated `.lb.poridhi.io` URL to open the RabbitMQ Management UI in a new tab. Sign in with `guest` / `guest`, then click **Queues**. Both `order.created` and `stock.result` appear with the green **D** badge, which means durable. They are created when the services start.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-3/images/Step%2013%20Open%20the%20Load%20Balancer%20modal%20in%20the%20lab%20UI_2.png" alt="Expose port 15672"></p>

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-3/images/Step%2013%20Open%20the%20Load%20Balancer%20modal%20in%20the%20lab%20UI_3.png" alt="RabbitMQ queues"></p>

## Step 14: Place an order through the asynchronous path

```bash
curl -s -w "\nHTTP %{http_code}\n" -X POST localhost:3003/orders \
  -H "Content-Type: application/json" \
  -d '{"userId":1,"productId":1,"quantity":2}'
```

The response is `202` with `"status":"PENDING"` and `"total":null`. The order is accepted but not yet confirmed.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-3/images/Step%2014%20Place%20an%20order%20through%20the%20asynchronous%20path_1.png" alt="Order accepted with 202"></p>

Read the order again after two seconds:

```bash
sleep 2
curl -s localhost:3003/orders/1
```

The status is now `CONFIRMED` and the total is `99`.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-3/images/Step%2014%20Place%20an%20order%20through%20the%20asynchronous%20path_2.png" alt="Order confirmed"></p>

```bash
docker compose logs product-service order-service | grep "\->"
```

The logs show the product service reserving stock and the order service applying the result.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-3/images/Step%2014%20Place%20an%20order%20through%20the%20asynchronous%20path_3.png" alt="Reservation logs"></p>

## Step 15: Place an order that cannot be fulfilled

```bash
curl -s -X POST localhost:3003/orders \
  -H "Content-Type: application/json" \
  -d '{"userId":1,"productId":1,"quantity":9999}'
sleep 2
curl -s localhost:3003/orders/2
```

The second order ends as `REJECTED`.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-3/images/Step%2015%20Place%20an%20order%20that%20cannot%20be%20fulfilled.png" alt="Rejected order"></p>

## Step 16: Trigger the retry on a failing dependency

Recreate the user service so its first two lookups fail:

```bash
FAIL_FIRST=2 docker compose up -d user-service
```

Place an order:

```bash
curl -s -w "\nHTTP %{http_code}\n" -X POST localhost:3003/orders \
  -H "Content-Type: application/json" \
  -d '{"userId":1,"productId":2,"quantity":1}'
```

The order still succeeds with `202`. Read the order service log:

```bash
docker compose logs order-service | grep retry
```

Two retry lines appear with delays of 200 ms and 400 ms. The third attempt succeeds.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-3/images/Step%2016%20Trigger%20the%20retry%20on%20a%20failing%20dependency.png" alt="Retry log lines"></p>

Turn failure injection off:

```bash
docker compose up -d user-service
```

## Step 17: Take the product service down mid-flow

Stop the product service and place an order:

```bash
docker compose stop product-service
curl -s -w "\nHTTP %{http_code}\n" -X POST localhost:3003/orders \
  -H "Content-Type: application/json" \
  -d '{"userId":1,"productId":1,"quantity":1}'
```

The order is accepted with `202`. Check the queue:

```bash
docker compose exec rabbitmq rabbitmqctl list_queues name messages
```

`order.created` holds one waiting message.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-3/images/Step%2017%20Take%20the%20product%20service%20down%20mid-flow_1.png" alt="Message waiting in order.created"></p>

Start the product service and read the orders:

```bash
docker compose start product-service
sleep 6
curl -s localhost:3003/orders
```

The waiting message was processed and the last order changed from `PENDING` to `CONFIRMED`.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-3/images/Step%2017%20Take%20the%20product%20service%20down%20mid-flow_2.png" alt="Order confirmed after restart"></p>

## Step 18: Compare with a synchronous dependency outage

The user lookup is still synchronous, so it behaves differently:

```bash
docker compose stop user-service
curl -s -w "\nHTTP %{http_code}\n" -X POST localhost:3003/orders \
  -H "Content-Type: application/json" \
  -d '{"userId":1,"productId":1,"quantity":1}'
docker compose start user-service
```

The response is `503` with `user-service is unavailable` after three retries. The client gets a clear error, and no order is stored.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-3/images/Step%2018%20Compare%20with%20a%20synchronous%20dependency%20outage.png" alt="503 while user-service is down"></p>

## Step 19: Send invalid input

```bash
curl -s -w "\nHTTP %{http_code}\n" -X POST localhost:3003/orders \
  -H "Content-Type: application/json" \
  -d '{"userId":1,"productId":1}'
```

The request is rejected before any service is called:

```
{"error":"quantity must be a positive integer"}
HTTP 400
```

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-3/images/Step%2019%20Send%20invalid%20input.png" alt="Validation error response"></p>

## Step 20: Stop the stack

```bash
docker compose down
```

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-7/lab-3/images/Step%2020%20Stop%20the%20stack.png" alt="docker compose down"></p>

Add `-v` to also remove the database volumes for a clean slate:

```bash
docker compose down -v
```

Remove the exposed port from the Load Balancer modal: delete the `15672` route for the RabbitMQ Management UI.

## Next Steps

This lab connected the services with two styles of communication. The user lookup became a hardened synchronous call that retries and then fails with a clear `503`. Stock reservation became a durable, idempotent message path that survives a service being down. The helper and messaging files are now copied between services, so the next step is to move them into one shared library and to test the full flow automatically across service restarts.
