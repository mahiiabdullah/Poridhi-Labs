# Lab 49: Cluster Provisioning

**Module 76 — Elasticsearch Cluster Setup**

This lab provisions three EC2 instances and installs Java 17 and Elasticsearch 8.x on each one. By the end you have three identical nodes ready to be configured into a cluster in Lab 50.

## Architecture

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-76-77/lab-49/images/architecture.png" alt="Lab 49 Architecture"></p>

## Concept

| Term                | Description                                                                                           |
|---------------------|-------------------------------------------------------------------------------------------------------|
| EC2 Instance        | A virtual machine in AWS that runs your operating system and applications.                            |
| Security Group      | A virtual firewall that controls which ports are open and who can reach them.                          |
| Elasticsearch       | A distributed search and analytics engine that stores data across a cluster of nodes.                 |
| JVM (Java 17)       | The runtime Elasticsearch runs on. Elasticsearch 8.x bundles its own JDK but the host JDK is useful for tooling. |
| Port 9200           | The HTTP port Elasticsearch exposes for REST API requests (indexing, searching, cluster health).       |
| Port 9300           | The transport port nodes use to talk to each other for cluster coordination and data replication.      |

## What You Will Build

Three EC2 instances (Ubuntu 22.04, `t3.medium`) in the same VPC and security group. Each instance has OpenJDK 17 and Elasticsearch 8.x installed and verified. The Elasticsearch service is installed but **not yet started** — Lab 50 handles configuration and cluster formation.

## Step 1: Create a key pair

If you do not already have an EC2 key pair, create one. This key is used to SSH into every instance.

```bash
aws ec2 create-key-pair \
  --key-name es-cluster-key \
  --key-type ed25519 \
  --query 'KeyMaterial' \
  --output text > es-cluster-key.pem

chmod 400 es-cluster-key.pem
```

If you already have a key pair, skip this step and substitute your key name in later commands.

## Step 2: Create a security group

The group opens three ports: SSH for management, 9200 for the Elasticsearch HTTP API, and 9300 for node-to-node transport.

```bash
SG_ID=$(aws ec2 create-security-group \
  --group-name es-cluster-sg \
  --description "Elasticsearch cluster - SSH, HTTP API, transport" \
  --query 'GroupId' \
  --output text)

echo "Security Group ID: $SG_ID"
```

Open SSH from anywhere (restrict the CIDR in production):

```bash
aws ec2 authorize-security-group-ingress \
  --group-id $SG_ID \
  --protocol tcp \
  --port 22 \
  --cidr 0.0.0.0/0
```

Open the Elasticsearch HTTP API:

```bash
aws ec2 authorize-security-group-ingress \
  --group-id $SG_ID \
  --protocol tcp \
  --port 9200 \
  --cidr 0.0.0.0/0
```

Open transport traffic between nodes in the same security group:

```bash
aws ec2 authorize-security-group-ingress \
  --group-id $SG_ID \
  --protocol tcp \
  --port 9300 \
  --source-group $SG_ID
```

Verify all three rules exist:

```bash
aws ec2 describe-security-groups \
  --group-ids $SG_ID \
  --query 'SecurityGroups[0].IpPermissions' \
  --output table
```

## Step 3: Look up the Ubuntu 22.04 AMI

The AMI ID changes per region. This command finds the latest official Canonical AMI for your current region:

```bash
AMI_ID=$(aws ssm get-parameters \
  --names /aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query 'Parameters[0].Value' \
  --output text)

echo "AMI ID: $AMI_ID"
```

If the SSM lookup fails, go to the [Ubuntu AMI Locator](https://cloud-images.ubuntu.com/locator/ec2/) and pick the `22.04 LTS amd64 hvm:ebs-gp3` AMI for your region.

## Step 4: Launch three EC2 instances

Launch three `t3.medium` instances (2 vCPU, 4 GB RAM — the minimum recommended for Elasticsearch).

**Instance 1 — es-master:**

```bash
INSTANCE_1=$(aws ec2 run-instances \
  --image-id $AMI_ID \
  --instance-type t3.medium \
  --key-name es-cluster-key \
  --security-group-ids $SG_ID \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=es-master}]' \
  --query 'Instances[0].InstanceId' \
  --output text)

echo "es-master: $INSTANCE_1"
```

**Instance 2 — es-data-1:**

```bash
INSTANCE_2=$(aws ec2 run-instances \
  --image-id $AMI_ID \
  --instance-type t3.medium \
  --key-name es-cluster-key \
  --security-group-ids $SG_ID \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=es-data-1}]' \
  --query 'Instances[0].InstanceId' \
  --output text)

echo "es-data-1: $INSTANCE_2"
```

**Instance 3 — es-data-2:**

```bash
INSTANCE_3=$(aws ec2 run-instances \
  --image-id $AMI_ID \
  --instance-type t3.medium \
  --key-name es-cluster-key \
  --security-group-ids $SG_ID \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=es-data-2}]' \
  --query 'Instances[0].InstanceId' \
  --output text)

echo "es-data-2: $INSTANCE_3"
```

## Step 5: Wait for the instances to reach `running` state

```bash
aws ec2 wait instance-running \
  --instance-ids $INSTANCE_1 $INSTANCE_2 $INSTANCE_3

echo "All three instances are running."
```

## Step 6: Collect the public IPs

```bash
aws ec2 describe-instances \
  --instance-ids $INSTANCE_1 $INSTANCE_2 $INSTANCE_3 \
  --query 'Reservations[].Instances[].[Tags[?Key==`Name`].Value|[0],PublicIpAddress,PrivateIpAddress]' \
  --output table
```

Sample output:

```
-------------------------------------------
|           DescribeInstances             |
+------------+----------------+-----------+
| es-master  | 54.210.11.22   | 10.0.1.10 |
| es-data-1  | 54.210.33.44   | 10.0.1.11 |
| es-data-2  | 54.210.55.66   | 10.0.1.12 |
+------------+----------------+-----------+
```

Save these IPs — you will need the **private IPs** in Lab 50 for `elasticsearch.yml` and the **public IPs** for SSH.

```bash
IP_MASTER=<es-master-public-ip>
IP_DATA1=<es-data-1-public-ip>
IP_DATA2=<es-data-2-public-ip>
```

Replace the placeholders with the real IPs from the table above.

## Step 7: SSH into es-master and update packages

```bash
ssh -i es-cluster-key.pem ubuntu@$IP_MASTER
```

Once connected:

```bash
sudo apt update && sudo apt upgrade -y
```

## Step 8: Install Java 17

Elasticsearch 8.x ships with a bundled JDK, but installing a system JDK gives you access to standard Java tools (`jps`, `jstack`, `jmap`) for debugging:

```bash
sudo apt install -y openjdk-17-jdk
```

Verify the installation:

```bash
java -version
```

Expected output:

```
openjdk version "17.0.x" 2024-xx-xx
OpenJDK Runtime Environment (build 17.0.x+x-Ubuntu-...)
OpenJDK 64-Bit Server VM (build 17.0.x+x-Ubuntu-..., mixed mode, sharing)
```

## Step 9: Import the Elasticsearch GPG key

```bash
wget -qO - https://artifacts.elastic.co/GPG-KEY-elasticsearch | \
  sudo gpg --dearmor -o /usr/share/keyrings/elasticsearch-keyring.gpg
```

## Step 10: Add the Elasticsearch APT repository

```bash
echo "deb [signed-by=/usr/share/keyrings/elasticsearch-keyring.gpg] https://artifacts.elastic.co/packages/8.x/apt stable main" | \
  sudo tee /etc/apt/sources.list.d/elastic-8.x.list
```

Update the package index so APT sees the new repo:

```bash
sudo apt update
```

## Step 11: Install Elasticsearch

```bash
sudo apt install -y elasticsearch
```

The installer prints a security auto-configuration block with a generated password and enrollment token. **Copy and save this output** — it contains the superuser password. For this lab series we will disable security in Lab 50 to keep the cluster setup simple.

Verify the package installed correctly:

```bash
dpkg -l elasticsearch
```

Expected output (version may differ):

```
ii  elasticsearch  8.17.x  amd64  Distributed RESTful search engine built for the cloud
```

Check that the configuration directory exists:

```bash
ls /etc/elasticsearch/
```

Expected files:

```
elasticsearch.yml  jvm.options  jvm.options.d  log4j2.properties  role_mapping.yml  roles.yml  users  users_roles
```

**Do not start Elasticsearch yet.** Lab 50 edits `elasticsearch.yml` first.

## Step 12: Exit the first node

```bash
exit
```

## Step 13: Install Java and Elasticsearch on es-data-1

SSH into the second instance:

```bash
ssh -i es-cluster-key.pem ubuntu@$IP_DATA1
```

Run the same installation steps (8–11) as a single block:

```bash
sudo apt update && sudo apt upgrade -y

sudo apt install -y openjdk-17-jdk

wget -qO - https://artifacts.elastic.co/GPG-KEY-elasticsearch | \
  sudo gpg --dearmor -o /usr/share/keyrings/elasticsearch-keyring.gpg

echo "deb [signed-by=/usr/share/keyrings/elasticsearch-keyring.gpg] https://artifacts.elastic.co/packages/8.x/apt stable main" | \
  sudo tee /etc/apt/sources.list.d/elastic-8.x.list

sudo apt update

sudo apt install -y elasticsearch
```

Verify:

```bash
java -version
dpkg -l elasticsearch
```

Exit:

```bash
exit
```

## Step 14: Install Java and Elasticsearch on es-data-2

SSH into the third instance:

```bash
ssh -i es-cluster-key.pem ubuntu@$IP_DATA2
```

Run the identical installation block:

```bash
sudo apt update && sudo apt upgrade -y

sudo apt install -y openjdk-17-jdk

wget -qO - https://artifacts.elastic.co/GPG-KEY-elasticsearch | \
  sudo gpg --dearmor -o /usr/share/keyrings/elasticsearch-keyring.gpg

echo "deb [signed-by=/usr/share/keyrings/elasticsearch-keyring.gpg] https://artifacts.elastic.co/packages/8.x/apt stable main" | \
  sudo tee /etc/apt/sources.list.d/elastic-8.x.list

sudo apt update

sudo apt install -y elasticsearch
```

Verify:

```bash
java -version
dpkg -l elasticsearch
```

Exit:

```bash
exit
```

## Step 15: Verify all three nodes

Run a quick remote check from your local machine to confirm every node has both Java and Elasticsearch:

```bash
for IP in $IP_MASTER $IP_DATA1 $IP_DATA2; do
  echo "--- $IP ---"
  ssh -i es-cluster-key.pem -o StrictHostKeyChecking=no ubuntu@$IP \
    "java -version 2>&1 | head -1 && dpkg -l elasticsearch | grep elasticsearch"
  echo ""
done
```

Expected output for each node:

```
--- 54.210.11.22 ---
openjdk version "17.0.x" 2024-xx-xx
ii  elasticsearch  8.17.x  amd64  Distributed RESTful search engine built for the cloud

--- 54.210.33.44 ---
openjdk version "17.0.x" 2024-xx-xx
ii  elasticsearch  8.17.x  amd64  Distributed RESTful search engine built for the cloud

--- 54.210.55.66 ---
openjdk version "17.0.x" 2024-xx-xx
ii  elasticsearch  8.17.x  amd64  Distributed RESTful search engine built for the cloud
```

All three nodes now have Java 17 and Elasticsearch 8.x installed. The Elasticsearch service is stopped on all nodes — configuration happens next.

## Next Steps

Lab 50 configures `elasticsearch.yml` on each node to assign distinct roles (master, data, ingest), set the cluster name, configure discovery, and form the three nodes into a single running cluster.
