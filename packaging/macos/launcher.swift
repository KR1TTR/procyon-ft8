// Procyon - macOS launcher.
// A small native app: it starts the bundled Python server, gives Procyon a Dock
// icon and a Quit command (so the server and any transmission always stop with
// it), and opens the web interface in the default browser.
import Cocoa

let port = 8073
let url = URL(string: "http://127.0.0.1:\(port)/")!

final class Delegate: NSObject, NSApplicationDelegate {
    var server: Process?
    var demo = false
    var quitting = false
    var restarting = false
    var demoItem: NSMenuItem!
    let logURL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Logs/Procyon/procyon.log")

    func applicationDidFinishLaunching(_ note: Notification) {
        buildMenu()
        startServer(openBrowser: true)
    }

    // MARK: menu

    func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem(); main.addItem(appItem)
        let app = NSMenu(); appItem.submenu = app
        app.addItem(withTitle: "About Procyon", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        app.addItem(.separator())
        let open = app.addItem(withTitle: "Open Procyon in Browser", action: #selector(openBrowser), keyEquivalent: "o")
        open.target = self
        demoItem = app.addItem(withTitle: "Demo Mode (simulated band)", action: #selector(toggleDemo), keyEquivalent: "")
        demoItem.target = self
        let logs = app.addItem(withTitle: "Show Log", action: #selector(showLog), keyEquivalent: "")
        logs.target = self
        app.addItem(.separator())
        app.addItem(withTitle: "Hide Procyon", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        app.addItem(.separator())
        app.addItem(withTitle: "Quit Procyon", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        NSApp.mainMenu = main
    }

    func applicationDockMenu(_ sender: NSApplication) -> NSMenu? {
        let m = NSMenu()
        m.addItem(withTitle: "Open Procyon in Browser", action: #selector(openBrowser), keyEquivalent: "").target = self
        return m
    }

    @objc func openBrowser() { NSWorkspace.shared.open(url) }
    @objc func showLog() { NSWorkspace.shared.activateFileViewerSelecting([logURL]) }

    @objc func toggleDemo() {
        demo.toggle()
        demoItem.state = demo ? .on : .off
        restarting = true
        stopServer()
        startServer(openBrowser: false)   // the open browser tab reconnects by itself
    }

    // Clicking the Dock icon again brings the interface back up.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        openBrowser()
        return false
    }

    // MARK: server process

    func startServer(openBrowser launchBrowser: Bool) {
        let res = Bundle.main.resourcePath!
        let python = Bundle.main.bundlePath + "/Contents/Frameworks/Python.framework/Versions/3.13/bin/python3.13"
        try? FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        let log = try? FileHandle(forWritingTo: logURL)

        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-m", "procyon", "--no-browser", "--port", String(port)] + (demo ? ["--demo"] : [])
        p.currentDirectoryURL = URL(fileURLWithPath: res)
        var env = ProcessInfo.processInfo.environment
        env["PYTHONPATH"] = res
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONNOUSERSITE"] = "1"
        env.removeValue(forKey: "PYTHONHOME")
        p.environment = env
        p.standardOutput = log
        p.standardError = log
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async { self?.serverExited(proc) }
        }
        do { try p.run() } catch {
            fail("Procyon could not start its engine.\n\n\(error.localizedDescription)")
            return
        }
        server = p
        restarting = false
        if launchBrowser { waitThenOpen() }
    }

    func stopServer() {
        guard let p = server, p.isRunning else { return }
        p.terminate()                       // SIGTERM: Procyon halts TX and closes cleanly
        let deadline = Date().addingTimeInterval(4)
        while p.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
        if p.isRunning { kill(p.processIdentifier, SIGKILL) }
    }

    func waitThenOpen(tries: Int = 0) {
        var req = URLRequest(url: url, timeoutInterval: 1)
        req.httpMethod = "HEAD"
        URLSession.shared.dataTask(with: req) { [weak self] _, resp, _ in
            DispatchQueue.main.async {
                if resp != nil { self?.openBrowser() }
                else if tries < 40 { DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { self?.waitThenOpen(tries: tries + 1) } }
            }
        }.resume()
    }

    func serverExited(_ proc: Process) {
        if quitting || restarting || proc !== server { return }
        if proc.terminationStatus == 0 { NSApp.terminate(nil); return }   // e.g. another copy was already running
        let tail = (try? String(contentsOf: logURL, encoding: .utf8))?
            .split(separator: "\n").suffix(8).joined(separator: "\n") ?? ""
        fail("Procyon stopped unexpectedly.\n\n\(tail)")
    }

    func fail(_ text: String) {
        let a = NSAlert()
        a.messageText = "Procyon"
        a.informativeText = text
        a.addButton(withTitle: "Quit")
        a.addButton(withTitle: "Show Log")
        if a.runModal() == .alertSecondButtonReturn { showLog() }
        quitting = true
        NSApp.terminate(nil)
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        quitting = true
        stopServer()
        return .terminateNow
    }
}

let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
