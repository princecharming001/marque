import Foundation

// Minimal assertion helpers for the LogicTests binary (no XCTest on this path).

var logicTestFailures = 0
var logicTestPasses = 0
private var currentSuite = ""

func suite(_ name: String) {
    currentSuite = name
    print("\n== \(name)")
}

func expect(_ condition: Bool, _ name: String, file: String = #fileID, line: Int = #line) {
    if condition {
        logicTestPasses += 1
        print("  PASS  \(name)")
    } else {
        logicTestFailures += 1
        print("  FAIL  \(name)  (\(file):\(line))")
    }
}

func expectEqual<T: Equatable>(_ got: T, _ want: T, _ name: String,
                               file: String = #fileID, line: Int = #line) {
    if got == want {
        logicTestPasses += 1
        print("  PASS  \(name)")
    } else {
        logicTestFailures += 1
        print("  FAIL  \(name)  got \(got), want \(want)  (\(file):\(line))")
    }
}

func expectClose(_ got: Double, _ want: Double, _ name: String, tolerance: Double = 1e-6,
                 file: String = #fileID, line: Int = #line) {
    if abs(got - want) <= tolerance {
        logicTestPasses += 1
        print("  PASS  \(name)")
    } else {
        logicTestFailures += 1
        print("  FAIL  \(name)  got \(got), want \(want)  (\(file):\(line))")
    }
}
