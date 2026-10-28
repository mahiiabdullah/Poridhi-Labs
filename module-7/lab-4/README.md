# Integration — Microservice Stack

**Module 7 — Enterprise Backend Architecture & Microservices Foundations**

This lab builds one production-shaped stack. Three layered services each own a database, and they talk over HTTP and one RabbitMQ message path. The helper code every service needs lives once in a shared library, and a scripted demo proves the order flow survives service restarts.

## Architecture

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-04/images/architecture.svg" alt="Architecture"></p>

## Concept

| Term | Description |
|------|-------------|
| Shared library | `shop-shared` holds the error type, validators, HTTP client, messaging helper, and event contract. Each helper exists once. |
| Library mount | Compose mounts `./shared` at `/app/node_modules/shop-shared`, so every service imports it by name: `require("shop-shared/errors")`. In production you would publish it as a versioned private package. |
| Dependency direction | Controllers call services, services call repositories and clients, and everything may import the shared library. Nothing imports upward. |
| Durable queue | A queue that survives restarts of RabbitMQ and of the services around it. A result can wait there while its consumer is down. |
| Demo script | `demo.sh` runs four scenarios and prints `PASS` or `FAIL` for each check. |

## What You Will Build

A stack with RabbitMQ, three databases, and three services. The `demo.sh` script runs four scenarios: a normal order, a product-service outage, an order-service restart with a result waiting in the queue, and a rejected order. Scenario 3 stops both services in the chain and still ends with a confirmed order.

## Prerequisites

- Docker and Docker Compose v2 on the lab VM. Node.js runs only inside the containers, so the host needs no Node.js.
- Ports `3001`, `3002`, and `3003` free on the VM.

## Step 1: Create the project directory

```bash
mkdir ~/shop-stack && cd ~/shop-stack
mkdir shared
for svc in user product order; do
  mkdir -p $svc-service/db $svc-service/src/repositories $svc-service/src/services $svc-service/src/controllers
done
mkdir -p order-service/src/clients
```

## Step 2: Confirm Docker and Docker Compose are installed

```bash
docker --version
docker compose version
```

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-04/images/Docker%20and%20Docker%20Compose%20versions.png" alt="Docker and Docker Compose versions"></p>

## Step 3: Create the shared library

Every service needs the same error type, validators, and Express helper. The order and product services also share the message contract and the RabbitMQ helper, and the order service needs the HTTP client with timeout and retry. Each of these files exists once, in `shared`.

Run this command to create the shared library:

```bash
cat > shared/errors.js << 'EOF'
// shared/errors.js
class AppError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

module.exports = { AppError };

EOF

cat > shared/validate.js << 'EOF'
// shared/validate.js
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

cat > shared/asyncHandler.js << 'EOF'
// shared/asyncHandler.js: forwards async errors to the Express error handler
module.exports = (fn) => (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);

EOF

cat > shared/events.js << 'EOF'
// shared/events.js: the message contract between order-service and product-service
//   order.created : { orderId, productId, quantity }
//   stock.result  : { orderId, status: "RESERVED" | "REJECTED", total }
module.exports = {
  QUEUES: { ORDER_CREATED: "order.created", STOCK_RESULT: "stock.result" },
};

EOF

cat > shared/rabbit.js << 'EOF'
// shared/rabbit.js: durable queues, persistent messages, publisher confirms, manual acks
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

cat > shared/httpClient.js << 'EOF'
// shared/httpClient.js: GET with a timeout and exponential backoff
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

cat > shared/package.json << 'EOF'
{
  "name": "shop-shared",
  "version": "1.0.0",
  "private": true
}

EOF
```

```bash
ls shared
```

`rabbit.js` loads `amqplib` from the `node_modules` of the service that imports it, so `shared/package.json` declares no dependencies.

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-04/images/Shared%20library%20files.png" alt="Shared library files"></p>

## Step 4: Create the common service files

Each service has the same Dockerfile, database pool, Express app, and server. `app.js` imports the error type by package name, `shop-shared/errors`. You write the files once in the user service and copy them to the other two.

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
const { AppError } = require("shop-shared/errors");

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
```

Run this command to copy the common files into the product and order services:

```bash
for svc in product order; do
  cp user-service/Dockerfile $svc-service/
  cp user-service/src/db.js user-service/src/app.js user-service/src/server.js $svc-service/src/
done
```

## Step 5: Create the package files and database schemas

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

Each service owns one database. The `stock_reservations` table records one outcome per order, so a redelivered message cannot reserve stock twice.

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

## Step 6: Write the user service

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
const { AppError } = require("shop-shared/errors");
const { parseId, requireString } = require("shop-shared/validate");

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
const asyncHandler = require("shop-shared/asyncHandler");

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

function buildContainer() {
  const db = createPool(config.databaseUrl);
  const userRepository = createUserRepository({ db });
  const userService = createUserService({ userRepository });

  const app = createApp({ mounts: [["/users", createUserController({ userService })]] });
  return { app, start: async () => {}, close: () => db.end() };
}

module.exports = { buildContainer };

EOF
```

## Step 7: Write the product service

The product service consumes `order.created`, reserves stock in one transaction, and publishes `stock.result`.

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
const { AppError } = require("shop-shared/errors");
const { parseId, requireString } = require("shop-shared/validate");

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
const asyncHandler = require("shop-shared/asyncHandler");

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
const { createMessaging } = require("shop-shared/rabbit");
const { QUEUES } = require("shop-shared/events");
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

## Step 8: Write the order service

The order service validates the user over HTTP with retries, stores the order as `PENDING`, publishes `order.created`, and applies `stock.result` when it arrives.

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
const { getJson } = require("shop-shared/httpClient");

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
const { AppError } = require("shop-shared/errors");
const { parseId } = require("shop-shared/validate");
const { QUEUES } = require("shop-shared/events");

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
const asyncHandler = require("shop-shared/asyncHandler");

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
const { createMessaging } = require("shop-shared/rabbit");
const { QUEUES } = require("shop-shared/events");
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

## Step 9: Create the docker-compose file

Each service mounts its own `src` folder and the shared library at `/app/node_modules/shop-shared`, so `require("shop-shared/errors")` resolves inside every container.

Run this command to create `docker-compose.yml`:

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
      - ./shared:/app/node_modules/shop-shared
    working_dir: /app
    environment:
      PORT: "3001"
      DATABASE_URL: postgres://postgres:postgres@user-db:5432/users
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
      - ./shared:/app/node_modules/shop-shared
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
      - ./shared:/app/node_modules/shop-shared
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

## Step 10: Verify the file layout

```bash
find ~/shop-stack -type f | LC_ALL=C sort
```

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-04/images/File%20layout.png" alt="File layout"></p>

## Step 11: Build and start the stack

`--wait` returns only when the databases and RabbitMQ pass their health checks and the services are running.

```bash
docker compose up -d --build --wait
```

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-04/images/docker%20compose%20up.png" alt="docker compose up"></p>

```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Seven containers run: RabbitMQ, three databases, and three services.

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-04/images/Container%20status%20table.png" alt="Container status table"></p>

## Step 12: Check each service health endpoint

```bash
sleep 5
for port in 3001 3002 3003; do curl -s localhost:$port/health; echo; done
```

Each service answers on its own port, which confirms that every import of `shop-shared` resolved.

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-04/images/Health%20endpoints.png" alt="Health endpoints"></p>

## Step 13: Document the service dependencies

Run this command to create `docs/dependencies.md`:

```bash
mkdir -p docs
cat > docs/dependencies.md << 'EOF'
# Service Dependency Diagram

Solid arrows are synchronous HTTP. Messages go through RabbitMQ. Each service owns exactly one database.

~~~mermaid
graph LR
  client([Client]) -->|"POST /orders"| order[order-service]
  order -->|"GET /users/:id (HTTP)"| user[user-service]
  order -->|"order.created"| mq{{RabbitMQ}}
  mq -->|"order.created"| product[product-service]
  product -->|"stock.result"| mq
  mq -->|"stock.result"| order
  user --> userdb[("users DB")]
  product --> productdb[("products DB")]
  order --> orderdb[("orders DB")]
  shared[["shop-shared"]] -.-> user
  shared -.-> product
  shared -.-> order
~~~

EOF
```

## Step 14: Create the demo script

The script exercises four scenarios and prints `PASS` or `FAIL` for each check.

Run this command to create `demo.sh`:

```bash
cat > demo.sh << 'EOF'
#!/usr/bin/env bash
ORDER=http://localhost:3003
FAILED=0

field() { grep -o "\"$1\":\"\?[^\",}]*" | head -1 | sed -E 's/.*:"?//'; }

create_order() {
  curl -s -X POST $ORDER/orders -H "Content-Type: application/json" \
    -d "{\"userId\":1,\"productId\":$1,\"quantity\":$2}"
}

wait_http() {
  for i in $(seq 1 40); do
    curl -sf "$1" > /dev/null && return 0
    sleep 1
  done
  return 1
}

wait_for() {
  for i in $(seq 1 40); do
    s=$(curl -s $ORDER/orders/$1 | field status)
    [ "$s" = "$2" ] && return 0
    sleep 1
  done
  return 1
}

check() {
  if [ "$1" = "0" ]; then echo "PASS  $2"; else echo "FAIL  $2"; FAILED=1; fi
}

wait_http http://localhost:3001/health
wait_http http://localhost:3002/health
wait_http http://localhost:3003/health

echo "== 1. Normal order"
id=$(create_order 1 1 | field id)
wait_for $id CONFIRMED; check $? "order $id confirmed through the message path"

echo "== 2. Product service outage"
docker compose stop product-service > /dev/null 2>&1
id=$(create_order 1 1 | field id)
sleep 2
status=$(curl -s $ORDER/orders/$id | field status)
[ "$status" = "PENDING" ]; check $? "order $id accepted and PENDING while product-service is down"
docker compose start product-service > /dev/null 2>&1
wait_for $id CONFIRMED; check $? "order $id confirmed after product-service restarted"

echo "== 3. Order service restart with a result waiting"
docker compose stop product-service > /dev/null 2>&1
id=$(create_order 1 1 | field id)
docker compose stop order-service > /dev/null 2>&1
docker compose start product-service > /dev/null 2>&1
sleep 10
docker compose start order-service > /dev/null 2>&1
wait_for $id CONFIRMED; check $? "order $id confirmed after order-service restarted"

echo "== 4. Insufficient stock"
id=$(create_order 1 9999 | field id)
wait_for $id REJECTED; check $? "order $id rejected for insufficient stock"

exit $FAILED

EOF
chmod +x demo.sh
```

## Step 15: Run the demo

```bash
./demo.sh
```

All five checks print `PASS`. Scenario 3 stops both services in the chain and still ends with a confirmed order, because the result waited in a durable queue.

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-04/images/Demo%20output%20with%20PASS%20lines.png" alt="Demo output with PASS lines"></p>

## Step 16: Confirm the queues drained

```bash
docker compose exec rabbitmq rabbitmqctl list_queues name messages
```

Both queues show `0` messages.

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-04/images/Queue%20message%20counts.png" alt="Queue message counts"></p>

## Step 17: Send invalid input

```bash
curl -s -w "\nHTTP %{http_code}\n" -X POST localhost:3003/orders \
  -H "Content-Type: application/json" \
  -d '{"userId":1,"productId":1}'
```

The request is rejected before any service is called.

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-04/images/Validation%20error%20response.png" alt="Validation error response"></p>

## Step 18: Stop the stack

```bash
docker compose down
```

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-04/images/docker%20compose%20down.png" alt="docker compose down"></p>

Add `-v` to also remove the database volumes for a clean slate:

```bash
docker compose down -v
```

## Next Steps

The stack now has three single-purpose services with their own data, a hardened HTTP path, a durable message path, and one shared library. Natural next steps are a gateway in front of the services, tracing across the HTTP and message paths, and deploying the same images to AWS.
