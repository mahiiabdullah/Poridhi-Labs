# Module 7: Enterprise Backend Architecture & Microservices Foundations

This module takes one small shop API from a flat Express file to a production-shaped microservice stack. You refactor it into layers, split it into three services with one database each, connect the services with HTTP and RabbitMQ, and prove the whole flow with a scripted demo. Every lab runs on the lab VM with Docker Compose and starts from an empty folder, so each one can be done on its own.

## Labs

| Lab | Title | What You Do |
|-----|-------|-------------|
| [Lab 1](./lab-1/README.md) | Layered Architecture Refactor | Split a flat Express app into controllers, services, and repositories, and unit-test the business rules without a database |
| [Lab 2](./lab-2/README.md) | Three-Service Microservice Split | Split the layered code into user, product, and order services with one PostgreSQL database each |
| [Lab 3](./lab-3/README.md) | Inter-Service Communication | Add HTTP timeouts and retries, and move stock reservation to a durable RabbitMQ message path |
| [Lab 4](./lab-4/README.md) | Integration: Microservice Stack | Build the three services on one shared library and run a scripted demo across service restarts |
