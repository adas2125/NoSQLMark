val mongoBase = CoreJob(
  batchname = "mongodb-comparison",
  dbname = "MongoDbClient",
  dbproperties = Map(
    "mongodb.url" ->
      "mongodb://10.10.1.1:27020/ycsb_manual_smoke?w=1&retryWrites=false&retryReads=false"
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
    readproportion = 0.5,
    updateproportion = 0.5,
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
