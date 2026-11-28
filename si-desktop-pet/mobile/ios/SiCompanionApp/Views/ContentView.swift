import SwiftUI
import Combine
import SiCompanionCore

@MainActor
final class SiCompanionStore: ObservableObject {
    @Published var stats = SyncStats()
    @Published var mood: SiMood = .happy
    @Published var bubbleText = "（祀执笔轻轻落墨）管理员，我在 iPhone 这边也陪着你呢。"
    @Published var journal: [SyncJournalRow] = []
    @Published var turns: [SyncChatTurn] = []
    @Published var syncEndpoint: String = UserDefaults.standard.string(forKey: "si_ios_sync_url") ?? ""
    @Published var directAPIBase: String = UserDefaults.standard.string(forKey: "si_ios_ai_base") ?? "https://api.deepseek.com/v1"
    @Published var directModel: String = UserDefaults.standard.string(forKey: "si_ios_ai_model") ?? "deepseek-chat"
    /// API Key stays strictly in RAM and is never persisted to UserDefaults/disk.
    @Published var directAPIKey: String = ""
    @Published var isSyncOnline = false
    @Published var isSending = false
    @Published var errorBanner: String?

    @Published var focusRemainingSeconds: Int?
    private var focusTimer: AnyCancellable?
    private let bag = SiCompanionBag()
    private let api = SiSyncAPIClient()

    init() {
        loadLocalState()
    }

    private func storageURL() -> URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("SiCompanion", isDirectory: true)
            .appendingPathComponent("ios_state.json")
    }

    private struct LocalSave: Codable {
        var stats: SyncStats
        var journal: [SyncJournalRow]
        var turns: [SyncChatTurn]
    }

    private func loadLocalState() {
        let url = storageURL()
        guard let data = try? Data(contentsOf: url),
              let saved = try? JSONDecoder().decode(LocalSave.self, from: data) else { return }
        stats = saved.stats
        journal = saved.journal
        turns = saved.turns
    }

    private func saveLocalState() {
        let url = storageURL()
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let data = try? JSONEncoder().encode(LocalSave(stats: stats, journal: Array(journal.prefix(50)), turns: Array(turns.suffix(30)))) {
            try? data.write(to: url, options: .atomic)
        }
    }

    func saveEndpoints() {
        UserDefaults.standard.set(syncEndpoint.trimmingCharacters(in: .whitespacesAndNewlines), forKey: "si_ios_sync_url")
        UserDefaults.standard.set(directAPIBase.trimmingCharacters(in: .whitespacesAndNewlines), forKey: "si_ios_ai_base")
        UserDefaults.standard.set(directModel.trimmingCharacters(in: .whitespacesAndNewlines), forKey: "si_ios_ai_model")
    }

    private func appendJournal(_ text: String) {
        let fmt = DateFormatter()
        fmt.dateFormat = "MM-dd HH:mm"
        journal.insert(SyncJournalRow(at: fmt.string(from: Date()), text: text), at: 0)
    }

    func pat() {
        mood = .happy
        bubbleText = bag.choose(.pat)
        stats.pats += 1
        stats.affinity += 1
        stats.mood = min(100, stats.mood + 2)
        appendJournal("摸了摸祀的头")
        saveLocalState()
        Task { await pushInteract("pat") }
    }

    func feed() {
        mood = .blush
        bubbleText = bag.choose(.feed)
        stats.snacks += 1
        stats.affinity += 3
        stats.mood = min(100, stats.mood + 6)
        appendJournal("送了一份点心")
        saveLocalState()
        Task { await pushInteract("snack") }
    }

    func contact(_ occasion: SiDialogue.Occasion) {
        switch occasion {
        case .stuck, .painting: mood = .thinking
        case .tired, .quiet: mood = .sleepy
        default: mood = .happy
        }
        bubbleText = bag.choose(occasion)
    }

    func toggleFocus(minutes: Int = 25) {
        if focusRemainingSeconds != nil {
            focusTimer?.cancel()
            focusTimer = nil
            focusRemainingSeconds = nil
            mood = .neutral
            bubbleText = bag.choose(.return)
            return
        }
        mood = .thinking
        bubbleText = bag.choose(.focusStart)
        focusRemainingSeconds = minutes * 60
        focusTimer = Timer.publish(every: 1, on: .main, in: .common)
            .autoconnect()
            .sink { [weak self] _ in
                guard let self, let rem = self.focusRemainingSeconds else { return }
                if rem <= 1 {
                    self.focusTimer?.cancel()
                    self.focusTimer = nil
                    self.focusRemainingSeconds = nil
                    self.stats.focusSessions += 1
                    self.stats.affinity += 5
                    self.stats.totalMinutes += minutes
                    self.mood = .happy
                    self.bubbleText = self.bag.choose(.focusDone)
                    self.appendJournal("完成了一段 \(minutes) 分钟专注")
                    self.saveLocalState()
                } else {
                    self.focusRemainingSeconds = rem - 1
                }
            }
    }

    func refreshSync() async {
        let base = syncEndpoint.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !base.isEmpty else {
            isSyncOnline = false
            return
        }
        do {
            let snap = try await api.fetchSyncStatus(baseURL: base)
            applySnapshot(snap)
            isSyncOnline = true
            errorBanner = nil
        } catch {
            isSyncOnline = false
            errorBanner = error.localizedDescription
        }
    }

    private func pushInteract(_ action: String) async {
        let base = syncEndpoint.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !base.isEmpty else { return }
        if let snap = try? await api.sendInteract(baseURL: base, action: action) {
            applySnapshot(snap)
            isSyncOnline = true
        }
    }

    private func applySnapshot(_ snap: SyncSnapshot) {
        stats = snap.stats
        if !snap.journal.isEmpty { journal = snap.journal }
        if !snap.turns.isEmpty { turns = snap.turns }
        saveLocalState()
    }

    func sendMessage(_ text: String) async {
        let msg = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !msg.isEmpty, !isSending else { return }
        isSending = true
        mood = .thinking
        errorBanner = nil
        defer { isSending = false }

        do {
            let syncBase = syncEndpoint.trimmingCharacters(in: .whitespacesAndNewlines)
            if !syncBase.isEmpty {
                let res = try await api.sendSyncedChat(baseURL: syncBase, message: msg)
                if let snap = res.snapshot {
                    applySnapshot(snap)
                } else if let reply = res.reply {
                    turns.append(SyncChatTurn(user: msg, assistant: reply))
                }
                if let reply = res.reply {
                    bubbleText = SiSyncAPIClient.cleanDisplayText(reply)
                }
                mood = .happy
                isSyncOnline = true
                return
            }

            let reply = try await api.sendDirectAIChat(
                apiBase: directAPIBase,
                model: directModel,
                apiKey: directAPIKey,
                history: turns,
                userMessage: msg
            )
            turns.append(SyncChatTurn(user: msg, assistant: reply))
            bubbleText = SiSyncAPIClient.cleanDisplayText(reply)
            mood = .happy
            appendJournal("在 iPhone 上和祀聊了一句")
            saveLocalState()
        } catch {
            mood = .troubled
            errorBanner = error.localizedDescription
        }
    }
}

struct ContentView: View {
    @StateObject private var store = SiCompanionStore()
    @State private var chatInput = ""
    @State private var showSettings = false

    private let inkBg = Color(red: 0.08, green: 0.11, blue: 0.10)
    private let jadeAccent = Color(red: 0.32, green: 0.79, blue: 0.56)
    private let goldAccent = Color(red: 0.83, green: 0.69, blue: 0.37)

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 16) {
                    stageCard
                    statsBar
                    quickInteractionBar
                    chatSection
                    journalSection
                }
                .padding()
            }
            .background(Color(.systemGroupedBackground))
            .navigationTitle("小祀 · Si")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showSettings = true
                    } label: {
                        Image(systemName: store.isSyncOnline ? "link.circle.fill" : "slider.horizontal.3")
                            .foregroundStyle(store.isSyncOnline ? jadeAccent : goldAccent)
                    }
                }
            }
            .sheet(isPresented: $showSettings) {
                SettingsSheet(store: store)
            }
            .task {
                await store.refreshSync()
            }
        }
    }

    private var stageCard: some View {
        VStack(spacing: 12) {
            Text(store.bubbleText)
                .font(.subheadline)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(RoundedRectangle(cornerRadius: 14).fill(Color(.secondarySystemBackground)))
                .overlay(RoundedRectangle(cornerRadius: 14).stroke(goldAccent.opacity(0.45), lineWidth: 1))

            Group {
                if let uiImg = UIImage(named: store.mood.rawValue) {
                    Image(uiImage: uiImg)
                        .resizable()
                        .scaledToFit()
                } else {
                    Image(systemName: "sparkles")
                        .font(.system(size: 72))
                        .foregroundStyle(jadeAccent)
                }
            }
            .frame(height: 210)
            .clipShape(RoundedRectangle(cornerRadius: 20))
            .onTapGesture {
                store.pat()
            }

            if let rem = store.focusRemainingSeconds {
                Text(String(format: "专注计时 %02d:%02d", rem / 60, rem % 60))
                    .font(.caption.monospacedDigit().bold())
                    .foregroundStyle(jadeAccent)
            }
        }
        .padding()
        .frame(maxWidth: .infinity)
        .background(RoundedRectangle(cornerRadius: 20).fill(inkBg.opacity(0.06)))
        .overlay(RoundedRectangle(cornerRadius: 20).stroke(jadeAccent.opacity(0.25), lineWidth: 1))
    }

    private var statsBar: some View {
        HStack(spacing: 10) {
            statPill("亲密度", "\(store.stats.affinity)")
            statPill("心情", "\(store.stats.mood)")
            statPill("摸头", "\(store.stats.pats)")
            statPill("专注", "\(store.stats.focusSessions)")
        }
    }

    private func statPill(_ title: String, _ value: String) -> some View {
        VStack(spacing: 2) {
            Text(title).font(.caption2).foregroundStyle(.secondary)
            Text(value).font(.headline.monospacedDigit()).foregroundStyle(jadeAccent)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
        .background(RoundedRectangle(cornerRadius: 12).fill(Color(.secondarySystemGroupedBackground)))
    }

    private var quickInteractionBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                Button("👋 摸摸头") { store.pat() }.buttonStyle(.borderedProminent).tint(jadeAccent)
                Button("🍵 送点心") { store.feed() }.buttonStyle(.bordered)
                Button(store.focusRemainingSeconds == nil ? "⏱ 专注25分" : "⏹ 结束专注") {
                    store.toggleFocus()
                }.buttonStyle(.bordered)
                ForEach(SiDialogue.contactOptions, id: \.title) { opt in
                    Button(opt.title) { store.contact(opt.occasion) }.buttonStyle(.bordered)
                }
            }
        }
    }

    private var chatSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("💬 与小祀聊聊")
                    .font(.headline)
                Spacer()
                Text(store.isSyncOnline ? "已同步 Mac/Win" : "本机 / 直连 API")
                    .font(.caption2)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(Capsule().fill(jadeAccent.opacity(0.18)))
            }

            if let err = store.errorBanner {
                Text(err).font(.caption).foregroundStyle(.orange)
            }

            ForEach(store.turns.suffix(6)) { turn in
                VStack(alignment: .leading, spacing: 4) {
                    Text("管理员：\(turn.user)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Text("祀：\(SiSyncAPIClient.cleanDisplayText(turn.assistant))")
                        .font(.subheadline)
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(RoundedRectangle(cornerRadius: 10).fill(jadeAccent.opacity(0.12)))
                }
            }

            HStack(spacing: 8) {
                TextField("和小祀说句话…", text: $chatInput)
                    .textFieldStyle(.roundedBorder)
                Button(store.isSending ? "…" : "发送") {
                    let msg = chatInput
                    chatInput = ""
                    Task { await store.sendMessage(msg) }
                }
                .buttonStyle(.borderedProminent)
                .tint(jadeAccent)
                .disabled(store.isSending || chatInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding()
        .background(RoundedRectangle(cornerRadius: 16).fill(Color(.secondarySystemGroupedBackground)))
    }

    private var journalSection: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("📔 相伴手记").font(.headline)
            if store.journal.isEmpty {
                Text("点击上方小祀或和她聊聊，记录就会留在这里。").font(.caption).foregroundStyle(.secondary)
            } else {
                ForEach(store.journal.prefix(6)) { row in
                    HStack {
                        Text(row.at).font(.caption2.monospacedDigit()).foregroundStyle(.secondary)
                        Text(row.text).font(.caption)
                        Spacer()
                    }
                }
            }
        }
        .padding()
        .background(RoundedRectangle(cornerRadius: 16).fill(Color(.secondarySystemGroupedBackground)))
    }
}

private struct SettingsSheet: View {
    @ObservedObject var store: SiCompanionStore
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section("连接 Mac mini / Windows 实时节点（可选）") {
                    TextField("例如 http://192.168.1.10:8765 或公网隧道 URL", text: $store.syncEndpoint)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                    Button("测试并同步桌面端数据") {
                        store.saveEndpoints()
                        Task { await store.refreshSync() }
                    }
                }
                Section("iOS 独立直连大模型（DeepSeek / 学校 API / Mac Ollama）") {
                    TextField("API Base URL", text: $store.directAPIBase)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                    TextField("Model 名称", text: $store.directModel)
                        .textInputAutocapitalization(.never)
                    SecureField("API Key（仅留运行内存，不写盘）", text: $store.directAPIKey)
                }
            }
            .navigationTitle("连接与同步设置")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") {
                        store.saveEndpoints()
                        dismiss()
                    }
                }
            }
        }
    }
}
