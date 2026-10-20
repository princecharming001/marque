import Foundation

// Entry point: every suite runs, then the process exits non-zero if any assertion failed.
runUploadRetryPolicyTests()
runCompressionPlanTests()
runServerClipAdoptionTests()
runJobPoliciesTests()
runUploadJournalTests()
runTweakWatchPolicyTests()
runRenderCacheTests()
runEditRestorePolicyTests()
runRetryJobPolicyTests()
runRerenderStartTests()
runTeleprompterLayoutTests()
runDaySummaryTests()
runVoiceProfileTests()
runScriptsV2WireTests()
runScriptReadLogicTests()

print("\n\(logicTestPasses) passed, \(logicTestFailures) failed")
exit(logicTestFailures == 0 ? 0 : 1)
