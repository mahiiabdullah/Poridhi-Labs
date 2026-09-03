# Module 76: Elasticsearch Cluster Setup

Stand up a multi-node Elasticsearch cluster and verify it forms a healthy, fault-tolerant topology.

![Architecture](./images/architecture.svg)

## What You Will Build

- A 3-node Elasticsearch cluster running on the host.
- Discovery via `zen` / `seed_hosts` and a `cluster.name` shared across nodes.
- Health and status checks via the `_cat/health` and `_cluster/health` APIs.

## Prerequisites

- Linux host (or WSL2 on Windows) with `curl` and `systemctl` available.
- At least 4 GB RAM per node and 5 GB free disk per node.
- `JAVA_HOME` set to a supported JDK (Elasticsearch bundles its own, but `JAVA_HOME` is needed for the CLI).

## Step 1 — Install Elasticsearch on each node

Install Elasticsearch on every node that will join the cluster. Repeat the install on each host, then come back here for the cluster configuration.

## Step 2 — Configure `elasticsearch.yml` on every node

Edit `/etc/elasticsearch/elasticsearch.yml` on each node. Use the same `cluster.name` everywhere and set `discovery.seed_hosts` to the IPs of the other two nodes.

```yaml
cluster.name: poridhi-es-cluster
node.name: node-1   # change per node
network.host: 0.0.0.0
discovery.seed_hosts: ["10.0.0.11", "10.0.0.12", "10.0.0.13"]
cluster.initial_master_nodes: ["node-1", "node-2", "node-3"]
```

## Step 3 — Start Elasticsearch on every node

```bash
sudo systemctl enable --now elasticsearch
sudo systemctl status elasticsearch
```

Wait for each node to report `started` before moving on.

## Step 4 — Verify cluster health

```bash
curl -s http://<node-1>:9200/_cat/health?v
curl -s http://<node-1>:9200/_cluster/health?pretty
```

You should see three nodes, a `green` (or `yellow`) status, and `number_of_nodes: 3`.

## Conclusion

You now have a 3-node Elasticsearch cluster where any node can serve a request and any single node loss does not lose data (when shards/replicas are configured). The next module covers shard allocation tuning and snapshot/restore for the same cluster.

## References

- [Elasticsearch: Set up a cluster](https://www.elastic.co/guide/en/elasticsearch/reference/current/setup.html)
- [Elasticsearch: Discovery](https://www.elastic.co/guide/en/elasticsearch/reference/current/modules-discovery.html)
