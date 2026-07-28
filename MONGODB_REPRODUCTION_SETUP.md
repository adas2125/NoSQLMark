# MongoDB/YCSB/NoSQLMark Setup

This guide prepares a fresh x86-64 Ubuntu 24.04 VM for the MongoDB
async-versus-sync experiment. Run the commands in one shell, in order.

## 1. Install prerequisites

```bash
set -euo pipefail

sudo apt-get update
sudo apt-get install -y \
  ca-certificates \
  curl \
  git \
  libcurl4 \
  maven \
  openjdk-8-jdk \
  tar

export JAVA_HOME=/usr/lib/jvm/java-8-openjdk-amd64
export PATH="$JAVA_HOME/bin:$PATH"

java -version
javac -version
mvn -version
```

NoSQLMark's historical Scala/sbt stack requires Java 8. The working setup used
Maven 3.8.7, but the Ubuntu-provided Maven 3 package is sufficient.

## 2. Set paths and clone the pinned source revisions

```bash
export NOSQLMARK_ROOT="$HOME/NoSQLMark"
export YCSB_ROOT="$HOME/YCSB"
export MONGO_BASE="/tmp/nosqlmark-mongodb-tools"

git clone https://github.com/steffenfriedrich/YCSB.git "$YCSB_ROOT"
git -C "$YCSB_ROOT" checkout --detach \
  b73ac8367b7de0356031684883338ec1826c1a4f

git clone https://github.com/steffenfriedrich/NoSQLMark.git "$NOSQLMARK_ROOT"
git -C "$NOSQLMARK_ROOT" checkout --detach \
  bfe14e57f7f103fb3c78609e66e2f47fc77290d8

mkdir -p "$NOSQLMARK_ROOT/artifacts" \
         "$NOSQLMARK_ROOT/logs" \
         "$NOSQLMARK_ROOT/results" \
         "$MONGO_BASE"

git -C "$YCSB_ROOT" rev-parse HEAD
git -C "$NOSQLMARK_ROOT" rev-parse HEAD
```

The above commits are already the latest commits from both these repositories.

## 3. Patch and build the YCSB MongoDB binding

Apply the two compatibility changes in "$YCSB_ROOT":

```bash
mongodb/pom.xml
pom.xml
```

The HTTPS change fixes historical dependency resolution. MongoDB Java driver
3.12.14 retains the API used by YCSB 0.14 while working with MongoDB 8.

Build the core and MongoDB binding, install them into Maven Local, and produce
the standalone YCSB MongoDB distribution:

```bash
cd "$YCSB_ROOT"

mvn -pl mongodb -am \
  -DskipTests \
  -Dcheckstyle.skip=true \
  install \
  2>&1 | tee "$NOSQLMARK_ROOT/artifacts/ycsb-mongodb-build.log"

test -f "$HOME/.m2/repository/com/yahoo/ycsb/core/0.14.0-SNAPSHOT/core-0.14.0-SNAPSHOT.jar"
test -f "$HOME/.m2/repository/com/yahoo/ycsb/mongodb-binding/0.14.0-SNAPSHOT/mongodb-binding-0.14.0-SNAPSHOT.jar"
test -f "$YCSB_ROOT/mongodb/target/ycsb-mongodb-binding-0.14.0-SNAPSHOT.tar.gz"
```

Extract the packaged YCSB client:

```bash
tar -xzf \
  "$YCSB_ROOT/mongodb/target/ycsb-mongodb-binding-0.14.0-SNAPSHOT.tar.gz" \
  -C "$MONGO_BASE"

export YCSB="$MONGO_BASE/ycsb-mongodb-binding-0.14.0-SNAPSHOT"

test -x "$YCSB/bin/ycsb.sh"
test -f "$YCSB/lib/mongodb-binding-0.14.0-SNAPSHOT.jar"
test -f "$YCSB/lib/mongo-java-driver-3.12.14.jar"
```

`ycsb.sh` launches the Java client directly and avoids the old Python-based
YCSB launcher's Python 2 dependency.

## 4. Patch and build NoSQLMark

Apply the minimal MongoDB-capable build changes to:
```bash
backbench/src/main/scala/de/unihamburg/informatik/nosqlmark/actors/CoreMaster.scala
backbench/src/main/scala/de/unihamburg/informatik/nosqlmark/protocols/MeasurementProtocol.scala
build.sbt
project/Dependencies.scala
```

This adds only the synchronous YCSB MongoDB binding, removes unrelated
datastore dependencies, and deletes two now-invalid unused imports. It does
not change NoSQLMark's scheduler or measurements.

Download the historical sbt launcher:

```bash
cd "$NOSQLMARK_ROOT"

curl -fL \
  https://repo.scala-sbt.org/scalasbt/ivy-releases/org.scala-sbt/sbt-launch/0.13.8/sbt-launch.jar \
  -o artifacts/sbt-launch-0.13.8.jar

echo "6570bb03df6138ffaa7ac0bbe35eb4ea79062d1146b6929c75cf238d14dd9158  artifacts/sbt-launch-0.13.8.jar" \
  | sha256sum -c -
```

Compile the backend and REPL:

```bash
cd "$NOSQLMARK_ROOT"

java -jar artifacts/sbt-launch-0.13.8.jar \
  'project backbench' compile \
  2>&1 | tee artifacts/nosqlmark-backbench-build.log

java -jar artifacts/sbt-launch-0.13.8.jar \
  'project repl' compile \
  2>&1 | tee artifacts/nosqlmark-repl-build.log
```

NoSQLMark's existing `NoSQLMarkDBFactory` will find `MongoDbClient` as
`com.yahoo.ycsb.db.MongoDbClient` on the backend classpath.

## 5. Download MongoDB 8.0.12

```bash
export MONGO_ARCHIVE="$MONGO_BASE/mongodb-linux-x86_64-ubuntu2404-8.0.12.tgz"

curl -fL \
  https://fastdl.mongodb.org/linux/mongodb-linux-x86_64-ubuntu2404-8.0.12.tgz \
  -o "$MONGO_ARCHIVE"

echo "7665edf8ec6f0da2515c49f784da22619f686fffed990a7696c5365ee9d334b3  $MONGO_ARCHIVE" \
  | sha256sum -c -

tar -xzf "$MONGO_ARCHIVE" -C "$MONGO_BASE"

export MONGOD="$MONGO_BASE/mongodb-linux-x86_64-ubuntu2404-8.0.12/bin/mongod"

test -x "$MONGOD"
"$MONGOD" --version

if ldd "$MONGOD" | grep -q "not found"; then
  echo "MongoDB has missing shared-library dependencies" >&2
  exit 1
fi
```

This extracts a standalone MongoDB binary. It does not install or alter a
system MongoDB service.

## 6. Prepare the NoSQLMark MongoDB job definitions

Creating this file does not submit or run a job:

```bash
cd "$NOSQLMARK_ROOT"

cat > artifacts/mongo_jobs.scala <<'SCALA'
val mongoBase = CoreJob(
  batchname = "mongodb-comparison",
  dbname = "MongoDbClient",
  dbproperties = Map(
    "mongodb.url" ->
      "mongodb://127.0.0.1:27020/ycsb_manual_smoke?w=1&retryWrites=false&retryReads=false"
  ),
  target = 100.0,
  nodes = 1,
  worker = 1,
  table = "usertable",
  phase = "transactional",
  asyncmode = true,
  counts = CoreCounts(
    recordcount = 1000,
    warmupcount = 0,
    operationcount = 3000,
    insertcount = 0,
    insertstart = 0,
    fieldcount = 1,
    fieldlength = 100
  ),
  proportions = CoreProportions(
    readproportion = 1.0,
    updateproportion = 0.0,
    insertproportion = 0.0,
    scanproportion = 0.0,
    readmodifywriteproportion = 0.0
  ),
  distributions = CoreDistributions(
    requestdistribution = "uniform",
    insertorder = "hashed"
  ),
  loadgeneration = CoreLoadGeneration(
    interrequesttimedistribution = "constant"
  ),
  logmeasurements = true,
  logjvmstats = false
)

val pairTag = System.currentTimeMillis.toString

val asyncExperiment = mongoBase.copy(
  jobID = nc.genID,
  batchname = "mongodb-async-pause-" + pairTag,
  asyncmode = true
)

val syncExperiment = mongoBase.copy(
  jobID = nc.genID,
  batchname = "mongodb-sync-pause-" + pairTag,
  asyncmode = false
)

println(asyncExperiment)
println(syncExperiment)
SCALA
```

## 7. Final readiness check

```bash
cd "$NOSQLMARK_ROOT"

test -x "$MONGOD"
test -x "$YCSB/bin/ycsb.sh"
test -f "$YCSB/lib/mongo-java-driver-3.12.14.jar"
test -f artifacts/sbt-launch-0.13.8.jar
test -f artifacts/mongo_jobs.scala

git -C "$YCSB_ROOT" diff --check
git -C "$NOSQLMARK_ROOT" diff --check

echo "YCSB, NoSQLMark, and MongoDB setup is complete."
echo "No MongoDB or NoSQLMark process has been started."
```
