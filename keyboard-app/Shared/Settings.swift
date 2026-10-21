import Foundation

enum Provider: String, Codable, CaseIterable, Identifiable {
    case openAI
    case claude

    var id: String { rawValue }

    var label: String {
        switch self {
        case .openAI: return "GPT (OpenAI)"
        case .claude: return "Claude (Anthropic)"
        }
    }

    var defaultModel: String {
        switch self {
        case .openAI: return "gpt-5-mini"
        case .claude: return "claude-haiku-5-5"
        }
    }
}

enum Language: String, Codable, CaseIterable, Identifiable {
    case korean, english, japanese, chinese, spanish, french, german, vietnamese

    var id: String { rawValue }

    var label: String {
        switch self {
        case .korean: return "한국어"
        case .english: return "영어"
        case .japanese: return "일본어"
        case .chinese: return "중국어"
        case .spanish: return "스페인어"
        case .french: return "프랑스어"
        case .german: return "독일어"
        case .vietnamese: return "베트남어"
        }
    }

    /// 프롬프트에 넣는 이름
    var promptName: String {
        switch self {
        case .korean: return "Korean"
        case .english: return "English"
        case .japanese: return "Japanese"
        case .chinese: return "Simplified Chinese"
        case .spanish: return "Spanish"
        case .french: return "French"
        case .german: return "German"
        case .vietnamese: return "Vietnamese"
        }
    }
}

struct Settings: Codable, Equatable {
    var provider: Provider = .openAI
    var openAIKey: String = ""
    var claudeKey: String = ""
    var openAIModel: String = ""
    var claudeModel: String = ""
    /// 기본 번역 언어
    var target: Language = .korean
    /// 원문이 이미 기본 언어일 때 번역할 언어
    var fallback: Language = .english

    var apiKey: String {
        provider == .openAI ? openAIKey : claudeKey
    }

    var model: String {
        let custom = provider == .openAI ? openAIModel : claudeModel
        let trimmed = custom.trimmingCharacters(in: .whitespaces)
        return trimmed.isEmpty ? provider.defaultModel : trimmed
    }
}

/// 앱과 키보드가 같은 키체인 그룹으로 설정을 공유해요.
enum SettingsStore {
    private static let account = "settings"

    static func load() -> Settings {
        guard let data = Keychain.read(account: account),
              let settings = try? JSONDecoder().decode(Settings.self, from: data)
        else { return Settings() }
        return settings
    }

    @discardableResult
    static func save(_ settings: Settings) -> Bool {
        guard let data = try? JSONEncoder().encode(settings) else { return false }
        return Keychain.write(data, account: account)
    }
}
