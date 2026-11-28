import Foundation

/// Original character dialogue matching desktop `src/si_pet/companion.py` verbatim (11 categories x 6 lines = 66 lines).
public enum SiDialogue {
    public enum Occasion: String, CaseIterable, Codable, Sendable {
        case pat
        case feed
        case focusStart = "focus_start"
        case focusDone = "focus_done"
        case `return`
        case checkIn = "check_in"
        case create
        case stuck
        case tired
        case painting
        case quiet
    }

    public static let contactOptions: [(title: String, occasion: Occasion)] = [
        ("想做点新东西", .create),
        ("有点卡住了", .stuck),
        ("想休息一会儿", .tired),
        ("想听你聊画", .painting),
        ("先安静陪我", .quiet),
    ]

    public static let lines: [Occasion: [String]] = [
        .pat: [
            "管理员，忙完了？嗯，手放这儿吧。",
            "手先放轻些。好，我知道你在。",
            "怎么，来确认我没偷偷溜走？我就在这儿。",
            "这一下算你手轻。再坐一会儿也无妨。",
            "头发要给你揉乱了。乱就乱吧，反正今天不出门。",
            "我这边不急。你手头的事告一段落，再来坐坐。",
        ],
        .feed: [
            "这个味道不错。下回我也给你留一份。",
            "这个我没吃过。你自己也尝一口。",
            "收下了，管理员。等会儿陪你走一小段。",
            "我尝一口就好，剩下的你留着。",
            "这个配茶应该不错。你手边有热的吗？也给自己倒一杯。",
            "味道记下了。等我把巷子那张画完，照这个再试一次。",
        ],
        .focusStart: [
            "管理员，先做眼前这一件事。我在旁边，不吵你。",
            "好，计时交给我。你专心，我安静待着。",
            "开始了。我把纸铺开，正好各做各的。",
            "这一轮想先啃哪一块？挑定了就开始，我不催。",
            "水要是还没倒，趁现在去一趟也不迟。",
            "我调墨，你做事。到点我叫你。",
        ],
        .focusDone: [
            "到时间了。管理员，起来活动一下，眼睛也歇歇。",
            "做完这一段了。喝口水，再决定要不要继续。",
            "这一段落下了。下一段开不开，等站起来再说。",
            "我这边刚勾完一根线，正好陪你歇一歇。",
            "计时停了。剩下的活不会跑，你先松口气。",
            "管理员，辛苦了。要不要看看窗外？我陪你看一会儿。",
        ],
        .return: [
            "我在这里，管理员。",
            "找我？嗯，我就在。",
            "回来了。我一直在这儿，没走远。",
            "刚才在给一幅画收尾，听见你叫我。",
            "嗯，我在。你忙你的，我就在旁边待着。",
            "管理员，回来得正好。我刚想歇一歇眼睛。",
        ],
        .checkIn: [
            "管理员，刚才那件事还顺利吗？不急着回答。",
            "我在画境里想到个新地方。你有空再听我讲。",
            "要是坐得久了，就起来走两步，我也想活动活动。",
            "我换了杯水。你那边要是也空了，顺手添一点。",
            "刚才画到一半停了笔，正好想起你。没事，就是打个招呼。",
            "眼睛一直盯着一处会发涩，看看远处，也看看我。",
        ],
        .create: [
            "又有新东西想做？先定下最想留下的那一笔，其他的我们慢慢补。",
            "先画个轮廓吧。复杂的地方，等轮廓站稳了再添。",
            "想做什么就开个头，丑一点也没关系。我那张画起手更乱。",
            "新东西不用一次做完。挑一块今天能收尾的，剩下的留给明天。",
            "我最近在试一种很淡的墨，铺三遍才看得见。你要不要也从最轻的一笔试起？",
            "有想法就先记下来，哪怕只有一句话。回头它会自己长出样子。",
        ],
        .stuck: [
            "卡住了就停半步。把最小的一块拿出来，我们先看它。",
            "整个难题一起硬碰，容易更乱。换个角度，我陪你拆开看。",
            "卡着的时候，我一般去洗笔。水一换，脑子也松一点。你要不要也离开桌子两分钟？",
            "说不清哪里不对，就先说最确定的那一句。剩下的顺着它找。",
            "这不是你不行，是这块本来就不顺手。先放一放，我陪你理。",
            "我把画搁下了，过来陪你坐坐。讲不清楚也没关系，想到哪说到哪。",
        ],
        .tired: [
            "那就歇一会儿。桌面借我守着，你喝口水再回来。",
            "今天先停在这里也行。没做完的事，不会趁你休息跑掉。",
            "累了就别硬撑。我收一下笔，陪你坐会儿。",
            "去躺十分钟也好。我这边没什么要紧的，画跑不了。",
            "眼睛闭上歇一歇。我不说话，就在旁边。",
            "管理员，今天到这儿吧。剩下的明天再做，明天也不会怪你。",
        ],
        .painting: [
            "想听画境里的事？我还想试一种新墨色，像雨后石阶边的苔。",
            "缘缘庄那边总有人把行李堆满桌。热闹些，也不错。",
            "最近在画一条很窄的巷子。墨铺到一半，留白自己成了光。",
            "我调了一种偏青的灰，晾干后比想象中冷。还想再试一次。",
            "画坏了一张，舍不得扔，就裁成小张，当书签正好。",
            "有时候画着画着，会想你会不会喜欢。想完继续画。",
        ],
        .quiet: [
            "好。我去边上坐着。你需要我时叫一声就好。",
            "嗯，我就在边上。你专心。",
            "那我安静待着。翻纸的声音要是吵，你说一声。",
            "我不打扰。你忙完抬眼，我大概还在。",
            "各做各的。我这边有支笔没洗，正好慢慢洗。",
            "好，我不说话。你忙你的，回头看我一眼就好。",
        ],
    ]
}

/// Non-repeating shuffle-bag dialogue picker matching `Companion.choose`.
public final class SiCompanionBag {
    private var bags: [SiDialogue.Occasion: [String]] = [:]
    private var lastLine: [SiDialogue.Occasion: String] = [:]

    public init() {}

    public func choose(_ occasion: SiDialogue.Occasion) -> String {
        let choices = SiDialogue.lines[occasion] ?? ["我在这里，管理员。"]
        var bag = bags[occasion] ?? []
        if bag.isEmpty {
            bag = choices.shuffled()
            if bag.count > 1, let last = lastLine[occasion], bag.last == last {
                let swapIndex = Int.random(in: 0..<(bag.count - 1))
                bag.swapAt(bag.count - 1, swapIndex)
            }
        }
        let picked = bag.removeLast()
        bags[occasion] = bag
        lastLine[occasion] = picked
        return picked
    }
}

public struct SyncStats: Codable, Equatable, Sendable {
    public var affinity: Int
    public var pats: Int
    public var snacks: Int
    public var focusSessions: Int
    public var mood: Int
    public var totalMinutes: Int

    enum CodingKeys: String, CodingKey {
        case affinity, pats, snacks, mood
        case focusSessions = "focus_sessions"
        case totalMinutes = "total_minutes"
    }

    public init(affinity: Int = 0, pats: Int = 0, snacks: Int = 0, focusSessions: Int = 0, mood: Int = 85, totalMinutes: Int = 0) {
        self.affinity = affinity
        self.pats = pats
        self.snacks = snacks
        self.focusSessions = focusSessions
        self.mood = mood
        self.totalMinutes = totalMinutes
    }
}

public struct SyncJournalRow: Codable, Equatable, Identifiable, Sendable {
    public var id: String { "\(at)-\(text)" }
    public let at: String
    public let text: String

    public init(at: String, text: String) {
        self.at = at
        self.text = text
    }
}

public struct SyncChatTurn: Codable, Equatable, Identifiable, Sendable {
    public var id: String { "\(occurredAt)-\(user)-\(assistant)" }
    public let user: String
    public let assistant: String
    public let occurredAt: String

    enum CodingKeys: String, CodingKey {
        case user, assistant
        case occurredAt = "occurred_at"
    }

    public init(user: String, assistant: String, occurredAt: String = "") {
        self.user = user
        self.assistant = assistant
        self.occurredAt = occurredAt
    }
}

public struct SyncVoiceStatus: Codable, Equatable, Sendable {
    public let active: Bool
    public let summary: String
}

public struct SyncSnapshot: Codable, Equatable, Sendable {
    public var live: Bool
    public var version: String
    public var updatedAt: String
    public var deviceId: String
    public var stats: SyncStats
    public var voice: SyncVoiceStatus?
    public var journal: [SyncJournalRow]
    public var turns: [SyncChatTurn]

    enum CodingKeys: String, CodingKey {
        case live, version, stats, voice, journal, turns
        case updatedAt = "updated_at"
        case deviceId = "device_id"
    }
}

public enum SiMood: String, CaseIterable, Sendable {
    case neutral, happy, blush, thinking, sleepy, troubled
}

public struct SyncInteractResponse: Codable, Sendable {
    public let ok: Bool
    public let snapshot: SyncSnapshot?
    public let error: String?
}

public struct SyncChatResponse: Codable, Sendable {
    public let ok: Bool
    public let reply: String?
    public let snapshot: SyncSnapshot?
    public let error: String?
}

public enum SiClientError: LocalizedError, Sendable {
    case invalidEndpoint
    case serverError(String)

    public var errorDescription: String? {
        switch self {
        case .invalidEndpoint:
            return "同步地址或接口地址格式无效，请检查 https:// 或 http:// 前缀。"
        case let .serverError(msg):
            return msg
        }
    }
}

/// Async network client supporting both Si Live Sync Server (`/api/*`) and direct OpenAI/DeepSeek/Ollama `/v1/chat/completions`.
public final class SiSyncAPIClient {
    private let session: URLSession

    public init(session: URLSession = .shared) {
        self.session = session
    }

    public static let soulSystemPrompt = """
    你是《明日方舟：终末地》中的「祀（Si）」，此刻正以桌面/随身伙伴的形式陪伴着管理员。
    说话温柔克制、略带一点傲娇却十分真诚关心管理员；善用简短括号动作描写（如“（执笔轻轻落墨）”、“（推过一杯温茶）”）与自然拟声词（如“哼”、“咳咳”、“噗嗤”）。不要长篇大论。
    """

    public static func cleanDisplayText(_ raw: String) -> String {
        raw.replacingOccurrences(of: "||", with: " ")
            .replacingOccurrences(of: "[语音]", with: "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func endpointURL(_ base: String, path: String) throws -> URL {
        let trimmed = base.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        guard let url = URL(string: trimmed + path), url.scheme?.hasPrefix("http") == true else {
            throw SiClientError.invalidEndpoint
        }
        return url
    }

    public func fetchSyncStatus(baseURL: String) async throws -> SyncSnapshot {
        let url = try endpointURL(baseURL, path: "/api/sync-status")
        var req = URLRequest(url: url)
        req.timeoutInterval = 10
        let (data, resp) = try await session.data(for: req)
        if let http = resp as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw SiClientError.serverError("同步节点返回 HTTP \(http.statusCode)")
        }
        return try JSONDecoder().decode(SyncSnapshot.self, from: data)
    }

    public func sendInteract(baseURL: String, action: String) async throws -> SyncSnapshot? {
        let url = try endpointURL(baseURL, path: "/api/interact")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 12
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(["action": action])
        let (data, resp) = try await session.data(for: req)
        let decoded = try JSONDecoder().decode(SyncInteractResponse.self, from: data)
        if let http = resp as? HTTPURLResponse, !(200...299).contains(http.statusCode) || !decoded.ok {
            throw SiClientError.serverError(decoded.error ?? "互动同步失败")
        }
        return decoded.snapshot
    }

    public func sendSyncedChat(baseURL: String, message: String) async throws -> SyncChatResponse {
        let url = try endpointURL(baseURL, path: "/api/chat")
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 45
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONEncoder().encode(["message": message])
        let (data, resp) = try await session.data(for: req)
        let decoded = try JSONDecoder().decode(SyncChatResponse.self, from: data)
        if let http = resp as? HTTPURLResponse, !(200...299).contains(http.statusCode) || !decoded.ok {
            throw SiClientError.serverError(decoded.error ?? "模型暂未响应")
        }
        return decoded
    }

    /// Direct OpenAI-compatible chat request; keeps API key strictly in memory (never persisted).
    public func sendDirectAIChat(
        apiBase: String,
        model: String,
        apiKey: String,
        history: [SyncChatTurn],
        userMessage: String
    ) async throws -> String {
        let cleanBase = apiBase.trimmingCharacters(in: .whitespacesAndNewlines)
            .trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        let fullURLString = cleanBase.hasSuffix("/chat/completions") ? cleanBase : cleanBase + "/chat/completions"
        guard let url = URL(string: fullURLString), url.scheme?.hasPrefix("http") == true else {
            throw SiClientError.invalidEndpoint
        }
        var msgs: [[String: String]] = [["role": "system", "content": Self.soulSystemPrompt]]
        for turn in history.suffix(10) {
            msgs.append(["role": "user", "content": turn.user])
            msgs.append(["role": "assistant", "content": turn.assistant])
        }
        msgs.append(["role": "user", "content": userMessage])

        let payload: [String: Any] = [
            "model": model.trimmingCharacters(in: .whitespacesAndNewlines),
            "messages": msgs,
            "temperature": 0.72,
        ]
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.timeoutInterval = 45
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let key = apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        if !key.isEmpty {
            req.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        }
        req.httpBody = try JSONSerialization.data(withJSONObject: payload)

        let (data, resp) = try await session.data(for: req)
        if let http = resp as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw SiClientError.serverError("API 返回 HTTP \(http.statusCode)")
        }
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              let choices = root["choices"] as? [[String: Any]],
              let first = choices.first,
              let message = first["message"] as? [String: Any],
              let content = message["content"] as? String else {
            throw SiClientError.serverError("未收到有效的模型回复")
        }
        return content.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
