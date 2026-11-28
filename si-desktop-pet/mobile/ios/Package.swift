// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "SiCompanionCore",
    platforms: [
        .iOS(.v16),
        .macOS(.v13),
    ],
    products: [
        .library(
            name: "SiCompanionCore",
            targets: ["SiCompanionCore"]
        ),
    ],
    targets: [
        .target(
            name: "SiCompanionCore",
            path: "Sources/SiCompanionCore"
        ),
        .testTarget(
            name: "SiCompanionCoreTests",
            dependencies: ["SiCompanionCore"],
            path: "Tests/SiCompanionCoreTests"
        ),
    ]
)
