# NoSQLMark open-loop RPS capacity check

Bare-bones calibration before enabling Mambo. Find the highest offered RPS whose
measured phase completes without failed or timed-out reads on a fixed topology.

- MongoDB: one shard, one replica-set member.
- Scaling disabled: do **not** install the Mambo operator or create a
  `MongodAutoscaler` resource.
- Each trial: 60-second warm-up, then 180-second measured phase.
- Warm-up-only queue warnings are allowed; the measured summary decides success.
- One RPS value at a time; no automated sweep.
- No plotting or long-term artifact collection.

The result is specific to this workload and client configuration. It is not a
universal MongoDB maximum.

## Safety

| Marker | Effect |
|---|---|
| `[READ ONLY]` | Inspects the cluster or metrics only. |
| `[LOCAL CHANGE]` | Changes a local or `/tmp` file on `node0`. |
| `[WARNING: POD CHANGE]` | Changes files or processes inside the experiment pod. |
| `[WARNING: CLUSTER CHANGE]` | Creates Kubernetes resources in `mambo-mongod-cpu`. |
| `[WARNING: DATABASE CHANGE]` | Writes MongoDB data or metadata. |

Use only namespace `mambo-mongod-cpu`. Do not modify `foxtrot`, shared
`monitoring`, or the control-plane node.

NoSQLMark may print the MongoDB URI and experiment password. Do not publish its
console output without redaction.

## Terminals

| Terminal | Purpose |
|---|---|
| **Main** | Setup, load, job definition, validation |
| **Prometheus** | Port-forward; leave running |
| **Backend** | NoSQLMark backbench process |
| **REPL** | Submit the job and receive its result |
| **Status** (optional) | CPU and fixed-topology checks |

In each terminal:

```bash
cd /users/adas2125/Autoscaling
export KCFG=/users/adas2125/.kube/amit.kubeconfig
export NS=mambo-mongod-cpu
```

## 1. Clean-state preflight

`[READ ONLY]`

```bash
kubectl --kubeconfig "$KCFG" config current-context
kubectl --kubeconfig "$KCFG" get nodes \
  -L node.kubernetes.io/instance-type \
  -L topology.kubernetes.io/zone
kubectl --kubeconfig "$KCFG" get storageclass
kubectl --kubeconfig "$KCFG" get namespace "$NS"
kubectl --kubeconfig "$KCFG" \
  get statefulsets -A -l app.kubernetes.io/name=mongodb-sharded
kubectl --kubeconfig "$KCFG" \
  get crd mongodautoscalers.autoscaler.mongodb.io
kubectl --kubeconfig "$KCFG" get pods -n foxtrot
helm --kubeconfig "$KCFG" list -A

git -C ../NoSQLMark rev-parse HEAD
kubectl version --client
helm version --short
jq --version
```

Expected before a fresh setup:

- `mambo-mongod-cpu` absent.
- No experiment MongoDB StatefulSets.
- Mambo CRD absent (`NotFound` is expected).
- Foxtrot healthy and shared `kps` still deployed.

If an old experiment namespace or autoscaler still exists, stop here and
review cleanup separately.

`[LOCAL CHANGE]`

```bash
helm repo add bitnami https://charts.bitnami.com/bitnami --force-update
helm repo update

cat > /tmp/mambo-rps-placement-values.yaml <<'EOF'
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
  -f /tmp/mambo-rps-placement-values.yaml \
  >/dev/null
```

## 2. Deploy the fixed MongoDB topology

These are the only required cluster deployments. Node exporter, MongoDB
exporter, the Mambo operator, and its autoscaler resource are intentionally
omitted.

`[WARNING: CLUSTER CHANGE]`

```bash
kubectl --kubeconfig "$KCFG" create namespace "$NS"

helm install my-mongodb-sharded bitnami/mongodb-sharded \
  --version 9.4.12 \
  --kubeconfig "$KCFG" \
  --namespace "$NS" \
  -f MongoDB/result/mongod_cpu_exp/values.yaml \
  -f /tmp/mambo-rps-placement-values.yaml
```

`[READ ONLY]`

```bash
kubectl --kubeconfig "$KCFG" \
  rollout status statefulset/my-mongodb-sharded-configsvr \
  -n "$NS" --timeout=600s
kubectl --kubeconfig "$KCFG" \
  rollout status statefulset/my-mongodb-sharded-shard0-data \
  -n "$NS" --timeout=600s
kubectl --kubeconfig "$KCFG" \
  rollout status deployment/my-mongodb-sharded-mongos \
  -n "$NS" --timeout=600s

kubectl --kubeconfig "$KCFG" \
  get statefulsets,deployments,pods,pvc -n "$NS" -o wide
kubectl --kubeconfig "$KCFG" \
  get statefulsets -n "$NS" \
  -o custom-columns=NAME:.metadata.name,DESIRED:.spec.replicas,READY:.status.readyReplicas
kubectl --kubeconfig "$KCFG" \
  get crd mongodautoscalers.autoscaler.mongodb.io
```

Expected:

- `my-mongodb-sharded-shard0-data`: desired/ready `1`.
- No `shard1-data` or later shard StatefulSet.
- No Mambo CRD (`NotFound`), operator, or autoscaler resource.

## 3. Prometheus and CPU checks

Shared KPS already supplies cAdvisor container CPU and kube-state resource
request metrics. A separate node exporter is not needed for this experiment.

**Prometheus terminal** — `[READ ONLY: TEMPORARY CONNECTION]`:

```bash
kubectl --kubeconfig "$KCFG" \
  port-forward -n monitoring svc/prometheus-operated 9090:9090
```

**Status terminal** — `[READ ONLY]`:

Verify the metrics exist:

```bash
curl -sG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=kube_pod_container_resource_requests{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard[0-9]+-data-.*",container="mongodb",resource="cpu"}' \
  | jq -r '.data.result[] | [.metric.pod, .value[1]] | @tsv'
```

Raw `mongod` CPU cores per pod:

```bash
curl -sG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=sum by(pod) (rate(container_cpu_usage_seconds_total{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard[0-9]+-data-.*",container="mongodb"}[1m]))' \
  | jq -r '.data.result[] | [.metric.pod, .value[1]] | @tsv'
```

`mongod` CPU as a percentage of its CPU request:

```bash
curl -sG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=100 * avg(sum by(pod) (rate(container_cpu_usage_seconds_total{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard[0-9]+-data-.*",container="mongodb"}[1m])) / sum by(pod) (kube_pod_container_resource_requests{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard[0-9]+-data-.*",container="mongodb",resource="cpu"}))' \
  | jq -r '.data.result[].value[1]'
```

NoSQLMark client CPU, after its pod is running:

```bash
curl -sG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=sum(rate(container_cpu_usage_seconds_total{namespace="mambo-mongod-cpu",pod="nosqlmark-client",container!="",container!="POD"}[1m]))' \
  | jq -r '.data.result[].value[1]'
```

Rerun these commands manually during each trial. CPU percentage may exceed
100% because it is normalized to the `0.5`-CPU request, not the one-CPU limit.

## 4. Prepare NoSQLMark

The backend and REPL share one client pod on the dedicated `m5a.large` node.

`[WARNING: CLUSTER CHANGE]`

```bash
kubectl --kubeconfig "$KCFG" run nosqlmark-client \
  --namespace "$NS" \
  --image=maven:3.8.7-eclipse-temurin-8 \
  --restart=Never \
  --overrides='{"spec":{"nodeSelector":{"kubernetes.io/hostname":"i-063b793db694be24c"}}}' \
  --command -- sleep infinity

kubectl --kubeconfig "$KCFG" \
  wait --for=condition=Ready pod/nosqlmark-client \
  -n "$NS" --timeout=300s
```

`[READ ONLY]`

```bash
kubectl --kubeconfig "$KCFG" get pod nosqlmark-client -n "$NS" -o wide
kubectl --kubeconfig "$KCFG" exec -n "$NS" nosqlmark-client -- bash -lc \
  'java -version; javac -version; mvn -version; git --version; curl --version; tar --version'
```

Expected: Java 8, Maven 3.8.7, and `git`, `curl`, and `tar` available.

Copy the reviewed local NoSQLMark source, then build its pinned YCSB binding.

`[WARNING: POD CHANGE]`

```bash
kubectl --kubeconfig "$KCFG" exec -n "$NS" nosqlmark-client -- \
  mkdir -p /NoSQLMark
kubectl --kubeconfig "$KCFG" cp \
  ../NoSQLMark/. "$NS/nosqlmark-client:/NoSQLMark"

kubectl --kubeconfig "$KCFG" \
  exec -it -n "$NS" nosqlmark-client -- bash -lc '
    set -euo pipefail
    git clone https://github.com/steffenfriedrich/YCSB.git /YCSB
    git -C /YCSB checkout --detach b73ac8367b7de0356031684883338ec1826c1a4f
    sed -i "s#http://www.allanbank.com/repo/#https://www.allanbank.com/repo/#" \
      /YCSB/mongodb/pom.xml
    sed -i "s#<mongodb.version>3.0.3</mongodb.version>#<mongodb.version>3.12.14</mongodb.version>#" \
      /YCSB/pom.xml
    cd /YCSB
    mvn -pl mongodb -am -DskipTests -Dcheckstyle.skip=true install
    mkdir -p /tmp/nosqlmark-mongodb-tools
    tar -xzf mongodb/target/ycsb-mongodb-binding-0.14.0-SNAPSHOT.tar.gz \
      -C /tmp/nosqlmark-mongodb-tools
    test -x /tmp/nosqlmark-mongodb-tools/ycsb-mongodb-binding-0.14.0-SNAPSHOT/bin/ycsb.sh
    test -f /tmp/nosqlmark-mongodb-tools/ycsb-mongodb-binding-0.14.0-SNAPSHOT/lib/mongo-java-driver-3.12.14.jar
  '

kubectl --kubeconfig "$KCFG" \
  exec -it -n "$NS" nosqlmark-client -- bash -lc '
    set -euo pipefail
    cd /NoSQLMark
    mkdir -p artifacts results
    curl -fL \
      https://repo.scala-sbt.org/scalasbt/ivy-releases/org.scala-sbt/sbt-launch/0.13.8/sbt-launch.jar \
      -o artifacts/sbt-launch-0.13.8.jar
    echo "6570bb03df6138ffaa7ac0bbe35eb4ea79062d1146b6929c75cf238d14dd9158  artifacts/sbt-launch-0.13.8.jar" \
      | sha256sum -c -
    java -jar artifacts/sbt-launch-0.13.8.jar "project backbench" compile
    java -jar artifacts/sbt-launch-0.13.8.jar "project repl" compile
  '
```

`[READ ONLY]` Final client check:

```bash
kubectl --kubeconfig "$KCFG" exec -n "$NS" nosqlmark-client -- bash -lc '
  test -f /NoSQLMark/artifacts/sbt-launch-0.13.8.jar
  test -f "$HOME/.m2/repository/com/yahoo/ycsb/mongodb-binding/0.14.0-SNAPSHOT/mongodb-binding-0.14.0-SNAPSHOT.jar"
  test -f /tmp/nosqlmark-mongodb-tools/ycsb-mongodb-binding-0.14.0-SNAPSHOT/lib/mongo-java-driver-3.12.14.jar
  echo ready
'
```

## 5. Load and shard once

Do this once. Changing only the test RPS later does not require another load.

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

`[WARNING: DATABASE CHANGE]` Loads one million records with 10 threads:

```bash
kubectl --kubeconfig "$KCFG" \
  exec -it -n "$NS" nosqlmark-client -- bash -lc '
    set -euo pipefail
    cd /tmp/nosqlmark-mongodb-tools/ycsb-mongodb-binding-0.14.0-SNAPSHOT
    ./bin/ycsb.sh load mongodb -s \
      -P workloads/workloadr \
      -threads 10 \
      -p "mongodb.url=mongodb://root:mongodb123@my-mongodb-sharded:27017/ycsb?authSource=admin&retryWrites=false&retryReads=false"
  '
```

`[WARNING: DATABASE CHANGE]` Enable sharding and set the original `_id` shard
key:

```bash
kubectl --kubeconfig "$KCFG" \
  exec -n "$NS" deployment/my-mongodb-sharded-mongos -- bash -c '
    /opt/bitnami/mongodb/bin/mongosh --host 127.0.0.1 --port 27017 \
      -u root \
      -p "$(cat /bitnami/mongodb/secrets/mongodb-root-password)" \
      --authenticationDatabase admin --quiet \
      --eval "sh.enableSharding(\"ycsb\"); sh.shardCollection(\"ycsb.usertable\", {_id:1})"
  '
```

`[READ ONLY]` Validate the dataset and fixed topology:

```bash
kubectl --kubeconfig "$KCFG" \
  exec -n "$NS" deployment/my-mongodb-sharded-mongos -- bash -c '
    /opt/bitnami/mongodb/bin/mongosh --host 127.0.0.1 --port 27017 \
      -u root \
      -p "$(cat /bitnami/mongodb/secrets/mongodb-root-password)" \
      --authenticationDatabase admin --quiet \
      --eval "printjson(db.adminCommand({listShards:1})); printjson({logicalCount:db.getSiblingDB(\"ycsb\").usertable.countDocuments({})}); printjson(db.getSiblingDB(\"config\").collections.findOne({_id:\"ycsb.usertable\"},{_id:1,key:1}))"
  '

kubectl --kubeconfig "$KCFG" \
  get statefulsets -n "$NS" \
  -o custom-columns=NAME:.metadata.name,DESIRED:.spec.replicas,READY:.status.readyReplicas
```

Expected: one shard, logical count `1000000`, shard key `{ _id: 1 }`, and
`shard0-data` at `1/1`. Let CPU return near its stable idle value before the
first trial.

## 6. Define one RPS trial

Run this in **Main** before each trial. Replace `250` with the RPS being tested.
Keep the distribution and pool settings unchanged throughout one sweep.

This defaults to a single-key hotspot: 80% of reads target approximately one of
the one million records. Use `zipfian` only for the earlier Zipfian workload.

`[LOCAL CHANGE]`

```bash
export TEST_RPS=250
export WARMUP_SECONDS=60
export MEASURE_SECONDS=180
export REQUEST_DISTRIBUTION=hotspot
export MAX_POOL_SIZE=100
export WAIT_QUEUE_MULTIPLE=5

export WARMUP_COUNT=$((TEST_RPS * WARMUP_SECONDS))
export OPERATION_COUNT=$((TEST_RPS * MEASURE_SECONDS))
export TRIAL_ID="rps-${TEST_RPS}-$(date -u +%Y%m%dT%H%M%SZ)"

printf 'Trial=%s RPS=%s warm-up=%ss (%s ops) measured=%ss (%s ops)\n' \
  "$TRIAL_ID" "$TEST_RPS" "$WARMUP_SECONDS" "$WARMUP_COUNT" \
  "$MEASURE_SECONDS" "$OPERATION_COUNT"

cat > /tmp/mambo-rps-job.scala <<EOF
val rpsCapacityTrial = CoreJob(
  jobID = nc.genID,
  batchname = "rps-capacity-$TRIAL_ID",
  workload = "CoreWorkload",
  dbname = "MongoDbClient",
  dbproperties = Map(
    "mongodb.url" ->
      "mongodb://root:mongodb123@my-mongodb-sharded:27017/ycsb?authSource=admin&readPreference=nearest&retryWrites=false&retryReads=false&maxPoolSize=$MAX_POOL_SIZE&waitQueueMultiple=$WAIT_QUEUE_MULTIPLE"
  ),
  target = ${TEST_RPS}.0,
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
    requestdistribution = "$REQUEST_DISTRIBUTION",
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

println(rpsCapacityTrial)
EOF
```

The explicit pool settings reproduce the current driver defaults: 100 MongoDB
connections and at most 500 waiting operations (`100 * 5`). Do not change them
mid-sweep. A larger pool or queue defines a separate capacity experiment.

`[WARNING: POD CHANGE]`

```bash
kubectl --kubeconfig "$KCFG" cp \
  /tmp/mambo-rps-job.scala \
  "$NS/nosqlmark-client:/NoSQLMark/artifacts/mambo-rps-job.scala"
```

## 7. Run the trial

Start a fresh Backend and REPL for every RPS value. This isolates warning
counts and removes queued Futures from an earlier trial.

**Backend terminal** — `[WARNING: POD CHANGE]`. Leave running:

```bash
kubectl --kubeconfig "$KCFG" \
  exec -it -n "$NS" nosqlmark-client -- bash -lc '
    cd /NoSQLMark
    : > /tmp/nosqlmark-capacity-backend.log
    java -jar artifacts/sbt-launch-0.13.8.jar \
      "project backbench" run \
      2>&1 | tee /tmp/nosqlmark-capacity-backend.log
  '
```

**REPL terminal** — `[WARNING: POD CHANGE]`:

```bash
kubectl --kubeconfig "$KCFG" \
  exec -it -n "$NS" nosqlmark-client -- bash -lc '
    set -euo pipefail
    cd /NoSQLMark
    CPFILE="$(mktemp /tmp/nosqlmark-repl-classpath.XXXXXX)"
    java -jar artifacts/sbt-launch-0.13.8.jar \
      "project repl" "export fullClasspath" > "$CPFILE"
    CP="$(tail -n 1 "$CPFILE")"
    java -Xmx1G \
      -Dlogback.configurationFile=config/logback.xml \
      -cp "$CP" \
      de.unihamburg.informatik.nosqlmark.repl.REPL
  '
```

Wait for `Connected to BackbenchService`, then enter:

```scala
:load /NoSQLMark/artifacts/mambo-rps-job.scala
nc.submitJob(rpsCapacityTrial)
```

Nominal duration is about four minutes: one minute of warm-up plus three
minutes measured, followed by any drain time. `warmupcount` is excluded from
the reported measurement histogram, but queue warnings during warm-up still
appear in the Backend terminal.

## 8. Monitor and decide pass/fail

**Status terminal** — `[READ ONLY]`:

```bash
kubectl --kubeconfig "$KCFG" \
  get statefulsets -n "$NS" \
  -o custom-columns=NAME:.metadata.name,DESIRED:.spec.replicas,READY:.status.readyReplicas

curl -sG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=100 * avg(sum by(pod) (rate(container_cpu_usage_seconds_total{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard[0-9]+-data-.*",container="mongodb"}[1m])) / sum by(pod) (kube_pod_container_resource_requests{namespace="mambo-mongod-cpu",pod=~"my-mongodb-sharded-shard[0-9]+-data-.*",container="mongodb",resource="cpu"}))' \
  | jq -r '.data.result[].value[1]'

curl -sG http://127.0.0.1:9090/api/v1/query \
  --data-urlencode 'query=sum(rate(container_cpu_usage_seconds_total{namespace="mambo-mongod-cpu",pod="nosqlmark-client",container!="",container!="POD"}[1m]))' \
  | jq -r '.data.result[].value[1]'
```

After `received result for job`, count queue warnings for context. This count
includes warm-up and is not the pass/fail test:

```bash
kubectl --kubeconfig "$KCFG" exec -n "$NS" nosqlmark-client -- bash -lc '
  grep -c "MongoWaitQueueFullException" /tmp/nosqlmark-capacity-backend.log || true
'
```

If the count is nonzero, inspect a few occurrences:

```bash
kubectl --kubeconfig "$KCFG" exec -n "$NS" nosqlmark-client -- bash -lc '
  grep -n "MongoWaitQueueFullException" /tmp/nosqlmark-capacity-backend.log | head
'
```

Confirm the submitted target and expected measured count:

```bash
kubectl --kubeconfig "$KCFG" exec -n "$NS" nosqlmark-client -- bash -lc '
  grep -E "batchname|target =|warmupcount|operationcount" \
    /NoSQLMark/artifacts/mambo-rps-job.scala
  '
```

Read the newest capacity result. This also works when `TRIAL_ID` is unset in
the status terminal:

```bash
kubectl --kubeconfig "$KCFG" exec -n "$NS" nosqlmark-client -- bash -lc '
  latest="$(ls -1dt /NoSQLMark/results/rps-capacity-* 2>/dev/null | head -n 1)"
  summary="$(find "$latest" -mindepth 2 -maxdepth 2 \
    -type f -name summary.json | head -n 1)"
  cat "$summary"
  ' | jq '{
    throughput: .Overall["Throughput(ops/sec)"],
    successful: .Read.Count,
    failed: (.["Read-FAILED"].Count // "0"),
    timed_out: (.["Read-TIMEDOUT"].Count // "0"),
    mean_ms: .Read["Mean(ms)"],
    p95_ms: .Read["95Percentile(ms)"],
    p99_ms: .Read["99Percentile(ms)"]
  }'
```

A measured-phase pass requires:

- Measured read count equals `OPERATION_COUNT`.
- No failed or timed-out read category.
- Reported throughput is close to `TEST_RPS`.
- Client CPU is not itself saturated.
- Topology remains one shard with one member.

Queue warnings confined to warm-up are acceptable. Warnings accompanied by
measured failed/timed-out reads make the trial fail.

The reported NoSQLMark throughput is offered/release throughput. It can remain
near the target even while MongoDB requests queue, so it is not sufficient by
itself.

## 9. Safely stop and try another RPS

For either a completed trial or an overloaded trial:

1. In **Backend**, press `Ctrl-C` first. This stops the process issuing the
   database operations.
2. In **REPL**, press `Ctrl-C`.
3. Verify that neither remote JVM remains.

`[READ ONLY]`

```bash
kubectl --kubeconfig "$KCFG" exec -n "$NS" nosqlmark-client -- bash -lc '
  ps -eo pid,stat,etime,args \
    | grep -E "sbt-launch-0.13.8.jar|de[.]unihamburg[.]informatik[.]nosqlmark[.]repl[.]REPL" \
    | grep -v grep || true
'
```

No output means the trial is stopped. Stopping only the REPL is insufficient:
the Backend owns the active workers. NoSQLMark's `StopJob` path is incomplete,
so do not rely on it.

If a process remains, copy its exact PID from the read-only command above.
Then run only for those explicit PIDs:

`[WARNING: POD PROCESS CHANGE]`

```bash
kubectl --kubeconfig "$KCFG" exec -n "$NS" nosqlmark-client -- \
  kill -TERM <BACKEND_PID> <REPL_PID>
```

Do not use a broad `pkill`. Verify again after `TERM`; review separately before
using `KILL`.

To test another rate:

1. Wait for MongoDB and client CPU to return near their pre-trial baseline.
2. Change `TEST_RPS` in section 6.
3. Rerun section 6 to create a new `TRIAL_ID`, counts, and job file.
4. Start fresh Backend and REPL processes using section 7.
5. Submit the new job.

Do **not** reload the database or redeploy MongoDB between RPS values.

Suggested manual search:

- Try `250`, then `500`, then increase in steps such as `250` until the measured
  phase fails or cannot maintain its target.
- Refine between the highest clean measured RPS and the first failing RPS.
- Later, repeat the final candidate several times before calling it a stable
  limit.

Full cluster cleanup is intentionally omitted. Namespace deletion destroys the
MongoDB data and client pod; review and authorize cleanup separately. Never
delete Foxtrot or shared monitoring resources.
