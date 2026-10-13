import Foundation
import Vision
import ImageIO
import CoreGraphics

func recognize(_ path:String) throws -> [String:Any] {
    let url=URL(fileURLWithPath:path)
    let handler=VNImageRequestHandler(url:url,options:[:])
    let request=VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = CommandLine.arguments.contains("--numeric") ? ["en-US"] : ["zh-Hans","en-US"]
    request.usesLanguageCorrection = false
    request.minimumTextHeight = 0.001
    try handler.perform([request])
    let observations=(request.results ?? []).compactMap { o -> [String:Any]? in
        guard let t=o.topCandidates(1).first else { return nil }
        let b=o.boundingBox
        var chars:[[String:Any]]=[]
        for start in CommandLine.arguments.contains("--simple") ? [] : Array(t.string.indices) {
            let end=t.string.index(after:start)
            if let cb=try? t.boundingBox(for:start..<end) {
                let c=cb.boundingBox
                chars.append(["text":String(t.string[start..<end]),"box":[c.minX,c.minY,c.width,c.height]])
            }
        }
        return ["text":t.string,"confidence":t.confidence,"box":[b.minX,b.minY,b.width,b.height],"chars":chars]
    }
    return ["path":path,"observations":observations]
}
for line in sequence(first:readLine(),next:{_ in readLine()}) {
    guard let line=line else {continue}
    do {
        let result=try autoreleasepool {try recognize(line)}
        let data=try JSONSerialization.data(withJSONObject:result,options:[.sortedKeys])
        print(String(data:data,encoding:.utf8)!)
    } catch {
        let data=try! JSONSerialization.data(withJSONObject:["path":line,"error":String(describing:error)])
        print(String(data:data,encoding:.utf8)!)
    }
    fflush(stdout)
}
