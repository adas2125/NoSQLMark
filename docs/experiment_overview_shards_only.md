# Mambo mongod CPU shard-only experiment

Three-shard, fixed-replica variant of the August 29, 2026 mongod CPU experiment.
Each execution writes to its own timestamped directory under
`experiment-artifacts/runs/`.

## Safety

| Marker | Effect |
|---|---|
| `[READ ONLY]` | Inspection only. |
| `[LOCAL CHANGE]` | Changes files on `node0` only. |
| `[WARNING: POD CHANGE]` | Changes files or starts processes inside a pod. |
| `[WARNING: HOST CHANGE]` | Installs software on `node0`. |
| `[WARNING: CLUSTER CHANGE]` | Creates or changes Kubernetes resources. |
| `[WARNING: DATABASE CHANGE]` | Writes MongoDB data or metadata. |

Namespace: `mambo-mongod-cpu`. Do not modify `foxtrot`, shared
`monitoring`, or the control-plane node.

## Run directory and terminals

Run this once in **Main** before any other command:

`[LOCAL CHANGE]`

```bash
cd /users/adas2125/Autoscaling
export RUN_ID="$(date -u +%Y%m%dT%H%M%S%NZ)"
export RUN_DIR="$PWD/experiment-artifacts/runs/$RUN_ID"
mkdir -p "$RUN_DIR"
{
  printf "export RUN_ID=%q\n" "$RUN_ID"
  printf "export RUN_DIR=%q\n" "$RUN_DIR"
} | tee experiment-artifacts/current-run-env.sh > "$RUN_DIR/run-env.sh"
printf "Run ID: %s\nRun directory: %s\n" "$RUN_ID" "$RUN_DIR"
```

In every additional terminal, load the same run:

```bash
cd /users/adas2125/Autoscaling
source experiment-artifacts/current-run-env.sh
printf "Using %s at %s\n" "$RUN_ID" "$RUN_DIR"
```

Terminal roles:

| Terminal | Use |
|---|---|
| **Main** | Steps 1-7, autoscaler manifest/application, YCSB workload, evidence, plot |
| **Prometheus** | Port-forward in Step 3; leave running through result collection |
| **Controller** | Controller log follower in Step 8 |
| **Kubernetes** | Pod watcher in Step 8 |
| **Status** (optional) | Live read-only status commands while Main runs YCSB |

Unless a block names another terminal, run it in **Main**. Timestamping protects
local artifacts only; Kubernetes resource names are reused, so the clean-state
preflight is still required.

## Recorded setup

- Mambo commit: `68314fe664c4a82883ee286d192c77f273525569`
- Operator image: `docker.io/b00611024/mongodb-autoscaler:test`
- Reference `:test` digest on August 29: `sha256:d9fbf2cbdade42f828094c8e815f5c70bc74c605efff5ff8c63b0793c739d20d`
- MongoDB chart: `bitnami/mongodb-sharded` `9.4.12`
- MongoDB image: `bitnamilegacy/mongodb-sharded:8.0.13-debian-12-r0`
- YCSB commit retrieved: `66302f301b13f60d4bcb2f29f478586bb1d6f2e0`
- Workload: 1,000,000 records; one continuous 8,000,000-read invocation;
  10 threads; Zipfian; `readPreference=nearest`; no `-target`
- Topology goal: `1 shard x 1 member -> 2 shards x 1 member -> 3 shards x 1 member`
- Replica scaling: disabled by setting both replica bounds to `1`
- CPU scale-up boundary: `60% + 10% = 70%` of the `0.5`-CPU request
- YCSB node: `i-063b793db694be24c` (`m5a.large`)

## 1. Tools and preflight

`[WARNING: HOST CHANGE]` Only if Docker/Buildx is missing:

```bash
sudo apt-get update
sudo apt-get install docker.io docker-buildx
```

`[READ ONLY]`

```bash
cd /users/adas2125/Autoscaling

git rev-parse HEAD
git status --short
kubectl version --client
helm version --short
sudo docker version --format '{{.Client.Version}}'
jq --version

kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig config current-context
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig get nodes \
  -L node.kubernetes.io/instance-type \
  -L topology.kubernetes.io/zone
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig get storageclass
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig get namespace mambo-mongod-cpu
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig get statefulsets -A \
  -l app.kubernetes.io/name=mongodb-sharded
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig get crd \
  mongodautoscalers.autoscaler.mongodb.io
helm --kubeconfig /users/adas2125/.kube/amit.kubeconfig list -A
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig get pods -n foxtrot -o wide
```

Before a clean run: experiment namespace, MongoDB StatefulSets, and Mambo CRD
absent. Existing `kps` and Foxtrot remain untouched.

`[LOCAL CHANGE]`

```bash
test -n "$RUN_ID" && test -d "$RUN_DIR"
git rev-parse HEAD | tee "$RUN_DIR/mambo-git-commit.txt"
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig version -o yaml \
  > "$RUN_DIR/kubernetes-version.yaml"
helm version > "$RUN_DIR/helm-version.txt"
sudo docker version > "$RUN_DIR/docker-version.txt"

helm repo add bitnami https://charts.bitnami.com/bitnami --force-update
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm repo update
helm search repo prometheus-community/prometheus-node-exporter --versions
printf '%s\n' '4.56.1' \
  | tee "$RUN_DIR/node-exporter-chart-version.txt"
```

## 2. Placement override

`[LOCAL CHANGE]`

```bash
tee "$RUN_DIR/placement-values.yaml" >/dev/null <<'EOF'
shardsvr:
  dataNode:
    affinity:
      nodeAffinity:
        requiredDuringSchedulingIgnoredDuringExecution:
          nodeSelectorTerms:
            - matchExpressions:
                - key: kubernetes.io/hostname
                  operator: In
                  values:
                    - i-00ecc4734204a6c6e
                    - i-0f4a2ec2693d23bd5
                    - i-091443c764376d8ba
                    - i-0c4d45abf9869abf0

mongos:
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - key: node.kubernetes.io/instance-type
                operator: In
                values: [m5a.large]

configsvr:
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - key: node.kubernetes.io/instance-type
                operator: In
                values: [m5a.large]
EOF

helm template my-mongodb-sharded bitnami/mongodb-sharded \
  --version 9.4.12 \
  --namespace mambo-mongod-cpu \
  -f MongoDB/result/mongod_cpu_exp/values.yaml \
  -f "$RUN_DIR/placement-values.yaml" \
  > "$RUN_DIR/rendered-mongodb.yaml"
```

## 3. Namespace and node-exporter

`[WARNING: CLUSTER CHANGE]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  create namespace mambo-mongod-cpu
```

**Prometheus terminal** — `[READ ONLY: TEMPORARY CONNECTION]`. Keep running:

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  port-forward -n monitoring svc/prometheus-operated 9090:9090
```

`[LOCAL CHANGE]`

```bash
tee "$RUN_DIR/node-exporter-values.yaml" >/dev/null <<'EOF'
prometheus:
  monitor:
    enabled: true

affinity:
  nodeAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
        - matchExpressions:
            - key: kubernetes.io/hostname
              operator: In
              values:
                - i-00ecc4734204a6c6e
                - i-0f4a2ec2693d23bd5
                - i-091443c764376d8ba
                - i-0c4d45abf9869abf0
EOF
```

`[WARNING: CLUSTER CHANGE]`

```bash
helm install mambo-node-exporter prometheus-community/prometheus-node-exporter \
  --version 4.56.1 \
  --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  --namespace mambo-mongod-cpu \
  -f "$RUN_DIR/node-exporter-values.yaml"
```

`[READ ONLY]`

```bash
helm --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  status mambo-node-exporter -n mambo-mongod-cpu
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get daemonsets,pods,services,servicemonitors -n mambo-mongod-cpu -o wide

curl -sG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=count by(instance) (node_cpu_seconds_total{mode="iowait",instance=~"172.20.254.176:9100|172.20.211.60:9100|172.20.202.200:9100|172.20.102.100:9100"})' \
  | jq -r '.data.result[] | [.metric.instance, .value[1]] | @tsv'
```

Expected: four endpoints, four CPU series each.

## 4. MongoDB and exporter

`[WARNING: CLUSTER CHANGE]`

```bash
helm install my-mongodb-sharded bitnami/mongodb-sharded \
  --version 9.4.12 \
  --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  --namespace mambo-mongod-cpu \
  -f MongoDB/result/mongod_cpu_exp/values.yaml \
  -f "$RUN_DIR/placement-values.yaml"
```

`[READ ONLY]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get pods -n mambo-mongod-cpu -o wide -w
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get statefulsets,pods,pvc -n mambo-mongod-cpu -o wide
```

Continue when config servers, three mongos pods, and shard0 data-0 are ready.

`[LOCAL CHANGE]`

```bash
cp MongoDB/mongodb-exporter/mongodb-exporter-servicemonitor.yaml \
  "$RUN_DIR/mongodb-exporter-servicemonitor.yaml"
sed -i 's/      - default/      - mambo-mongod-cpu/' \
  "$RUN_DIR/mongodb-exporter-servicemonitor.yaml"
```

`[WARNING: CLUSTER CHANGE]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  apply -n mambo-mongod-cpu \
  -f MongoDB/mongodb-exporter/mongodb-exporter-deployment.yaml \
  -f MongoDB/mongodb-exporter/mongodb-exporter-service.yaml
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  apply -f "$RUN_DIR/mongodb-exporter-servicemonitor.yaml"
```

`[READ ONLY]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  rollout status deployment/mongodb-exporter -n mambo-mongod-cpu --timeout=300s

curl -sG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=up{namespace="mambo-mongod-cpu",service="mongodb-exporter"}' \
  | jq -r '.data.result[] | [.metric.instance, .value[1]] | @tsv'
curl -sG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=kube_pod_container_resource_requests{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard.*-data-.*",resource="cpu"}' \
  | jq -r '.data.result[] | [.metric.pod, .metric.container, .value[1]] | @tsv'
curl -sG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=container_cpu_usage_seconds_total{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard.*-data-.*",container!="",container!="POD"}' \
  | jq -r '.data.result[] | [.metric.pod, .metric.container, .value[1]] | @tsv'
```

## 5. Mambo operator

Use the developer-provided manifest directly. It references the public `:test`
tag. Record what that mutable tag resolves to for this run. Pulling it does not
modify the public image. The digest inspection is informational only: the
deployment still uses `:test`; it does not substitute or pin the digest.

`[READ ONLY: EXTERNAL REGISTRY]`

```bash
sudo docker buildx imagetools inspect \
  docker.io/b00611024/mongodb-autoscaler:test \
  | tee "$RUN_DIR/operator-image-inspect.txt"
```

`[WARNING: CLUSTER CHANGE]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  apply -f MongoDBOperator/dist/install.yaml
```

`[READ ONLY]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  rollout status deployment/mongodboperator-controller-manager \
  -n mongodboperator-system --timeout=300s
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get deployment,pods -n mongodboperator-system -o wide
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get crd mongodautoscalers.autoscaler.mongodb.io
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  logs -n mongodboperator-system \
  deployment/mongodboperator-controller-manager -c manager --tail=100
```

## 6. YCSB pod and build

`[WARNING: CLUSTER CHANGE]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig run ycsb-test \
  --namespace mambo-mongod-cpu \
  --image=maven:3.8-openjdk-11 \
  --restart=Never \
  --overrides='{"spec":{"nodeSelector":{"kubernetes.io/hostname":"i-063b793db694be24c"}}}' \
  --command -- sleep infinity
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  wait --for=condition=Ready pod/ycsb-test \
  -n mambo-mongod-cpu --timeout=300s
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get pod ycsb-test -n mambo-mongod-cpu -o wide
```

`[WARNING: POD CHANGE]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -n mambo-mongod-cpu ycsb-test -- \
  git clone https://github.com/brianfrankcooper/YCSB.git /YCSB
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -n mambo-mongod-cpu ycsb-test -- \
  git -C /YCSB checkout 66302f301b13f60d4bcb2f29f478586bb1d6f2e0
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -n mambo-mongod-cpu ycsb-test -- git -C /YCSB rev-parse HEAD \
  | tee "$RUN_DIR/ycsb-git-commit.txt"
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -n mambo-mongod-cpu ycsb-test -- git -C /YCSB log -1 --oneline

kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig cp \
  MongoDB/result/mongod_cpu_exp/workloadr \
  mambo-mongod-cpu/ycsb-test:/YCSB/workloads/workloadr
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -n mambo-mongod-cpu ycsb-test -- bash -lc \
  'sed -i "s#http://www.allanbank.com/repo/#https://www.allanbank.com/repo/#" /YCSB/mongodb/pom.xml'
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -n mambo-mongod-cpu ycsb-test -- git -C /YCSB diff -- mongodb/pom.xml \
  | tee "$RUN_DIR/ycsb-https-repository.patch"
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -it -n mambo-mongod-cpu ycsb-test -- bash -lc \
  'cd /YCSB && mvn -U -pl site.ycsb:mongodb-binding -am clean package -DskipTests'
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -it -n mambo-mongod-cpu ycsb-test -- bash -lc \
  'cd /YCSB && mvn -pl site.ycsb:core dependency:copy-dependencies -DincludeScope=runtime -DoutputDirectory=target/dependency'
```

HTTPS edit: Maven legacy repository fix. Dependency step: HTrace and other
runtime jars required by `bin/ycsb.sh`.

## 7. Load and shard

`[WARNING: DATABASE CHANGE]` Uses 10 load threads for a faster load.

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -it -n mambo-mongod-cpu ycsb-test -- env RUN_ID="$RUN_ID" bash -lc '
    cd /YCSB
    mkdir -p "logs/$RUN_ID"
    bin/ycsb.sh load mongodb -s \
      -P workloads/workloadr \
      -p "mongodb.url=mongodb://root:mongodb123@my-mongodb-sharded:27017/ycsb?authSource=admin" \
      -threads 10 \
      2>&1 | tee "logs/$RUN_ID/load.log"
  '
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig cp \
  "mambo-mongod-cpu/ycsb-test:/YCSB/logs/$RUN_ID/load.log" \
  "$RUN_DIR/load.log"
```

Expected: 1,000,000 successful inserts.

Uses 10 load threads as requested. This should load the same 1,000,000 records
faster than the single-thread load used on August 29. Wait 10 minutes afterward
so the five-minute metrics window clears before activating Mambo.

The load creates 1,000,000 records. Step 9 overrides only `operationcount`,
causing 8,000,000 reads against those records.

`[READ ONLY: STABILIZATION]`

```bash
sleep 600
```

`[WARNING: CLUSTER AND DATABASE CHANGE]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig run mongodb-shell \
  --namespace mambo-mongod-cpu \
  --rm -it \
  --restart=Never \
  --image=docker.io/bitnamilegacy/mongodb-sharded:8.0.13-debian-12-r0 \
  --overrides='{"spec":{"nodeSelector":{"kubernetes.io/hostname":"i-063b793db694be24c"}}}' \
  --command -- mongosh \
  'mongodb://root:mongodb123@my-mongodb-sharded:27017/admin?authSource=admin'
```

Inside `mongosh`:

```javascript
sh.enableSharding("ycsb")
sh.shardCollection("ycsb.usertable", { _id: 1 })
sh.status()
db.getSiblingDB("ycsb").usertable.countDocuments()
db.getSiblingDB("config").collections.findOne(
  { _id: "ycsb.usertable" },
  { _id: 1, key: 1 }
)
exit
```

`[READ ONLY]`

```bash
curl -sG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=mongodb_collstats_latencyStats_reads_ops{database="ycsb",collection="usertable"}' \
  | jq -r '.data.result[] | [.metric.shard, .value[1]] | @tsv'
curl -sG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=mongodb_collstats_latencyStats_writes_ops{database="ycsb",collection="usertable"}' \
  | jq -r '.data.result[] | [.metric.shard, .value[1]] | @tsv'
```

## 8. Observers and autoscaler

**Controller terminal** — `[READ ONLY]`:

```bash
cd /users/adas2125/Autoscaling
source experiment-artifacts/current-run-env.sh
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  logs -n mongodboperator-system \
  deployment/mongodboperator-controller-manager \
  -c manager --timestamps -f \
  | tee "$RUN_DIR/controller.log"
```

**Kubernetes terminal** — `[READ ONLY]`:

```bash
cd /users/adas2125/Autoscaling
source experiment-artifacts/current-run-env.sh
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get pods -n mambo-mongod-cpu -o wide -w \
  | tee "$RUN_DIR/kubernetes-watch.log"
```

**Main terminal** — `[LOCAL CHANGE]`:

```bash
cp MongoDB/result/mongod_cpu_exp/autoscaler_v1alpha1_mongodautoscaler.yaml \
  "$RUN_DIR/mongodautoscaler.yaml"
sed -i \
  's/    namespace: default/    namespace: mambo-mongod-cpu/' \
  "$RUN_DIR/mongodautoscaler.yaml"
sed -i \
  '/  name: mongodautoscaler-sample/a\  namespace: mambo-mongod-cpu' \
  "$RUN_DIR/mongodautoscaler.yaml"
sed -i \
  's/    maxShards: 2/    maxShards: 3/' \
  "$RUN_DIR/mongodautoscaler.yaml"
sed -i \
  's/    maxReplicas: 2/    maxReplicas: 1/' \
  "$RUN_DIR/mongodautoscaler.yaml"
sed -n '1,45p' "$RUN_DIR/mongodautoscaler.yaml"
```

`[READ ONLY]` Server-side validation:

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  apply --dry-run=server \
  -n mambo-mongod-cpu \
  -f "$RUN_DIR/mongodautoscaler.yaml" \
  -o name
```

`[WARNING: CLUSTER CHANGE]` Activates Mambo:

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  apply -n mambo-mongod-cpu \
  -f "$RUN_DIR/mongodautoscaler.yaml"
```

## 9. Continuous closed-loop read workload

`[WARNING: EXPERIMENTAL DATABASE LOAD]` One continuous invocation avoids YCSB
restart and connection-pool artifacts.

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -it -n mambo-mongod-cpu ycsb-test -- env RUN_ID="$RUN_ID" bash -lc '
    cd /YCSB
    mkdir -p "logs/$RUN_ID"
    bin/ycsb.sh run mongodb -s \
      -P workloads/workloadr \
      -p operationcount=8000000 \
      -p "mongodb.url=mongodb://root:mongodb123@my-mongodb-sharded:27017/ycsb?authSource=admin&readPreference=nearest" \
      -threads 10 \
      2>&1 | tee "logs/$RUN_ID/run_10_continuous.log"
  '
```

- Eight million reads against one million records.
- No `-target`.
- Ten threads.
- Expected duration: approximately 20–45 minutes.
- Keep the Controller and Kubernetes terminals running.
- Let YCSB finish after reaching 8,000,000 operations.
- Expected scale-up sequence: shards `1 -> 2`, resharding, five-minute
  cooldown, then shards `2 -> 3`.
- Use `Ctrl-C` early only if MongoDB becomes unhealthy or the experiment must
  stop. The plotter can process a partial log.
- After YCSB finishes, immediately freeze Mambo in Step 10 before stopping the
  Controller and Kubernetes log followers.

**Status terminal** (optional) — `[READ ONLY]`. Live status and the same CPU/I/O-wait checks used during the run:

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get mongodautoscaler mongodautoscaler-sample \
  -n mambo-mongod-cpu -o json | jq '.status'
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get statefulsets,jobs -n mambo-mongod-cpu

curl -sG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=100 * avg(sum by(pod) (rate(container_cpu_usage_seconds_total{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard[0-9]+-data.*",container!="POD",container!=""}[5m])) / sum by(pod) (kube_pod_container_resource_requests{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard[0-9]+-data.*",resource="cpu"}))' \
  | jq -r '.data.result[].value[1]'
curl -sG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=100 * avg(sum by(instance) (rate(node_cpu_seconds_total{mode="iowait",instance="172.20.211.60:9100"}[5m])) / sum by(instance) (rate(node_cpu_seconds_total{instance="172.20.211.60:9100"}[5m])))' \
  | jq -r '.data.result[].value[1]'
```

## 10. Freeze final three-shard topology

Immediately after YCSB finishes, inspect and then pause Mambo. This preserves
the three-shard state before the five-minute metrics window becomes low enough
to trigger scale-down.

`[READ ONLY]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get mongodautoscaler mongodautoscaler-sample \
  -n mambo-mongod-cpu -o json | jq '.status'
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get statefulsets,jobs -n mambo-mongod-cpu
```

Expected before pausing:

- `scalingPhase: Idle`.
- `lastDesiredShards: 3`.
- `lastDesiredReplicas: 1`.
- Three shard StatefulSets, each `1/1`.
- No `add-membership-*` Jobs.

If the topology did not reach 3 x 1, preserve the observed state and classify
the run as incomplete.

`[WARNING: CLUSTER CHANGE]` Pause Mambo before collecting final evidence.
MongoDB stays running.

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  scale deployment/mongodboperator-controller-manager \
  -n mongodboperator-system --replicas=0
```

`[READ ONLY]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get deployment,pods -n mongodboperator-system
```

## 11. Preserve results

`[LOCAL CHANGE]`

```bash
mkdir -p "$RUN_DIR/ycsb"
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig cp \
  "mambo-mongod-cpu/ycsb-test:/YCSB/logs/$RUN_ID/." \
  "$RUN_DIR/ycsb/"

kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get statefulsets -n mambo-mongod-cpu -o yaml \
  > "$RUN_DIR/final-statefulsets.yaml"
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get pods -n mambo-mongod-cpu -o wide \
  > "$RUN_DIR/final-pods.txt"
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get pvc -n mambo-mongod-cpu -o wide \
  > "$RUN_DIR/final-pvcs.txt"

kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -n mambo-mongod-cpu deployment/my-mongodb-sharded-mongos -- bash -c '
    /opt/bitnami/mongodb/bin/mongosh --host 127.0.0.1 --port 27017 \
      -u root \
      -p "$(cat /bitnami/mongodb/secrets/mongodb-root-password)" \
      --authenticationDatabase admin --quiet \
      --eval "printjson(db.adminCommand({listShards:1})); db.getSiblingDB(\"ycsb\").usertable.getShardDistribution()"
  ' | tee "$RUN_DIR/final-shard-distribution.txt"
```

Expected shard-only result: three shard StatefulSets with one member each.
Record the final document and chunk distribution because balancing may remain uneven.

## 12. Python plotter

Included script:
[`analysis/plot_mongod_cpu_shards_only.py`](analysis/plot_mongod_cpu_shards_only.py).

Inputs for this run:

- YCSB: `$RUN_DIR/ycsb/run_10_*.log`
- Controller: `$RUN_DIR/controller.log`
- Output: `$RUN_DIR/plots/throughput_vs_time.png`
- Optional cutoff: `--end-time HH:MM:SS` (UTC, using the run's sample date)

The expected YCSB and controller-log line formats are documented at the top
of the script.

`[WARNING: HOST CHANGE]` Only if Matplotlib is missing:

```bash
sudo apt-get install python3-matplotlib
```

`[LOCAL CHANGE]`

```bash
cd /users/adas2125/Autoscaling
source experiment-artifacts/current-run-env.sh
python3 analysis/plot_mongod_cpu_shards_only.py "$RUN_DIR"
```

Optional example stopping at `05:50:00` UTC:

```bash
python3 analysis/plot_mongod_cpu_shards_only.py "$RUN_DIR" \
  --end-time 05:50:00
```

Output:

```text
$RUN_DIR/plots/throughput_vs_time.png
```

## Original 2 x 2 reference result (comparison only)

- Shard scaling succeeded.
- Replica data plane succeeded: 2 shards x 2 healthy members.
- Controller workflow failed: duplicate `rs.add()` left it stuck.
- Sharp plot cliffs align with separate YCSB invocation boundaries.
- Classification: data-plane scaling success; autonomous-controller failure.

## Cleanup

Not included. Namespace deletion destroys MongoDB, PVCs, YCSB, exporters, and
experiment data. Review and authorize separately. Never delete Foxtrot or
shared monitoring resources.
