# Lab 50: Cluster Configuration

**Module 76 — Elasticsearch Cluster Setup**

This lab configures the three Elasticsearch nodes provisioned in Lab 49 into a single cluster. Each node receives a distinct role — master, data, or data+ingest — and the cluster forms automatically through unicast discovery.

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
| `discovery.seed_hosts`    | A list of addresses the node contacts at startup to discover the cluster. Uses private IPs and port 9300.        |
| `cluster.initial_master_nodes` | A one-time bootstrap list of master-eligible node names. Used only on the very first cluster start.        |
| `network.host`            | The IP address Elasticsearch binds to. Set to the node's private IP so other nodes and clients can reach it.     |
| `xpack.security.enabled`  | Toggles TLS and authentication. Disabled in this lab to focus on cluster formation.                              |

## What You Will Build

A three-node Elasticsearch cluster named `poridhi-es-cluster` with the following role assignment:

| Node       | `node.roles`       | Purpose                                    |
|------------|--------------------|--------------------------------------------|
| es-master  | `[master]`         | Dedicated cluster manager — no data stored |
| es-data-1  | `[data]`           | Stores index shards, runs queries          |
| es-data-2  | `[data, ingest]`   | Stores shards and runs ingest pipelines    |

After starting the cluster you verify health, inspect node roles, and index a test document.

## Prerequisites

Complete Lab 49 first. You need:

- Three running EC2 instances (`es-master`, `es-data-1`, `es-data-2`) with Elasticsearch 8.x installed.
- The private IP of each instance. Run this from your local machine to retrieve them:

```bash
aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=es-master,es-data-1,es-data-2" \
  --query 'Reservations[].Instances[].[Tags[?Key==`Name`].Value|[0],PrivateIpAddress,PublicIpAddress]' \
  --output table
```

Sample output:

```
-------------------------------------------
|           DescribeInstances             |
+------------+------------+--------------+
| es-master  | 10.0.1.10  | 54.210.11.22 |
| es-data-1  | 10.0.1.11  | 54.210.33.44 |
| es-data-2  | 10.0.1.12  | 54.210.55.66 |
+------------+------------+--------------+
```

Save the private IPs as shell variables for the rest of the lab:

```bash
PRIV_MASTER=<es-master-private-ip>
PRIV_DATA1=<es-data-1-private-ip>
PRIV_DATA2=<es-data-2-private-ip>
```

Also save the public IPs for SSH:

```bash
IP_MASTER=<es-master-public-ip>
IP_DATA1=<es-data-1-public-ip>
IP_DATA2=<es-data-2-public-ip>
```

Replace every placeholder with the real values from the table.

## Step 1: Configure the master node

SSH into `es-master`:

```bash
ssh -i es-cluster-key.pem ubuntu@$IP_MASTER
```

Back up the default configuration, then write the new one:

```bash
sudo cp /etc/elasticsearch/elasticsearch.yml /etc/elasticsearch/elasticsearch.yml.bak
```

```bash
sudo tee /etc/elasticsearch/elasticsearch.yml > /dev/null << 'EOF'
# ======================== Elasticsearch Configuration =========================
#
# Cluster
# -------
cluster.name: poridhi-es-cluster

# Node
# ----
node.name: es-master
node.roles: [master]

# Network
# -------
network.host: 0.0.0.0
http.port: 9200
transport.port: 9300

# Discovery
# ---------
discovery.seed_hosts:
  - PRIV_MASTER_IP:9300
  - PRIV_DATA1_IP:9300
  - PRIV_DATA2_IP:9300

cluster.initial_master_nodes:
  - es-master

# Security (disabled for this lab)
# --------
xpack.security.enabled: false
xpack.security.enrollment.enabled: false
xpack.security.http.ssl.enabled: false
xpack.security.transport.ssl.enabled: false
EOF
```

Now replace the placeholder IPs with the real private IPs:

```bash
sudo sed -i "s/PRIV_MASTER_IP/$PRIV_MASTER/" /etc/elasticsearch/elasticsearch.yml
sudo sed -i "s/PRIV_DATA1_IP/$PRIV_DATA1/"   /etc/elasticsearch/elasticsearch.yml
sudo sed -i "s/PRIV_DATA2_IP/$PRIV_DATA2/"    /etc/elasticsearch/elasticsearch.yml
```

> **Note:** If you do not have the `$PRIV_*` variables set inside the SSH session, replace the `sed` placeholders manually:
>
> ```bash
> sudo sed -i "s/PRIV_MASTER_IP/10.0.1.10/" /etc/elasticsearch/elasticsearch.yml
> sudo sed -i "s/PRIV_DATA1_IP/10.0.1.11/"   /etc/elasticsearch/elasticsearch.yml
> sudo sed -i "s/PRIV_DATA2_IP/10.0.1.12/"    /etc/elasticsearch/elasticsearch.yml
> ```
>
> Use your actual private IPs from the Prerequisites step.

Verify the config looks correct:

```bash
cat /etc/elasticsearch/elasticsearch.yml
```

Expected output (IPs will differ):

```yaml
cluster.name: poridhi-es-cluster
node.name: es-master
node.roles: [master]
network.host: 0.0.0.0
http.port: 9200
transport.port: 9300
discovery.seed_hosts:
  - 10.0.1.10:9300
  - 10.0.1.11:9300
  - 10.0.1.12:9300
cluster.initial_master_nodes:
  - es-master
xpack.security.enabled: false
xpack.security.enrollment.enabled: false
xpack.security.http.ssl.enabled: false
xpack.security.transport.ssl.enabled: false
```

`node.roles: [master]` makes this a **dedicated master** node. It participates in elections and manages cluster state but does not store data.

Exit the SSH session:

```bash
exit
```

## Step 2: Configure the first data node

SSH into `es-data-1`:

```bash
ssh -i es-cluster-key.pem ubuntu@$IP_DATA1
```

Back up and write the config:

```bash
sudo cp /etc/elasticsearch/elasticsearch.yml /etc/elasticsearch/elasticsearch.yml.bak
```

```bash
sudo tee /etc/elasticsearch/elasticsearch.yml > /dev/null << 'EOF'
# ======================== Elasticsearch Configuration =========================
#
# Cluster
# -------
cluster.name: poridhi-es-cluster

# Node
# ----
node.name: es-data-1
node.roles: [data]

# Network
# -------
network.host: 0.0.0.0
http.port: 9200
transport.port: 9300

# Discovery
# ---------
discovery.seed_hosts:
  - PRIV_MASTER_IP:9300
  - PRIV_DATA1_IP:9300
  - PRIV_DATA2_IP:9300

cluster.initial_master_nodes:
  - es-master

# Security (disabled for this lab)
# --------
xpack.security.enabled: false
xpack.security.enrollment.enabled: false
xpack.security.http.ssl.enabled: false
xpack.security.transport.ssl.enabled: false
EOF
```

Replace the placeholder IPs:

```bash
sudo sed -i "s/PRIV_MASTER_IP/10.0.1.10/" /etc/elasticsearch/elasticsearch.yml
sudo sed -i "s/PRIV_DATA1_IP/10.0.1.11/"   /etc/elasticsearch/elasticsearch.yml
sudo sed -i "s/PRIV_DATA2_IP/10.0.1.12/"    /etc/elasticsearch/elasticsearch.yml
```

Use your actual private IPs. Verify:

```bash
cat /etc/elasticsearch/elasticsearch.yml
```

`node.roles: [data]` makes this a **data-only** node. It holds primary and replica shards and runs search queries but never gets elected master.

Exit:

```bash
exit
```

## Step 3: Configure the second data node with ingest

SSH into `es-data-2`:

```bash
ssh -i es-cluster-key.pem ubuntu@$IP_DATA2
```

Back up and write the config:

```bash
sudo cp /etc/elasticsearch/elasticsearch.yml /etc/elasticsearch/elasticsearch.yml.bak
```

```bash
sudo tee /etc/elasticsearch/elasticsearch.yml > /dev/null << 'EOF'
# ======================== Elasticsearch Configuration =========================
#
# Cluster
# -------
cluster.name: poridhi-es-cluster

# Node
# ----
node.name: es-data-2
node.roles: [data, ingest]

# Network
# -------
network.host: 0.0.0.0
http.port: 9200
transport.port: 9300

# Discovery
# ---------
discovery.seed_hosts:
  - PRIV_MASTER_IP:9300
  - PRIV_DATA1_IP:9300
  - PRIV_DATA2_IP:9300

cluster.initial_master_nodes:
  - es-master

# Security (disabled for this lab)
# --------
xpack.security.enabled: false
xpack.security.enrollment.enabled: false
xpack.security.http.ssl.enabled: false
xpack.security.transport.ssl.enabled: false
EOF
```

Replace the placeholder IPs:

```bash
sudo sed -i "s/PRIV_MASTER_IP/10.0.1.10/" /etc/elasticsearch/elasticsearch.yml
sudo sed -i "s/PRIV_DATA1_IP/10.0.1.11/"   /etc/elasticsearch/elasticsearch.yml
sudo sed -i "s/PRIV_DATA2_IP/10.0.1.12/"    /etc/elasticsearch/elasticsearch.yml
```

Use your actual private IPs. Verify:

```bash
cat /etc/elasticsearch/elasticsearch.yml
```

`node.roles: [data, ingest]` makes this node store shards **and** run ingest pipelines. Ingest pipelines let you transform, enrich, or filter documents before they reach the index — for example, parsing a timestamp string or adding a GeoIP field.

Exit:

```bash
exit
```

## Step 4: Start Elasticsearch on the master node first

The master node must be started first so the cluster can bootstrap. SSH into `es-master`:

```bash
ssh -i es-cluster-key.pem ubuntu@$IP_MASTER
```

Enable the service so it starts on boot, then start it now:

```bash
sudo systemctl daemon-reload
sudo systemctl enable elasticsearch
sudo systemctl start elasticsearch
```

Wait a few seconds and check the status:

```bash
sudo systemctl status elasticsearch --no-pager
```

The output must end with `Active: active (running)`. If it shows `failed`, check the logs:

```bash
sudo journalctl -u elasticsearch --no-pager -n 50
```

Verify Elasticsearch is responding on port 9200:

```bash
curl -s http://localhost:9200
```

Expected response:

```json
{
  "name" : "es-master",
  "cluster_name" : "poridhi-es-cluster",
  "cluster_uuid" : "_na_",
  "version" : {
    "number" : "8.17.x",
    ...
  },
  "tagline" : "You Know, for Search"
}
```

The `cluster_uuid` shows `_na_` because the cluster has only one node and has not fully bootstrapped yet. It updates once the data nodes join.

Exit:

```bash
exit
```

## Step 5: Start Elasticsearch on the data nodes

SSH into `es-data-1` and start the service:

```bash
ssh -i es-cluster-key.pem ubuntu@$IP_DATA1
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable elasticsearch
sudo systemctl start elasticsearch
```

Wait a few seconds and verify:

```bash
sudo systemctl status elasticsearch --no-pager
curl -s http://localhost:9200
```

The `name` field should read `es-data-1`. Exit:

```bash
exit
```

Repeat on `es-data-2`:

```bash
ssh -i es-cluster-key.pem ubuntu@$IP_DATA2
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable elasticsearch
sudo systemctl start elasticsearch
```

Verify:

```bash
sudo systemctl status elasticsearch --no-pager
curl -s http://localhost:9200
```

The `name` field should read `es-data-2`. Exit:

```bash
exit
```

## Step 6: Check cluster health

From any node (or from your local machine if port 9200 is reachable), query the cluster health endpoint. SSH into `es-master`:

```bash
ssh -i es-cluster-key.pem ubuntu@$IP_MASTER
```

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

If `status` is `yellow`, one or more replica shards are unassigned — usually because a node has not finished joining. Wait 30 seconds and retry. If `number_of_nodes` is less than 3, a node failed to discover the cluster — double-check its `discovery.seed_hosts` private IPs and that port 9300 is open in the security group.

## Step 7: List the cluster nodes

```bash
curl -s http://localhost:9200/_cat/nodes?v
```

Expected output:

```
ip         heap.percent ram.percent cpu load_1m load_5m load_15m node.role master name
10.0.1.10            25          60   2    0.10    0.08     0.05 m         *      es-master
10.0.1.11            30          55   3    0.12    0.09     0.06 d         -      es-data-1
10.0.1.12            28          58   2    0.11    0.08     0.05 di        -      es-data-2
```

The `node.role` column encodes the roles:

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
abc123...              10.0.1.10  10.0.1.10  es-master
```

The elected master is `es-master`. Since it is the only master-eligible node in this lab, it is always the elected master.

## Step 9: Index a test document

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

## Step 10: Read the document back

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

## Step 11: Verify shard allocation

Check which data nodes hold the shards for `test-index`:

```bash
curl -s http://localhost:9200/_cat/shards/test-index?v
```

Expected output:

```
index       shard prirep state   docs store ip         node
test-index  0     p      STARTED    1 4.5kb 10.0.1.11  es-data-1
test-index  0     r      STARTED    1 4.5kb 10.0.1.12  es-data-2
```

`p` = primary shard, `r` = replica. Each lives on a different data node, so the document survives the loss of either one.

`es-master` holds no shards because its role is `[master]` only.

## Step 12: Final cluster health check

```bash
curl -s http://localhost:9200/_cluster/health?pretty
```

`status` should now be `green` with `active_primary_shards: 1` and `active_shards: 2` (one primary + one replica for `test-index`).

Exit:

```bash
exit
```

## Cluster Summary

| Node       | Private IP  | Roles          | Elected Master | Holds Shards |
|------------|-------------|----------------|----------------|--------------|
| es-master  | 10.0.1.10   | `master`       | ✔              | ✘            |
| es-data-1  | 10.0.1.11   | `data`         | ✘              | ✔            |
| es-data-2  | 10.0.1.12   | `data, ingest` | ✘              | ✔            |

The three nodes form the cluster `poridhi-es-cluster`. The master manages state, the data nodes store shards, and `es-data-2` can additionally run ingest pipelines.

## Cleanup

To terminate the instances and delete the security group when you are done:

```bash
aws ec2 terminate-instances \
  --instance-ids $INSTANCE_1 $INSTANCE_2 $INSTANCE_3

aws ec2 wait instance-terminated \
  --instance-ids $INSTANCE_1 $INSTANCE_2 $INSTANCE_3

aws ec2 delete-security-group --group-id $SG_ID
```

## Next Steps

This lab completes Module 76. You now have a working three-node Elasticsearch cluster with dedicated master, data, and ingest roles. Module 77 extends this foundation with index management, mappings, and search operations.
