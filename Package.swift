// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "NCS2Kit",
    platforms: [.macOS(.v13)],
    products: [
        .library(name: "NCS2Kit", targets: ["NCS2Kit"]),
    ],
    targets: [
        .target(
            name: "NCS2Kit",
            path: "examples",
            swiftSettings: [
                .define("NCS2_CLIENT_LIBRARY"),
                .define("NCS2_MULTI_CLIENT_LIBRARY"),
            ]
        ),
        .testTarget(
            name: "NCS2KitTests",
            dependencies: ["NCS2Kit"],
            path: "swift-tests"
        ),
    ]
)
