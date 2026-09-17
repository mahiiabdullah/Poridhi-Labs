# Lab 49 + Lab 50 — AWS Console Edition

**Module 76 — Elasticsearch Cluster Setup**

This guide walks through Lab 49 (Cluster Provisioning) and Lab 50 (Cluster Configuration) entirely from the AWS Management Console. No `aws` CLI, no local SSH client required — everything happens in your browser using **EC2 Instance Connect**.

> **Why this version?** Your local VM has a RAM problem, so the AWS CLI workflow from the standard labs will not run there. The AWS Console runs in your browser, so the RAM of your VM doesn't matter.

---

## Quick Reference Card

Print this page (or just keep it open). You'll refer back to it constantly.

### The three instances you'll launch

| Node name     | Instance type | Private IP (example) | Public IP (assigned by AWS) | Role              |
|---------------|---------------|----------------------|-----------------------------|-------------------|
| `es-master`   | `t3.medium`   | `10.0.1.10`          | (copy from console)         | `[master]`        |
| `es-data-1`   | `t3.medium`   | `10.0.1.11`          | (copy from console)         | `[data]`          |
| `es-data-2`   | `t3.medium`   | `10.0.1.12`          | (copy from console)         | `[data, ingest]`  |

### Resources you create

| Resource        | Name           | Notes                                          |
|-----------------|----------------|------------------------------------------------|
| Key pair        | `es-lab-key`   | `.pem` downloads automatically                 |
| Security group  | `es-lab-sg`    | Opens 22, 9200, 9300                           |
| AMI             | Ubuntu 24.04   | AMI ID shown on EC2 → AMIs → Public images     |
| 3 × EC2         | above          | Ubuntu 24.04, t3.medium, in default VPC        |

### Cost reminder

- 3 × `t3.medium` ≈ **$0.04/hr each** → about **$0.12/hr total** → about **$3/day** if left running 24/7.
- Stopping instances costs almost nothing (just EBS storage, ~$0.10/day per volume).
- Terminating deletes everything.

---

## Pre-Flight Checklist (do this once)

- [ ] Browser open (Chrome / Edge / Firefox — anything modern)
- [ ] Poridhi-supplied AWS Console URL saved
- [ ] IAM username + password (or access keys) from Poridhi
- [ ] Notepad / text file open on your PC to copy public IPs into

---

# Lab 49: Cluster Provisioning

**Goal:** Three Ubuntu 24.04 EC2 instances, each running its own copy of Elasticsearch 8.x, all in the same VPC. No cluster yet — that's Lab 50.

## Part A — Sign in with Poridhi credentials

1. Open the URL Poridhi gave you. It looks like `https://<id>.signin.aws.amazon.com/console` or a Poridhi lab page with an **"Open AWS Console"** button.
2. Sign in with the IAM username + password Poridhi provided (or with the access keys if they gave you those).
3. Top-right of the AWS Console, confirm the **Region** dropdown shows the region Poridhi assigned (commonly `us-east-1` or `ap-southeast-1`). **Change it if needed** — every step below must use this same region.
4. Top-right, click your username → **Copy account ID**. Save it somewhere — you'll need it if you get logged out.

> **Region matters.** If you accidentally launch in `us-east-1` but your Poridhi credentials are for `ap-southeast-1`, you'll get permission errors. Double-check now.

## Part B — Create a key pair

1. In the top search bar, type `EC2` and press Enter → click **EC2** (opens the EC2 Dashboard).
2. Left sidebar → **Network & Security** → **Key Pairs**.
3. Click **Create key pair** (top right).
4. Fill in:
   - **Name:** `es-lab-key`
   - **Key pair type:** `RSA`
   - **Private key file format:** `.pem`
5. Click **Create key pair**. Your browser downloads `es-lab-key.pem` automatically.

Keep that file safe — it's the only way to SSH in from a terminal. (Lab 50 below uses **EC2 Instance Connect** which lets the browser handle the key for you, but having `es-lab-key.pem` is still useful.)

> **PowerShell fix for the key file (only if you'll SSH from your laptop):** Windows sometimes saves `.pem` files without proper line endings. Open PowerShell and run:
> ```powershell
> # Replace with the actual path of your downloaded file
> $pem = "$env:USERPROFILE\Downloads\es-lab-key.pem"
> (Get-Content $pem -Raw) | Set-Content -NoNewline -Encoding ASCII $pem
> ```

## Part C — Create a security group

1. EC2 Dashboard → left sidebar → **Network & Security** → **Security Groups** → **Create security group**.
2. **Basic details:**
   - **Security group name:** `es-lab-sg`
   - **Description:** `Elasticsearch lab - SSH from my IP, ES traffic inside VPC`
   - **VPC:** pick **default VPC** (the one with `IsDefault = true`).
3. **Inbound rules** — click **Add rule** three times and fill in:

   | Type        | Protocol | Port range | Source          | Why                                 |
   |-------------|----------|------------|-----------------|-------------------------------------|
   | SSH         | TCP      | 22         | My IP           | Lets you SSH from your laptop / browser |
   | Custom TCP  | TCP      | 9200       | Custom → 10.0.0.0/16 | ES HTTP between nodes              |
   | Custom TCP  | TCP      | 9300       | Custom → 10.0.0.0/16 | ES transport between nodes         |

   > **If your VPC CIDR is different** (e.g. `172.31.0.0/16`), check it: left sidebar → **Virtual Private Cloud** → **Your VPCs** → click the default VPC → **CIDR** column shows it. Use that exact CIDR.

4. **Outbound rules** — leave default (All traffic, 0.0.0.0/0).
5. Click **Create security group**. **Save the Security group ID** (`sg-xxxxxxxxxxxxxxxxx`) — it's shown at the top of the next page.

## Part D — Find a Ubuntu 24.04 AMI ID

1. EC2 Dashboard → left sidebar → **Images** → **AMIs**.
2. Change the search dropdown from **Owned by me** to **Public images**.
3. In the search bar, paste exactly:
   ```
   ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*
   ```
4. Right side → **Architecture:** `64-bit (x86)`.
5. Press Enter. The newest AMI appears first. **Copy the AMI ID** (`ami-xxxxxxxxxxxxxxxxx`) — you'll paste it three times in Part E.

## Part E — Launch three instances

You'll launch one instance, then **repeat twice more** with a different name. Each takes ~2 minutes.

For each of the three nodes (`es-master`, `es-data-1`, `es-data-2`):

1. EC2 Dashboard → **Instances** → **Launch instances**.
2. **Name and tags:** `es-master` (then `es-data-1`, `es-data-2` for the next two).
3. **Application and OS Images:**
   - Option A — paste the AMI ID from Part D into the search bar and select it.
   - Option B — click **Browse more AMIs** → **Quick Start** → **Ubuntu** → pick **Ubuntu Server 24.04 LTS** (HVM), SSD Volume Type, 64-bit (x86).
4. **Instance type:** `t3.medium` (search for it in the filter box).
5. **Key pair (login):** select `es-lab-key`.
6. **Network settings** — click **Edit**:
   - **VPC:** default VPC
   - **Subnet:** pick any (e.g. the first one in the dropdown)
   - **Auto-assign public IP:** `Enable`
   - **Firewall (security groups):** **Select existing security group** → pick `es-lab-sg`.
7. **Configure storage:** 8 GiB, gp3 is fine. (Optional: bump to 20 GiB if you want headroom.)
8. **Advanced details** → scroll to the bottom → **User data** → paste this exact block:

   ```bash
   #!/bin/bash
   set -euo pipefail
   apt-get update -y
   apt-get install -y openjdk-17-jdk-headless wget gnupg apt-transport-https
   wget -qO - https://artifacts.elastic.co/GPG-KEY-elasticsearch \
     | gpg --dearmor -o /usr/share/keyrings/elasticsearch-keyring.gpg
   echo "deb [signed-by=/usr/share/keyrings/elasticsearch-keyring.gpg] https://artifacts.elastic.co/packages/8.x/apt stable main" \
     > /etc/apt/sources.list.d/elastic-8.x.list
   apt-get update -y
   apt-get install -y elasticsearch
   echo "-Xms512m"  > /etc/elasticsearch/jvm.options.d/heap.options
   echo "-Xmx512m" >> /etc/elasticsearch/jvm.options.d/heap.options
   systemctl daemon-reload
   systemctl enable --now elasticsearch.service
   ```

9. **Summary** panel on the right → **Launch instance**.
10. Wait for the **Success** screen. Click the instance ID (`i-0xxxx...`) at the bottom — it opens the Instances list.

**Repeat steps 1–10 twice more** for `es-data-1` and `es-data-2`. The same user-data block goes in each.

## Part F — Wait for instances and grab IPs

1. EC2 → **Instances** → you'll see three rows. Wait until **Instance state** = `Running` and **Status check** = `2/2 checks passed` (this is the green checkmark — takes ~3–5 min total because user-data is still installing).
2. Click the **Instance ID** column header to make sure it's not truncated.
3. Copy each instance's **Public IPv4 address** into a notepad:

   ```
   i-0aaa...  es-master  54.123.45.67
   i-0bbb...  es-data-1  54.123.45.68
   i-0ccc...  es-data-2  54.123.45.69
   ```

   (Click the checkbox next to each row, then look at the **Details** tab below — **Public IPv4 address** is near the top.)

## Part G — Verify Elasticsearch on each node (browser SSH)

This uses EC2 Instance Connect — a browser-based SSH terminal that AWS hosts.

For each of the three nodes:

1. Select the row in **Instances** → click **Connect** (top right).
2. Make sure the **EC2 Instance Connect** tab is selected. **Username:** `ubuntu`. **Connect using:** `Connect using EC2 Instance Connect`. Click **Connect**.
3. A terminal opens in your browser. Paste this loop:

   ```bash
   for i in $(seq 1 60); do
     if curl -sf http://localhost:9200 >/dev/null 2>&1; then
       echo "Elasticsearch up after $i attempts"
       curl -s http://localhost:9200
       exit 0
     fi
     sleep 5
   done
   echo "Elasticsearch did not come up in time"
   sudo journalctl -u elasticsearch --no-pager -n 100
   exit 1
   ```

4. **Expected output:** JSON ending with `"tagline": "You Know, for Search"` and `"name": "es-master"` (or `es-data-1`, `es-data-2`).

> **If it doesn't come up:** the same loop prints the last 100 lines of the systemd journal. Most common fix is `vm.max_map_count` — paste this in the same terminal:
> ```bash
> sudo sysctl -w vm.max_map_count=262144
> sudo systemctl restart elasticsearch
> ```

## Part H — Done with Lab 49

Don't terminate yet — Lab 50 needs them running. Just close the browser terminals.

**Checkpoint — write down before Lab 50:**

```
ES_MASTER_IP = (es-master's public IPv4 from console)
ES_DATA1_IP  = (es-data-1's public IPv4 from console)
ES_DATA2_IP  = (es-data-2's public IPv4 from console)
```

---

# Lab 50: Cluster Configuration

**Goal:** Edit `/etc/elasticsearch/elasticsearch.yml` on each of the three EC2 instances so they share a cluster name, declare distinct roles, and discover each other by private IP. End state: one Elasticsearch cluster of three nodes with status `green`.

## Part A — Verify the three instances are still running

EC2 → **Instances**. Confirm all three show **Running** with **2/2 checks passed**. If any are **Stopped**, select them → **Instance state** → **Start instance** → wait until 2/2.

## Part B — Refresh public IPs and test SSH

Public IPs change after a stop/start. Click each instance, copy its current **Public IPv4 address** into your notepad. Update the variables from your Lab 49 checkpoint:

```
ES_MASTER_IP=54.123.45.67
ES_DATA1_IP=54.123.45.68
ES_DATA2_IP=54.123.45.69
```

Quick connectivity test from `es-master`'s browser terminal (EC2 Instance Connect into it):

```bash
ES_DATA1_IP=PASTE_HERE
ES_DATA2_IP=PASTE_HERE
for ip in "$ES_DATA1_IP" "$ES_DATA2_IP"; do
  echo "--- $ip ---"
  curl -sf http://$ip:9200 >/dev/null && echo OK || echo NOT_READY
done
```

Both lines must end with `OK`.

## Part C — Get private IPs

These are stable — same after stop/start.

EC2 → **Instances** → select all three rows → **Details** tab below → **Private IPv4 addresses**. Note them:

```
es-master  10.0.1.10
es-data-1  10.0.1.11
es-data-2  10.0.1.12
```

> **Your actual IPs may differ.** Use whatever AWS assigned. The examples below use placeholders; substitute your real values.

## Part D — Write the per-node elasticsearch.yml (browser SSH)

You'll do this three times — once per node — using EC2 Instance Connect into each node.

### On `es-master`

Connect to `es-master` via EC2 Instance Connect. Paste this entire block, replacing the four placeholder IPs with your actual ones:

```bash
ES_MASTER_PRIVATE=10.0.1.10
ES_DATA1_PRIVATE=10.0.1.11
ES_DATA2_PRIVATE=10.0.1.12

cat > /tmp/elasticsearch.yml <<EOF
cluster.name: docker-cluster
node.name: es-master
node.roles: [master]
network.host: 0.0.0.0
http.port: 9200
discovery.seed_hosts:
  - $ES_DATA1_PRIVATE
  - $ES_DATA2_PRIVATE
cluster.initial_master_nodes:
  - es-master
EOF

# back up the old file, install the new one, fix ownership
sudo cp /etc/elasticsearch/elasticsearch.yml /etc/elasticsearch/elasticsearch.yml.bak
sudo tee /etc/elasticsearch/elasticsearch.yml >/dev/null < /tmp/elasticsearch.yml
sudo chown root:elasticsearch /etc/elasticsearch/elasticsearch.yml
sudo chmod 660 /etc/elasticsearch/elasticsearch.yml

# verify
sudo grep -E "^(cluster.name|node.name|node.roles|discovery.seed_hosts)" /etc/elasticsearch/elasticsearch.yml
```

You should see `cluster.name: docker-cluster`, `node.name: es-master`, `node.roles: [master]`, and the two data-node private IPs.

### On `es-data-1`

Connect to `es-data-1` via EC2 Instance Connect. Paste:

```bash
ES_MASTER_PRIVATE=10.0.1.10
ES_DATA1_PRIVATE=10.0.1.11
ES_DATA2_PRIVATE=10.0.1.12

cat > /tmp/elasticsearch.yml <<EOF
cluster.name: docker-cluster
node.name: es-data-1
node.roles: [data]
network.host: 0.0.0.0
http.port: 9200
discovery.seed_hosts:
  - $ES_MASTER_PRIVATE
  - $ES_DATA2_PRIVATE
EOF

sudo cp /etc/elasticsearch/elasticsearch.yml /etc/elasticsearch/elasticsearch.yml.bak
sudo tee /etc/elasticsearch/elasticsearch.yml >/dev/null < /tmp/elasticsearch.yml
sudo chown root:elasticsearch /etc/elasticsearch/elasticsearch.yml
sudo chmod 660 /etc/elasticsearch/elasticsearch.yml
sudo grep -E "^(cluster.name|node.name|node.roles)" /etc/elasticsearch/elasticsearch.yml
```

### On `es-data-2`

Connect to `es-data-2` via EC2 Instance Connect. Paste:

```bash
ES_MASTER_PRIVATE=10.0.1.10
ES_DATA1_PRIVATE=10.0.1.11
ES_DATA2_PRIVATE=10.0.1.12

cat > /tmp/elasticsearch.yml <<EOF
cluster.name: docker-cluster
node.name: es-data-2
node.roles: [data, ingest]
network.host: 0.0.0.0
http.port: 9200
discovery.seed_hosts:
  - $ES_MASTER_PRIVATE
  - $ES_DATA1_PRIVATE
EOF

sudo cp /etc/elasticsearch/elasticsearch.yml /etc/elasticsearch/elasticsearch.yml.bak
sudo tee /etc/elasticsearch/elasticsearch.yml >/dev/null < /tmp/elasticsearch.yml
sudo chown root:elasticsearch /etc/elasticsearch/elasticsearch.yml
sudo chmod 660 /etc/elasticsearch/elasticsearch.yml
sudo grep -E "^(cluster.name|node.name|node.roles)" /etc/elasticsearch/elasticsearch.yml
```

## Part E — Restart Elasticsearch on every node

EC2 Instance Connect into each instance in turn and run:

```bash
sudo systemctl restart elasticsearch.service
for i in $(seq 1 30); do
  if curl -sf http://localhost:9200 >/dev/null 2>&1; then
    echo "up after $i attempts"; exit 0
  fi
  sleep 3
done
echo "did not come up"; sudo journalctl -u elasticsearch --no-pager -n 80; exit 1
```

Each terminal should print `up after N attempts`.

## Part F — Check cluster health

Back on `es-master`'s browser terminal:

```bash
curl -s http://localhost:9200/_cluster/health?pretty
```

Look for:
- `"cluster_name": "docker-cluster"`
- `"status": "green"`
- `"number_of_nodes": 3`
- `"number_of_data_nodes": 2`
- `"active_shards_percent_as_number": "100.0"`

Then list the nodes:

```bash
curl -s "http://localhost:9200/_cat/nodes?v"
```

Expected three rows, with `node.role` showing `m`, `d`, and `di`.

And the master column:

```bash
curl -s "http://localhost:9200/_cat/nodes?h=ip,node.role,node.name,master"
```

Expected: one row has `*` in the master column (that's `es-master`), the others have `-`.

## Part G — Troubleshooting (only if cluster is yellow/red)

From `es-master`'s browser terminal — paste each block one at a time:

```bash
ES_MASTER_PRIVATE=10.0.1.10
ES_DATA1_PRIVATE=10.0.1.11
ES_DATA2_PRIVATE=10.0.1.12

# Check 1 — TCP 9300 between nodes
for ip in $ES_DATA1_PRIVATE $ES_DATA2_PRIVATE; do
  echo -n "$ip:9300 "
  timeout 3 bash -c "</dev/tcp/$ip/9300" 2>&1 && echo OK || echo FAIL
done

# Check 2 — recent cluster logs
sudo journalctl -u elasticsearch --no-pager -n 200 | grep -E "master|discovery|seed" | tail -40
```

`OK` on both, plus log lines that mention `elected`, `joined`, or `master changed`, means things are working — re-run the cluster health check.

If the 9300 reachability fails, the security group needs an extra rule:

1. EC2 → **Security Groups** → select `es-lab-sg` → **Edit inbound rules** → **Add rule**.
2. Type: **Custom TCP**, Port: `9300`, Source: `10.0.0.0/16` (your VPC CIDR — check Part C).
3. Click **Save rules**.

### Common log messages

| Log message                                              | What it means                                    | Fix                                  |
|----------------------------------------------------------|--------------------------------------------------|--------------------------------------|
| `master not discovered or elected yet`                   | One node's `node.roles` is missing `master`      | Re-do Part D on `es-master`           |
| `failed to send join request to master ... connection refused` | TCP 9300 blocked between nodes                | Add the 9300 rule above               |
| `cluster.name ... does not match local cluster.name`     | One node's `cluster.name` differs                 | Re-check the three YAMLs all say `docker-cluster` |

## Part H — Teardown (do this at the end of every session!)

1. EC2 → **Instances** → select all three rows → **Instance state** → **Stop instance** (saves money, keeps data) or **Terminate instance** (deletes everything).
2. EC2 → **Security Groups** → select `es-lab-sg` → **Actions** → **Delete security group** (only after instances are terminated/stopped).
3. EC2 → **Key Pairs** → select `es-lab-key` → **Actions** → **Delete** (only after instances are terminated, otherwise you lock yourself out).
4. Delete `es-lab-key.pem` from your Downloads folder.

---

## Cheat Sheet: Common Console Searches

If you ever get lost, type these into the top search bar:

| Looking for            | Search bar |
|------------------------|------------|
| EC2 Dashboard          | `EC2`      |
| Running instances      | `EC2 Instances` |
| Key pairs              | `EC2 Key pairs` |
| Security groups        | `EC2 Security Groups` |
| AMI catalog            | `EC2 AMIs` |
| Change region          | Top-right **Region** dropdown |

---

## Cheat Sheet: Common EC2 Instance Connect Commands

You're in a normal Ubuntu shell. The most useful commands:

```bash
# View Elasticsearch logs
sudo journalctl -u elasticsearch --no-pager -n 100

# Restart Elasticsearch
sudo systemctl restart elasticsearch.service

# Check Elasticsearch is up
curl -sf http://localhost:9200 && echo OK

# View the current config
sudo cat /etc/elasticsearch/elasticsearch.yml

# Edit the current config (if you prefer vim/nano)
sudo nano /etc/elasticsearch/elasticsearch.yml
sudo systemctl restart elasticsearch.service
```

---

## What Success Looks Like

After Lab 50 Part F, you should be able to run, from `es-master`:

```bash
curl -s http://localhost:9200/_cluster/health?pretty
```

…and get back JSON that includes `"status": "green"` and `"number_of_nodes": 3`. That's it — the cluster is up and ready for the next lab.

---

*End of guide. Questions → check the Troubleshooting section first, then ping Poridhi support.*
