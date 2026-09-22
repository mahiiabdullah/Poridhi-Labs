# Lab 49: Cluster Provisioning

**Module 76 — Elasticsearch Cluster Setup**

This lab provisions three Elasticsearch nodes on AWS EC2 using only the **AWS Management Console** (no CLI, no scripts). Each node is its own Ubuntu 24.04 EC2 instance with a dedicated role — `master`, `data`, or `data + ingest` — running in the same VPC so they can discover each other over the private network. By the end you have three running Elasticsearch instances ready to be wired into a cluster in the next lab.

## Architecture

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-76-77/lab-49/images/architecture.svg" alt="Lab 49 Architecture"></p>

## Concept

| Term           | Description                                                                                            |
|----------------|--------------------------------------------------------------------------------------------------------|
| VPC            | An isolated virtual network in AWS. All three nodes live in the same VPC so they can talk privately.   |
| Public Subnet  | A subnet whose route table sends `0.0.0.0/0` to an Internet Gateway. Instances here can get a public IP. |
| Internet Gateway | A VPC gateway attached to your VPC that lets instances in public subnets reach the internet.         |
| Route Table    | A set of rules that determine where traffic from a subnet is sent. Need one default route to the IGW.  |
| EC2 Instance   | A virtual server in AWS. Each Elasticsearch node runs on its own instance.                              |
| AMI            | An Amazon Machine Image — the template for an instance's root volume. Ubuntu 24.04 LTS in this lab.    |
| Key Pair       | A public/private key pair used for SSH access. AWS stores the public key; you keep the private `.pem`.  |
| Security Group | A virtual firewall attached to each instance. Controls inbound and outbound traffic.                  |
| User Data      | A shell script that runs once on first boot. Installs Java and Elasticsearch automatically.            |
| Port 9200      | The HTTP port Elasticsearch exposes for REST API requests (indexing, searching, cluster health).       |
| Port 9300      | The transport port nodes use to talk to each other for cluster coordination.                          |
| `t3.medium`    | A burstable general-purpose instance — 2 vCPU, 4 GB RAM. Sized for three small Elasticsearch nodes.    |

Three EC2 instances in the same VPC are the production equivalent of three Docker containers in one compose file. Each instance gets a stable private IP and DNS name the others use for discovery.

## Learning Objectives

By the end of this lab you will have:

1. Created a dedicated VPC (`10.0.0.0/16`) for the cluster.
2. Created a public subnet inside the VPC.
3. Created and attached an Internet Gateway.
4. Created a route table that sends `0.0.0.0/0` to the IGW and associated it with the subnet.
5. Created a key pair for SSH access.
6. Created a security group that opens SSH, ES HTTP (9200), and ES transport (9300).
7. Launched three `t3.medium` instances with a hardened user-data script that installs Java and Elasticsearch and sets the kernel setting Elasticsearch needs.
8. Verified Elasticsearch is responding on port 9200 on all three nodes.

## Prerequisites

- An AWS account with permissions to launch EC2, create security groups, create key pairs, create VPC resources, and read instance metadata.
- A modern browser (Chrome / Firefox / Edge).
- A notepad or scratch file to copy values like IP addresses and the AMI ID.
- Roughly **30 minutes**. Most of that time is waiting for the user-data script to install Java + Elasticsearch on three instances.

> **Cost.** Three `t3.medium` instances in most regions cost about **$0.12/hour** (~$3/day). Terminate them in Step 13 when you are done.

---

## Step-by-Step Guide

### Step 1: Pick a Region

Pick the region closest to you in the top-right of the AWS Console. **Use the same region for every step in this lab** — VPCs, subnets, and key pairs are regional, so cross-region references silently fail.

This guide uses `ap-southeast-1` (Singapore) in examples. Anywhere `us-east-1`, `eu-west-1`, or any other region works identically.

---

### Step 2: Create Your VPC

1. Open the **AWS Management Console** and search for `VPC` in the search bar at the top.
2. Click **Your VPCs** in the left sidebar.
3. Click **Create VPC** at the top right.
4. Fill in:
   - **Name tag:** `es-lab-vpc`
   - **IPv4 CIDR block:** `10.0.0.0/16`
   - **Tenancy:** `Default`
5. Click **Create VPC**.
6. Confirm the row appears with state **Available**.

> **Don't skip the Name tag.** You will select this VPC from a dropdown in 4 more steps — typing `es-lab-vpc` later is faster than matching IDs.

> **CIDR note.** `10.0.0.0/16` gives 65,536 addresses. If your account has a peering conflict (another VPC uses the same range), change to `172.31.0.0/16` and substitute that everywhere.

---

### Step 3: Create a Public Subnet

1. Left sidebar → **Subnets** → top-right **Create subnet**.
2. Fill in:
   - **VPC:** pick `es-lab-vpc`.
   - **Subnet name:** `es-lab-public-subnet`.
   - **Availability Zone:** any one in your region (e.g. `ap-southeast-1a`).
   - **IPv4 CIDR block:** `10.0.0.0/24` (256 addresses — more than enough for three nodes).
3. Click **Create subnet**.
4. Click the `es-lab-public-subnet` row, then **Actions** → **Edit subnet settings** → tick **Enable auto-assign public IPv4 address** → **Save**.

> **If you skipped the Name tag in Step 2**, the VPC dropdown will show VPC IDs. Match the ID you copied to the VPC you created.

---

### Step 4: Create and Attach an Internet Gateway

1. Left sidebar → **Internet Gateways** → **Create internet gateway**.
2. **Name:** `es-lab-igw` → **Create**.
3. The new IGW shows state **Detached**. Click it → top → **Actions** → **Attach to VPC** → pick `es-lab-vpc` → **Attach**.
4. Confirm state becomes **Attached**.

---

### Step 5: Create the Route Table and Associate the Subnet

1. Left sidebar → **Route Tables** → top-right **Create route table**.
2. Fill in:
   - **Name:** `es-lab-rt`
   - **VPC:** `es-lab-vpc`
3. Click **Create route table**.
4. Select the `es-lab-rt` row → bottom panel **Routes** tab → **Edit routes** → **Add route**:
   - **Destination:** `0.0.0.0/0`
   - **Target:** pick **Internet Gateway** → select `es-lab-igw`
   - **Save changes**.
5. Bottom panel → **Subnet associations** tab → **Edit subnet associations** → tick `es-lab-public-subnet` → **Save associations**.

At this point the network diagram on the VPC page should show `es-lab-vpc` → `es-lab-rt` → `es-lab-public-subnet` linked. If you see only `Main` route table linked, you skipped step 5.

---

### Step 6: Create the Key Pair

1. Top search bar → type `EC2` → **EC2**.
2. Left sidebar → **Network & Security** → **Key Pairs**.
3. Top-right → **Create key pair**.
4. Fill in:
   - **Name:** `es-lab-key`
   - **Key pair type:** `RSA`
   - **Private key file format:** `.pem`
5. Click **Create key pair**. Your browser downloads `es-lab-key.pem` automatically.

> **Keep that file safe.** It is the only credential that proves you own the instances. If you lose it, you cannot SSH in ever again — you have to terminate and relaunch.

> **PowerShell fix.** Windows sometimes saves `.pem` files without proper line endings. If you plan to SSH from your laptop later:
> ```powershell
> $pem = "$env:USERPROFILE\Downloads\es-lab-key.pem"
> icacls $pem /inheritance:r /grant:r "$($env:USERNAME):R"
> ```

---

### Step 7: Create the Security Group

1. Left sidebar → **Network & Security** → **Security Groups** → **Create security group**.
2. **Basic details:**
   - **Security group name:** `es-lab-sg`
   - **Description:** `Elasticsearch lab - SSH from my IP + browser, ES traffic inside VPC`
   - **VPC:** `es-lab-vpc`
3. **Inbound rules** — click **Add rule** once per row and fill in:

   **Row 1 — SSH from your laptop (PowerShell SSH path):**

   | Type | Protocol | Port range | Source   |
   |------|----------|------------|----------|
   | SSH  | TCP      | 22         | My IP    |

   **Row 2 — SSH from the browser (EC2 Instance Connect path).** This one is what stops the red `Error establishing SSH connection to your instance` banner — see the callout below.

   | Type | Protocol | Port range | Source                          |
   |------|----------|------------|---------------------------------|
   | SSH  | TCP      | 22         | Custom → start typing `com.amazonaws.<your-region>.ec2-instance-connect` and pick the prefix list |

   **Rows 3 & 4 — Elasticsearch inside the VPC:**

   | Type        | Protocol | Port range | Source              | Why                       |
   |-------------|----------|------------|---------------------|---------------------------|
   | Custom TCP  | TCP      | 9200       | Custom → `10.0.0.0/16` | ES HTTP between nodes     |
   | Custom TCP  | TCP      | 9300       | Custom → `10.0.0.0/16` | ES transport between nodes |

   > **Critical — the EC2 Instance Connect prefix list.** When you click **Connect** in the console and pick the **EC2 Instance Connect** tab, your browser opens an SSH session whose TCP connection **originates from AWS-owned relay IPs in your region**, not from your laptop. The `Source: My IP` rule does not cover those IPs, so the SG drops the connection before SSH even starts — that's exactly the red `Error establishing SSH connection to your instance. Try again later.` banner.
   >
   > The cleanest fix is to add a **second** SSH rule with `Source = com.amazonaws.<your-region>.ec2-instance-connect` (a managed prefix list AWS keeps up to date). AWS Console → type the prefix list name in the Source box and pick the row that matches your region. For `ap-southeast-1` the prefix list ID is `pl-0e4bc37e3d28b9d51` — but using the name is better because it self-updates.
   >
   > **Quick alternative** if the prefix list isn't loading: switch the Source dropdown to **Custom** and paste your region's IPs from [the AWS docs table](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-instance-connect-prerequisites.html#ec2-instance-connect-ip-ranges). For `ap-southeast-1` those are `3.0.5.32/29`, `13.212.68.192/27`, `13.213.21.42/31`, `13.213.21.44/31`, `13.213.21.46/31`, `13.213.21.48/31`, `13.213.21.50/31`, `13.213.21.52/31`, `13.213.21.54/31`, `13.213.21.56/31`, `13.213.21.58/31`. Pick one form — not both — or you'll carry an extra rule forever.

4. **Outbound rules:** leave default (All traffic, `0.0.0.0/0`).
5. Click **Create security group**. **Copy the Security group ID** from the top of the next page (`sg-xxxxxxxxxxxxxxxxx`).

> **If your VPC CIDR is different** (e.g. you used `172.31.0.0/16`), type that exact CIDR for the 9200 and 9300 rows instead of `10.0.0.0/16`. To check: VPC Dashboard → **Your VPCs** → click `es-lab-vpc` → **CIDR** column.

---

### Step 8: Find a Ubuntu 24.04 AMI

The AMI is the template for the instance's root volume.

**Recommended path (catalog search):**
1. EC2 Dashboard → left sidebar → **Images** → **AMIs**.
2. Change the search dropdown from **Owned by me** to **Public images**.
3. In the search bar, type `ubuntu-noble-24.04` and press Enter. The newest Ubuntu 24.04 AMI is at the top.
4. Pick the row whose **AMI name** starts with `ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-`. **Owner** must be `099720109477` (Canonical).
5. **Copy the AMI ID** (`ami-xxxxxxxxxxxxxxxxx`) — you will paste it three times in Step 9.

**Fallback (Quick Start tile inside the Launch wizard):**
- Skip this step. In Step 9, when you get to **Application and OS Images**, click the **Ubuntu** tile → pick **Ubuntu Server 24.04 LTS (HVM), SSD Volume Type**. The AMI ID is auto-populated.

> **Why one AMI for all three.** Same image = same Java + same ES version = no version skew when they form a cluster.

---

### Step 9: Launch Three Instances

Repeat this whole step three times, once per node. The only thing that changes is the **Name** in step 2.

For each of the three nodes (`es-master`, `es-data-1`, `es-data-2`):

1. EC2 Dashboard → **Instances** → top-right **Launch instances**.
2. **Name and tags:** `es-master` (then `es-data-1`, `es-data-2` on the next two launches).
3. **Application and OS Images:**
   - **Quick Start** → **Ubuntu** → select **Ubuntu Server 24.04 LTS** (HVM), SSD Volume Type, 64-bit (x86), OR
   - Paste the AMI ID from Step 8 in the search bar and press Enter.
4. **Instance type:** `t3.medium` (type in the filter box if needed).
5. **Key pair (login):** select `es-lab-key`.
6. **Network settings** — click **Edit**:
   - **VPC:** `es-lab-vpc`
   - **Subnet:** `es-lab-public-subnet`
   - **Auto-assign public IP:** `Enable`
   - **Firewall (security groups):** **Select existing security group** → pick `es-lab-sg`.
7. **Configure storage:** **20 GiB** gp3. (8 GiB is the default but is too tight for Java + Elasticsearch; bumping to 20 GiB removes a real failure mode.)
8. **Advanced details** → scroll to the bottom → expand **User data** → paste this **exact block**:

    ```bash
    #!/bin/bash
    set -euo pipefail
    export DEBIAN_FRONTEND=noninteractive

    # Kernel setting Elasticsearch requires. Setting it in user-data (not later)
    # means ES starts with it already applied — no manual fix-up needed.
    sysctl -w vm.max_map_count=262144

    apt-get update -y
    apt-get install -y openjdk-17-jdk-headless wget gnupg apt-transport-https ca-certificates

    wget -qO - https://artifacts.elastic.co/GPG-KEY-elasticsearch \
      | gpg --dearmor -o /usr/share/keyrings/elasticsearch-keyring.gpg
    echo "deb [signed-by=/usr/share/keyrings/elasticsearch-keyring.gpg] https://artifacts.elastic.co/packages/8.x/apt stable main" \
      > /etc/apt/sources.list.d/elastic-8.x.list
    apt-get update -y
    apt-get install -y elasticsearch

    # Pin the JVM heap so three t3.medium instances stay under their 4 GB RAM cap.
    printf -- '-Xms512m\n-Xmx512m\n' > /etc/elasticsearch/jvm.options.d/heap.options

    systemctl daemon-reload
    systemctl enable --now elasticsearch.service
    ```

9. Right-side **Summary** panel → **Launch instance**.
10. Wait for the green **Success** screen. Click the **instance ID** (`i-0xxxx...`) at the bottom — it opens the Instances list.

Repeat steps 1–10 twice more for `es-data-1` and `es-data-2`. The same user-data block goes in each.

> **What changed from a typical recipe.** The user-data adds `vm.max_map_count=262144` and bumps disk to 20 GiB. Both remove the two most common failure modes.

> **If user-data fails, no retry.** EC2 only runs user-data once on first boot. If you find a typo after launching, fix the script in user-data **and** terminate + relaunch (the next launch runs the corrected script). Editing `/var/lib/cloud/...` manually is not worth the time.

---

### Step 10: Wait for Instances and Grab Public IPs

1. EC2 → **Instances** — watch until all three rows show:
   - **Instance state = Running**
   - **Status check = 2/2 checks passed** (the green checkmark)

   This takes **3–5 minutes** total because the user-data script is installing Java and Elasticsearch in the background. Wait for `2/2 passed`, not just `Running` — that is the signal the OS finished booting **and** the instance is reachable.
2. Click each row, copy the **Public IPv4 address** from the **Details** tab into a notepad:

    ```
    es-master   i-0aaa...   54.123.45.67
    es-data-1   i-0bbb...   54.123.45.68
    es-data-2   i-0ccc...   54.123.45.69
    ```

> **Tip.** If your home IP changes during the lab (DHCP, VPN toggle), re-authorise SSH in `es-lab-sg`: Inbound rules → Edit → the **My IP** row → Source → My IP. Leave the EC2 Instance Connect rule alone.

---

### Step 11: Verify Elasticsearch on Each Node

You can verify from either the browser (EC2 Instance Connect) or your laptop (PowerShell SSH with `es-lab-key.pem`).

#### Option A — Browser SSH (EC2 Instance Connect)

For each of the three nodes:

1. Select the row → click **Connect** (top right).
2. **EC2 Instance Connect** tab is selected by default. **Username:** `ubuntu`. Click **Connect**.
3. A terminal opens. Paste this loop:

    ```bash
    for i in $(seq 1 60); do
      if curl -sf http://localhost:9200 >/dev/null 2>&1; then
        echo "Elasticsearch up after $i attempts"
        curl -s http://localhost:9200 | python3 -m json.tool
        exit 0
      fi
      sleep 5
    done
    echo "Elasticsearch did not come up in time"
    sudo journalctl -u elasticsearch --no-pager -n 100
    exit 1
    ```

4. **Expected output:** JSON ending with `"tagline": "You Know, for Search"` and `"name": "es-master"` (or `es-data-1`, `es-data-2`).

If you see `Elasticsearch did not come up in time`, paste:
```bash
sudo sysctl -w vm.max_map_count=262144
sudo systemctl restart elasticsearch
```
…then re-run the loop.

#### Option B — PowerShell SSH from your laptop (fallback)

If EC2 Instance Connect gives `Error establishing SSH connection to your instance` (intermittent on some accounts):

```powershell
ssh -i "$env:USERPROFILE\Downloads\es-lab-key.pem" ubuntu@<es-master-public-ip>
```
First-time prompt: type `yes` to accept the host fingerprint. Then run the same verification loop from Option A.

#### What good output looks like

```json
{
    "name": "es-master",
    "cluster_name": "elasticsearch",
    "cluster_uuid": "...",
    "version": {
        "number": "8.13.4",
        ...
    },
    "tagline": "You Know, for Search"
}
```

`cluster_name` will read `elasticsearch` (the default) on all three. They are not yet wired together; Lab 50 sets `cluster.name` and `discovery.seed_hosts` to form one cluster.

---

### Step 12: Confirm Cross-Node Network Reachability

From `es-master`, query `es-data-1`'s private IP on port 9200 (use EC2 Instance Connect or SSH). First grab the IP from the console:

EC2 → **Instances** → click `es-data-1` row → **Details** tab → copy **Private IPv4 address**.

Then in the SSH session on `es-master`, run:

```bash
curl -s "http://<es-data-1-private-ip>:9200" | python3 -m json.tool
```

Expected: the same JSON shape as Step 11, with `"name": "es-data-1"`. If the request times out, open EC2 console → **Security Groups** → `es-lab-sg` → **Inbound rules**, and confirm port 9200 source is `10.0.0.0/16` (or your VPC CIDR), not just your IP.

---

### Step 13: Stop or Terminate

**Stop** (saves most of the cost; data and software survive):

EC2 → **Instances** → tick all three rows → **Instance state** → **Stop instance**.

**Terminate** (deletes everything):

EC2 → **Instances** → tick all three rows → **Instance state** → **Terminate instance**.

If you intend to continue to Lab 50 immediately, **skip this step** — the next lab builds on the running cluster.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| VPC dropdown says "No VPC available" | Region has no default VPC and you haven't built one yet | Complete Steps 2–5 first, then refresh |
| AMI search returns zero rows | Wildcard search string too strict on the new console | Type `ubuntu-noble-24.04` instead of the full name |
| Instance stays on `1/2 checks passed` | User-data install is still running | Wait 2–3 more minutes. The 2/2 green check can lag the actual install by a minute |
| `Failed to connect to your instance` in EC2 Instance Connect | The browser SSH connects from AWS-owned relay IPs in your region, not from your laptop. `Source: My IP` doesn't cover those IPs, so the SG drops the packet before SSH starts | Add a second SSH (22) rule with `Source = com.amazonaws.<your-region>.ec2-instance-connect` (a managed prefix list). PowerShell SSH still works because that traffic really does come from your laptop |
| `Permission denied (publickey)` from PowerShell SSH | `.pem` doesn't match the instance's key pair | Confirm Key pair name in **Instances → Details** matches `es-lab-key`. If you recreated the key pair, terminate and relaunch with the new `.pem` |
| `curl http://localhost:9200` returns `connection refused` | Elasticsearch not running yet | `sudo systemctl status elasticsearch` — if `inactive`, `sudo systemctl start elasticsearch`; if `failed`, check `sudo journalctl -u elasticsearch --no-pager -n 50` |
| `vm.max_map_count` error in journalctl | Kernel setting too low (should not happen with the updated user-data) | `sudo sysctl -w vm.max_map_count=262144 && sudo systemctl restart elasticsearch`, then re-run the verification loop |
| Disk fills up during install (8 GiB default) | Java + Elasticsearch + system files exceed 8 GB | Terminate instance → relaunch with **20 GiB** storage in Step 9 |
| `apt-get update` hangs on `http://ports.ubuntu.com` | Mirror unreachable in this region | Re-launch in a different region, or retry the instance launch (the user-data will re-run on a fresh boot) |
| Cross-node curl times out in Step 12 | Security group missing the 9200/9300 rules on the VPC CIDR | EC2 → Security Groups → `es-lab-sg` → Inbound rules → confirm port 9200 and 9300 source is your VPC CIDR |

## Next Steps

In Lab 50 we edit `/etc/elasticsearch/elasticsearch.yml` on each instance to set a shared `cluster.name`, distinct `node.roles` per node, and `discovery.seed_hosts` so the three EC2 instances form one Elasticsearch cluster. Because Elasticsearch runs as a service, restart with `sudo systemctl restart elasticsearch` on each node after the config change.
