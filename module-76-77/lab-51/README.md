# Lab 51: Flask–Elasticsearch Integration

**Module 77 — Connecting Flask with Elasticsearch**

This lab connects a Flask API to an Elasticsearch cluster using the official `elasticsearch-py` client. The API exposes two endpoints: `/index` to store documents and `/search` to query them. Both Elasticsearch and Flask run in Docker Compose so the entire stack starts with a single command.

## Architecture

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-76-77/lab-51/images/architecture.svg" alt="Lab 51 Architecture"></p>

## Concept

| Term                    | Description                                                                                            |
|-------------------------|--------------------------------------------------------------------------------------------------------|
| `elasticsearch-py`      | The official low-level Python client for Elasticsearch. Wraps the REST API and handles serialisation, retries, and node pooling. |
| Index                   | An Elasticsearch namespace that holds a collection of documents. Similar to a database table.          |
| Document                | A JSON object stored in an index. Each document has an auto-generated or user-supplied `_id`.          |
| Full-text search        | A query type that tokenises the search term and matches it against an inverted index built from stored document fields. |
| `match` query           | An Elasticsearch query that analyses the search string and returns documents whose fields contain matching terms. |
| `multi_match` query     | Like `match`, but searches across multiple fields in one call.                                         |

## What You Will Build

A two-container stack: a single-node Elasticsearch 8.x instance and a Flask API that connects to it via `elasticsearch-py`. The Flask API exposes three endpoints:

| Method | Path      | What it does                                                        |
|--------|-----------|---------------------------------------------------------------------|
| GET    | `/`       | Health check — returns the service name and the Elasticsearch cluster info. |
| POST   | `/index`  | Accepts a JSON document and indexes it into Elasticsearch.          |
| GET    | `/search` | Accepts a query parameter `q` and runs a full-text search.          |

## Step 1: Create the project directory

```bash
mkdir -p ~/lab-51/app
cd ~/lab-51
```

## Step 2: Confirm Docker and Docker Compose are installed

```bash
docker --version
docker compose version
```

Both commands must print a version string. If either fails, install Docker Engine before continuing.

## Step 3: Create the requirements file

```bash
cat > requirements.txt << 'EOF'
flask==3.0.3
elasticsearch==8.13.1
EOF
```

`elasticsearch==8.13.1` is the Python client that matches the Elasticsearch 8.x server image used in the Compose file.

## Step 4: Create the Dockerfile

```bash
cat > Dockerfile << 'EOF'
FROM python:3.12-slim

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
EOF
```

## Step 5: Create the docker-compose file

Two services: a single-node Elasticsearch instance and the Flask API. Security is disabled (`xpack.security.enabled=false`) so the Python client connects over plain HTTP without credentials — appropriate for a local lab, not for production.

```bash
cat > docker-compose.yml << 'EOF'
services:
  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.13.1
    container_name: lab51-elasticsearch
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false
      - ES_JAVA_OPTS=-Xms512m -Xmx512m
    ports:
      - "9200:9200"
    volumes:
      - es_data:/usr/share/elasticsearch/data
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:9200/_cluster/health || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 10

  web:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: lab51-web
    command: python app.py
    ports:
      - "5000:5000"
    volumes:
      - ./app:/code
    working_dir: /code
    depends_on:
      elasticsearch:
        condition: service_healthy

volumes:
  es_data:
EOF
```

`discovery.type=single-node` tells Elasticsearch not to wait for other nodes. The web service only starts after the healthcheck on `/_cluster/health` passes.

## Step 6: Create the Flask application

This is the core of the lab. The application connects to Elasticsearch at startup and exposes the `/index` and `/search` endpoints.

```bash
cat > app/app.py << 'EOF'
# app/app.py
from flask import Flask, jsonify, request
from elasticsearch import Elasticsearch

app = Flask(__name__)

# Point Flask at the Elasticsearch container by service name
es = Elasticsearch("http://elasticsearch:9200")

INDEX_NAME = "lab51-docs"


@app.route("/", methods=["GET"])
def health():
    """Health check — returns the Elasticsearch cluster info."""
    info = es.info()
    return jsonify({
        "service": "lab51-flask-elasticsearch",
        "status": "ok",
        "elasticsearch": {
            "cluster_name": info["cluster_name"],
            "version": info["version"]["number"],
        },
    })


@app.route("/index", methods=["POST"])
def index_document():
    """Index a JSON document into Elasticsearch.

    Expects a JSON body with at least a 'title' and 'content' field.
    An optional 'tags' field (list of strings) is supported.

    Example request body:
        {
            "title": "Flask and Elasticsearch",
            "content": "This lab teaches you how to connect Flask to ES.",
            "tags": ["flask", "elasticsearch", "python"]
        }
    """
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Request body must be JSON"}), 400

    title = body.get("title")
    content = body.get("content")

    if not title or not content:
        return jsonify({"error": "'title' and 'content' fields are required"}), 400

    doc = {
        "title": title,
        "content": content,
        "tags": body.get("tags", []),
    }

    result = es.index(index=INDEX_NAME, document=doc)

    return jsonify({
        "status": "indexed",
        "index": result["_index"],
        "id": result["_id"],
        "result": result["result"],
    }), 201


@app.route("/search", methods=["GET"])
def search_documents():
    """Full-text search across indexed documents.

    Query parameter:
        q — the search term (required)

    Example:
        GET /search?q=flask
    """
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Query parameter 'q' is required"}), 400

    search_body = {
        "query": {
            "multi_match": {
                "query": query,
                "fields": ["title^2", "content", "tags"],
            }
        }
    }

    result = es.search(index=INDEX_NAME, body=search_body)

    hits = []
    for hit in result["hits"]["hits"]:
        hits.append({
            "id": hit["_id"],
            "score": hit["_score"],
            "source": hit["_source"],
        })

    return jsonify({
        "query": query,
        "total": result["hits"]["total"]["value"],
        "hits": hits,
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
EOF
```

Key details:

- `Elasticsearch("http://elasticsearch:9200")` — the hostname `elasticsearch` resolves to the container because both services share the same Compose network.
- `multi_match` searches across `title`, `content`, and `tags` simultaneously. The `^2` boost on `title` ranks title matches higher.
- `es.index(index=INDEX_NAME, document=doc)` creates the index automatically on the first call. No manual index creation needed.

## Step 7: Verify the file layout

```bash
find ~/lab-51 -type f
```

Expected output:

```
/home/poridhian/lab-51/Dockerfile
/home/poridhian/lab-51/docker-compose.yml
/home/poridhian/lab-51/requirements.txt
/home/poridhian/lab-51/app/app.py
```

## Step 8: Build and start the stack

```bash
cd ~/lab-51
docker compose up -d --build
```

The first run downloads the Elasticsearch image (~800 MB) and builds the Flask image. Subsequent runs are instant.

Wait for both containers to be healthy:

```bash
docker compose ps
```

`lab51-elasticsearch` shows `healthy` and `lab51-web` shows `Up`.

## Step 9: Open the Load Balancer modal

Find the host IP:

```bash
hostname -I
```

Use the first IP as `LB_IP`. Open the Load Balancer modal.

Expose two ports:

| Enter IP | Enter Port |
|----------|------------|
| `LB_IP` | `5000` (Flask API) |
| `LB_IP` | `9200` (Elasticsearch direct) |

Click **Expose** for each. Copy the generated `.lb.poridhi.io` URL for port 5000 — the rest of the lab uses it as `<FLASK-LB-URL>`.

## Step 10: Check the health endpoint

```bash
curl <FLASK-LB-URL>/
```

Expected response:

```json
{
  "service": "lab51-flask-elasticsearch",
  "status": "ok",
  "elasticsearch": {
    "cluster_name": "docker-cluster",
    "version": "8.13.1"
  }
}
```

This confirms Flask is connected to Elasticsearch and the cluster is reachable.

## Step 11: Index the first document

Send a POST request with a JSON body containing `title`, `content`, and optionally `tags`:

```bash
curl -X POST <FLASK-LB-URL>/index \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Introduction to Elasticsearch",
    "content": "Elasticsearch is a distributed search and analytics engine built on Apache Lucene.",
    "tags": ["elasticsearch", "search", "lucene"]
  }'
```

Expected response:

```json
{
  "status": "indexed",
  "index": "lab51-docs",
  "id": "some-auto-generated-id",
  "result": "created"
}
```

The `result: created` confirms the document was stored. The `id` is auto-generated by Elasticsearch.

## Step 12: Index more sample data

Add a few more documents so the search endpoint has something to work with:

```bash
curl -X POST <FLASK-LB-URL>/index \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Flask Web Framework",
    "content": "Flask is a lightweight WSGI web application framework written in Python.",
    "tags": ["flask", "python", "web"]
  }'

curl -X POST <FLASK-LB-URL>/index \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Connecting Flask to Elasticsearch",
    "content": "The elasticsearch-py library provides a low-level client for connecting Python applications to an Elasticsearch cluster.",
    "tags": ["flask", "elasticsearch", "python", "integration"]
  }'

curl -X POST <FLASK-LB-URL>/index \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Docker Compose for Development",
    "content": "Docker Compose defines multi-container applications in a single YAML file. Services, networks, and volumes are declared together.",
    "tags": ["docker", "compose", "devops"]
  }'

curl -X POST <FLASK-LB-URL>/index \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Full-Text Search Fundamentals",
    "content": "Full-text search tokenises documents into terms and builds an inverted index. Queries are analysed the same way so terms match regardless of case or stemming.",
    "tags": ["search", "full-text", "inverted-index"]
  }'
```

Five documents are now indexed. Each `curl` returns `"result": "created"`.

## Step 13: Search for documents

Elasticsearch needs a second to refresh the index after writes. Wait a moment, then search:

```bash
curl "<FLASK-LB-URL>/search?q=flask"
```

Expected response:

```json
{
  "query": "flask",
  "total": 2,
  "hits": [
    {
      "id": "...",
      "score": 2.85,
      "source": {
        "title": "Flask Web Framework",
        "content": "Flask is a lightweight WSGI web application framework written in Python.",
        "tags": ["flask", "python", "web"]
      }
    },
    {
      "id": "...",
      "score": 1.92,
      "source": {
        "title": "Connecting Flask to Elasticsearch",
        "content": "The elasticsearch-py library provides a low-level client...",
        "tags": ["flask", "elasticsearch", "python", "integration"]
      }
    }
  ]
}
```

The `title` field has a `^2` boost, so documents with "flask" in the title score higher.

## Step 14: Try different search queries

Run a few more searches to see multi-field matching in action:

```bash
curl "<FLASK-LB-URL>/search?q=elasticsearch"
```

Returns documents mentioning Elasticsearch in title, content, or tags.

```bash
curl "<FLASK-LB-URL>/search?q=docker"
```

Returns the Docker Compose document.

```bash
curl "<FLASK-LB-URL>/search?q=python+web"
```

Returns documents mentioning either "python" or "web" across any field. Elasticsearch scores documents higher when more terms match.

## Step 15: Verify the data directly in Elasticsearch

You can also query Elasticsearch directly to confirm the indexed data:

```bash
curl http://localhost:9200/lab51-docs/_count
```

Expected output:

```json
{
  "count": 5,
  "_shards": {
    "total": 1,
    "successful": 1,
    "skipped": 0,
    "failed": 0
  }
}
```

List all documents in the index:

```bash
curl "http://localhost:9200/lab51-docs/_search?pretty&size=10"
```

This returns the raw Elasticsearch response with all five documents and their metadata.

## Step 16: Test error handling

Send a request without the required fields:

```bash
curl -X POST <FLASK-LB-URL>/index \
  -H "Content-Type: application/json" \
  -d '{}'
```

Expected response:

```json
{
  "error": "'title' and 'content' fields are required"
}
```

Search without a query parameter:

```bash
curl "<FLASK-LB-URL>/search"
```

Expected response:

```json
{
  "error": "Query parameter 'q' is required"
}
```

Both return `400 Bad Request` with a clear error message.

## Step 17: Stop the stack

```bash
cd ~/lab-51
docker compose down
```

Add `-v` to also remove the Elasticsearch data volume for a clean slate:

```bash
docker compose down -v
```

## Quick Run (Copy-Paste Safe)

A single-paste bundle that walks through Steps 1–14 end to end:

```bash
set -euo pipefail

# 1. Project structure
mkdir -p ~/lab-51/app && cd ~/lab-51

# 3. Requirements
cat > requirements.txt << 'EOF'
flask==3.0.3
elasticsearch==8.13.1
EOF

# 4. Dockerfile
cat > Dockerfile << 'EOF'
FROM python:3.12-slim
WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
EOF

# 5. Docker Compose
cat > docker-compose.yml << 'EOF'
services:
  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.13.1
    container_name: lab51-elasticsearch
    environment:
      - discovery.type=single-node
      - xpack.security.enabled=false
      - ES_JAVA_OPTS=-Xms512m -Xmx512m
    ports:
      - "9200:9200"
    volumes:
      - es_data:/usr/share/elasticsearch/data
    healthcheck:
      test: ["CMD-SHELL", "curl -sf http://localhost:9200/_cluster/health || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 10

  web:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: lab51-web
    command: python app.py
    ports:
      - "5000:5000"
    volumes:
      - ./app:/code
    working_dir: /code
    depends_on:
      elasticsearch:
        condition: service_healthy

volumes:
  es_data:
EOF

# 6. Flask app
cat > app/app.py << 'PYEOF'
from flask import Flask, jsonify, request
from elasticsearch import Elasticsearch

app = Flask(__name__)
es = Elasticsearch("http://elasticsearch:9200")
INDEX_NAME = "lab51-docs"


@app.route("/", methods=["GET"])
def health():
    info = es.info()
    return jsonify({
        "service": "lab51-flask-elasticsearch",
        "status": "ok",
        "elasticsearch": {
            "cluster_name": info["cluster_name"],
            "version": info["version"]["number"],
        },
    })


@app.route("/index", methods=["POST"])
def index_document():
    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "Request body must be JSON"}), 400
    title = body.get("title")
    content = body.get("content")
    if not title or not content:
        return jsonify({"error": "'title' and 'content' fields are required"}), 400
    doc = {"title": title, "content": content, "tags": body.get("tags", [])}
    result = es.index(index=INDEX_NAME, document=doc)
    return jsonify({
        "status": "indexed",
        "index": result["_index"],
        "id": result["_id"],
        "result": result["result"],
    }), 201


@app.route("/search", methods=["GET"])
def search_documents():
    query = request.args.get("q", "").strip()
    if not query:
        return jsonify({"error": "Query parameter 'q' is required"}), 400
    result = es.search(
        index=INDEX_NAME,
        body={"query": {"multi_match": {"query": query, "fields": ["title^2", "content", "tags"]}}},
    )
    hits = [{"id": h["_id"], "score": h["_score"], "source": h["_source"]} for h in result["hits"]["hits"]]
    return jsonify({"query": query, "total": result["hits"]["total"]["value"], "hits": hits})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
PYEOF

# 8. Build and start
docker compose up -d --build

echo "Waiting for Elasticsearch health..."
until curl -sf http://localhost:9200/_cluster/health > /dev/null 2>&1; do sleep 3; done
echo "Elasticsearch is up."

echo "Waiting for Flask..."
until curl -sf http://localhost:5000/ > /dev/null 2>&1; do sleep 2; done
echo "Flask is up."

# 10. Health check
echo "--- Health ---"
curl -s http://localhost:5000/

# 11-12. Index sample data
echo ""
echo "--- Indexing sample data ---"
for doc in \
  '{"title":"Introduction to Elasticsearch","content":"Elasticsearch is a distributed search and analytics engine built on Apache Lucene.","tags":["elasticsearch","search","lucene"]}' \
  '{"title":"Flask Web Framework","content":"Flask is a lightweight WSGI web application framework written in Python.","tags":["flask","python","web"]}' \
  '{"title":"Connecting Flask to Elasticsearch","content":"The elasticsearch-py library provides a low-level client for connecting Python applications to an Elasticsearch cluster.","tags":["flask","elasticsearch","python","integration"]}' \
  '{"title":"Docker Compose for Development","content":"Docker Compose defines multi-container applications in a single YAML file.","tags":["docker","compose","devops"]}' \
  '{"title":"Full-Text Search Fundamentals","content":"Full-text search tokenises documents into terms and builds an inverted index.","tags":["search","full-text","inverted-index"]}'; do
  curl -s -X POST http://localhost:5000/index -H "Content-Type: application/json" -d "$doc"
  echo ""
done

# Wait for Elasticsearch to refresh
sleep 2

# 13-14. Search
echo "--- Search: flask ---"
curl -s "http://localhost:5000/search?q=flask"
echo ""

echo "--- Search: elasticsearch ---"
curl -s "http://localhost:5000/search?q=elasticsearch"
echo ""

echo "--- Search: docker ---"
curl -s "http://localhost:5000/search?q=docker"
echo ""

echo "=== Lab 51 complete ==="
```

## Next Steps

This lab establishes the Flask + Elasticsearch integration pattern. Follow-up work could include:

- **Custom index mappings** — define explicit field types and analysers before indexing.
- **Pagination** — add `from` and `size` parameters to the search endpoint.
- **Bulk indexing** — use `es.bulk()` to index thousands of documents in a single API call.
