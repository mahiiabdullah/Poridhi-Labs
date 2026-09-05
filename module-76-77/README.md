# Module 76 — Elasticsearch Cluster Setup

This module walks through provisioning, installing, and configuring a multi-node Elasticsearch cluster on AWS EC2. By the end you have a three-node cluster with dedicated master, data, and ingest roles communicating over a private network.

## Labs

| Lab  | Title                  | What You Do                                                                 |
|------|------------------------|-----------------------------------------------------------------------------|
| [Lab 49](lab-49/README.md) | Cluster Provisioning  | Launch 3 EC2 instances (Ubuntu 22.04) and install Java 17 + Elasticsearch 8.x on each. |
| [Lab 50](lab-50/README.md) | Cluster Configuration | Assign node roles (master, data, ingest), configure discovery, form and verify the cluster. |

## Prerequisites

- An AWS account with permissions to create EC2 instances, security groups, and key pairs.
- AWS CLI v2 installed and configured (`aws configure`).
- An SSH client.

## Architecture Overview

The cluster runs on three `t3.medium` EC2 instances in the same VPC:

| Node       | Roles          | Purpose                                    |
|------------|----------------|--------------------------------------------|
| es-master  | `master`       | Dedicated cluster manager — no data stored |
| es-data-1  | `data`         | Stores index shards, runs queries          |
| es-data-2  | `data, ingest` | Stores shards and runs ingest pipelines    |
