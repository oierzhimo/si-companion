import XCTest
@testable import SiCompanionCore

final class CompanionCoreTests: XCTestCase {
    func testDialogueLineInventoryHasExact66LinesAcross11Occasions() {
        XCTAssertEqual(SiDialogue.lines.count, 11)
        let total = SiDialogue.lines.values.reduce(0) { $0 + $1.count }
        XCTAssertEqual(total, 66)
        for occasion in SiDialogue.Occasion.allCases {
            XCTAssertEqual(SiDialogue.lines[occasion]?.count, 6, "Expected 6 lines for \(occasion)")
        }
    }

    func testShuffleBagNeverRepeatsBackToBackAcrossCycleBoundary() {
        let bag = SiCompanionBag()
        var previous: String?
        for _ in 0..<24 {
            let line = bag.choose(.pat)
            XCTAssertFalse(line.isEmpty)
            XCTAssertNotEqual(line, previous)
            previous = line
        }
    }

    func testSyncSnapshotJSONRoundTrip() throws {
        let json = """
        {
          "live": true,
          "version": "2.1.2",
          "updated_at": "2026-11-28 13:00:00",
          "device_id": "mac-mini-01",
          "stats": {
            "affinity": 18,
            "pats": 7,
            "snacks": 3,
            "focus_sessions": 2,
            "mood": 92,
            "total_minutes": 45
          },
          "voice": {
            "active": true,
            "summary": "已接入耳机"
          },
          "journal": [{"at": "11-28 12:55", "text": "管理员摸了摸她的头"}],
          "turns": [{"user": "在吗", "assistant": "哼，我一直在呢。", "occurred_at": "2026-11-28T12:55:00"}]
        }
        """
        let decoded = try JSONDecoder().decode(SyncSnapshot.self, from: Data(json.utf8))
        XCTAssertTrue(decoded.live)
        XCTAssertEqual(decoded.stats.affinity, 18)
        XCTAssertEqual(decoded.stats.focusSessions, 2)
        XCTAssertEqual(decoded.turns.first?.assistant, "哼，我一直在呢。")
    }
}
