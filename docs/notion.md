# Si-An: MongoDB

Main Git repo: https://github.com/Chen-Si-An/Autoscaling

## MongoDB Links

1. [**Replication Manual**](https://www.mongodb.com/docs/manual/replication/) - Discusses the replication strategy (1 primary and multiple secondary). Clients send read and write requests to the primary unless read from secondary is allowed. MongoDB does asynchronous replication.
2. [**Read Isolation, Consistency and Recency**](https://www.mongodb.com/docs/manual/core/read-isolation-consistency-recency/) - Uncommitted updates can be returned on read if the read concern is “local” or “available”, no matter the write concern. It guarantees causal consistency (Doubt: If replication is asynchronous, and a read from secondary is allowed, how is causal consistency guaranteed) (guarantees are only within a client session with “majority” read and write concern - details [here](https://www.mongodb.com/docs/manual/core/causal-consistency-read-write-concerns/))
3. [**Adding a new shard**](https://www.mongodb.com/docs/manual/tutorial/add-shards-to-shard-cluster/)
4. [**Shard Cluster Balancer**](https://www.mongodb.com/docs/manual/core/sharding-balancer-administration/) - It automatically balances the shards if the difference in the data is more than 384 MB.

**When to Shard MongoDB?**

1. [https://kinsta.com/blog/mongodb-sharding/](https://kinsta.com/blog/mongodb-sharding/)
2. [https://stackoverflow.com/questions/17810499/when-to-start-mongodb-sharding](https://stackoverflow.com/questions/17810499/when-to-start-mongodb-sharding)

Add MongoDB shards when the write workload gets heavy and the node reaches 60-70% utilization based on disk capacity and RAM usage.

Add replica sets when there is read-heavy workload

MongoDB recently added a feature to pick a good shard key: [https://www.youtube.com/watch?v=\_CbHeqq79BA&ab\_channel=MongoDB](https://www.youtube.com/watch?v=_CbHeqq79BA\&ab_channel=MongoDB)

”And applications need to specify shard key, else mongodb will look through all the shards and can’t make use of the horizontal scaling”

## Start a standalone shared MongoDB cluster

1. First, add the bitnami helm repository

```bash
helm repo add bitnami <https://charts.bitnami.com/bitnami>
helm repo update

```

1. Create a file `values.yaml`, which will contain the cluster's configuration, including the number of shards and replicas.
    Some info on storage class ([https://www.kubecost.com/kubernetes-best-practices/kubernetes-storage-class/](https://www.kubecost.com/kubernetes-best-practices/kubernetes-storage-class/))

**Note that as the helm chart from bitnami seems updated, the name of configurable fields have changed; thus, please use the following** **`values.yaml`** **if using chart** **`mongodb-sharded-9.4.12`**

```yaml
# Global settings
global:
  storageClass: "kops-csi-1-21"    # Replace with your storage class name (verify with: kubectl get storageclass)

auth:
  enabled: true
  rootPassword: mongodb123
  rootUser: root

replicaSet:
  enabled: true

# Number of shards (each shard is a replica set)
shards: 1  

image:
  registry: docker.io
  repository: bitnamilegacy/mongodb-sharded
  tag: 8.0.13-debian-12-r0
  pullPolicy: IfNotPresent

# Config servers settings
configsvr:
  replicaCount: 3
  persistence:
    enabled: true
    size: 10Gi
  resources:
    requests:
      memory: "128Mi"
      cpu: "50m"
    limits:
      memory: "512Mi"
      # cpu: "500m"
  affinity:
    podAntiAffinity:
      preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          labelSelector:
            matchExpressions:
            - key: app.kubernetes.io/component
              operator: In
              values:
              - configsvr
          topologyKey: kubernetes.io/hostname

# Mongos router settings
mongos:
  replicaCount: 3
  resources:
    requests:
      memory: "128Mi"
      cpu: "0.375"
    limits:
      memory: "512Mi"
      # cpu: "1.25"
  affinity:
    podAntiAffinity:
      preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          labelSelector:
            matchExpressions:
            - key: app.kubernetes.io/component
              operator: In
              values:
              - mongos
          topologyKey: kubernetes.io/hostname

# Shard (data node) settings; each shard replica set has one or more data nodes
shardsvr:
	persistence:
    enabled: true
    size: 16Gi
  dataNode:
    replicaCount: 1
    resources:
      requests:
        memory: "512Mi"
        cpu: "0.375"
      limits:
        memory: "1024Mi"
        cpu: "0.5"
    affinity:
      podAntiAffinity:
        requiredDuringSchedulingIgnoredDuringExecution:
        - labelSelector:
            matchExpressions:
            - key: app.kubernetes.io/component
              operator: In
              values:
              - shardsvr
          topologyKey: kubernetes.io/hostname

```

\<aside>

💡

⚠️ Deprecated — Only for reference

```bash
# Global settings
global:
  storageClass: "kops-csi-1-21"    # Replace with your storage class name (verify with: kubectl get storageclass)

mongodbRootPassword: mongodb123
replicaSet:
  enabled: true

# Number of shards (each shard is a replica set)
shards: 2  

# Config servers settings
configsvr:
  replicas: 3
  persistence:
    enabled: true
    size: 10Gi

# Mongos router settings
mongos:
  replicas: 2

# Shard (data node) settings; each shard replica set has one or more data nodes
shardsvr:
  dataNode:
    replicas: 3
    persistence:
      enabled: true
      size: 8Gi

```

\</aside>

1. Install the helm chart

```bash
helm install my-mongodb-sharded bitnami/mongodb-sharded -f values.yaml

```

1. Check that all pods deploy and become ready `kubectl get pods`
    If any pod is stuck (for example, with “Pending” status due to unbound PersistentVolumeClaims), recheck your storage configuration. Ensure that the storage class specified in your values file exists (use: kubectl get storageclass) and supports dynamic provisioning, or pre-create a matching PersistentVolume.
2. Remove `CPU limits` from each `deployment` and `statefulset`

```bash
# Remove CPU limits
kubectl edit deployment my-mongodb-sharded-mongos
kubectl edit statefulset my-mongodb-sharded-shard0-data

```

1. Find the mongos service that acts as the entry point (its name is usually similar to the release)

```bash
kubectl get svc

```

1. Connect to one of the mongos pods using the Mongo shell:

```bash
kubectl run --namespace default my-mongodb-sharded-client --rm --tty -i --restart='Never' --image docker.io/bitnami/mongodb-sharded:8.0.4-debian-12-r2 --command -- mongosh admin --host my-mongodb-sharded --authenticationDatabase admin -u root -p $MONGODB_ROOT_PASSWORD
# Here the service name is my-mongodb-sharded. Change it as per your service name

# To get the password
export MONGODB_ROOT_PASSWORD=$(kubectl get secret --namespace default my-mongodb-sharded -o jsonpath="{.data.mongodb-root-password}" | base64 -d)

```

Once connected, run

```bash
sh.status()

```

1. Open a nodeport for the deployment present. Check whether the following command works

```bash
mongosh "mongodb://<node-ip>:<node-port>" -u root -p <password> --authenticationDatabase admin

```

1. Run a YCSB workoad `mongoworkload` . The workload need to be created accordingly.

```bash
bin/ycsb.sh load mongodb -s   -P workloads/workloada   -p mongodb.url="mongodb://root:qVCgnBBKfa@54.215.252.131:30795/ycsb?authSource=admin"
bin/ycsb.sh run mongodb -s   -P workloads/workloada   -p mongodb.url="mongodb://root:qVCgnBBKfa@54.215.252.131:30795/ycsb?authSource=admin"

# for secondary read preference, change read preference in mongoshell as well as connection string for ycsb
bin/ycsb.sh run mongodb -s   -P workloads/workloadc   -p mongodb.url="mongodb://root:lsWnljF3HJ@54.176.13.201:32018/ycsb?authSource=admin&readPreference=secondary" -threads 40

```

**To check the statistics**

Open a mongo-client and run `db.stats()` to get the overall statistics. Run `db.serverStatus().opcounters` to get the number of operations run. To open a mongo-client, run

```bash
kubectl run -it --rm mongo-client --image=mongo:6.0 --command -- mongosh --host <mongos-service> -u root -p <password> --authenticationDatabase admin

> db.serverStatus().opcounters


```

[image.png](attachment:2ee22984-f859-4b67-a7c7-e0cbd5027101\:image.png)

## Check read/write status

### mongotop

`mongotop` provides statistics on a per-collection level.

Enter into the terminal of a shard and then run the command

```bash
kubectl exec <shard-name> -it -- /bin/bash

mongotop "mongodb://127.0.0.1:27017" -u root -p <password> --authenticationDatabase admin 
# For socialnet, the password is "password" 

```

[image.png](attachment:047e2f18-7a1b-492a-975d-b0ceead9bc16\:image.png)

[image.png](attachment:3e2bec6c-0fe7-474f-bd29-30a4bfb8edbb\:image.png)

[image.png](attachment:3f468c59-47e0-4d53-ae3c-cfec07d2dbeb\:image.png)

**Need to check: Why running mongotop command on different replica sets give different collections?**

Shard0-data-0

[image.png](attachment:3f468c59-47e0-4d53-ae3c-cfec07d2dbeb\:image.png)

Shard0-data-1

[image.png](attachment\:ba141be5-c615-4711-b6f9-741f5cbace91\:image.png)

### mongostat

It gets the statistics of the particular replica set in the shard on which the command was entered.

```bash
kubectl exec <shard-name> -it -- /bin/bash

mongostat "mongodb://127.0.0.1:27017" -u root -p <password> --authenticationDatabase admin 
# For socialnet, the password is "passowrd" 

```

[image.png](attachment:38f1d9f9-35e8-4897-8c26-3792adb11a46\:image.png)

To get the statistics of all the replicasets use `--discover` flag with the `mongostat` command

[image.png](attachment:272a681b-1da7-460f-8adb-c209b3bd6bef\:image.png)

## **Install mongosh on terminal**

[https://www.mongodb.com/docs/mongodb-shell/install/](https://www.mongodb.com/docs/mongodb-shell/install/)

After installing this, you can enter the `mongos` terminal from anywhere

```bash
mongosh "mongodb://<node-ip>:<node-port>" -u root -p <password> --authenticationDatabase admin

```

### Enable Sharding

After loading the YCSB dataset, check `sh.status()` . You should see a `ycsb` dataset. But if it does not show anything in the `databases.collections` section, it means we have to enable sharding and shard the collection.

**Note: Figure out why this isn’t automatic. → MongoDB does not know which collections should be sharded, nor what shard key to use, since the choice of shard key is critical for performance and data distribution.**

To enable sharding, run the following

```bash
kubectl run --namespace default my-mongodb-sharded-client --rm --tty -i --restart='Never' --image docker.io/bitnami/mongodb-sharded:8.0.4-debian-12-r2 --command -- mongosh admin --host my-mongodb-sharded --authenticationDatabase admin -u root -p $MONGODB_ROOT_PASSWORD

# Enters mongosh terminal
use config
sh.enableSharding("ycsb")

# Now check the collection that you want to shard
use ycsb
show collections

# let's say that the collection name is 'usertable'
sh.shardCollection("ycsb.usertable", { _id: 1 })

```

Now run `sh.status()` . The output is as follows:

```bash
---
databases
[
  {
    database: { _id: 'config', primary: 'config', partitioned: true },
    collections: {
      'config.system.sessions': {
        shardKey: { _id: 1 },
        unique: false,
        balancing: true,
        chunkMetadata: [ { shard: 'my-mongodb-sharded-shard-0', nChunks: 1 } ],
        chunks: [
          { min: { _id: MinKey() }, max: { _id: MaxKey() }, 'on shard': 'my-mongodb-sharded-shard-0', 'last modified': Timestamp({ t: 1, i: 0 }) }
        ],
        tags: []
      }
    }
  },
  {
    database: {
      _id: 'ycsb',
      primary: 'my-mongodb-sharded-shard-1',
      version: {
        uuid: UUID('de2a42dc-5da3-4415-be3a-aae49b7ec09e'),
        timestamp: Timestamp({ t: 1739211909, i: 2 }),
        lastMod: 1
      }
    },
    collections: {
      'ycsb.usertable': {
        shardKey: { _id: 1 },
        unique: false,
        balancing: true,
        chunkMetadata: [ { shard: 'my-mongodb-sharded-shard-1', nChunks: 1 } ],
        chunks: [
          { min: { _id: MinKey() }, max: { _id: MaxKey() }, 'on shard': 'my-mongodb-sharded-shard-1', 'last modified': Timestamp({ t: 1, i: 0 }) }
        ],
        tags: []
      }
    }
  }
]

```

The output shows that the `ycsb.usertable` collection is shared but it is entirely present as 1 chunk in `my-mongodb-sharded-shard-1`.

**Why this happened?**

This is because there must be a minimum amount of data difference that should be present for chunk splitting to take place. The data size was only 1 MB at this point, so chunk splitting did not take place. Default range size for a chunk is 128MB. The default migration threshold is 3\*range size = 384 MB. ([https://www.mongodb.com/docs/manual/core/sharding-data-partitioning/](https://www.mongodb.com/docs/manual/core/sharding-data-partitioning/))

To split data manually,

```bash
sh.splitAt("<db-name>.<collection-name>", { _id: <split-point> })

#Eg.:
sh.splitAt("ycsb.usertable", { _id: <split-point> })

```

To merge data manually,

```bash
use config

db.adminCommand({
  mergeChunks: "<database-name>.<collection-name>",
  bounds: [ { _id: MinKey() }, { _id: MaxKey() } ]
});

```

**Split a chunk and move chunk to another shard**

```bash
# Splitting a chunk
sh.splitAt("ycsb.usertable", {"_id": "user5865656527817951015"})

# Move chunk
# Remember: To move a chunk {low_id:high_id}, mention the low_id when referring to the chunk. 
# For Example: Chunk mentioned here {"_id": MinKey()} moves the chunk ` min: { _id: MinKey() }, max: { _id: 'user3386927887164874747' }`
sh.moveChunk("ycsb.usertable", {"_id": MinKey()}, "my-mongodb-sharded-shard-0", { forceJumbo: true })


```

### Check Tiger stats

```bash
kubectl exec -it my-mongodb-sharded-shard0-data-0 -- \
  mongosh -u root -p $MONGODB_ROOT_PASSWORD \
  --authenticationDatabase admin
  
> db.serverStatus().wiredTiger

```

## Check Read Concerns and preference

```bash
# For read concern [local, available, majority, linearizable, snapshot]
db.adminCommand({ getDefaultRWConcern: 1 }).defaultReadConcern

# For read preference [primary, primaryPreferred, secondary, secondaryPreferred, nearest]
db.getMongo().getReadPref()

# set read preference
db.getMongo().setReadPref(<preference_type>)

```

### Some operations on the MongoDB collection

**Check the number of records**

```bash
use <database-name>
# use ycsb

db.<collection>.countDocuments({})

```

**Retrieve one document from the collection and print its keys and values**

```bash
var oneDoc = db.usertable.findOne();
printjson(oneDoc);

# prints the number of fields
var fieldCount = Object.keys(oneDoc).length;
print("Number of fields in one document: " + fieldCount);

# prints all the keys
print("Keys: " + Object.keys(oneDoc).join(", "));

```

**Check datasize**

```bash
db.<collection-name>.dataSize();

```

**Which shard has what data**

```bash
db.<collection-name>.getShardDistribution()

```

[image.png](attachment:75a3c207-b588-4cce-8f87-6a2ac95b93c3\:image.png)

### Write all the ids to a file ids.json

```bash
# Install Mongo Database Tools (<https://www.mongodb.com/docs/database-tools/installation/#std-label-dbtools_installation>)

mongoexport   --host=13.56.158.134:30094 --username=root --password=LAxlesETQV \
  --authenticationDatabase=admin \
  --db=ycsb \
  --collection=usertable \
  --fields=_id \
  --out=ids.json
  

```

### YCSB

[https://courses.cs.duke.edu/fall13/compsci590.4/838-CloudPapers/ycsb.pdf](https://courses.cs.duke.edu/fall13/compsci590.4/838-CloudPapers/ycsb.pdf)

[https://benchant.com/blog/ycsb-custom-workloads](https://benchant.com/blog/ycsb-custom-workloads)

[https://benchant.com/blog/ycsb](https://benchant.com/blog/ycsb)

[https://psy-lob-saw.blogspot.com/2015/03/fixing-ycsb-coordinated-omission.html](https://psy-lob-saw.blogspot.com/2015/03/fixing-ycsb-coordinated-omission.html)

**Workload Parameters**

https://github.com/brianfrankcooper/YCSB/wiki/Core-Properties

- executiontime: Runtime of the workload (in minutes)
- threadcount: Number of parallel threads
- recordcount: Number of initial records
- insertstart: start record (default = 0)
- operationcount: number of operations (default = 1000)
- fieldcount: number of database fields of an entry (default = 10)
- fieldlength: length of each database field (default = 500)
- readallfields: true = all fields are read (default); false = only one field is read (key)
- readproportion: read portion of the workload (0 - 1)
- writeproportion: write portion of the workload (0 - 1)
- updateproportion: Update portion of the workload (0 - 1)
- scanproportion: Scan portion of the workload (0 - 1)
- requestdistribution: request access pattern (UNIFORM, ZIPFIAN, LATEST)
- readmodifywriteproportion: read-modify-write portion of the workload
- insertorder: insert sort order (default = HASHED; ORDERED
- maxscanlength: maximum records of a scan (default = 1000)
- scanlengthdistribution: Distribution of scan length distribution (UNIFORM, ZIPFIAN, LATEST)

**Coordinated Omission**

YCSB suffers from Coordinated Omission problem. It measures the intended latency and uses HdrHistogram to tackle the problem.

https://github.com/brianfrankcooper/YCSB/blob/master/core/CHANGES.md

Analysis on Hot Keys: [https://brooker.co.za/blog/2023/02/07/hot-keys.html](https://brooker.co.za/blog/2023/02/07/hot-keys.html)

## Autoscaling Building Block

### GitHub Repo

https://github.com/Chen-Si-An/Autoscaling

### Environment Setup

m5.large node \* 3

### Scaling metrics: cpu; Scaling actions: add replica

- Prerequisite

Install Metrics Server

```bash
kubectl apply -f <https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml>

```

- Use HPA to scale StatefulSet

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: shard0-data-hpa
  namespace: default
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: StatefulSet
    name: my-mongodb-sharded-shard0-data
  minReplicas: 1
  maxReplicas: 3
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 75
  behavior:
    scaleUp:
      policies:
        - type: Pods
          value: 1
          periodSeconds: 90
      stabilizationWindowSeconds: 150
    scaleDown:
      policies:
        - type: Pods
          value: 1
          periodSeconds: 90
      stabilizationWindowSeconds: 150

```

```bash
kubectl apply -f shard0-hpa.yaml

```

- Add hooks to `values.yaml` for `postStart` and `preStart`, where we need to execute `rs.add()` to add replica in `postStart` and `rs.remove()` to remove replica in `preStart`

```yaml
# Shard (data node) settings; each shard replica set has one or more data nodes
shardsvr:
  dataNode:
    replicaCount: 1
    persistence:
      enabled: true
      size: 12Gi
    resources:
      requests:
        memory: "512Mi"
        cpu: "0.375"
      limits:
        memory: "1024Mi"
        cpu: "0.5"
    affinity:
      podAntiAffinity:
        requiredDuringSchedulingIgnoredDuringExecution:
        - labelSelector:
            matchExpressions:
            - key: app.kubernetes.io/component
              operator: In
              values:
              - shardsvr
          topologyKey: kubernetes.io/hostname
    lifecycleHooks:
      postStart:
        exec:
          command:
            - /bin/bash
            - -c
            - |
              PRIMARY_POD="my-mongodb-sharded-shard0-data-0.my-mongodb-sharded-headless.default.svc.cluster.local"
              MONGO_USER="${MONGODB_ROOT_USER:-root}"
              MONGO_PASS="$(cat ${MONGODB_ROOT_PASSWORD_FILE})"
              MONGO_URI="mongodb://$MONGO_USER:$MONGO_PASS@$PRIMARY_POD:27017/admin"
              HOSTNAME="$(hostname -f)"
              if [ "$HOSTNAME" != "$PRIMARY_POD" ]; then
                mongosh "$MONGO_URI" --eval "rs.status()" | grep "$HOSTNAME" || \
                mongosh "$MONGO_URI" --eval "rs.add('$HOSTNAME:27017')" || true
              fi
      preStop:
        exec:
          command:
            - /bin/bash
            - -c
            - |
              PRIMARY_POD="my-mongodb-sharded-shard0-data-0.my-mongodb-sharded-headless.default.svc.cluster.local"
              MONGO_USER="${MONGODB_ROOT_USER:-root}"
              MONGO_PASS="$(cat ${MONGODB_ROOT_PASSWORD_FILE})"
              MONGO_URI="mongodb://$MONGO_USER:$MONGO_PASS@$PRIMARY_POD:27017/admin"
              HOSTNAME="$(hostname -f)"
              if [ "$HOSTNAME" != "$PRIMARY_POD" ]; then
                mongosh "$MONGO_URI" --eval "rs.remove('$HOSTNAME:27017')" || true
              fi

```

- Experiment - ycsb benchmark

recordcount=2000000; readproportion=1; requestdistribution=zipfian; 10 threads

```bash
bin/ycsb.sh run mongodb -s   -P workloads/workloadr   -p mongodb.url="mongodb://root:mongodb123@my-mongodb-sharded:27017/ycsb?authSource=admin&readPreference=nearest" -threads 10 -target 3000 2>&1 | tee logs/run_10.log

```

Since we have `minReplicas: 1` and `maxReplicas: 3`, we could see three different stages as time elapsed.

[output.png](attachment:06fba302-3a10-42b3-9e56-209ef4e32763\:output.png)

[output1.png](attachment\:cfbceace-7f96-43d2-aaa2-a927420d36ab\:output1.png)

### Scaling metrics: iowait; Scaling actions: add replica

- Prerequisite

Install Prometheus stack

```bash
helm repo add prometheus-community <https://prometheus-community.github.io/helm-charts>
helm repo update
helm install kps prometheus-community/kube-prometheus-stack -n monitoring --create-namespace
kubectl -n monitoring get pods -l app.kubernetes.io/name=prometheu

```

Access Prometheus web UI from local browser

```bash
kubectl port-forward -n monitoring svc/kps-kube-prometheus-stack-prometheus 9090:9090

```

Access Grafana web UI from local browser

```bash
kubectl port-forward -n monitoring svc/kps-grafana 3000:80

```

Install KEDA

```bash
helm repo add kedacore <https://kedacore.github.io/charts>
helm repo update
helm install keda kedacore/keda -n keda --create-namespace
kubectl -n keda get deployments

```

- Use KEDA to scale StatefulSet

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: shard0-iowait-scaler
  namespace: default
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: StatefulSet
    name: my-mongodb-sharded-shard0-data
  minReplicaCount: 1
  maxReplicaCount: 3
  pollingInterval: 30       # seconds between checks
  cooldownPeriod: 150       # seconds to wait before scaling down
  triggers:
    - type: prometheus
      metadata:
        serverAddress: <http://prometheus-operated.monitoring.svc:9090>
        metricName: node_iowait_percent
        query: >
          100 *
          max(
            sum by(instance) (rate(node_cpu_seconds_total{mode="iowait"}[2m]))
            /
            sum by(instance) (rate(node_cpu_seconds_total[2m]))
          )
        threshold: "5"
  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleUp:
          policies:
            - type: Pods
              value: 1
              periodSeconds: 120
          stabilizationWindowSeconds: 150
        scaleDown:
          policies:
            - type: Pods
              value: 1
              periodSeconds: 30
          stabilizationWindowSeconds: 60


```

```bash
kubectl apply -f shard0-iowait-scaler.yaml

```

- Experiment - ycsb benchmark

recordcount=8000000; readproportion=1; requestdistribution=uniform; 3 threads

```bash
bin/ycsb.sh run mongodb -s   -P workloads/workloadr   -p mongodb.url="mongodb://root:mongodb123@my-mongodb-sharded:27017/ycsb?authSource=admin&readPreference=nearest" -threads 3 -target 3000 2>&1 | tee logs/run_3.log

```

We didn’t observe much improvement from the results, likely because the current workload isn’t heavy enough to cause significant disk contention.

[output.png](attachment:62e75c59-04e4-4ce9-aa62-b1a964992c32\:output.png)

[output1.png](attachment:15f6aa26-c66a-438c-9f3f-e084f8163439\:output1.png)

### Scaling metrics: cpu; Scaling actions: add shard

~~Add shard: PrometheusRule → Alertmanager send a webhook to Argo EventSource → Argo Sensor triggers Argo Workflow → ~~~~`helm upgrade`~~  → Not reliable

Remove shard: Use Helm Upgrade to decrease the shard count would cause problem. Instead, we should run `db.adminCommand({ removeShard: "my-mongodb-sharded-shard-1" })` and run this command to confirm the shard is successfully removed. ~~Afterwards, we could decrease the shard count through ~~~~`helm upgrade`~~. → Not reliable

### What do we need for our **Master list**?

- Decision Tree ✅
- Implementation of adding replica/shard based on metrics ✅
- Implementation of a publishable autoscaler 
  - [x]  Learn to design custom operator
  - [x]  Operator for scaling mongos (HPA might be enough)
  - [x]  Operator for adding replica
  - [x]  Operator for adding shard

## Autoscaling Operator (Mambo)

### GitHub Repo

https://github.com/Chen-Si-An/Autoscaling

### Disadvantages of using `helm upgrade`:

- **No transactional awareness** - Helm doesn’t wait for `sh.addShard()` success — Mongo may remain partially configured.
- **No rollback** - If the new shard fails to register, Helm can’t revert Mongo’s internal state.
- **Operational blindness** - Helm has no concept of cluster metrics (balancer lag, replication lag, cache size).
- **No reconciliation** - it doesn’t reconcile cluster state or maintain topology.

### Decision Tree

mongos

[mongos.jpg](attachment\:d74abe50-1c9e-4630-8c0f-dd59baf137cf\:mongos.jpg)

mongod

[mongod.jpg](attachment:438465d9-a40f-4f6c-92b0-d7e4524a467d\:mongod.jpg)

### Autoscaler Operator Components

1. Kubenetes Prometheus Stack
   - Install Prometheus stack
   ```bash
   helm repo add prometheus-community <https://prometheus-community.github.io/helm-charts>
   helm repo update
   helm install kps prometheus-community/kube-prometheus-stack -n monitoring --create-namespace

   ```
   - Access Prometheus web UI
   ```bash
   kubectl port-forward -n monitoring svc/kps-kube-prometheus-stack-prometheus 9090:9090

   ```
2. MongoDB Exporter
   - Install MongoDB Exporter
   ```bash
   kubectl apply -f mongodb-exporter-deployment.yaml
   kubectl apply -f mongodb-exporter-service.yaml
   kubectl apply -f mongodb-exporter-servicemonitor.yaml

   ```
   - Test MongoDB Exporter
   ```bash
   kubectl port-forward svc/mongodb-exporter 9216:9216 -n default
   curl <http://localhost:9216/metrics> >> test.txt

   ```
3. Install CRD and Deploy Controller
   - Install Operator
     ```bash
     kubectl apply -f dist/install.yaml

     ```
   - Check controller logs
     ```bash
     kubectl -n mongodboperator-system logs deploy/mongodboperator-controller-manager -c manager -f --timestamps

     ```
4. mongos Autoscaler
   - Metrics
     - CPU
   - Actions
     - Add/Remove Replica
   - Create mongos Autoscaler
     ```bash
     kubectl apply -f config/samples/autoscaler_v1alpha1_mongosautoscaler.yaml

     ```
5. mongod Autoscaler
   - Metrics
     - CPU
     - Disk I/O Utilization
     - Read-write Ratio
   - Actions:
     - Add/Remove Shard
     - Add/Remove Replica
   - Create mongos Autoscaler
     ```bash
     kubectl apply -f config/samples/autoscaler_v1alpha1_mongodautoscaler.yaml

     ```

### Experiment Results

- Environment Setup
  - 5 \* m5.2xlarge instances
  - Bitnami helm chart provision
    ```bash
    # Clean old sharded cluster if needed
    helm uninstall my-mongodb-sharded
    kubectl delete pvc --all

    helm repo add bitnami <https://charts.bitnami.com/bitnami>
    helm repo update
    helm install my-mongodb-sharded bitnami/mongodb-sharded -f values.yaml

    ```
    Note that we should use corresponding `values.yaml` for each experiment
  - Autoscaler operator

    Please follow this [section](https://app.notion.com/p/Si-An-MongoDB-1abc7c312f2f802eb62cd28b8a999d17?pvs=21) to install and deploy necessary operator components
  - YCSB benchmark
    ```bash
    # Create a pod inside cluster to run ycsb benchmark
    kubectl run ycsb-test --image=maven:3.8-openjdk-11 --rm -it --restart=Never -- bash

    # Clone ycsb repo in the created pod
    kubectl attach ycsb-test -it
    git clone <https://github.com/brianfrankcooper/YCSB.git>

    # Initialize a db for latter tests
    bin/ycsb.sh load mongodb -s   -P workloads/workloadr   -p mongodb.url="mongodb://root:mongodb123@my-mongodb-sharded:27017/ycsb?authSource=admin"

    # Enable sharding (run commands in mongosh terminal)
    use config
    sh.enableSharding("ycsb")
    use ycsb
    sh.shardCollection("ycsb.usertable", { _id: 1 })

    # Create autoscaler
    kubectl apply -f config/samples/autoscaler_v1alpha1_mongodautoscaler.yaml

    # Run tests
    bin/ycsb.sh run mongodb -s   -P workloads/workloadr   -p mongodb.url="mongodb://root:mongodb123@my-mongodb-sharded:27017/ycsb?authSource=admin&readPreference=nearest" -threads 10 2>&1 | tee logs/run_10.log

    ```
    Note that we should use corresponding `autoscaler yaml` file and `workload` file for each experiment
- mongos CPU-bounded

  [image.png](attachment\:cd9201a4-2863-4ae1-a031-d72e6eed03a4\:image.png)

  [image.png](attachment:999b667a-2d1f-46f6-958c-77d6d1ad4c33\:image.png)

  **Note that to reproduce this experiment, the YCSB benchmark must be manually relaunched immediately after each scaling action, as YCSB does not automatically detect newly added** **`mongos`** **instances.**
- mongod CPU-bounded

  [image.png](attachment:9af8d5a9-426e-4365-8f62-e9b2f899086c\:image.png)

  [image.png](attachment:6b384ce2-0c19-4b88-909b-92ea9f3c03b6\:image.png)
- mongod disk I/O-bouded

  [image.png](attachment\:b3aaccd5-41a2-4e17-b31f-0666d3a678f0\:image.png)

  [image.png](attachment\:b0157dfa-b907-4579-b7a9-5860d73161c4\:image.png)

### Statement

1. [IBM Cloud Databases For MongoDB](https://cloud.ibm.com/docs/databases-for-mongodb?topic=databases-for-mongodb-autoscaling\&utm_source=chatgpt.com\&interface=ui) 
   - Scale disk and RAM (vertical)
   - Possible downtime for scaling deployment
   - [https://stackoverflow.com/questions/75603148/how-do-i-set-up-autoscaling-on-an-ibm-cloud-mongodb-database](https://stackoverflow.com/questions/75603148/how-do-i-set-up-autoscaling-on-an-ibm-cloud-mongodb-database)
2. [MongoDB Atlas](https://www.mongodb.com/docs/atlas/cluster-autoscaling/) 
   - Cluster Tier Auto-Scaling (vertical)
   - Reactive auto-scaling uses threshold based on CPU and memory
   - Predictive auto-scaling uses machine learning based on historical usage patterns
3. [Percona Operator for MongoDB](https://docs.percona.com/percona-for-mongodb) 
   - Like [Bitnami Helm Chart](https://github.com/bitnami/charts), focuses on deployment and management (manual scaling)
   - [GitHub repo](https://github.com/percona?q=\&type=all\&language=\&sort=)
4. [Autoscaler Operator](https://github.com/Chen-Si-An/Autoscaling) (our solution) 
   - Horizontal Scaling
     - Support scaling for both mongos and mongod
     - Use node affinity to run instances on separate worker nodes (like Percona Operator)
     - CPU, disk I/O utilization, read-write ratio
   - Resolved problems
     - Steep price over growing data using vertical scaling
     [image.png](attachment:47739cc2-ccc6-437c-b5fc-53fd2049503a\:image.png)
     - Limited growing space for vertical scaling
     - Original focus on scaling mongod instances while mongos also has significant impact on performance (scatter-gather action)
     - Fit different type of workload
     - Avoid “cycling behavior” through tracking resharding and replicating status
   - Reason why there isn’t similar solution existing
     - Most solutions focus on vertical scaling because both scaling criteria and scaling action are relatively simple
     - Intricacy of handling sharding. Multiple real applications even only use one replica set, like IBM Cloud Databases for MongoDB.
     - Get around complexity to provide managed services for general use cases

### Steps to Build Operator

1. Initialize the project
   ```bash
   # Prerequisites
   go install sigs.k8s.io/kubebuilder/cmd/kubebuilder@latest
   go install sigs.k8s.io/controller-tools/cmd/controller-gen@latest
   go install sigs.k8s.io/kustomize/kustomize/v5@latest

   # Create operator project
   mkdir MongoDBOperator && cd MongoDBOperator
   go mod init github.com/yourname/MongoDBOperator
   kubebuilder init --domain mongodb.io --owner "Your Name"

   ```
2. Create the CRD and controller
   ```bash
   kubebuilder create api --group autoscaler --version v1alpha1 --kind MongosAutoscaler
   kubebuilder create api --group autoscaler --version v1alpha1 --kind MongodAutoscaler

   ```
3. Implement `xxx_types.go` under /api directory and `xxx_controller.go` under /internal/controller directory
4. Generate code containing DeepCopy, DeepCopyInto, and DeepCopyObject method implementations
   ```bash
   make generate

   ```
   This will create/modify:
   ```bash
   api/v1alpha1/zz_generated.deepcopy.go

   ```
5. Generate manifests and RBAC
   ```bash
   make manifests

   ```
   This will create/modify:
   ```bash
   config/crd/bases/autoscale.mongodb.io_mongodautoscalers.yaml
   config/rbac/role.yaml

   ```
6. Build and deploy the operator
   ```bash
   make docker-buildx PLATFORMS="linux/amd64" IMG=docker.io/yourusername/mongodb-autoscaler:v0.0.0
   make deploy IMG=docker.io/yourusername/mongodb-autoscaler:v0.0.0

   ```
   This installs:
   - CRD
   - Controller Deployment
   - RBAC
   - Namespace + ServiceAccount under `mongodboperator-system`
7. Apply an example CR
   ```bash
   kubectl apply -f config/samples/autoscaler_v1alpha1_mongosautoscaler.yaml
   kubectl apply -f config/samples/autoscaler_v1alpha1_mongodautoscaler.yaml

   ```
8. Verify operation
   ```bash
   kubectl get mongodautoscaler
   kubectl describe mongodautoscaler mongodautoscaler-sample
   kubectl -n mongodboperator-system logs deploy/mongodboperator-controller-manager -c manager -f --timestamps

   ```
9. Build Installer

make build-installer IMG=docker.io/yourusername/mongodb-autoscaler:v0.0.0
