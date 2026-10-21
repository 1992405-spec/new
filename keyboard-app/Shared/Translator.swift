import Foundation

enum TranslateError: LocalizedError {
    case missingKey
    case badResponse(String)

    var errorDescription: String? {
        switch self {
        case .missingKey: return "API 키가 없어요. AI 번역 키보드 앱에서 입력해 주세요."
        case .badResponse(let message): return message
        }
    }
}

struct Translator {
    let settings: Settings

    func translate(_ text: String) async throws -> String {
        let key = settings.apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !key.isEmpty else { throw TranslateError.missingKey }

        let system = """
        You are a translator. Translate the user's text into \(settings.target.promptName). \
        If the text is already in \(settings.target.promptName), translate it into \(settings.fallback.promptName) instead. \
        Keep the tone, line breaks, emoji and formatting. Output only the translation, with no quotes or explanations.
        """

        var request: URLRequest
        let body: [String: Any]
        switch settings.provider {
        case .openAI:
            request = URLRequest(url: URL(string: "https://api.openai.com/v1/chat/completions")!)
            request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
            body = [
                "model": settings.model,
                "messages": [
                    ["role": "system", "content": system],
                    ["role": "user", "content": text],
                ],
            ]
        case .claude:
            request = URLRequest(url: URL(string: "https://api.anthropic.com/v1/messages")!)
            request.setValue(key, forHTTPHeaderField: "x-api-key")
            request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")
            body = [
                "model": settings.model,
                "max_tokens": 4096,
                "system": system,
                "messages": [["role": "user", "content": text]],
            ]
        }
        request.httpMethod = "POST"
        request.timeoutInterval = 60
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (data, response) = try await URLSession.shared.data(for: request)
        let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]

        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            let message = (json["error"] as? [String: Any])?["message"] as? String
            throw TranslateError.badResponse("오류 \(http.statusCode): \(message ?? "알 수 없는 오류")")
        }

        let output: String?
        switch settings.provider {
        case .openAI:
            let choices = json["choices"] as? [[String: Any]]
            output = (choices?.first?["message"] as? [String: Any])?["content"] as? String
        case .claude:
            let blocks = json["content"] as? [[String: Any]] ?? []
            let texts = blocks.compactMap { $0["type"] as? String == "text" ? $0["text"] as? String : nil }
            output = texts.isEmpty ? nil : texts.joined()
        }
        guard let result = output?.trimmingCharacters(in: .whitespacesAndNewlines), !result.isEmpty else {
            throw TranslateError.badResponse("번역 결과가 비어 있어요.")
        }
        return result
    }
}
