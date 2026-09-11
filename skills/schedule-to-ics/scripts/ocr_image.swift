// Vision OCR 兜底：当模型无法直接看图，或需要精确坐标来重建课表网格时使用。
//
// 用法（推荐用同目录的 ocr.sh 包装脚本，它会设置好模块缓存路径）:
//   swift ocr_image.swift 课表.png [作息时间表.png ...]
//
// 输出：先打印 "== 路径 (宽x高)"，然后按行分组，每行形如
//   行 y≈231: x=26 第1节 | x=150 高等数学 | x=512 大学物理
// y 越小越靠上，x 是左边界，单位是图片宽/高的千分比。x 相近的就是课表的同一列。
//
// 注意：识别需要访问系统 Vision 服务，在沙箱里会失败（nilError），需要提权后重试。

import AppKit
import Foundation
import Vision

let paths = Array(CommandLine.arguments.dropFirst())
guard !paths.isEmpty else {
    FileHandle.standardError.write("用法: swift ocr_image.swift <图片路径> [更多图片...]\n".data(using: .utf8)!)
    exit(2)
}

/// 归为同一行时允许的 y 偏差（千分比）
let rowTolerance = 15

for path in paths {
    guard let image = NSImage(contentsOfFile: path),
          let tiff = image.tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: tiff),
          let cgImage = bitmap.cgImage else {
        FileHandle.standardError.write("无法读取图片: \(path)\n".data(using: .utf8)!)
        continue
    }
    print("== \(path) (\(cgImage.width)x\(cgImage.height))")

    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["zh-Hans", "en-US"]
    request.usesLanguageCorrection = false
    request.minimumTextHeight = 0.0

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    do {
        try handler.perform([request])
    } catch {
        let message = "识别失败 \(path): \(error)\n"
            + "如果在沙箱里运行，请用 require_escalated 重试（Vision 需要访问系统服务）。\n"
        FileHandle.standardError.write(message.data(using: .utf8)!)
        continue
    }

    struct Piece {
        let x: Int
        let y: Int
        let text: String
    }

    let pieces: [Piece] = (request.results ?? []).compactMap { observation in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let box = observation.boundingBox
        return Piece(
            x: Int((box.minX * 1000).rounded()),
            y: Int(((1 - box.midY) * 1000).rounded()),
            text: candidate.string
        )
    }

    var rows: [[Piece]] = []
    for piece in pieces.sorted(by: { $0.y < $1.y }) {
        if let anchor = rows.last?.first, abs(piece.y - anchor.y) <= rowTolerance {
            rows[rows.count - 1].append(piece)
        } else {
            rows.append([piece])
        }
    }

    for row in rows {
        let sorted = row.sorted { $0.x < $1.x }
        let anchor = Int((Double(sorted.map(\.y).reduce(0, +)) / Double(sorted.count)).rounded())
        let cells = sorted.map { "x=\($0.x) \($0.text)" }.joined(separator: " | ")
        print("行 y≈\(anchor): \(cells)")
    }
}
