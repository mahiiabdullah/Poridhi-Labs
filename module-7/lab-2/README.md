# Three-Service Microservice Split

**Module 7 — Enterprise Backend Architecture & Microservices Foundations**

This lab splits a shop API with users, products, and orders into three services. Each service owns its code and its database. The order service never reads the other tables. It calls the user and product services over HTTP instead. Because every service is built from controller, service, and repository layers, the order logic does not care whether a repository talks to SQL or to another service.

## Architecture

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-02/images/architecture.svg" alt="Architecture"></p>

## Concept

| Term | Description |
|------|-------------|
| Service boundary | One service owns one business capability and the code behind it. |
| Database per service | Each service has its own PostgreSQL container. No service reads another service's tables. |
| HTTP client adapter | An object in the order service that exposes the same method names as a repository but calls another service over HTTP. |
| `POST /products/:id/reserve` | The endpoint that lets other services decrease stock without touching the product table. |
| No cross-database foreign key | A foreign key cannot cross a database boundary, so `orders` stores `user_id` and `product_id` as plain integers. |

## What You Will Build

Three containerized services orchestrated by one Compose file, each with its own PostgreSQL container. The user service listens on `3001`, the product service on `3002`, and the order service on `3003`. The order service's `orderService.js` depends on a `userRepository` and a `productRepository`. Its composition root injects HTTP clients behind those two names, so the business logic contains no HTTP code.

## Prerequisites

- Docker and Docker Compose v2 on the lab VM. Node.js runs only inside the containers, so the host needs no Node.js.
- Ports `3001`, `3002`, and `3003` free on the VM.

## Step 1: Create the project directory

```bash
mkdir ~/shop-services && cd ~/shop-services
for svc in user product order; do
  mkdir -p $svc-service/db $svc-service/src/lib $svc-service/src/repositories $svc-service/src/services $svc-service/src/controllers
done
```

## Step 2: Confirm Docker and Docker Compose are installed

```bash
docker --version
docker compose version
```

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-02/images/Docker%20and%20Docker%20Compose%20versions.png" alt="Docker and Docker Compose versions"></p>

## Step 3: Create the common files

Every service is built the same way: a Dockerfile that installs the dependencies, configuration from environment variables, a database pool, an Express app with one error handler, a server that shuts down cleanly, and three small helpers. You write them once in the user service and copy them to the other two.

Run this command to create the common files in `user-service`:

```bash
cat > user-service/Dockerfile << 'EOF'
FROM node:20-alpine

WORKDIR /app

COPY package.json .
RUN npm install --omit=dev

EOF

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

The image holds only the dependencies. Compose mounts each service's `src` folder, so a code change needs a container restart and no image rebuild.

Run this command to copy the common files into the product and order services:

```bash
for svc in product order; do
  cp user-service/Dockerfile $svc-service/
  cp user-service/src/db.js user-service/src/app.js user-service/src/server.js $svc-service/src/
  cp user-service/src/lib/*.js $svc-service/src/lib/
done
cp user-service/src/config.js product-service/src/
```

The order service gets its own `config.js` in Step 8 because it also needs the addresses of the other two services.

```bash
find . -type f | LC_ALL=C sort
```

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-02/images/Common%20files%20per%20service.png" alt="Common files per service"></p>

## Step 4: Create the package files

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
    "express": "4.19.2",
    "pg": "8.12.0"
  }
}

EOF
```

## Step 5: Create one database schema per service

The `orders` table has no foreign keys.

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

INSERT INTO products (name, price, stock) VALUES ('Keyboard', 49.50, 100), ('Mouse', 19.99, 50);

EOF

cat > order-service/db/init.sql << 'EOF'
CREATE TABLE orders (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  total NUMERIC(12,2) NOT NULL,
  status TEXT NOT NULL DEFAULT 'CONFIRMED',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

EOF
```

## Step 6: Write the user service

The user service owns the `users` table. The repository holds the SQL, the service holds the rules, and the controller maps HTTP to service calls. `container.js` is the composition root that wires them together.

Run this command to create the user service files:

```bash
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

Other services must be able to reserve stock, so the product service exposes `POST /products/:id/reserve` next to its create, list, and get routes. `decrementStock` changes stock only when enough is left, in one SQL statement.

Run this command to create the product service files:

```bash
cat > product-service/src/repositories/productRepository.js << 'EOF'
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
    async reserveStock(id, quantity) {
      const reserved = await productRepository.decrementStock(parseId(id), parseId(quantity, "quantity"));
      if (!reserved) throw new AppError(409, "insufficient stock");
      return reserved;
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
  router.post("/:id/reserve", asyncHandler(async (req, res) => {
    res.json(await productService.reserveStock(req.params.id, req.body.quantity));
  }));
  return router;
}

module.exports = { createProductController };

EOF

cat > product-service/src/container.js << 'EOF'
// product-service/src/container.js
const config = require("./config");
const { createPool } = require("./db");
const { createApp } = require("./app");
const { createProductRepository } = require("./repositories/productRepository");
const { createProductService } = require("./services/productService");
const { createProductController } = require("./controllers/productController");

function buildContainer() {
  const db = createPool(config.databaseUrl);
  const productRepository = createProductRepository({ db });
  const productService = createProductService({ productRepository });

  const app = createApp({ mounts: [["/products", createProductController({ productService })]] });
  return { app, start: async () => {}, close: () => db.end() };
}

module.exports = { buildContainer };

EOF
```

## Step 8: Write the order service

The order service owns only the `orders` table. `orderService` depends on three repositories and never sees SQL or HTTP. For users and products, the composition root injects HTTP clients that expose the same method names as a repository.

Run this command to create the order service files:

```bash
mkdir -p order-service/src/clients

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
  productServiceUrl: required("PRODUCT_SERVICE_URL"),
};

EOF

cat > order-service/src/clients/userClient.js << 'EOF'
// order-service/src/clients/userClient.js: same shape as userRepository, backed by HTTP
function createUserClient({ baseUrl }) {
  return {
    async findById(id) {
      const res = await fetch(`${baseUrl}/users/${id}`);
      if (res.status === 404) return null;
      if (!res.ok) throw new Error(`user-service responded ${res.status}`);
      return res.json();
    },
  };
}

module.exports = { createUserClient };

EOF

cat > order-service/src/clients/productClient.js << 'EOF'
// order-service/src/clients/productClient.js: same shape as productRepository, backed by HTTP
function createProductClient({ baseUrl }) {
  return {
    async findById(id) {
      const res = await fetch(`${baseUrl}/products/${id}`);
      if (res.status === 404) return null;
      if (!res.ok) throw new Error(`product-service responded ${res.status}`);
      return res.json();
    },
    async decrementStock(id, quantity) {
      const res = await fetch(`${baseUrl}/products/${id}/reserve`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ quantity }),
      });
      if (res.status === 409) return null;
      if (!res.ok) throw new Error(`product-service responded ${res.status}`);
      return res.json();
    },
  };
}

module.exports = { createProductClient };

EOF

cat > order-service/src/repositories/orderRepository.js << 'EOF'
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

cat > order-service/src/services/orderService.js << 'EOF'
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

cat > order-service/src/controllers/orderController.js << 'EOF'
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

cat > order-service/src/container.js << 'EOF'
// order-service/src/container.js
const config = require("./config");
const { createPool } = require("./db");
const { createApp } = require("./app");
const { createUserClient } = require("./clients/userClient");
const { createProductClient } = require("./clients/productClient");
const { createOrderRepository } = require("./repositories/orderRepository");
const { createOrderService } = require("./services/orderService");
const { createOrderController } = require("./controllers/orderController");

function buildContainer() {
  const db = createPool(config.databaseUrl);

  // orderService is unchanged. These two objects now call other services over HTTP.
  const userRepository = createUserClient({ baseUrl: config.userServiceUrl });
  const productRepository = createProductClient({ baseUrl: config.productServiceUrl });
  const orderRepository = createOrderRepository({ db });

  const orderService = createOrderService({ userRepository, productRepository, orderRepository });
  const app = createApp({ mounts: [["/orders", createOrderController({ orderService })]] });
  return { app, start: async () => {}, close: () => db.end() };
}

module.exports = { buildContainer };

EOF
```

## Step 9: Create the docker-compose file

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
    ports:
      - "3001:3001"
    volumes:
      - ./user-service/src:/app/src
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
    ports:
      - "3002:3002"
    volumes:
      - ./product-service/src:/app/src
    working_dir: /app
    environment:
      PORT: "3002"
      DATABASE_URL: postgres://postgres:postgres@product-db:5432/products
    depends_on:
      product-db:
        condition: service_healthy

  order-service:
    build:
      context: ./order-service
      dockerfile: Dockerfile
    container_name: order-service
    command: node src/server.js
    ports:
      - "3003:3003"
    volumes:
      - ./order-service/src:/app/src
    working_dir: /app
    environment:
      PORT: "3003"
      DATABASE_URL: postgres://postgres:postgres@order-db:5432/orders
      USER_SERVICE_URL: http://user-service:3001
      PRODUCT_SERVICE_URL: http://product-service:3002
    depends_on:
      order-db:
        condition: service_healthy

EOF
```

If the heredoc gets mangled by your terminal, run `wc -l docker-compose.yml` afterwards. It prints `92 docker-compose.yml`.

## Step 10: Verify the file layout

```bash
find ~/shop-services -type f | LC_ALL=C sort
```

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-02/images/File%20layout.png" alt="File layout"></p>

## Step 11: Build and start the stack

```bash
docker compose up -d --build
```

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-02/images/docker%20compose%20up.png" alt="docker compose up"></p>

```bash
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Six containers run: three databases and three services. The databases show `healthy`.

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-02/images/Container%20status%20table.png" alt="Container status table"></p>

## Step 12: Check each service health endpoint

```bash
for port in 3001 3002 3003; do curl -s localhost:$port/health; echo; done
```

Each service answers on its own port.

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-02/images/Health%20endpoints.png" alt="Health endpoints"></p>

## Step 13: Test the order flow across services

Run this command to create `smoke.sh`:

```bash
cat > smoke.sh << 'EOF'
#!/usr/bin/env bash
JSON="Content-Type: application/json"

call() {
  local label=$1; shift
  echo "--- $label"
  curl -s -w "\nHTTP %{http_code}\n" "$@"
  echo
}

call "create user (user-service)"      -X POST http://localhost:3001/users -H "$JSON" -d "{\"name\":\"Grace\",\"email\":\"grace$(date +%s)@example.com\"}"
call "list products (product-service)" http://localhost:3002/products
call "create order (order-service)"    -X POST http://localhost:3003/orders -H "$JSON" -d '{"userId":1,"productId":1,"quantity":2}'
call "order more than stock"           -X POST http://localhost:3003/orders -H "$JSON" -d '{"userId":1,"productId":1,"quantity":9999}'
call "order for unknown user"          -X POST http://localhost:3003/orders -H "$JSON" -d '{"userId":9999,"productId":1,"quantity":1}'
call "list orders"                     http://localhost:3003/orders

EOF
chmod +x smoke.sh
./smoke.sh
```

The order returns `201` with `total` 99. The oversized order returns `409`, and the unknown user returns `404`.

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-02/images/Order%20flow%20smoke%20test.png" alt="Order flow smoke test"></p>

## Step 14: Confirm the order reduced stock in the product service

The order service never touched the product table. It asked the product service to do it.

```bash
curl -s localhost:3002/products/1
```

`stock` dropped from 100 to 98.

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-02/images/Product%20stock%20after%20the%20order.png" alt="Product stock after the order"></p>

## Step 15: Confirm each database holds only its own tables

```bash
docker compose exec user-db psql -U postgres -d users -c "\dt"
docker compose exec product-db psql -U postgres -d products -c "\dt"
docker compose exec order-db psql -U postgres -d orders -c "\dt"
```

The three databases list `users`, `products`, and `orders` respectively. No database can join across services.

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-02/images/Tables%20per%20database.png" alt="Tables per database"></p>

## Step 16: Send invalid input

```bash
curl -s -w "\nHTTP %{http_code}\n" -X POST localhost:3003/orders \
  -H "Content-Type: application/json" \
  -d '{"userId":1}'
```

The order service rejects the request before it calls any other service.

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-02/images/Validation%20error%20response.png" alt="Validation error response"></p>

## Step 17: Observe what a service outage does

Stop the user service and place an order:

```bash
docker compose stop user-service
curl -s -w "\nHTTP %{http_code}\n" -X POST localhost:3003/orders \
  -H "Content-Type: application/json" \
  -d '{"userId":1,"productId":1,"quantity":1}'
```

The order service returns a generic `500`. It has no timeout, no retry, and no way to tell the client that the user service is down.

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-02/images/Order%20response%20while%20user-service%20is%20down.png" alt="Order response while user-service is down"></p>

Start the user service again:

```bash
docker compose start user-service
```

## Step 18: Stop the stack

```bash
docker compose down
```

<p align="center"><img src="https://raw.githubusercontent.com/poridhioss/lab-asset/main/System-Design-Labs/Milestones/MileStone-02/Module-07/lab-02/images/docker%20compose%20down.png" alt="docker compose down"></p>

Add `-v` to also remove the database volumes for a clean slate:

```bash
docker compose down -v
```

## Next Steps

This lab turned one codebase into three independently deployable services, each with its own database. The order service still calls the other two directly, and it fails when they are down. The next step is to harden the synchronous calls with timeouts and retries, and to move stock reservation onto a message queue so it survives a service being down.
