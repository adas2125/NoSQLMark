# Mambo open-loop one-key hotspot experiment

Goal: test whether adding a shard balances **heat**, not just data. One logical
YCSB key receives 80% of reads. Mambo may add shard 1, but that key and its
chunk can only stay on one shard or move there as a unit.

Primary result: per-shard read rate and per-pod `mongod` CPU after balancing.
At a fixed 1,000 offered RPS, do not expect throughput above 1,000 RPS.

## Fixed design

- Initial topology: 1 shard, 1 member. Final bound: 2 shards, 1 member each.
- Mambo: CPU target `50%`, tolerance `5%`; scale-up boundary is strictly
  greater than `55%`. Window `5m`; cooldown `3600s`.
- NoSQLMark: open loop, constant arrivals, 1 worker, 1,000 RPS.
- Warm-up: 60 seconds / 60,000 reads.
- Measurement: 15 minutes / 900,000 reads.
- Workload: 100% reads, `hotspotopnfraction=0.8`, one hot logical key.
- The one hot logical key hashes to MongoDB `_id`
  `user6284781860667377211`.

With balanced cold data, the hot-key owner should receive roughly 90% of
reads: the 80% hot fraction plus about half of the remaining 20%. It may be
somewhat different if cold data remains imbalanced.

## Safety

| Marker | Effect |
|---|---|
| `[READ ONLY]` | Inspects Kubernetes, MongoDB, or metrics. |
| `[LOCAL CHANGE]` | Changes local files/settings; experiment data goes only below this run directory. |
| `[WARNING: POD CHANGE]` | Changes files or processes in the experiment client pod. |
| `[WARNING: CLUSTER CHANGE]` | Creates or changes Mambo experiment resources. |
| `[WARNING: DATABASE CHANGE]` | Writes MongoDB data or sharding metadata. |

Use only `mambo-mongod-cpu`. Never modify `foxtrot`, shared KPS, or the
control-plane node. Commands containing the MongoDB URI may expose the
experiment password in logs; redact it before publication.

## Terminals

| Terminal | Purpose |
|---|---|
| **Main** | Setup, manifests, snapshots, preservation |
| **Prometheus** | Port-forward; leave running through data capture |
| **Controller** | Timestamped Mambo log |
| **Kubernetes** | Pod watch |
| **Backend** | NoSQLMark backbench |
| **REPL** | Submit the one job |
| **Status** | Read-only live checks |

## 1. Create an isolated run directory

**Main** — `[LOCAL CHANGE]`:

```bash
cd /users/adas2125/Autoscaling

export KCFG=/users/adas2125/.kube/amit.kubeconfig
export NS=mambo-mongod-cpu
export HOTSPOT_ROOT="$PWD/hotspot-experiment-artifacts"
export RUN_ID="$(date -u +%Y%m%dT%H%M%S%NZ)"
export RUN_DIR="$HOTSPOT_ROOT/$RUN_ID"

export TARGET_RPS=1000
export WARMUP_SECONDS=60
export MEASURE_SECONDS=900
export WARMUP_COUNT=$((TARGET_RPS * WARMUP_SECONDS))
export OPERATION_COUNT=$((TARGET_RPS * MEASURE_SECONDS))
export MAX_POOL_SIZE=100
export WAIT_QUEUE_MULTIPLE=5
export HOT_KEY=user6284781860667377211

mkdir -p "$HOTSPOT_ROOT"
mkdir "$RUN_DIR"
mkdir -p "$RUN_DIR"/{manifests,logs,metrics,snapshots,nosqlmark}

{
  printf 'export KCFG=%q\n' "$KCFG"
  printf 'export NS=%q\n' "$NS"
  printf 'export RUN_ID=%q\n' "$RUN_ID"
  printf 'export RUN_DIR=%q\n' "$RUN_DIR"
  printf 'export TARGET_RPS=%q\n' "$TARGET_RPS"
  printf 'export WARMUP_COUNT=%q\n' "$WARMUP_COUNT"
  printf 'export OPERATION_COUNT=%q\n' "$OPERATION_COUNT"
  printf 'export MAX_POOL_SIZE=%q\n' "$MAX_POOL_SIZE"
  printf 'export WAIT_QUEUE_MULTIPLE=%q\n' "$WAIT_QUEUE_MULTIPLE"
  printf 'export HOT_KEY=%q\n' "$HOT_KEY"
} > "$RUN_DIR/run-env.sh"

git rev-parse HEAD | tee "$RUN_DIR/autoscaling-commit.txt"
git -C ../NoSQLMark rev-parse HEAD | tee "$RUN_DIR/nosqlmark-commit.txt"
printf 'RUN_ID=%s\nRUN_DIR=%s\n' "$RUN_ID" "$RUN_DIR"
```

In every additional terminal, replace `RUN_ID_VALUE` with the printed ID:

```bash
cd /users/adas2125/Autoscaling
export RUN_ID=RUN_ID_VALUE
source "hotspot-experiment-artifacts/$RUN_ID/run-env.sh"
```

Do not use `experiment-artifacts/current-run-env.sh`; this run is isolated.

## 2. Preflight and choose one setup path

`[READ ONLY]`

```bash
kubectl --kubeconfig "$KCFG" config current-context
kubectl --kubeconfig "$KCFG" get nodes \
  -L node.kubernetes.io/instance-type \
  -L topology.kubernetes.io/zone
kubectl --kubeconfig "$KCFG" get namespace "$NS"
kubectl --kubeconfig "$KCFG" get statefulsets -A \
  -l app.kubernetes.io/name=mongodb-sharded
kubectl --kubeconfig "$KCFG" \
  get crd mongodautoscalers.autoscaler.mongodb.io
kubectl --kubeconfig "$KCFG" get deployment mongodb-exporter -n "$NS"
kubectl --kubeconfig "$KCFG" get service mongodb-exporter -n "$NS"
kubectl --kubeconfig "$KCFG" \
  get servicemonitor kps-mongodb-exporter -n "$NS"
kubectl --kubeconfig "$KCFG" \
  get servicemonitor kps-mongodb-exporter -n monitoring
kubectl --kubeconfig "$KCFG" get namespace mongodboperator-system
kubectl --kubeconfig "$KCFG" get clusterrole mongodboperator-manager-role
kubectl --kubeconfig "$KCFG" \
  get clusterrolebinding mongodboperator-manager-rolebinding
helm --kubeconfig "$KCFG" list -A
kubectl --kubeconfig "$KCFG" get pods -n foxtrot
```

Choose exactly one:

- **Fresh:** namespace, experiment MongoDB, and Mambo CRD are absent. Run
  sections 3 and 4.
- **Reuse current capacity setup:** one healthy `shard0-data` member,
  `nosqlmark-client`, one million records, and `{_id:1}` already exist; no
  Mambo operator or CR exists. Skip section 3 and run the validation at the
  end of section 4. Do **not** load again.

`NotFound` is expected for both exporter ServiceMonitors and every operator
resource above. Stop if an old autoscaler, shard 1, exporter, or operator is
present. Do not apply over, upgrade, or delete it as part of this run.

## 3. Fresh MongoDB setup only

Skip this entire section when reusing the current capacity setup.

`[LOCAL CHANGE]`

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami --force-update
helm repo add prometheus-community \
  https://prometheus-community.github.io/helm-charts --force-update
helm repo update

cat > "$RUN_DIR/manifests/placement-values.yaml" <<'EOF'
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
  --namespace "$NS" \
  -f MongoDB/result/mongod_cpu_exp/values.yaml \
  -f "$RUN_DIR/manifests/placement-values.yaml" \
  > "$RUN_DIR/manifests/rendered-mongodb.yaml"
```

`[WARNING: CLUSTER CHANGE]`

```bash
kubectl --kubeconfig "$KCFG" create namespace "$NS"

helm install my-mongodb-sharded bitnami/mongodb-sharded \
  --version 9.4.12 \
  --kubeconfig "$KCFG" \
  --namespace "$NS" \
  -f MongoDB/result/mongod_cpu_exp/values.yaml \
  -f "$RUN_DIR/manifests/placement-values.yaml"
```

`[READ ONLY]`

```bash
kubectl --kubeconfig "$KCFG" rollout status \
  statefulset/my-mongodb-sharded-configsvr -n "$NS" --timeout=600s
kubectl --kubeconfig "$KCFG" rollout status \
  statefulset/my-mongodb-sharded-shard0-data -n "$NS" --timeout=600s
kubectl --kubeconfig "$KCFG" rollout status \
  deployment/my-mongodb-sharded-mongos -n "$NS" --timeout=600s
kubectl --kubeconfig "$KCFG" get statefulsets,pods,pvc -n "$NS" -o wide
```

Expected: config servers `3/3`, three mongos pods, and shard 0 at `1/1`.

## 4. Prepare, load, and shard NoSQLMark

For a fresh setup, run all commands. For reuse, run only the final validation.

### Fresh client and build

`[WARNING: CLUSTER CHANGE]`

```bash
kubectl --kubeconfig "$KCFG" run nosqlmark-client \
  --namespace "$NS" \
  --image=maven:3.8.7-eclipse-temurin-8 \
  --restart=Never \
  --overrides='{"spec":{"nodeSelector":{"kubernetes.io/hostname":"i-063b793db694be24c"}}}' \
  --command -- sleep infinity
kubectl --kubeconfig "$KCFG" wait --for=condition=Ready \
  pod/nosqlmark-client -n "$NS" --timeout=300s
```

`[WARNING: POD CHANGE]`

```bash
kubectl --kubeconfig "$KCFG" exec -n "$NS" nosqlmark-client -- \
  mkdir -p /NoSQLMark
kubectl --kubeconfig "$KCFG" cp \
  ../NoSQLMark/. "$NS/nosqlmark-client:/NoSQLMark"

kubectl --kubeconfig "$KCFG" exec -it -n "$NS" \
  nosqlmark-client -- bash -lc '
    set -euo pipefail
    mkdir -p /NoSQLMark/artifacts
    git clone https://github.com/steffenfriedrich/YCSB.git /YCSB
    git -C /YCSB checkout --detach b73ac8367b7de0356031684883338ec1826c1a4f
    sed -i "s#http://www.allanbank.com/repo/#https://www.allanbank.com/repo/#" \
      /YCSB/mongodb/pom.xml
    sed -i "s#<mongodb.version>3.0.3</mongodb.version>#<mongodb.version>3.12.14</mongodb.version>#" \
      /YCSB/pom.xml
    git -C /YCSB diff > /NoSQLMark/artifacts/ycsb-compatibility.patch
    cd /YCSB
    mvn -pl mongodb -am -DskipTests -Dcheckstyle.skip=true install
    mkdir -p /tmp/nosqlmark-mongodb-tools
    tar -xzf mongodb/target/ycsb-mongodb-binding-0.14.0-SNAPSHOT.tar.gz \
      -C /tmp/nosqlmark-mongodb-tools
  '

kubectl --kubeconfig "$KCFG" exec -it -n "$NS" \
  nosqlmark-client -- bash -lc '
    set -euo pipefail
    cd /NoSQLMark
    mkdir -p artifacts logs results
    curl -fL \
      https://repo.scala-sbt.org/scalasbt/ivy-releases/org.scala-sbt/sbt-launch/0.13.8/sbt-launch.jar \
      -o artifacts/sbt-launch-0.13.8.jar
    echo "6570bb03df6138ffaa7ac0bbe35eb4ea79062d1146b6929c75cf238d14dd9158  artifacts/sbt-launch-0.13.8.jar" \
      | sha256sum -c -
    java -jar artifacts/sbt-launch-0.13.8.jar "project backbench" compile
    java -jar artifacts/sbt-launch-0.13.8.jar "project repl" compile
  '
```

### Fresh load and shard

`[WARNING: POD CHANGE]`

```bash
kubectl --kubeconfig "$KCFG" cp \
  MongoDB/result/mongod_cpu_exp/workloadr \
  "$NS/nosqlmark-client:/tmp/nosqlmark-mongodb-tools/ycsb-mongodb-binding-0.14.0-SNAPSHOT/workloads/workloadr"
kubectl --kubeconfig "$KCFG" exec -n "$NS" nosqlmark-client -- bash -lc '
  sed -i "s/site.ycsb.workloads.CoreWorkload/com.yahoo.ycsb.workloads.CoreWorkload/" \
    /tmp/nosqlmark-mongodb-tools/ycsb-mongodb-binding-0.14.0-SNAPSHOT/workloads/workloadr
'
```

`[WARNING: DATABASE CHANGE]`

```bash
kubectl --kubeconfig "$KCFG" exec -it -n "$NS" \
  nosqlmark-client -- env RUN_ID="$RUN_ID" bash -lc '
    set -euo pipefail
    YCSB=/tmp/nosqlmark-mongodb-tools/ycsb-mongodb-binding-0.14.0-SNAPSHOT
    mkdir -p "/NoSQLMark/logs/$RUN_ID"
    cd "$YCSB"
    ./bin/ycsb.sh load mongodb -s \
      -P workloads/workloadr -threads 10 \
      -p "mongodb.url=mongodb://root:mongodb123@my-mongodb-sharded:27017/ycsb?authSource=admin&retryWrites=false&retryReads=false" \
      2>&1 | tee "/NoSQLMark/logs/$RUN_ID/ycsb-load.log"
  '

kubectl --kubeconfig "$KCFG" exec -n "$NS" \
  deployment/my-mongodb-sharded-mongos -- bash -c '
    /opt/bitnami/mongodb/bin/mongosh --host 127.0.0.1 --port 27017 \
      -u root -p "$(cat /bitnami/mongodb/secrets/mongodb-root-password)" \
      --authenticationDatabase admin --quiet \
      --eval "sh.enableSharding(\"ycsb\"); sh.shardCollection(\"ycsb.usertable\", {_id:1})"
  '

kubectl --kubeconfig "$KCFG" cp \
  "$NS/nosqlmark-client:/NoSQLMark/logs/$RUN_ID/ycsb-load.log" \
  "$RUN_DIR/logs/ycsb-load.log"
```

### Validation for both paths

`[READ ONLY]`

```bash
kubectl --kubeconfig "$KCFG" get statefulsets -n "$NS" \
  -o custom-columns=NAME:.metadata.name,DESIRED:.spec.replicas,READY:.status.readyReplicas

kubectl --kubeconfig "$KCFG" exec -n "$NS" \
  deployment/my-mongodb-sharded-mongos -- bash -c '
    /opt/bitnami/mongodb/bin/mongosh --host 127.0.0.1 --port 27017 \
      -u root -p "$(cat /bitnami/mongodb/secrets/mongodb-root-password)" \
      --authenticationDatabase admin --quiet \
      --eval "printjson({logicalCount:db.getSiblingDB(\"ycsb\").usertable.countDocuments({})}); printjson(db.getSiblingDB(\"config\").collections.findOne({_id:\"ycsb.usertable\"},{_id:1,key:1}))"
  '

kubectl --kubeconfig "$KCFG" exec -n "$NS" nosqlmark-client -- bash -lc '
  test -f /NoSQLMark/artifacts/sbt-launch-0.13.8.jar
  test -f "$HOME/.m2/repository/com/yahoo/ycsb/mongodb-binding/0.14.0-SNAPSHOT/mongodb-binding-0.14.0-SNAPSHOT.jar"
  ps -eo args | grep -E "sbt-launch|nosqlmark[.]repl[.]REPL" | grep -v grep || true
'
```

Expected: one shard at `1/1`, logical count `1000000`, shard key `{_id:1}`,
build checks pass, and no backend/REPL process is active.

## 5. Install observability and Mambo

Run this section for both setup paths.

**Prometheus terminal** — `[READ ONLY: TEMPORARY CONNECTION]`:

```bash
kubectl --kubeconfig "$KCFG" port-forward \
  -n monitoring svc/prometheus-operated 9090:9090
```

Leave it running until section 10 finishes.

**Main** — `[READ ONLY]`:

```bash
kubectl --kubeconfig "$KCFG" get prometheus \
  kps-kube-prometheus-stack-prometheus -n monitoring \
  -o jsonpath='{.spec.serviceMonitorSelector}{"\n"}{.spec.serviceMonitorNamespaceSelector}{"\n"}'
```

Continue only when both selectors print `{}`. This lets KPS discover the
experiment-owned ServiceMonitor inside `$NS`; no object in `monitoring` is changed.

`[LOCAL CHANGE]`

```bash
helm repo add prometheus-community \
  https://prometheus-community.github.io/helm-charts --force-update
helm repo update
```

### Node exporter

Mambo queries I/O wait at each data pod's node IP on port `9100`.

`[LOCAL CHANGE]`

```bash
cat > "$RUN_DIR/manifests/node-exporter-values.yaml" <<'EOF'
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
  --kubeconfig "$KCFG" --namespace "$NS" \
  -f "$RUN_DIR/manifests/node-exporter-values.yaml"
```

### MongoDB exporter

It supplies per-shard reads and Mambo's write-ratio metric. Pin it to an
`m5a.large` so it does not perturb a data node.

`[LOCAL CHANGE]`

```bash
cp MongoDB/mongodb-exporter/mongodb-exporter-servicemonitor.yaml \
  "$RUN_DIR/manifests/mongodb-exporter-servicemonitor.yaml"
sed -i \
  -e 's/  namespace: monitoring/  namespace: mambo-mongod-cpu/' \
  -e 's/      - default/      - mambo-mongod-cpu/' \
  "$RUN_DIR/manifests/mongodb-exporter-servicemonitor.yaml"
```

`[WARNING: CLUSTER CHANGE]`

```bash
kubectl --kubeconfig "$KCFG" apply -n "$NS" \
  -f MongoDB/mongodb-exporter/mongodb-exporter-deployment.yaml \
  -f MongoDB/mongodb-exporter/mongodb-exporter-service.yaml
kubectl --kubeconfig "$KCFG" apply -n "$NS" \
  -f "$RUN_DIR/manifests/mongodb-exporter-servicemonitor.yaml"
kubectl --kubeconfig "$KCFG" patch deployment/mongodb-exporter -n "$NS" \
  --type=merge \
  -p '{"spec":{"template":{"spec":{"nodeSelector":{"node.kubernetes.io/instance-type":"m5a.large"}}}}}'
```

### Mambo operator

Use the developer manifest and public `:test` image. Inspect only; never push.

`[LOCAL CHANGE; READ ONLY: EXTERNAL REGISTRY]`

```bash
sudo docker buildx imagetools inspect \
  docker.io/b00611024/mongodb-autoscaler:test \
  | tee "$RUN_DIR/operator-image-inspect.txt"
```

`[WARNING: CLUSTER CHANGE]`

```bash
kubectl --kubeconfig "$KCFG" apply -f MongoDBOperator/dist/install.yaml
```

`[LOCAL CHANGE; READ ONLY]`

```bash
kubectl --kubeconfig "$KCFG" rollout status deployment/mongodb-exporter \
  -n "$NS" --timeout=300s
kubectl --kubeconfig "$KCFG" rollout status \
  deployment/mongodboperator-controller-manager \
  -n mongodboperator-system --timeout=300s
kubectl --kubeconfig "$KCFG" get daemonsets,pods,services,servicemonitors \
  -n "$NS" -o wide

kubectl --kubeconfig "$KCFG" get pod -n "$NS" \
  -l app.kubernetes.io/name=mongodb-exporter \
  -o jsonpath='{.items[0].status.containerStatuses[0].imageID}{"\n"}' \
  | tee "$RUN_DIR/mongodb-exporter-image-id.txt"
kubectl --kubeconfig "$KCFG" get pod -n mongodboperator-system \
  -l control-plane=controller-manager \
  -o jsonpath='{.items[0].status.containerStatuses[0].imageID}{"\n"}' \
  | tee "$RUN_DIR/operator-image-id.txt"
```

### Required metric gates

Wait at least one exporter scrape, then run `[READ ONLY]`:

```bash
curl -fsSG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=up{namespace="mambo-mongod-cpu",service="mongodb-exporter"}' \
  | jq -e '.data.result | any(.value[1] == "1")'

curl -fsSG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=mongodb_collstats_latencyStats_reads_ops{namespace="mambo-mongod-cpu",database="ycsb",collection="usertable"}' \
  | jq -e '(.data.result | length > 0) and all(.data.result[]; ((.metric.shard // "") | length) > 0)'

export DATA_NODE_IP="$(kubectl --kubeconfig "$KCFG" get pod \
  my-mongodb-sharded-shard0-data-0 -n "$NS" \
  -o jsonpath='{.status.hostIP}')"
curl -fsSG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode "query=node_cpu_seconds_total{mode=\"iowait\",instance=\"$DATA_NODE_IP:9100\"}" \
  | jq -e '.data.result | length > 0'
```

All three must pass before creating the autoscaler. If the `collstats` query is
empty, rerun the logical-count query, wait 30 seconds, and retry.

Also let post-load CPU settle below the `55%` boundary before starting.

```bash
curl -fsSG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=100 * avg(sum by(pod) (rate(container_cpu_usage_seconds_total{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard[0-9]+-data.*",container!="POD",container!=""}[5m])) / sum by(pod) (kube_pod_container_resource_requests{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard[0-9]+-data.*",resource="cpu"}))' \
  | jq -r '.data.result[].value[1]'
```

## 6. Save the pre-scale state

Define this read-only helper once in **Main**. It records data balance and the
hot key's owning shard without changing MongoDB.

`[LOCAL CHANGE; READ ONLY DATABASE QUERY]`

```bash
capture_state() {
  local label="$1"
  kubectl --kubeconfig "$KCFG" exec -n "$NS" \
    deployment/my-mongodb-sharded-mongos -- bash -c '
      /opt/bitnami/mongodb/bin/mongosh --host 127.0.0.1 --port 27017 \
        -u root -p "$(cat /bitnami/mongodb/secrets/mongodb-root-password)" \
        --authenticationDatabase admin --quiet --eval '\''
          const hot = "user6284781860667377211";
          const c = db.getSiblingDB("ycsb").usertable;
          printjson({capturedAt:new Date(), hotKey:hot,
            document:c.findOne({_id:hot},{_id:1})});
          printjson(db.adminCommand({listShards:1}));
          printjson(db.adminCommand({balancerStatus:1}));
          printjson({logicalCount:c.countDocuments({})});
          c.getShardDistribution();
          printjson(c.explain("executionStats").find({_id:hot}));
        '\''
    ' | tee "$RUN_DIR/snapshots/$label.txt"
}

capture_state pre-scale
```

Stop if `document` is `null`; the assumed hot `_id` must be verified.

## 7. Start observers and configure Mambo

**Controller terminal** — `[LOCAL CHANGE; READ ONLY CLUSTER]`:

```bash
kubectl --kubeconfig "$KCFG" logs -n mongodboperator-system \
  deployment/mongodboperator-controller-manager \
  -c manager --timestamps -f \
  | tee "$RUN_DIR/logs/controller.log"
```

**Kubernetes terminal** — `[LOCAL CHANGE; READ ONLY CLUSTER]`:

```bash
kubectl --kubeconfig "$KCFG" get pods -n "$NS" -o wide -w \
  | tee "$RUN_DIR/logs/kubernetes-watch.log"
```

**Main** — `[LOCAL CHANGE]`:

```bash
cp MongoDB/result/mongod_cpu_exp/autoscaler_v1alpha1_mongodautoscaler.yaml \
  "$RUN_DIR/manifests/mongodautoscaler.yaml"
sed -i \
  -e 's/    namespace: default/    namespace: mambo-mongod-cpu/' \
  -e '/  name: mongodautoscaler-sample/a\  namespace: mambo-mongod-cpu' \
  -e 's/    maxReplicas: 2/    maxReplicas: 1/' \
  -e 's/    cpuTargetPercent: 60/    cpuTargetPercent: 50/' \
  -e 's/    cpuTolerancePercent: 10/    cpuTolerancePercent: 5/' \
  -e 's/    cooldownSeconds: 300/    cooldownSeconds: 3600/' \
  "$RUN_DIR/manifests/mongodautoscaler.yaml"
sed -n '1,45p' "$RUN_DIR/manifests/mongodautoscaler.yaml"
```

Expected bounds: shards `1..2`, replicas `1..1`, CPU `50 +/- 5`, window `5m`,
cooldown `3600`. The long cooldown begins after scale-up completes and prevents
a scale-down during this run.

`[READ ONLY: SERVER DRY RUN]`

```bash
kubectl --kubeconfig "$KCFG" apply --dry-run=server -n "$NS" \
  -f "$RUN_DIR/manifests/mongodautoscaler.yaml" -o yaml \
  > "$RUN_DIR/manifests/mongodautoscaler-dry-run.yaml"
```

Do not apply it until Backend and REPL are connected in section 8.

## 8. Define and run the one continuous job

**Main** — `[LOCAL CHANGE]`:

```bash
cat > "$RUN_DIR/manifests/mambo-hotspot-job.scala" <<EOF
val hotspotExperiment = CoreJob(
  jobID = nc.genID,
  batchname = "mambo-hotspot-$RUN_ID",
  workload = "CoreWorkload",
  dbname = "MongoDbClient",
  dbproperties = Map(
    "mongodb.url" ->
      "mongodb://root:mongodb123@my-mongodb-sharded:27017/ycsb?authSource=admin&readPreference=nearest&retryWrites=false&retryReads=false&maxPoolSize=$MAX_POOL_SIZE&waitQueueMultiple=$WAIT_QUEUE_MULTIPLE"
  ),
  target = ${TARGET_RPS}.0,
  nodes = 1,
  worker = 1,
  table = "usertable",
  phase = "transactional",
  asyncmode = true,
  counts = CoreCounts(
    recordcount = 1000000,
    warmupcount = $WARMUP_COUNT,
    operationcount = $OPERATION_COUNT,
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
    requestdistribution = "hotspot",
    insertorder = "hashed",
    hotspotdatafraction = 0.0000011,
    hotspotopnfraction = 0.8
  ),
  loadgeneration = CoreLoadGeneration(
    interrequesttimedistribution = "constant"
  ),
  logmeasurements = true,
  logjvmstats = false
)

println(hotspotExperiment)
EOF
```

`0.0000011 * 1,000,000` truncates to one hot logical key.

`[WARNING: POD CHANGE]`

```bash
kubectl --kubeconfig "$KCFG" cp \
  "$RUN_DIR/manifests/mambo-hotspot-job.scala" \
  "$NS/nosqlmark-client:/NoSQLMark/artifacts/mambo-hotspot-job.scala"
```

**Backend terminal** — `[WARNING: POD CHANGE]`; leave running:

```bash
kubectl --kubeconfig "$KCFG" exec -it -n "$NS" \
  nosqlmark-client -- env RUN_ID="$RUN_ID" bash -lc '
    cd /NoSQLMark
    mkdir -p "logs/$RUN_ID"
    java -jar artifacts/sbt-launch-0.13.8.jar \
      "project backbench" run \
      2>&1 | tee "logs/$RUN_ID/backbench-console.log"
  '
```

**REPL terminal** — `[WARNING: POD CHANGE]`; start but do not submit yet:

```bash
kubectl --kubeconfig "$KCFG" exec -it -n "$NS" \
  nosqlmark-client -- env RUN_ID="$RUN_ID" bash -lc '
    set -euo pipefail
    cd /NoSQLMark
    mkdir -p "logs/$RUN_ID"
    CPFILE="$(mktemp /tmp/nosqlmark-repl-classpath.XXXXXX)"
    java -jar artifacts/sbt-launch-0.13.8.jar \
      "project repl" "export fullClasspath" > "$CPFILE"
    CP="$(tail -n 1 "$CPFILE")"
    java -Xmx1G -Dlogback.configurationFile=config/logback.xml \
      -cp "$CP" de.unihamburg.informatik.nosqlmark.repl.REPL \
      2>&1 | tee "logs/$RUN_ID/repl-console.log"
  '
```

Wait for `Connected to BackbenchService`.

**Main** — `[LOCAL CHANGE]`; record the range start, then activate Mambo:

```bash
export START_EPOCH="$(date -u +%s)"
date -u +%Y-%m-%dT%H:%M:%SZ | tee "$RUN_DIR/experiment-start-utc.txt"
printf '%s\n' "$START_EPOCH" | tee "$RUN_DIR/experiment-start-epoch.txt"
printf 'export START_EPOCH=%q\n' "$START_EPOCH" >> "$RUN_DIR/run-env.sh"
```

`[WARNING: CLUSTER CHANGE]`

```bash
kubectl --kubeconfig "$KCFG" apply -n "$NS" \
  -f "$RUN_DIR/manifests/mongodautoscaler.yaml"
```

Immediately record workload start in **Main** — `[LOCAL CHANGE]`:

```bash
export WORKLOAD_START_EPOCH="$(date -u +%s)"
date -u +%Y-%m-%dT%H:%M:%SZ | tee "$RUN_DIR/workload-start-utc.txt"
printf '%s\n' "$WORKLOAD_START_EPOCH" \
  | tee "$RUN_DIR/workload-start-epoch.txt"
```

Then enter in **REPL**:

```scala
:load /NoSQLMark/artifacts/mambo-hotspot-job.scala
nc.submitJob(hotspotExperiment)
```

Expected runtime: about 16 minutes. Keep all terminals running.

## 9. Observe scaling and stable post-scale skew

**Status terminal** — `[READ ONLY]`:

```bash
kubectl --kubeconfig "$KCFG" get mongodautoscaler \
  mongodautoscaler-sample -n "$NS" -o json | jq '.status'
kubectl --kubeconfig "$KCFG" get statefulsets,jobs -n "$NS"

curl -fsSG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=100 * sum by(pod) (rate(container_cpu_usage_seconds_total{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard[0-9]+-data.*",container="mongodb"}[1m])) / sum by(pod) (kube_pod_container_resource_requests{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard[0-9]+-data.*",container="mongodb",resource="cpu"})' \
  | jq -r '.data.result[] | [.metric.pod,.value[1]] | @tsv'

curl -fsSG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=max by(shard) (rate(mongodb_collstats_latencyStats_reads_ops{namespace="mambo-mongod-cpu",database="ycsb",collection="usertable"}[1m]))' \
  | jq -r '.data.result[] | [.metric.shard,.value[1]] | @tsv'
```

Confirm from `controller.log` that scale-up was CPU-triggered: observed CPU
must exceed `55`; I/O wait must remain below its `35` scale-up boundary.

While the workload is still running, wait for:

- `lastDesiredShards: 2` and `scalingPhase: Idle`;
- both shard StatefulSets at `1/1`;
- repeated `balancerStatus.inBalancerRound: false` observations.

Then, in the same **Main** shell where `capture_state` was defined:

```bash
capture_state post-scale-1
sleep 60
capture_state post-scale-2
```

Continue for at least five stable two-shard minutes after `post-scale-2`. If
the job ends sooner, mark the run inconclusive and repeat with a longer
`MEASURE_SECONDS`/`OPERATION_COUNT`; do not start a second overlapping job.

The controller's resharding-complete message checks one balancer instant. Two
snapshots help verify that the post-scale distribution is stable.

Do not assume the hot key moved. Either outcome supports the test if one shard
continues to own the key and dominate post-scale reads/CPU.

## 10. Validate and preserve before cleanup

After REPL prints `received result for job`, inspect the measured result.

`[READ ONLY]`

```bash
kubectl --kubeconfig "$KCFG" exec -n "$NS" nosqlmark-client -- \
  env RUN_ID="$RUN_ID" bash -lc '
    summary="$(find "/NoSQLMark/results/mambo-hotspot-$RUN_ID" \
      -mindepth 2 -maxdepth 2 -type f -name summary.json | head -n 1)"
    printf "Summary: %s\n" "$summary" >&2
    cat "$summary"
  ' | jq '{
    runtime: .Overall["RunTime(ms)"],
    throughput: .Overall["Throughput(ops/sec)"],
    read_count: .Read.Count,
    failed: (.["Read-FAILED"].Count // "0"),
    timed_out: (.["Read-TIMEDOUT"].Count // "0"),
    mean_ms: .Read["Mean(ms)"],
    p95_ms: .Read["95Percentile(ms)"],
    p99_ms: .Read["99Percentile(ms)"]
  }'
```

A valid measured phase has `read_count=900000`, `failed=0`, `timed_out=0`,
and throughput near 1,000. Warm-up-only queue warnings do not invalidate it;
failed measured reads do.

After the job and both stable snapshots — `[LOCAL CHANGE]`:

```bash
export END_EPOCH="$(date -u +%s)"
date -u +%Y-%m-%dT%H:%M:%SZ | tee "$RUN_DIR/experiment-end-utc.txt"
printf '%s\n' "$END_EPOCH" | tee "$RUN_DIR/experiment-end-epoch.txt"
```

### Preserve historical Prometheus data

Do this before stopping the port-forward or deleting exporters.

`[LOCAL CHANGE; READ ONLY METRICS]`

```bash
CPU_BY_POD='100 * sum by(pod) (rate(container_cpu_usage_seconds_total{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard[0-9]+-data.*",container="mongodb"}[1m])) / sum by(pod) (kube_pod_container_resource_requests{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard[0-9]+-data.*",container="mongodb",resource="cpu"})'
MAMBO_CPU='100 * avg(sum by(pod) (rate(container_cpu_usage_seconds_total{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard[0-9]+-data.*",container!="POD",container!=""}[5m])) / sum by(pod) (kube_pod_container_resource_requests{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard[0-9]+-data.*",resource="cpu"}))'
READS_BY_SHARD='max by(shard) (rate(mongodb_collstats_latencyStats_reads_ops{namespace="mambo-mongod-cpu",database="ycsb",collection="usertable"}[1m]))'
CLIENT_CPU='sum(rate(container_cpu_usage_seconds_total{namespace="mambo-mongod-cpu",pod="nosqlmark-client",container!="",container!="POD"}[1m]))'

save_range() {
  local name="$1" query="$2"
  curl -fsSG http://127.0.0.1:9090/api/v1/query_range \
    --data-urlencode "query=$query" \
    --data-urlencode "start=$START_EPOCH" \
    --data-urlencode "end=$END_EPOCH" \
    --data-urlencode 'step=15s' \
    > "$RUN_DIR/metrics/$name.json"
  jq -e '.status == "success" and (.data.result | length > 0)' \
    "$RUN_DIR/metrics/$name.json" >/dev/null
}

save_range cpu-by-pod "$CPU_BY_POD"
save_range mambo-cpu "$MAMBO_CPU"
save_range reads-by-shard "$READS_BY_SHARD"
save_range client-cpu "$CLIENT_CPU"

jq -e '[.data.result[].metric.pod] | unique | sort == ["my-mongodb-sharded-shard0-data-0","my-mongodb-sharded-shard1-data-0"]' \
  "$RUN_DIR/metrics/cpu-by-pod.json" >/dev/null
jq -e '[.data.result[].metric.shard] | unique | sort == ["my-mongodb-sharded-shard-0","my-mongodb-sharded-shard-1"]' \
  "$RUN_DIR/metrics/reads-by-shard.json" >/dev/null

printf '%s\n' \
  "CPU_BY_POD=$CPU_BY_POD" \
  "MAMBO_CPU=$MAMBO_CPU" \
  "READS_BY_SHARD=$READS_BY_SHARD" \
  "CLIENT_CPU=$CLIENT_CPU" \
  > "$RUN_DIR/metrics/queries.txt"
printf 'start=%s end=%s\n' "$START_EPOCH" "$END_EPOCH" \
  > "$RUN_DIR/metrics/range.txt"
```

The shard-1 series starts when its pod/exporter series appears. Preserve that
missing prefix as missing data later; do not replace it with zero.

### Preserve NoSQLMark and final Kubernetes state

Stop Backend and REPL with `Ctrl-C` only after the result is printed. Then:

`[LOCAL CHANGE; READ ONLY CLUSTER/POD]`

```bash
mkdir -p "$RUN_DIR/nosqlmark"/{backend-logs,client-logs,results}

kubectl --kubeconfig "$KCFG" cp \
  "$NS/nosqlmark-client:/NoSQLMark/backbench/logs/." \
  "$RUN_DIR/nosqlmark/backend-logs/"
kubectl --kubeconfig "$KCFG" cp \
  "$NS/nosqlmark-client:/NoSQLMark/logs/$RUN_ID/." \
  "$RUN_DIR/nosqlmark/client-logs/"
kubectl --kubeconfig "$KCFG" cp \
  "$NS/nosqlmark-client:/NoSQLMark/results/mambo-hotspot-$RUN_ID/." \
  "$RUN_DIR/nosqlmark/results/"

test -n "$(find "$RUN_DIR/nosqlmark/backend-logs" \
  -type f -name 'timeseries*.log' -size +0c \
  -newermt "@$START_EPOCH" -print -quit)"

kubectl --kubeconfig "$KCFG" get mongodautoscaler \
  mongodautoscaler-sample -n "$NS" -o yaml \
  > "$RUN_DIR/snapshots/final-mongodautoscaler.yaml"
kubectl --kubeconfig "$KCFG" get statefulsets -n "$NS" -o yaml \
  > "$RUN_DIR/snapshots/final-statefulsets.yaml"
kubectl --kubeconfig "$KCFG" get pods -n "$NS" -o wide \
  > "$RUN_DIR/snapshots/final-pods.txt"
kubectl --kubeconfig "$KCFG" get pvc -n "$NS" -o wide \
  > "$RUN_DIR/snapshots/final-pvcs.txt"
kubectl --kubeconfig "$KCFG" get jobs -n "$NS" -o yaml \
  > "$RUN_DIR/snapshots/final-jobs.yaml"
kubectl --kubeconfig "$KCFG" get events -n "$NS" \
  --sort-by=.metadata.creationTimestamp \
  > "$RUN_DIR/logs/final-events.txt"

capture_state final
find "$RUN_DIR" -maxdepth 5 -type f -printf '%P\t%k KB\n' \
  | sort | tee "$RUN_DIR/artifact-index.txt"
```

Copying every `backbench/logs/timeseries*.log` is intentional: the time-series
appender rolls files, and later analysis must filter all of them to this job's
timestamps.

### Freeze only after the workflow is idle

`[READ ONLY]`

```bash
kubectl --kubeconfig "$KCFG" get mongodautoscaler \
  mongodautoscaler-sample -n "$NS" -o json | jq '.status'
```

If it is `Idle` and evidence is preserved, pause Mambo before later cleanup.

`[WARNING: CLUSTER CHANGE]`

```bash
kubectl --kubeconfig "$KCFG" scale \
  deployment/mongodboperator-controller-manager \
  -n mongodboperator-system --replicas=0
```

Now stop Controller, Kubernetes, and Prometheus with `Ctrl-C`. Cleanup is a
separate reviewed step. Never delete `foxtrot` or shared `monitoring`.

## 11. Interpretation and repeat policy

The hotspot limitation is supported when all of these hold:

1. The clean 1,000-RPS measured phase completes.
2. Mambo scales from one to two shards because its 5-minute CPU average
   crossed `55%`, not because I/O wait crossed `35%`.
3. Balancing becomes stable and data exists on both shards.
4. The hot key remains owned by exactly one shard, whether it stayed or moved.
5. That owner continues to dominate read rate and `mongod` CPU while Mambo's
   average across both shards falls.

This is a worst-case indivisible-key hotspot, not a claim about every skewed
workload. Repeat with a new `RUN_ID` at least three times. A later uniform-key
control should use the same topology, RPS, duration, and Mambo policy.
