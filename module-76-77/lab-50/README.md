# Lab 50: Cluster Configuration

**Module 76 — Elasticsearch Cluster Setup**

This lab wires the three Elasticsearch containers provisioned earlier into a single cluster. Each container receives a distinct role — master, data, or data+ingest — and the cluster forms automatically through service-name discovery on the Docker bridge network.

## Architecture

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-76-77/lab-50/images/architecture.png" alt="Lab 50 Architecture"></p>

## Concept

| Term                      | Description                                                                                                     |
|---------------------------|-----------------------------------------------------------------------------------------------------------------|
| `cluster.name`            | A string that all nodes must share to form the same cluster. Nodes with different cluster names ignore each other. |
| `node.name`               | A human-readable identifier for a single node, visible in the cluster state and logs.                            |
| `node.roles`              | A list that controls what a node does. Common roles: `master`, `data`, `ingest`.                                 |
| Master-eligible node      | A node with the `master` role. It can be elected to manage cluster state, index metadata, and shard allocation.  |
| Data node                 | A node with the `data` role. It stores index shards and runs search and indexing operations.                     |
| Ingest node               | A node with the `ingest` role. It runs ingest pipelines to transform documents before indexing.                  |
| Unicast discovery         | A cluster-forming mode where each node contacts a list of seed hosts (here, Docker service names) instead of multicasting. |
| `cluster.initial_master_nodes` | A one-time bootstrap list of master-eligible node names. Used only on the very first cluster start.        |
| `xpack.security.enabled`  | Toggles TLS and authentication. Disabled in this lab to focus on cluster formation.                              |

On the Poridhi lab host the three containers share a single bridge network (`lab49_net`). Service names (`es-master`, `es-data-1`, `es-data-2`) act as the discovery addresses — there are no real private IPs to wire into `elasticsearch.yml`.

## What You Will Build

A three-node Elasticsearch cluster named `poridhi-es-cluster` with the following role assignment:

| Node       | `node.roles`       | Purpose                                    |
|------------|--------------------|--------------------------------------------|
| es-master  | `[master]`         | Dedicated cluster manager — no data stored |
| es-data-1  | `[data]`           | Stores index shards, runs queries          |
| es-data-2  | `[data, ingest]`   | Stores shards and runs ingest pipelines    |

After starting the cluster you verify health, inspect node roles, and index a test document.

## Prerequisites

Complete the previous lab first. You need:

- The `~/lab-49` directory created earlier.
- The three Elasticsearch containers stopped (you ran `docker compose down` at the end of the previous lab).
- The named volumes still present (`es_master_data`, `es_data1_data`, `es_data2_data`).

## Step 1: Create the lab-50 directory

```bash
mkdir -p ~/lab-50
cd ~/lab-50
```

Use a fresh directory so this lab keeps its own compose file and config without touching the previous lab.

## Step 2: Write the multi-node docker-compose file

Create `docker-compose.yml`:

```bash
cat > docker-compose.yml << 'EOF'
services:
  es-master:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.13.4
    container_name: es-master
    environment:
      - cluster.name=poridhi-es-cluster
      - node.name=es-master
      - node.roles=[master]
      - discovery.seed_hosts=es-data-1,es-data-2
      - cluster.initial_master_nodes=es-master
      - network.host=0.0.0.0
      - http.port=9200
      - transport.port=9300
      - ES_JAVA_OPTS=-Xms512m -Xmx512m
      - xpack.security.enabled=false
      - xpack.security.enrollment.enabled=false
      - xpack.security.http.ssl.enabled=false
      - xpack.security.transport.ssl.enabled=false
    ulimits:
      memlock:
        soft: -1
        hard: -1
    volumes:
      - es_master_data:/usr/share/elasticsearch/data
    ports:
      - "9200:9200"
      - "9300:9300"
    healthcheck:
      test: ["CMD-SHELL", "curl -s http://localhost:9200/_cluster/health | grep -q -E '\"status\":\"(green|yellow)\"'"]
      interval: 5s
      timeout: 3s
      retries: 30

  es-data-1:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.13.4
    container_name: es-data-1
    environment:
      - cluster.name=poridhi-es-cluster
      - node.name=es-data-1
      - node.roles=[data]
      - discovery.seed_hosts=es-master,es-data-2
      - cluster.initial_master_nodes=es-master
      - network.host=0.0.0.0
      - http.port=9200
      - transport.port=9300
      - ES_JAVA_OPTS=-Xms512m -Xmx512m
      - xpack.security.enabled=false
      - xpack.security.enrollment.enabled=false
      - xpack.security.http.ssl.enabled=false
      - xpack.security.transport.ssl.enabled=false
    ulimits:
      memlock:
        soft: -1
        hard: -1
    volumes:
      - es_data1_data:/usr/share/elasticsearch/data
    ports:
      - "9201:9200"
      - "9301:9300"
    healthcheck:
      test: ["CMD-SHELL", "curl -s http://localhost:9200/_cluster/health | grep -q -E '\"status\":\"(green|yellow)\"'"]
      interval: 5s
      timeout: 3s
      retries: 30

  es-data-2:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.13.4
    container_name: es-data-2
    environment:
      - cluster.name=poridhi-es-cluster
      - node.name=es-data-2
      - node.roles=[data,ingest]
      - discovery.seed_hosts=es-master,es-data-1
      - cluster.initial_master_nodes=es-master
      - network.host=0.0.0.0
      - http.port=9200
      - transport.port=9300
      - ES_JAVA_OPTS=-Xms512m -Xmx512m
      - xpack.security.enabled=false
      - xpack.security.enrollment.enabled=false
      - xpack.security.http.ssl.enabled=false
      - xpack.security.transport.ssl.enabled=false
    ulimits:
      memlock:
        soft: -1
        hard: -1
    volumes:
      - es_data2_data:/usr/share/elasticsearch/data
    ports:
      - "9202:9200"
      - "9302:9300"
    healthcheck:
      test: ["CMD-SHELL", "curl -s http://localhost:9200/_cluster/health | grep -q -E '\"status\":\"(green|yellow)\"'"]
      interval: 5s
      timeout: 3s
      retries: 30

volumes:
  es_master_data:
    external: true
    name: lab-49_es_master_data
  es_data1_data:
    external: true
    name: lab-49_es_data1_data
  es_data2_data:
    external: true
    name: lab-49_es_data2_data

networks:
  default:
    name: lab49_net
    external: true
EOF
```

Key changes from the previous lab:

- `discovery.type=single-node` is removed and replaced with a shared `cluster.name=poridhi-es-cluster` plus `discovery.seed_hosts` pointing at the other two service names.
- `node.name` and `node.roles` are set per container so the cluster knows which node is master, data, or data+ingest.
- The three named volumes are re-attached as `external` volumes, so any data already indexed earlier (none yet, but useful for future labs) is preserved.
- The default network `lab49_net` is declared `external: true` — Docker Compose creates it automatically when the first stack starts, and subsequent stacks can join it by name.

## Step 3: Confirm the compose file is valid

```bash
docker compose config
```

Expected output: the same YAML is printed back with all variables resolved. No errors.

## Step 4: Start the cluster

```bash
docker compose up -d
```

The three containers start in parallel. The master must be elected before the data nodes can join. Wait about a minute, then check status:

```bash
docker compose ps
```

Expected output (all three `Up` and `(healthy)`):

```
NAME        IMAGE                                                 COMMAND                  SERVICE     CREATED         STATUS                    PORTS
es-master   docker.elastic.co/elasticsearch/elasticsearch:8.13.4  "/bin/tini /usr/local…"   es-master   X seconds ago   Up X seconds (healthy)    0.0.0.0:9200->9200/tcp, 0.0.0.0:9300->9300/tcp
es-data-1   docker.elastic.co/elasticsearch/elasticsearch:8.13.4  "/bin/tini /usr/local…"   es-data-1   X seconds ago   Up X seconds (healthy)    0.0.0.0:9201->9200/tcp, 0.0.0.0:9301->9300/tcp
es-data-2   docker.elastic.co/elasticsearch/elasticsearch:8.13.4  "/bin/tini /usr/local…"   es-data-2   X seconds ago   Up X seconds (healthy)    0.0.0.0:9202->9200/tcp, 0.0.0.0:9302->9300/tcp
```

## Step 5: Tail the master log while the cluster forms

```bash
docker compose logs -f es-master
```

Watch for:

```
{"message":"elected-as-master", ...}
{"message":"cluster state version changed from ... to ...", ...}
{"message":"node-left", ...}
```

The cluster boots first as a single master, then the data nodes join. Press `Ctrl+C` once you see `cluster state version changed` lines for all three nodes.

## Step 6: Check cluster health

From the host, query the cluster health endpoint on the master:

```bash
curl -s http://localhost:9200/_cluster/health?pretty
```

Expected response:

```json
{
  "cluster_name" : "poridhi-es-cluster",
  "status" : "green",
  "timed_out" : false,
  "number_of_nodes" : 3,
  "number_of_data_nodes" : 2,
  "active_primary_shards" : 0,
  "active_shards" : 0,
  "relocating_shards" : 0,
  "initializing_shards" : 0,
  "unassigned_shards" : 0,
  "delayed_unassigned_shards" : 0,
  "number_of_pending_tasks" : 0,
  "number_of_in_flight_fetch" : 0,
  "task_max_waiting_in_queue_millis" : 0,
  "active_shards_percent_as_number" : 100.0
}
```

Key values to confirm:

| Field                 | Expected | Meaning                                                   |
|-----------------------|----------|-----------------------------------------------------------|
| `status`              | `green`  | All primary and replica shards are allocated.              |
| `number_of_nodes`     | `3`      | All three nodes joined the cluster.                        |
| `number_of_data_nodes`| `2`      | Two nodes have the `data` role (es-data-1 and es-data-2). |

If `status` is `yellow`, one or more replica shards are unassigned — usually because a node has not finished joining. Wait 30 seconds and retry. If `number_of_nodes` is less than 3, a node failed to discover the cluster — inspect its logs:

```bash
docker compose logs es-data-1 | tail -50
```

## Step 7: List the cluster nodes

```bash
curl -s http://localhost:9200/_cat/nodes?v
```

Expected output:

```
ip         heap.percent ram.percent cpu load_1m load_5m load_15m node.role master name
10.0.1.2            25          60   2    0.10    0.08     0.05 m         *      es-master
10.0.1.3            30          55   3    0.12    0.09     0.06 d         -      es-data-1
10.0.1.4            28          58   2    0.11    0.08     0.05 di        -      es-data-2
```

The IP addresses shown are Docker bridge addresses (they will differ from the sample). The `node.role` column encodes the roles:

| Code | Role      |
|------|-----------|
| `m`  | master    |
| `d`  | data      |
| `i`  | ingest    |

`es-master` shows `m` (master-eligible) and the `*` in the `master` column confirms it won the election. `es-data-1` shows `d` (data only). `es-data-2` shows `di` (data + ingest).

## Step 8: Verify the elected master

```bash
curl -s http://localhost:9200/_cat/master?v
```

Expected output:

```
id                     host       ip         node
abc123...              10.0.1.2  10.0.1.2   es-master
```

The elected master is `es-master`. Since it is the only master-eligible node in this lab, it is always the elected master.

## Step 9: Expose the master API in the Load Balancer

Open the **Load Balancer** modal in the lab UI. Run this to find the host IP:

```bash
hostname -I
```

Use the first IP printed as `LB_IP`.

| Enter IP   | Enter Port |
|------------|------------|
| `LB_IP`    | `9200` (es-master HTTP API) |

Click **Expose**. Copy the generated `.lb.poridhi.io` URL — the rest of the lab uses it as `<ES-LB-URL>`.

Verify from another terminal:

```bash
curl -s <ES-LB-URL>/_cluster/health?pretty
```

The same JSON from Step 6 should appear, confirming the cluster is reachable from outside the lab host.

## Step 10: Index a test document

Write a document to a new index to confirm the data path works end-to-end:

```bash
curl -s -X POST http://localhost:9200/test-index/_doc/1 \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Cluster provisioning complete",
    "module": 76,
    "lab": 50,
    "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"
  }' | python3 -m json.tool
```

Expected response:

```json
{
    "_index": "test-index",
    "_id": "1",
    "_version": 1,
    "result": "created",
    "_shards": {
        "total": 2,
        "successful": 2,
        "failed": 0
    },
    "_seq_no": 0,
    "_primary_term": 1
}
```

`"successful": 2` means the primary shard and one replica were both written. The document is now stored across the two data nodes.

## Step 11: Read the document back

```bash
curl -s http://localhost:9200/test-index/_doc/1?pretty
```

Expected response:

```json
{
  "_index" : "test-index",
  "_id" : "1",
  "_version" : 1,
  "_seq_no" : 0,
  "_primary_term" : 1,
  "found" : true,
  "_source" : {
    "title" : "Cluster provisioning complete",
    "module" : 76,
    "lab" : 50,
    "timestamp" : "2026-09-05T14:30:00Z"
  }
}
```

## Step 12: Verify shard allocation

Check which data nodes hold the shards for `test-index`:

```bash
curl -s http://localhost:9200/_cat/shards/test-index?v
```

Expected output:

```
index       shard prirep state   docs store ip         node
test-index  0     p      STARTED    1 4.5kb 10.0.1.3  es-data-1
test-index  0     r      STARTED    1 4.5kb 10.0.1.4  es-data-2
```

`p` = primary shard, `r` = replica. Each lives on a different data node, so the document survives the loss of either one.

`es-master` holds no shards because its role is `[master]` only.

## Step 13: Final cluster health check

```bash
curl -s http://localhost:9200/_cluster/health?pretty
```

`status` should now be `green` with `active_primary_shards: 1` and `active_shards: 2` (one primary + one replica for `test-index`).

## Cluster Summary

| Node       | Roles          | Elected Master | Holds Shards |
|------------|----------------|----------------|--------------|
| es-master  | `master`       | yes            | no           |
| es-data-1  | `data`         | no             | yes          |
| es-data-2  | `data, ingest` | no             | yes          |

The three containers form the cluster `poridhi-es-cluster`. The master manages state, the data nodes store shards, and `es-data-2` can additionally run ingest pipelines.

## Step 14: Stop the cluster

```bash
docker compose down
```

The containers are removed, but the named volumes are kept so `test-index` survives. If you also want to drop the volumes:

```bash
docker compose down -v
```

`-v` removes the named volumes too, so the next `docker compose up` starts with an empty cluster.

## Next Steps

This lab completes the cluster setup. You now have a working three-node Elasticsearch cluster with dedicated master, data, and ingest roles. Follow-up labs extend this foundation with index management, mappings, and search operations.
