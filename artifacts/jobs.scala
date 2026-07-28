val base = CoreJob(
  batchname = "redis-open-loop-demo",
  dbname = "de.unihamburg.informatik.nosqlmark.db.RedisJedisPoolClient",
  dbproperties = Map(
    "redis.host" -> "127.0.0.1",
    "redis.port" -> "6380"
  ),
  target = 1000.0,
  nodes = 1,
  worker = 1,
  phase = "transactional",
  asyncmode = true,
  counts = CoreCounts(
    recordcount = 10000,
    warmupcount = 0,
    operationcount = 30000,
    insertcount = 10000,
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
    requestdistribution = "uniform"
  ),
  loadgeneration = CoreLoadGeneration(
    interrequesttimedistribution = "constant"
  ),
  logmeasurements = true,
  logjvmstats = false
)

val loadJob = base.copy(
  jobID = nc.genID,
  batchname = "redis-load",
  phase = "load",
  asyncmode = true,
  target = 2000.0,
  counts = base.counts.copy(
    warmupcount = 0,
    operationcount = 10000,
    insertcount = 10000
  ),
  logmeasurements = false
)

val openJob = base.copy(
  jobID = nc.genID,
  batchname = "open-loop",
  asyncmode = true
)

val closedJob = base.copy(
  jobID = nc.genID,
  batchname = "closed-loop",
  asyncmode = false
)
