# Mambo mongod CPU experiment — NoSQLMark open loop

Open-loop counterpart to `experiment_overview_shards_only.md`. It uses
NoSQLMark `asyncmode=true` at a fixed 500 reads/second and validates one Mambo
scale event from one shard to two. Replicas remain fixed at one.

Each execution writes to a timestamped directory under
`experiment-artifacts/runs/`.

## Safety

| Marker | Effect |
|---|---|
| `[READ ONLY]` | Inspection only. |
| `[LOCAL CHANGE]` | Changes files on `node0` only. |
| `[WARNING: POD CHANGE]` | Changes files or starts processes inside the experiment pod. |
| `[WARNING: HOST CHANGE]` | Installs software on `node0`. |
| `[WARNING: CLUSTER CHANGE]` | Creates or changes Kubernetes resources. |
| `[WARNING: DATABASE CHANGE]` | Writes MongoDB data or metadata. |

Namespace: `mambo-mongod-cpu`. Do not modify `foxtrot`, shared `monitoring`,
or the control-plane node.

NoSQLMark/YCSB may print the complete MongoDB URI, including the experiment
password. Treat backend, REPL, and NoSQLMark logs as credential-bearing. Do not
publish them without redaction.

## What changes from the closed-loop run

- Same chart, image, placement, one-million-record dataset, and shard key.
- Shards scale `1 -> 2`; replicas remain fixed at one.
- CPU target `20%` with `10%` tolerance: scale-up boundary `30%`.
- Same Zipfian, read-only, approximately 1 KiB records.
- Same client node: `i-063b793db694be24c` (`m5a.large`).
- Data loading still uses YCSB with 10 threads.
- Measured phase uses NoSQLMark with one open-loop pacing worker.
- `target` is aggregate offered operations/second; it is not completed
  throughput and is not YCSB `-threads`.
- Constant inter-request times match `experiment_notes.txt`. Exponential
  arrivals would be a separate Poisson-arrival experiment.

NoSQLMark embeds YCSB 0.14's `CoreWorkload` and synchronous `MongoDbClient`.
With `asyncmode=true`, it releases each blocking MongoDB call into a Future and
continues pacing without waiting for completion. Latency includes client queue
delay, avoiding coordinated omission. The hard-coded per-operation timeout is
five seconds; extreme overload can accumulate Futures even after timeout.

## Run directory and terminals

This setup intentionally lowers Mambo's CPU threshold so 500 reads/second
triggers a scale event without saturating the first shard. It validates the
open-loop workflow and scaling transition, not maximum capacity. Throughput
should remain near the fixed 500 ops/s target; look for latency and CPU changes.

Run once in **Main**:

`[LOCAL CHANGE]`

```bash
cd /users/adas2125/Autoscaling
export RUN_ID="$(date -u +%Y%m%dT%H%M%S%NZ)"
export RUN_DIR="$PWD/experiment-artifacts/runs/$RUN_ID"
export OPEN_LOOP_TARGET_OPS=500
export OPEN_LOOP_WARMUP_COUNT=30000
export OPEN_LOOP_OPERATION_COUNT=450000
mkdir "$RUN_DIR"
{
  printf "export RUN_ID=%q\n" "$RUN_ID"
  printf "export RUN_DIR=%q\n" "$RUN_DIR"
  printf "export OPEN_LOOP_TARGET_OPS=%q\n" "$OPEN_LOOP_TARGET_OPS"
  printf "export OPEN_LOOP_WARMUP_COUNT=%q\n" "$OPEN_LOOP_WARMUP_COUNT"
  printf "export OPEN_LOOP_OPERATION_COUNT=%q\n" "$OPEN_LOOP_OPERATION_COUNT"
} | tee experiment-artifacts/current-run-env.sh > "$RUN_DIR/run-env.sh"
printf "Run ID: %s\nRun directory: %s\nTarget: %s ops/s\nWarm-up: %s\nMeasured operations: %s\n" \
  "$RUN_ID" "$RUN_DIR" "$OPEN_LOOP_TARGET_OPS" \
  "$OPEN_LOOP_WARMUP_COUNT" "$OPEN_LOOP_OPERATION_COUNT"
```

At 500 releases/second, warm-up takes 60 seconds and the measured 450,000
operations take 15 minutes. Allow roughly 16 minutes plus startup and drain.

In every additional terminal:

```bash
cd /users/adas2125/Autoscaling
source experiment-artifacts/current-run-env.sh
printf "Using %s at %s; target=%s ops/s\n" \
  "$RUN_ID" "$RUN_DIR" "$OPEN_LOOP_TARGET_OPS"
```

| Terminal | Use |
|---|---|
| **Main** | Deployment, load, job definition, evidence |
| **Prometheus** | Port-forward; leave running |
| **Controller** | Mambo controller log follower |
| **Kubernetes** | Experiment pod watcher |
| **Backend** | Long-running NoSQLMark backbench process |
| **REPL** | Submit the NoSQLMark job and receive results |
| **Status** (optional) | Read-only live checks |

Kubernetes resource names are reused. Complete the clean-state preflight even
though local artifacts use unique directories.

## Recorded setup

- Mambo commit: `68314fe664c4a82883ee286d192c77f273525569`
- Operator image: `docker.io/b00611024/mongodb-autoscaler:test`
- MongoDB chart: `bitnami/mongodb-sharded` `9.4.12`
- MongoDB image: `bitnamilegacy/mongodb-sharded:8.0.13-debian-12-r0`
- Current reviewed NoSQLMark commit:
  `f64622415a2670323675e56263427641eb7c72aa`
- YCSB fork: `steffenfriedrich/YCSB` at
  `b73ac8367b7de0356031684883338ec1826c1a4f`
- YCSB: `0.14.0-SNAPSHOT`, `com.yahoo.ycsb` namespace
- MongoDB Java driver: `3.12.14`
- NoSQLMark: Java 8, Scala 2.11.8, sbt launcher 0.13.8
- Client image: `maven:3.8.7-eclipse-temurin-8`
- Workload: 1,000,000 records; 30,000 warm-up reads; 450,000 measured reads;
  500 ops/s; Zipfian; constant inter-arrival; `readPreference=nearest`;
  one pacing worker
- Topology bounds: `1 x 1` through `2 x 1`
- CPU boundaries: target `20%`, tolerance `10%`; scale up above `30%` and
  scale down below `10%` of the `0.5`-CPU request

Reference run: scale decision at 33.44% CPU; `addShard` succeeded after about
90 seconds; resharding completed after about 120 seconds. Mean latency changed
from 3.96 ms before scaling to 3.45 ms afterward.

The reviewed local NoSQLMark is newer than the stale `bfe14e5` revision named
in `MONGODB_REPRODUCTION_SETUP.md`; its MongoDB compatibility changes are
already committed. Do not reset or patch the local NoSQLMark source.
The URI also uses `retryReads=false` and `retryWrites=false`, following the
tested NoSQLMark MongoDB setup. Record this difference from the previous
closed-loop URI when comparing trials.


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
git -C ../NoSQLMark rev-parse HEAD
git -C ../NoSQLMark status --short
kubectl version --client
helm version --short
sudo docker version --format '{{.Client.Version}}'
jq --version

kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig config current-context
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig get nodes \
  -L node.kubernetes.io/instance-type \
  -L topology.kubernetes.io/zone
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig get storageclass
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get namespace mambo-mongod-cpu
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get statefulsets -A -l app.kubernetes.io/name=mongodb-sharded
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get crd mongodautoscalers.autoscaler.mongodb.io
helm --kubeconfig /users/adas2125/.kube/amit.kubeconfig list -A
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get pods -n foxtrot -o wide
```

Expected clean state: experiment namespace, MongoDB StatefulSets, and Mambo
CRD absent. Shared `kps` and Foxtrot remain present and untouched.

`[LOCAL CHANGE]`

```bash
test -n "$RUN_ID" && test -d "$RUN_DIR"
git rev-parse HEAD | tee "$RUN_DIR/mambo-git-commit.txt"
git -C ../NoSQLMark rev-parse HEAD | tee "$RUN_DIR/nosqlmark-git-commit.txt"
git -C ../NoSQLMark status --short | tee "$RUN_DIR/nosqlmark-git-status.txt"
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig version -o yaml \
  > "$RUN_DIR/kubernetes-version.yaml"
helm version > "$RUN_DIR/helm-version.txt"
sudo docker version > "$RUN_DIR/docker-version.txt"

helm repo add bitnami https://charts.bitnami.com/bitnami --force-update
helm repo add prometheus-community \
  https://prometheus-community.github.io/helm-charts --force-update
helm repo update
printf '%s\n' '4.56.1' | tee "$RUN_DIR/node-exporter-chart-version.txt"
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

**Prometheus terminal** — `[READ ONLY: TEMPORARY CONNECTION]`:

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
```

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
  rollout status deployment/mongodb-exporter \
  -n mambo-mongod-cpu --timeout=300s

curl -sG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=up{namespace="mambo-mongod-cpu",service="mongodb-exporter"}' \
  | jq -r '.data.result[] | [.metric.instance, .value[1]] | @tsv'
curl -sG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=kube_pod_container_resource_requests{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard.*-data-.*",resource="cpu"}' \
  | jq -r '.data.result[] | [.metric.pod, .metric.container, .value[1]] | @tsv'
```

## 5. Mambo operator

Use the developer-provided manifest and public `:test` tag. Record what the
mutable tag resolves to; do not push or modify the public image.

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
```

## 6. NoSQLMark client pod and build

The backend and REPL run in one pod. They then share the reviewed stock
loopback Akka configuration (`127.0.0.1:2552` and `127.0.0.1:2559`) without a
NoSQLMark source/config change.

`[WARNING: CLUSTER CHANGE]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig run nosqlmark-client \
  --namespace mambo-mongod-cpu \
  --image=maven:3.8.7-eclipse-temurin-8 \
  --restart=Never \
  --overrides='{"spec":{"nodeSelector":{"kubernetes.io/hostname":"i-063b793db694be24c"}}}' \
  --command -- sleep infinity
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  wait --for=condition=Ready pod/nosqlmark-client \
  -n mambo-mongod-cpu --timeout=300s
```

`[READ ONLY]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get pod nosqlmark-client -n mambo-mongod-cpu \
  -o jsonpath='{.status.containerStatuses[0].imageID}{"\n"}' \
  | tee "$RUN_DIR/nosqlmark-client-image.txt"
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get pod nosqlmark-client -n mambo-mongod-cpu -o wide
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -n mambo-mongod-cpu nosqlmark-client -- bash -lc \
  'java -version; javac -version; mvn -version; git --version; curl --version; tar --version'
```

Expected: Java 8 and Maven 3.8.7. Stop here if `git`, `curl`, or `tar` is
missing; install only the missing pod tools after reviewing that change.

Copy the reviewed local NoSQLMark tree. This does not modify `../NoSQLMark`.

`[WARNING: POD CHANGE]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -n mambo-mongod-cpu nosqlmark-client -- mkdir -p /NoSQLMark
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig cp \
  ../NoSQLMark/. mambo-mongod-cpu/nosqlmark-client:/NoSQLMark
```

Build the pinned YCSB fork. The two `sed` commands make explicit the
compatibility edits omitted from `MONGODB_REPRODUCTION_SETUP.md`.

`[WARNING: POD CHANGE]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -it -n mambo-mongod-cpu nosqlmark-client -- bash -lc '
    set -euo pipefail
    git clone https://github.com/steffenfriedrich/YCSB.git /YCSB
    git -C /YCSB checkout --detach b73ac8367b7de0356031684883338ec1826c1a4f
    sed -i "s#http://www.allanbank.com/repo/#https://www.allanbank.com/repo/#" \
      /YCSB/mongodb/pom.xml
    sed -i "s#<mongodb.version>3.0.3</mongodb.version>#<mongodb.version>3.12.14</mongodb.version>#" \
      /YCSB/pom.xml
    git -C /YCSB diff --check
    git -C /YCSB diff > /NoSQLMark/artifacts/ycsb-compatibility.patch
    cd /YCSB
    mvn -pl mongodb -am -DskipTests -Dcheckstyle.skip=true install \
      2>&1 | tee /NoSQLMark/artifacts/ycsb-mongodb-build.log
    mkdir -p /tmp/nosqlmark-mongodb-tools
    tar -xzf mongodb/target/ycsb-mongodb-binding-0.14.0-SNAPSHOT.tar.gz \
      -C /tmp/nosqlmark-mongodb-tools
    test -x /tmp/nosqlmark-mongodb-tools/ycsb-mongodb-binding-0.14.0-SNAPSHOT/bin/ycsb.sh
    test -f /tmp/nosqlmark-mongodb-tools/ycsb-mongodb-binding-0.14.0-SNAPSHOT/lib/mongo-java-driver-3.12.14.jar
    jar tf "$HOME/.m2/repository/com/yahoo/ycsb/mongodb-binding/0.14.0-SNAPSHOT/mongodb-binding-0.14.0-SNAPSHOT.jar" \
      | grep com/yahoo/ycsb/db/MongoDbClient.class
  '
```

Build the reviewed NoSQLMark source. Do not reapply the historical NoSQLMark
patch list; those changes are already in the local revision.

`[WARNING: POD CHANGE]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -it -n mambo-mongod-cpu nosqlmark-client -- bash -lc '
    set -euo pipefail
    cd /NoSQLMark
    mkdir -p artifacts logs results
    curl -fL \
      https://repo.scala-sbt.org/scalasbt/ivy-releases/org.scala-sbt/sbt-launch/0.13.8/sbt-launch.jar \
      -o artifacts/sbt-launch-0.13.8.jar
    echo "6570bb03df6138ffaa7ac0bbe35eb4ea79062d1146b6929c75cf238d14dd9158  artifacts/sbt-launch-0.13.8.jar" \
      | sha256sum -c -
    java -jar artifacts/sbt-launch-0.13.8.jar \
      "project backbench" compile \
      2>&1 | tee artifacts/nosqlmark-backbench-build.log
    java -jar artifacts/sbt-launch-0.13.8.jar \
      "project repl" compile \
      2>&1 | tee artifacts/nosqlmark-repl-build.log
  '
```

## 7. Load and shard the same dataset

The old YCSB fork uses `com.yahoo.ycsb`, while the recorded workload file says
`site.ycsb`. Change only that class namespace in the pod copy.

`[WARNING: POD CHANGE]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig cp \
  MongoDB/result/mongod_cpu_exp/workloadr \
  mambo-mongod-cpu/nosqlmark-client:/tmp/nosqlmark-mongodb-tools/ycsb-mongodb-binding-0.14.0-SNAPSHOT/workloads/workloadr
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -n mambo-mongod-cpu nosqlmark-client -- bash -lc '
    sed -i "s/site.ycsb.workloads.CoreWorkload/com.yahoo.ycsb.workloads.CoreWorkload/" \
      /tmp/nosqlmark-mongodb-tools/ycsb-mongodb-binding-0.14.0-SNAPSHOT/workloads/workloadr
  '
```

`[WARNING: DATABASE CHANGE]` Uses the same 10 load threads and one million
records as the shard-only experiment.

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -it -n mambo-mongod-cpu nosqlmark-client -- env RUN_ID="$RUN_ID" bash -lc '
    set -euo pipefail
    YCSB=/tmp/nosqlmark-mongodb-tools/ycsb-mongodb-binding-0.14.0-SNAPSHOT
    mkdir -p "/NoSQLMark/logs/$RUN_ID"
    cd "$YCSB"
    ./bin/ycsb.sh load mongodb -s \
      -P workloads/workloadr \
      -threads 10 \
      -p "mongodb.url=mongodb://root:mongodb123@my-mongodb-sharded:27017/ycsb?authSource=admin&retryWrites=false&retryReads=false" \
      2>&1 | tee "/NoSQLMark/logs/$RUN_ID/ycsb-load.log"
  '
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig cp \
  "mambo-mongod-cpu/nosqlmark-client:/NoSQLMark/logs/$RUN_ID/ycsb-load.log" \
  "$RUN_DIR/load.log"
```

Expected: 1,000,000 successful inserts. Wait ten minutes so the five-minute
metrics window clears before activating Mambo.

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

Expected: logical count 1,000,000 and shard key `{ _id: 1 }`.

## 8. Observers and shard-only autoscaler

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

The sample already has `minShards: 1` and `maxShards: 2`. Keep those values.
Only lower the CPU target and fix replicas at one.

```bash
cp MongoDB/result/mongod_cpu_exp/autoscaler_v1alpha1_mongodautoscaler.yaml \
  "$RUN_DIR/mongodautoscaler.yaml"
sed -i 's/    namespace: default/    namespace: mambo-mongod-cpu/' \
  "$RUN_DIR/mongodautoscaler.yaml"
sed -i '/  name: mongodautoscaler-sample/a\  namespace: mambo-mongod-cpu' \
  "$RUN_DIR/mongodautoscaler.yaml"
sed -i 's/    maxReplicas: 2/    maxReplicas: 1/' \
  "$RUN_DIR/mongodautoscaler.yaml"
sed -i 's/    cpuTargetPercent: 60/    cpuTargetPercent: 20/' \
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

## 9. Define and run the open-loop job

Do not edit or use `../NoSQLMark/artifacts/mongo_jobs.scala`: it targets a
standalone private IP and currently contains a different workload mix. Create
a run-specific artifact instead.

**Main terminal** — `[LOCAL CHANGE]` and `[WARNING: POD CHANGE]`:

```bash
cat > "$RUN_DIR/mambo-openloop-job.scala" <<EOF
val openLoopExperiment = CoreJob(
  jobID = nc.genID,
  batchname = "mambo-openloop-$RUN_ID",
  workload = "CoreWorkload",
  dbname = "MongoDbClient",
  dbproperties = Map(
    "mongodb.url" ->
      "mongodb://root:mongodb123@my-mongodb-sharded:27017/ycsb?authSource=admin&readPreference=nearest&retryWrites=false&retryReads=false"
  ),
  target = ${OPEN_LOOP_TARGET_OPS}.0,
  nodes = 1,
  worker = 1,
  table = "usertable",
  phase = "transactional",
  asyncmode = true,
  counts = CoreCounts(
    recordcount = 1000000,
    warmupcount = $OPEN_LOOP_WARMUP_COUNT,
    operationcount = $OPEN_LOOP_OPERATION_COUNT,
    insertcount = 0,
    insertstart = 0,
    fieldcount = 10,
    fieldlength = 100,
    readallfields = true,
    writeallfields = true
  ),
  proportions = CoreProportions(
    readproportion = 1.0,
    updateproportion = 0.0,
    insertproportion = 0.0,
    scanproportion = 0.0,
    readmodifywriteproportion = 0.0
  ),
  distributions = CoreDistributions(
    requestdistribution = "zipfian",
    insertorder = "hashed"
  ),
  loadgeneration = CoreLoadGeneration(
    interrequesttimedistribution = "constant"
  ),
  logmeasurements = true,
  logjvmstats = false
)

println(openLoopExperiment)
EOF

kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig cp \
  "$RUN_DIR/mambo-openloop-job.scala" \
  mambo-mongod-cpu/nosqlmark-client:/NoSQLMark/artifacts/mambo-openloop-job.scala
```

`nodes=1` means one NoSQLMark backend node, not one MongoDB shard. `worker=1`
means one release scheduler, not one in-flight request. Async Futures provide
concurrency. Ten workers would be a separate experiment and can create
synchronized constant-rate microbursts.

**Backend terminal** — `[WARNING: POD CHANGE]`. Leave running:

```bash
cd /users/adas2125/Autoscaling
source experiment-artifacts/current-run-env.sh
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -it -n mambo-mongod-cpu nosqlmark-client -- env RUN_ID="$RUN_ID" bash -lc '
    cd /NoSQLMark
    mkdir -p "logs/$RUN_ID"
    java -jar artifacts/sbt-launch-0.13.8.jar \
      "project backbench" run \
      2>&1 | tee "logs/$RUN_ID/backbench-console.log"
  '
```

**REPL terminal** — `[WARNING: POD CHANGE]`:

```bash
cd /users/adas2125/Autoscaling
source experiment-artifacts/current-run-env.sh
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -it -n mambo-mongod-cpu nosqlmark-client -- env RUN_ID="$RUN_ID" bash -lc '
    set -euo pipefail
    cd /NoSQLMark
    mkdir -p "logs/$RUN_ID"
    CPFILE="$(mktemp /tmp/nosqlmark-repl-classpath.XXXXXX)"
    java -jar artifacts/sbt-launch-0.13.8.jar \
      "project repl" "export fullClasspath" > "$CPFILE"
    CP="$(tail -n 1 "$CPFILE")"
    java -Xmx1G \
      -Dlogback.configurationFile=config/logback.xml \
      -cp "$CP" \
      de.unihamburg.informatik.nosqlmark.repl.REPL \
      2>&1 | tee "logs/$RUN_ID/repl-console.log"
  '
```

Wait for `Connected to BackbenchService`, then enter in the REPL:

```scala
:load /NoSQLMark/artifacts/mambo-openloop-job.scala
nc.submitJob(openLoopExperiment)
```

Leave both processes running. Normal completion takes about 16 minutes, prints
`received result for job`, and writes `summary.json`, `workload.json`, HDR
histograms, and percentile files under `results/mambo-openloop-$RUN_ID/`.

Transient `MongoWaitQueueFullException` messages may occur during warm-up. The
measured phase is valid only when the final summary contains all 450,000 reads
and no failed or timed-out category.

The live status `throughput` is release throughput. It is not necessarily
MongoDB completion throughput. When MongoDB falls behind, offered rate can
remain near target while latency, queued work, failures, or timeouts increase.

**Status terminal** — `[READ ONLY]`:

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
  --data-urlencode 'query=container_memory_working_set_bytes{namespace="mambo-mongod-cpu",pod="nosqlmark-client",container!="",container!="POD"}' \
  | jq -r '.data.result[] | [.metric.container, .value[1]] | @tsv'
```

If NoSQLMark memory grows continuously or many `READ-TIMEDOUT`/
`READ-FAILED` results appear, preserve evidence and stop the run; do not raise
the target. NoSQLMark does not implement reliable mid-job cancellation, and an
aborted job may have time-series logs but no final summary.

## 10. Freeze the observed topology

After NoSQLMark completes, inspect the result before pausing Mambo. The
configured maximum is two shards. If the run remains at one, the lowered CPU
boundary was not crossed.

`[READ ONLY]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get mongodautoscaler mongodautoscaler-sample \
  -n mambo-mongod-cpu -o json | jq '.status'
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get statefulsets,jobs -n mambo-mongod-cpu
```

If a scale workflow is active, allow it to reach `Idle` unless MongoDB is
unhealthy. Then freeze the observed topology before the five-minute window
cools enough to cause scale-down.

`[WARNING: CLUSTER CHANGE]`

```bash
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  scale deployment/mongodboperator-controller-manager \
  -n mongodboperator-system --replicas=0
```

After scaling the operator to zero, stop the Backend, REPL, Controller,
Kubernetes, and Prometheus terminals with `Ctrl-C`. Exit code 130 after
`received result` only records that terminal interruption. For an intentional
partial run, freeze Mambo first and then stop Backend; expect no final summary.

## 11. Preserve results

Copy all backend log files because the 50 MB time-series appender rolls files.

`[LOCAL CHANGE]`

```bash
test ! -e "$RUN_DIR/nosqlmark"
mkdir -p "$RUN_DIR/nosqlmark/backend-logs" \
  "$RUN_DIR/nosqlmark/client-logs" \
  "$RUN_DIR/nosqlmark/results"

kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig cp \
  mambo-mongod-cpu/nosqlmark-client:/NoSQLMark/backbench/logs/. \
  "$RUN_DIR/nosqlmark/backend-logs/"
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig cp \
  mambo-mongod-cpu/nosqlmark-client:/NoSQLMark/logs/. \
  "$RUN_DIR/nosqlmark/client-logs/"
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig cp \
  "mambo-mongod-cpu/nosqlmark-client:/NoSQLMark/results/mambo-openloop-$RUN_ID/." \
  "$RUN_DIR/nosqlmark/results/"
kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig cp \
  mambo-mongod-cpu/nosqlmark-client:/NoSQLMark/artifacts/ycsb-compatibility.patch \
  "$RUN_DIR/ycsb-compatibility.patch"

kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  get mongodautoscaler mongodautoscaler-sample \
  -n mambo-mongod-cpu -o yaml \
  > "$RUN_DIR/final-mongodautoscaler.yaml"
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
  get events -n mambo-mongod-cpu --sort-by=.metadata.creationTimestamp \
  > "$RUN_DIR/final-events.txt"

kubectl --kubeconfig /users/adas2125/.kube/amit.kubeconfig \
  exec -n mambo-mongod-cpu deployment/my-mongodb-sharded-mongos -- bash -c '
    /opt/bitnami/mongodb/bin/mongosh --host 127.0.0.1 --port 27017 \
      -u root \
      -p "$(cat /bitnami/mongodb/secrets/mongodb-root-password)" \
      --authenticationDatabase admin --quiet \
      --eval "printjson(db.adminCommand({listShards:1})); printjson({logicalCount:db.getSiblingDB(\"ycsb\").usertable.countDocuments({})}); db.getSiblingDB(\"ycsb\").usertable.getShardDistribution()"
  ' | tee "$RUN_DIR/final-shard-distribution.txt"

find "$RUN_DIR" -maxdepth 4 -type f -printf '%P\t%k KB\n' | sort
```

If the job was aborted, the results-directory copy may return `NotFound`.
Preserve the backend time-series and console logs anyway.

## 12. Plot

Do not use the closed-loop YCSB plotters. Reuse the verified open-loop plotter
snapshot from the successful run. The wrapper below normalizes rollover names
and writes into a new directory, so existing results are never overwritten.

`[LOCAL CHANGE]`

```bash
cd /users/adas2125/Autoscaling
source experiment-artifacts/current-run-env.sh

PLOTTER="$PWD/experiment-artifacts/runs/20260830T195907630058291Z/scaling/scale500-cpu30-15m-20260830T214206770952687Z/analysis-openloop-8pQwmtrq/plot_openloop_scaling_v2.py"
test -f "$PLOTTER"
ANALYSIS_DIR="$(mktemp -d "$RUN_DIR/analysis-openloop-XXXXXXXX")"
mkdir "$ANALYSIS_DIR/results" "$ANALYSIS_DIR/console-logs"

cp -a "$RUN_DIR/nosqlmark/results/." "$ANALYSIS_DIR/results/"
cp "$RUN_DIR/nosqlmark/client-logs/$RUN_ID/backbench-console.log" "$ANALYSIS_DIR/console-logs/backbench-console.log"
cp "$RUN_DIR/controller.log" "$ANALYSIS_DIR/controller.log"
cp "$PLOTTER" "$ANALYSIS_DIR/plot_openloop_scaling.py"
find "$RUN_DIR/nosqlmark/backend-logs" -maxdepth 1 -type f -name "timeseries-*.log" -exec cat {} + > "$ANALYSIS_DIR/timeseries-20260830_212642.0.log"
cp "$RUN_DIR/nosqlmark/backend-logs/timeseries.log" "$ANALYSIS_DIR/timeseries.log"

python3 "$ANALYSIS_DIR/plot_openloop_scaling.py"
printf "Analysis directory: %s\n" "$ANALYSIS_DIR"
```

Output: `$ANALYSIS_DIR/openloop_scaling.png`. It shows mean latency and mongod
CPU, with markers for shard scaling and resharding completion.

## Cleanup

Not included. Namespace deletion destroys MongoDB, PVCs, NoSQLMark, exporters,
and experiment data. Review and authorize cleanup separately. Never delete
Foxtrot or shared monitoring resources.
