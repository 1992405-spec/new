import Foundation
import Security

enum Keychain {
    private static let service = "aikeyboard"

    /// Info.plist의 SharedKeychainGroup (project.yml에서 채워져요)
    private static var accessGroup: String? {
        Bundle.main.object(forInfoDictionaryKey: "SharedKeychainGroup") as? String
    }

    private static func baseQuery(account: String) -> [String: Any] {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        if let group = accessGroup { query[kSecAttrAccessGroup as String] = group }
        return query
    }

    static func read(account: String) -> Data? {
        var query = baseQuery(account: account)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess else { return nil }
        return result as? Data
    }

    static func write(_ data: Data, account: String) -> Bool {
        let query = baseQuery(account: account)
        let attributes: [String: Any] = [
            kSecValueData as String: data,
            // 키보드는 잠금 상태에서도 뜰 수 있어서 첫 잠금 해제 이후로 열어둬요
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]
        let status = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            let add = query.merging(attributes) { _, new in new }
            return SecItemAdd(add as CFDictionary, nil) == errSecSuccess
        }
        return status == errSecSuccess
    }
}
