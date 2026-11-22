import AppKit
import Foundation

@main
struct MoneyTrackerLauncher {
    static func main() {
        let appURL = Bundle.main.bundleURL
        let projectURL = appURL.deletingLastPathComponent()
        let scriptURL = projectURL.appendingPathComponent("launch-mac.sh")
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = [scriptURL.path]

        do {
            try process.run()
            process.waitUntilExit()
            if process.terminationStatus != 0 {
                let alert = NSAlert()
                alert.messageText = "Money Tracker could not start"
                alert.informativeText = "Check ~/Library/Logs/Personal Money Tracker/server.log for details."
                alert.runModal()
            }
        } catch {
            let alert = NSAlert()
            alert.messageText = "Money Tracker could not start"
            alert.informativeText = error.localizedDescription
            alert.runModal()
        }
    }
}
