import UIKit

final class KeyboardViewController: UIInputViewController {
    private var settings = Settings()
    private var busy = false
    /// 되돌리기용: 마지막으로 바꾼 원문과 넣은 번역
    private var lastEdit: (original: String, inserted: String)?

    private let statusLabel = UILabel()
    private let globeButton = UIButton(type: .system)
    private let languageButton = UIButton(type: .system)
    private var actionButtons: [UIButton] = []

    override func viewDidLoad() {
        super.viewDidLoad()
        buildLayout()
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        settings = SettingsStore.load()
        updateLanguageButton()
        if !hasFullAccess {
            setStatus("설정에서 ‘전체 접근 허용’을 켜야 번역할 수 있어요.")
        } else if settings.apiKey.isEmpty {
            setStatus("AI 번역 키보드 앱에서 API 키를 먼저 입력해 주세요.")
        } else {
            setStatus("글을 선택하고 ‘번역으로 대체’를 누르세요.")
        }
    }

    override func viewWillLayoutSubviews() {
        super.viewWillLayoutSubviews()
        globeButton.isHidden = !needsInputModeSwitchKey
    }

    // MARK: - Layout

    private func buildLayout() {
        statusLabel.font = .systemFont(ofSize: 14)
        statusLabel.textColor = .secondaryLabel
        statusLabel.numberOfLines = 3
        statusLabel.textAlignment = .center
        statusLabel.setContentCompressionResistancePriority(.defaultLow, for: .vertical)

        let replace = makeButton("번역으로 대체", primary: true, action: #selector(translateSelection))
        let before = makeButton("앞 글 전부 번역", action: #selector(translateBeforeCursor))
        let paste = makeButton("복사한 글 번역해 넣기", action: #selector(translateClipboard))
        let undo = makeButton("되돌리기", action: #selector(undoLastEdit))
        actionButtons = [replace, before, paste, undo]

        globeButton.setImage(UIImage(systemName: "globe"), for: .normal)
        globeButton.tintColor = .label
        globeButton.addTarget(self, action: #selector(handleInputModeList(from:with:)), for: .allTouchEvents)
        styleKey(globeButton)
        globeButton.widthAnchor.constraint(equalToConstant: 48).isActive = true

        languageButton.addTarget(self, action: #selector(swapLanguages), for: .touchUpInside)
        styleKey(languageButton)

        let space = makeKey(title: "space", action: #selector(insertSpace))
        let delete = makeKey(image: "delete.left", action: #selector(deleteBackward))
        let enter = makeKey(image: "return", action: #selector(insertNewline))
        delete.widthAnchor.constraint(equalToConstant: 52).isActive = true
        enter.widthAnchor.constraint(equalToConstant: 52).isActive = true

        let row1 = row([replace, before])
        let row2 = row([paste, undo])
        let row3 = row([globeButton, languageButton, space, delete, enter])
        languageButton.widthAnchor.constraint(equalTo: space.widthAnchor).isActive = true

        let stack = UIStackView(arrangedSubviews: [statusLabel, row1, row2, row3])
        stack.axis = .vertical
        stack.spacing = 8
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)

        let height = view.heightAnchor.constraint(equalToConstant: 236)
        height.priority = .defaultHigh
        NSLayoutConstraint.activate([
            height,
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 6),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -6),
            stack.topAnchor.constraint(equalTo: view.topAnchor, constant: 8),
            stack.bottomAnchor.constraint(equalTo: view.bottomAnchor, constant: -6),
            row1.heightAnchor.constraint(equalToConstant: 46),
            row2.heightAnchor.constraint(equalTo: row1.heightAnchor),
            row3.heightAnchor.constraint(equalTo: row1.heightAnchor),
        ])
    }

    private func row(_ views: [UIView]) -> UIStackView {
        let stack = UIStackView(arrangedSubviews: views)
        stack.axis = .horizontal
        stack.spacing = 6
        stack.distribution = .fill
        if views.count == 2 {
            stack.distribution = .fillEqually
        }
        return stack
    }

    private func makeButton(_ title: String, primary: Bool = false, action: Selector) -> UIButton {
        var config: UIButton.Configuration = primary ? .filled() : .gray()
        config.title = title
        config.cornerStyle = .medium
        config.titleTextAttributesTransformer = UIConfigurationTextAttributesTransformer { attrs in
            var attrs = attrs
            attrs.font = .systemFont(ofSize: 15, weight: .semibold)
            return attrs
        }
        let button = UIButton(configuration: config)
        button.titleLabel?.adjustsFontSizeToFitWidth = true
        button.addTarget(self, action: action, for: .touchUpInside)
        return button
    }

    private func makeKey(title: String? = nil, image: String? = nil, action: Selector) -> UIButton {
        let button = UIButton(type: .system)
        if let title { button.setTitle(title, for: .normal) }
        if let image { button.setImage(UIImage(systemName: image), for: .normal) }
        button.tintColor = .label
        button.setTitleColor(.label, for: .normal)
        button.addTarget(self, action: action, for: .touchUpInside)
        styleKey(button)
        return button
    }

    private func styleKey(_ button: UIButton) {
        button.backgroundColor = .secondarySystemBackground
        button.layer.cornerRadius = 8
        button.titleLabel?.font = .systemFont(ofSize: 15)
    }

    private func updateLanguageButton() {
        languageButton.setTitle("→ \(settings.target.label)", for: .normal)
        languageButton.setTitleColor(.label, for: .normal)
    }

    private func setStatus(_ text: String) {
        statusLabel.text = text
    }

    private func setBusy(_ value: Bool) {
        busy = value
        actionButtons.forEach { $0.isEnabled = !value }
    }

    // MARK: - Actions

    @objc private func translateSelection() {
        guard let original = textDocumentProxy.selectedText, !original.isEmpty else {
            setStatus("바꿀 글을 먼저 선택해 주세요. 선택을 알려주지 않는 앱에서는 ‘앞 글 전부 번역’을 써 보세요.")
            return
        }
        run(original) { [weak self] result in
            guard let self else { return }
            // 기다리는 동안 선택이 바뀌었으면 엉뚱한 곳을 덮어쓰지 않게 복사만 해요
            guard self.textDocumentProxy.selectedText == original else {
                UIPasteboard.general.string = result
                self.setStatus("선택이 바뀌어서 번역을 복사해 뒀어요: \(result)")
                return
            }
            self.textDocumentProxy.insertText(result)
            self.lastEdit = (original, result)
            self.setStatus("바꿨어요. 원래대로 돌리려면 ‘되돌리기’를 누르세요.")
        }
    }

    @objc private func translateBeforeCursor() {
        guard let original = textDocumentProxy.documentContextBeforeInput,
              !original.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            setStatus("커서 앞에 번역할 글이 없어요.")
            return
        }
        run(original) { [weak self] result in
            guard let self else { return }
            guard self.textDocumentProxy.documentContextBeforeInput == original else {
                UIPasteboard.general.string = result
                self.setStatus("글이 바뀌어서 번역을 복사해 뒀어요: \(result)")
                return
            }
            for _ in 0..<original.count { self.textDocumentProxy.deleteBackward() }
            self.textDocumentProxy.insertText(result)
            self.lastEdit = (original, result)
            self.setStatus("바꿨어요. 원래대로 돌리려면 ‘되돌리기’를 누르세요.")
        }
    }

    @objc private func translateClipboard() {
        guard let original = UIPasteboard.general.string, !original.isEmpty else {
            setStatus("복사된 글이 없어요.")
            return
        }
        run(original) { [weak self] result in
            guard let self else { return }
            self.textDocumentProxy.insertText(result)
            self.lastEdit = ("", result)
            self.setStatus("번역을 넣었어요.")
        }
    }

    @objc private func undoLastEdit() {
        guard let edit = lastEdit else {
            setStatus("되돌릴 번역이 없어요.")
            return
        }
        let before = textDocumentProxy.documentContextBeforeInput ?? ""
        // 앞 글이 길면 일부만 알려줘서, 넣은 번역의 끝부분과 맞는지도 확인해요
        guard !before.isEmpty, before.hasSuffix(edit.inserted) || edit.inserted.hasSuffix(before) else {
            setStatus("커서를 번역한 글 바로 뒤에 두고 다시 눌러 주세요.")
            return
        }
        for _ in 0..<edit.inserted.count { textDocumentProxy.deleteBackward() }
        textDocumentProxy.insertText(edit.original)
        lastEdit = nil
        setStatus("되돌렸어요.")
    }

    @objc private func swapLanguages() {
        (settings.target, settings.fallback) = (settings.fallback, settings.target)
        SettingsStore.save(settings)
        updateLanguageButton()
        setStatus("이제 \(settings.target.label)로 번역해요. (이미 \(settings.target.label)면 \(settings.fallback.label)로)")
    }

    @objc private func insertSpace() { textDocumentProxy.insertText(" ") }
    @objc private func insertNewline() { textDocumentProxy.insertText("\n") }
    @objc private func deleteBackward() { textDocumentProxy.deleteBackward() }

    // MARK: - Translate

    private func run(_ text: String, completion: @escaping (String) -> Void) {
        guard !busy else { return }
        guard hasFullAccess else {
            setStatus("설정 → 일반 → 키보드 → 키보드 → AI 번역에서 ‘전체 접근 허용’을 켜 주세요.")
            return
        }
        settings = SettingsStore.load()
        let translator = Translator(settings: settings)
        setBusy(true)
        setStatus("\(settings.provider == .openAI ? "GPT" : "Claude")로 번역 중…")
        Task { @MainActor in
            defer { self.setBusy(false) }
            do {
                completion(try await translator.translate(text))
            } catch {
                self.setStatus(error.localizedDescription)
            }
        }
    }
}
