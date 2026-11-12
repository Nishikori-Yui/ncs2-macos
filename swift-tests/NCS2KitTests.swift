import XCTest
import NCS2Kit

final class NCS2KitTests: XCTestCase {
    func testPublicMultiTensorDescriptorCountsElements() throws {
        let descriptor = NCS2MultiTensorDescriptor(index: 0, dtype: "f32", shape: [2, 4])
        XCTAssertEqual(try descriptor.elementCount(), 8)
    }

    func testSchemaOneDescriptorCountsElements() throws {
        let descriptor = NCS2TensorDescriptor(dtype: "f32", shape: [1, 3, 16, 16])
        XCTAssertEqual(try descriptor.elementCount(), 768)
    }

    func testClientInitializersRejectInvalidTimeoutBeforeLaunching() {
        let unused = URL(fileURLWithPath: "/unused")
        XCTAssertThrowsError(try NCS2WorkerClient(worker: unused, plugins: unused, model: unused, timeout: 301))
        XCTAssertThrowsError(try NCS2MultiWorkerClient(
            worker: unused, plugins: unused, model: unused, startupTimeout: 301
        ))
    }

    func testPublicMultiTensorDescriptorRejectsUnsupportedShapes() {
        let invalid = [
            NCS2MultiTensorDescriptor(index: 0, dtype: "f16", shape: [4]),
            NCS2MultiTensorDescriptor(index: 0, dtype: "f32", shape: []),
            NCS2MultiTensorDescriptor(index: 0, dtype: "f32", shape: Array(repeating: 1, count: 9)),
            NCS2MultiTensorDescriptor(index: 0, dtype: "f32", shape: [16_385]),
            NCS2MultiTensorDescriptor(index: 0, dtype: "f32", shape: [0, 4]),
        ]

        for descriptor in invalid {
            XCTAssertThrowsError(try descriptor.elementCount())
        }
    }
}
