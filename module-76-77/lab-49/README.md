# Lab 49: Cluster Provisioning

**Module 76 — Elasticsearch Cluster Setup**

This lab provisions three Elasticsearch nodes on the Poridhi lab host using Docker Compose. Each node runs in its own container on a shared bridge network so they can discover each other by service name. By the end you have three Elasticsearch containers ready to be wired into a cluster in the next lab.

## Architecture

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-76-77/lab-49/images/architecture.png" alt="Lab 49 Architecture"></p>

## Concept

| Term                | Description                                                                                            |
|---------------------|--------------------------------------------------------------------------------------------------------|
| Docker Compose      | A tool for defining and running multi-container applications using a single declarative YAML file.     |
| Docker Network      | A virtual bridge network that lets containers resolve each other by service name without hard-coded IPs. |
| Named Volume        | A persistent storage volume managed by Docker, identified by name, that survives container restarts.   |
| Elasticsearch       | A distributed search and analytics engine that stores data across a cluster of nodes.                 |
| Container           | A lightweight, isolated process that runs an application on top of the host OS kernel.                |
| Port 9200           | The HTTP port Elasticsearch exposes for REST API requests (indexing, searching, cluster health).       |
| Port 9300           | The transport port nodes use to talk to each other for cluster coordination and data replication.      |

A `docker-compose.yml` with three `elasticsearch` services on the same bridge network is the Poridhi equivalent of launching three EC2 instances. Each service gets a stable DNS name — `es-master`, `es-data-1`, `es-data-2` — that the others use for discovery. The named volume replaces the EC2 EBS root volume so data and cluster state survive container restarts.

## What You Will Build

Three Elasticsearch 8.x containers running on one host, networked together:

| Service       | Role             | Purpose                                    |
|---------------|------------------|--------------------------------------------|
| `es-master`   | `[master]`       | Dedicated cluster manager — no data stored |
| `es-data-1`   | `[data]`         | Stores index shards, runs queries          |
| `es-data-2`   | `[data, ingest]` | Stores shards and runs ingest pipelines    |

Each service binds port 9200 and 9300 on the host. This lab only starts the containers with a minimal config — the next lab assigns the distinct roles and brings the cluster up.

## Prerequisites

- Docker and Docker Compose are installed on the lab host:

```bash
docker --version
docker compose version
```
- You can reach the Puku CLI terminal on the lab host.
- About 2 GB of free RAM for three Elasticsearch containers (each defaults to 1 GB heap).

## Step 1: Create the project directory

```bash
mkdir -p ~/lab-49
cd ~/lab-49
```

This directory will hold `docker-compose.yml` plus the persistent data volumes for each node.

## Step 2: Write the docker-compose file

Create `docker-compose.yml`:

```bash
cat > docker-compose.yml << 'EOF'
services:
  es-master:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.13.4
    container_name: es-master
    environment:
      - discovery.type=single-node
      - ES_JAVA_OPTS=-Xms512m -Xmx512m
      - xpack.security.enabled=false
    ulimits:
      memlock:
        soft: -1
        hard: -1
    volumes:
      - es_master_data:/usr/share/elasticsearch/data
    ports:
      - "9200:9200"
      - "9300:9300"

  es-data-1:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.13.4
    container_name: es-data-1
    environment:
      - discovery.type=single-node
      - ES_JAVA_OPTS=-Xms512m -Xmx512m
      - xpack.security.enabled=false
    ulimits:
      memlock:
        soft: -1
        hard: -1
    volumes:
      - es_data1_data:/usr/share/elasticsearch/data
    ports:
      - "9201:9200"
      - "9301:9300"

  es-data-2:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.13.4
    container_name: es-data-2
    environment:
      - discovery.type=single-node
      - ES_JAVA_OPTS=-Xms512m -Xmx512m
      - xpack.security.enabled=false
    ulimits:
      memlock:
        soft: -1
        hard: -1
    volumes:
      - es_data2_data:/usr/share/elasticsearch/data
    ports:
      - "9202:9200"
      - "9302:9300"

volumes:
  es_master_data:
  es_data1_data:
  es_data2_data:

networks:
  default:
    name: lab49_net
    driver: bridge
EOF
```

Each container uses `discovery.type=single-node` for now — this lets it boot in isolation. The next lab switches the cluster to multi-node configuration with a shared `cluster.name`.

`ES_JAVA_OPTS=-Xms512m -Xmx512m` keeps the JVM heap small (512 MB) so three containers can run comfortably on the lab host.

## Step 3: Pull the Elasticsearch image

```bash
docker compose pull
```

The image is large — about 1 GB. Wait for the download to finish.

## Step 4: Confirm the compose file is valid

```bash
docker compose config
```

Expected output: the same YAML is printed back with all variables resolved. No errors.

## Step 5: Start the three containers

```bash
docker compose up -d
```

The `-d` flag runs the containers in the background. Elasticsearch takes 30–60 seconds per node to become healthy.

## Step 6: Check the container status

```bash
docker compose ps
```

Expected output:

```
NAME        IMAGE                                                 COMMAND                  SERVICE     CREATED         STATUS                          PORTS
es-master   docker.elastic.co/elasticsearch/elasticsearch:8.13.4  "/bin/tini /usr/local…"   es-master   X seconds ago   Up X seconds (health: starting)  0.0.0.0:9200->9200/tcp, 0.0.0.0:9300->9300/tcp
es-data-1   docker.elastic.co/elasticsearch/elasticsearch:8.13.4  "/bin/tini /usr/local…"   es-data-1   X seconds ago   Up X seconds (health: starting)  0.0.0.0:9201->9200/tcp, 0.0.0.0:9301->9300/tcp
es-data-2   docker.elastic.co/elasticsearch/elasticsearch:8.13.4  "/bin/tini /usr/local…"   es-data-2   X seconds ago   Up X seconds (health: starting)  0.0.0.0:9202->9200/tcp, 0.0.0.0:9302->9300/tcp
```

Each row shows `Up` with `(health: starting)`. After about a minute the status changes to `(healthy)`.

## Step 7: Tail the master node logs

```bash
docker compose logs -f es-master
```

The first time the container starts, Elasticsearch prints a long startup banner ending with a line similar to:

```
{"message":"started","service":{"node":{"name":"es-master"}}, ... }
```

Press `Ctrl+C` to detach. The container keeps running — you only stop watching the logs.

If you see `ERROR: [1] bootstrap checks failed` with `max virtual memory areas vm.max_map_count [65530] is too low`, raise the host limit and restart:

```bash
sudo sysctl -w vm.max_map_count=262144
docker compose restart
```

Then re-run the verification in Step 8.

## Step 8: Verify es-master is responding on port 9200

```bash
curl -s http://localhost:9200
```

Expected response:

```json
{
  "name" : "es-master",
  "cluster_name" : "docker-test-cluster",
  "cluster_uuid" : "...",
  "version" : {
    "number" : "8.13.4",
    ...
  },
  "tagline" : "You Know, for Search"
}
```

The `name` field matches the container name. `cluster_name` reads `docker-test-cluster` because each container is currently in `single-node` discovery mode.

## Step 9: Verify es-data-1

```bash
curl -s http://localhost:9201
```

Expected response has `"name" : "es-data-1"` and `"cluster_name" : "docker-test-cluster"`. The two data nodes are still in separate single-node clusters — the next lab wires them together.

## Step 10: Verify es-data-2

```bash
curl -s http://localhost:9202
```

Expected response has `"name" : "es-data-2"`.

## Step 11: Expose the master API in the Load Balancer

Open the **Load Balancer** modal in the lab UI. Run this command to find the host IP:

```bash
hostname -I
```

Use the first IP printed as `LB_IP`. Open the Load Balancer modal.

| Enter IP   | Enter Port |
|------------|------------|
| `LB_IP`    | `9200` (es-master HTTP API) |

Click **Expose**. Copy the generated `.lb.poridhi.io` URL — the rest of the lab uses it as `<ES-LB-URL>`.

Test from another terminal:

```bash
curl -s <ES-LB-URL>
```

The same JSON from Step 8 should appear — the lab host is reachable from outside through the load balancer.

## Step 12: Confirm all three nodes respond

```bash
for port in 9200 9201 9202; do
  echo "--- localhost:$port ---"
  curl -s "http://localhost:$port" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"name={d['name']} cluster={d['cluster_name']}\")"
  echo ""
done
```

Expected output:

```
--- localhost:9200 ---
name=es-master cluster=docker-test-cluster

--- localhost:9201 ---
name=es-data-1 cluster=docker-test-cluster

--- localhost:9202 ---
name=es-data-2 cluster=docker-test-cluster
```

All three nodes boot independently. Each lives in its own `docker-test-cluster` single-node cluster because `discovery.type=single-node` is still in effect.

## Step 13: Stop the stack

```bash
docker compose down
```

The containers are stopped and removed, but the named volumes (`es_master_data`, `es_data1_data`, `es_data2_data`) are kept so the data persists across lab restarts.

Confirm the containers are gone:

```bash
docker compose ps
```

Expected output:

```
NAME      IMAGE     COMMAND   SERVICE   CREATED   STATUS    PORTS
```

All three lines are empty. The host is clean.

## Step 14: Confirm the volumes still exist

```bash
docker volume ls | grep -E 'es_(master|data[12])'
```

Expected output:

```
local     es_master_data
local     es_data1_data
local     es_data2_data
```

These volumes survive `docker compose down` and `docker compose up` — they are how the cluster keeps its indices across restarts.

## Next Steps

The next lab changes `docker-compose.yml` to a multi-node setup: a single shared `cluster.name`, distinct `node.roles` per container, and a discovery list using service names so the three containers form one Elasticsearch cluster.
