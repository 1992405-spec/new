import SwiftUI
import UIKit

struct ContentView: View {
    @State private var settings = SettingsStore.load()
    @State private var savedMessage: String?
    @State private var testInput = "Hello! How are you today?"
    @State private var testOutput = ""
    @State private var testing = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("사용할 AI", selection: $settings.provider) {
                        ForEach(Provider.allCases) { Text($0.label).tag($0) }
                    }
                    if settings.provider == .openAI {
                        SecureField("OpenAI API 키 (sk-...)", text: $settings.openAIKey)
                        TextField("모델 (기본 \(Provider.openAI.defaultModel))", text: $settings.openAIModel)
                    } else {
                        SecureField("Claude API 키 (sk-ant-...)", text: $settings.claudeKey)
                        TextField("모델 (기본 \(Provider.claude.defaultModel))", text: $settings.claudeModel)
                    }
                } header: {
                    Text("AI")
                } footer: {
                    Text("API 키는 이 기기의 키체인에만 저장돼요. 모델 칸을 비워 두면 기본 모델을 써요.")
                }
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()

                Section {
                    Picker("번역할 언어", selection: $settings.target) {
                        ForEach(Language.allCases) { Text($0.label).tag($0) }
                    }
                    Picker("이미 그 언어면", selection: $settings.fallback) {
                        ForEach(Language.allCases) { Text($0.label).tag($0) }
                    }
                } header: {
                    Text("언어")
                } footer: {
                    Text("예: 번역할 언어가 한국어면 외국어는 한국어로, 한국어는 \(settings.fallback.label)로 번역해요.")
                }

                Section {
                    Button("저장") { save() }
                    if let savedMessage { Text(savedMessage).foregroundStyle(.secondary) }
                }

                Section("번역 테스트") {
                    TextField("번역할 문장", text: $testInput, axis: .vertical)
                    Button(testing ? "번역 중…" : "번역해 보기") { runTest() }
                        .disabled(testing)
                    if !testOutput.isEmpty { Text(testOutput).textSelection(.enabled) }
                }

                Section {
                    Text("1. 설정 → 일반 → 키보드 → 키보드 → 새로운 키보드 추가 → ‘AI 번역’")
                    Text("2. 추가한 ‘AI 번역’을 눌러 ‘전체 접근 허용’ 켜기 (번역 서버에 연결하려면 필요해요)")
                    Text("3. 아무 입력창에서 🌐 키를 눌러 AI 번역 키보드로 바꾸기")
                    Button("설정 앱 열기") {
                        if let url = URL(string: UIApplication.openSettingsURLString) {
                            UIApplication.shared.open(url)
                        }
                    }
                } header: {
                    Text("키보드 켜는 법")
                }
            }
            .navigationTitle("AI 번역 키보드")
        }
    }

    private func save() {
        savedMessage = SettingsStore.save(settings) ? "저장했어요." : "저장에 실패했어요."
    }

    private func runTest() {
        save()
        testing = true
        testOutput = ""
        let translator = Translator(settings: settings)
        let input = testInput
        Task {
            do {
                testOutput = try await translator.translate(input)
            } catch {
                testOutput = error.localizedDescription
            }
            testing = false
        }
    }
}
