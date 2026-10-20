// Renders Procyon's icon.svg onto a macOS-style rounded tile and writes an .iconset folder.
// usage: swift make_icon.swift icon.svg out.iconset
import Cocoa

let args = CommandLine.arguments
guard args.count == 3, let svg = NSImage(contentsOf: URL(fileURLWithPath: args[1])) else {
    FileHandle.standardError.write("usage: make_icon icon.svg out.iconset\n".data(using: .utf8)!)
    exit(1)
}
let out = URL(fileURLWithPath: args[2])
try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)

func render(_ px: Int) -> Data {
    let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px, bitsPerSample: 8,
                               samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                               colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    let s = CGFloat(px)
    // Apple's icon grid: the tile fills ~82% of the canvas, corner radius ~22.4% of the tile
    let tile = NSRect(x: s * 0.09, y: s * 0.09, width: s * 0.82, height: s * 0.82)
    let path = NSBezierPath(roundedRect: tile, xRadius: tile.width * 0.224, yRadius: tile.width * 0.224)
    let shadow = NSShadow()
    shadow.shadowColor = NSColor.black.withAlphaComponent(0.35)
    shadow.shadowBlurRadius = s * 0.02
    shadow.shadowOffset = NSSize(width: 0, height: -s * 0.012)
    NSGraphicsContext.saveGraphicsState()
    shadow.set()
    NSColor(red: 0.08, green: 0.11, blue: 0.18, alpha: 1).setFill()
    path.fill()
    NSGraphicsContext.restoreGraphicsState()
    NSGradient(starting: NSColor(red: 0.16, green: 0.21, blue: 0.34, alpha: 1),
               ending: NSColor(red: 0.05, green: 0.07, blue: 0.13, alpha: 1))!.draw(in: path, angle: -90)
    let art = tile.insetBy(dx: tile.width * 0.13, dy: tile.width * 0.13)
    svg.draw(in: art, from: .zero, operation: .sourceOver, fraction: 1)
    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

for base in [16, 32, 128, 256, 512] {
    try! render(base).write(to: out.appendingPathComponent("icon_\(base)x\(base).png"))
    try! render(base * 2).write(to: out.appendingPathComponent("icon_\(base)x\(base)@2x.png"))
}
