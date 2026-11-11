// Persistent schema-2 worker for static f32 multi-input/multi-output IR.
#include "ncs2_json.hpp"
#include <openvino/openvino.hpp>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <limits>
#include <iomanip>
#include <iostream>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;
using J = ncs2json::Value;
constexpr size_t frame_limit = 1024 * 1024;
constexpr size_t tensor_limit = 16384;

struct TensorSpec {
    size_t index;
    ov::Shape shape;
};

struct Message {
    std::string id;
    std::string operation;
    std::vector<std::vector<float>> inputs;
};

size_t count(const ov::Shape& shape) {
    if (shape.empty() || shape.size() > 8)
        throw std::runtime_error("Tensor rank must be 1..8.");
    size_t result = 1;
    for (const auto dimension : shape) {
        if (dimension == 0 || result > tensor_limit / dimension)
            throw std::runtime_error("Tensor exceeds 16384 elements.");
        result *= dimension;
    }
    return result;
}

std::string json_quote(const std::string& text) {
    std::ostringstream out;
    out << '"';
    for (const unsigned char c : text) {
        switch (c) {
            case '"': out << "\\\""; break;
            case '\\': out << "\\\\"; break;
            case '\b': out << "\\b"; break;
            case '\f': out << "\\f"; break;
            case '\n': out << "\\n"; break;
            case '\r': out << "\\r"; break;
            case '\t': out << "\\t"; break;
            default:
                if (c < 0x20) {
                    out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                        << static_cast<int>(c) << std::dec << std::setfill(' ');
                } else out << c;
        }
    }
    return out << '"', out.str();
}

std::string shape_json(const ov::Shape& shape) {
    std::ostringstream out;
    out << '[';
    for (size_t i = 0; i < shape.size(); ++i) {
        if (i) out << ',';
        out << shape[i];
    }
    return out << ']', out.str();
}

size_t integer(const J& value, const std::string& field) {
    if (value.kind != J::Number || !std::isfinite(value.number) ||
        value.number < 0 || value.number > tensor_limit || std::floor(value.number) != value.number)
        throw std::runtime_error(field + " must be a nonnegative integer.");
    return static_cast<size_t>(value.number);
}

ov::Shape shape_from_json(const J& value, const std::string& field) {
    if (value.kind != J::Array || value.array.empty() || value.array.size() > 8)
        throw std::runtime_error(field + " must have rank 1..8.");
    ov::Shape shape;
    for (const auto& dimension : value.array) {
        const auto item = integer(dimension, field + " dimension");
        if (item == 0) throw std::runtime_error(field + " dimensions must be positive.");
        shape.push_back(item);
    }
    count(shape);
    return shape;
}

Message decode(const std::string& line, const std::vector<TensorSpec>& specs) {
    const auto root = ncs2json::Parser(line).parse();
    if (root.kind != J::Object) throw std::runtime_error("Request must be a JSON object.");
    if (integer(root.at("schema_version"), "schema_version") != 2)
        throw std::runtime_error("Unsupported schema_version.");
    Message message;
    message.id = root.at("request_id").as_string();
    if (message.id.empty() || message.id.size() > 128)
        throw std::runtime_error("request_id must contain 1..128 UTF-8 bytes.");
    message.operation = root.at("operation").as_string();
    const std::set<std::string> allowed = message.operation == "infer"
        ? std::set<std::string>{"schema_version", "request_id", "operation", "inputs"}
        : std::set<std::string>{"schema_version", "request_id", "operation"};
    for (const auto& field : root.object)
        if (!allowed.count(field.first)) throw std::runtime_error("Unknown request field: " + field.first);
    if (message.operation != "infer" && message.operation != "health" &&
        message.operation != "shutdown")
        throw std::runtime_error("Unknown operation.");
    if (message.operation != "infer") return message;

    const auto& tensors = root.at("inputs");
    if (tensors.kind != J::Array || tensors.array.size() != specs.size())
        throw std::runtime_error("inputs must contain exactly the ready tensor count.");
    message.inputs.resize(specs.size());
    for (const auto& tensor : tensors.array) {
        if (tensor.kind != J::Object ||
            tensor.object.size() != 4 ||
            tensor.at("dtype").as_string() != "f32")
            throw std::runtime_error("Each input needs dtype, index, shape, and data.");
        const auto index = integer(tensor.at("index"), "input index");
        if (index >= specs.size())
            throw std::runtime_error("Input index is out of range.");
        const auto shape = shape_from_json(tensor.at("shape"), "input shape");
        if (shape != specs[index].shape) throw std::runtime_error("Input shape mismatch.");
        const auto& data = tensor.at("data");
        if (data.kind != J::Array || data.array.size() != count(shape))
            throw std::runtime_error("Input element count mismatch.");
        if (!message.inputs[index].empty()) throw std::runtime_error("Duplicate input index.");
        message.inputs[index].reserve(data.array.size());
        for (const auto& value : data.array) {
            if (value.kind != J::Number ||
                std::abs(value.number) > std::numeric_limits<float>::max() ||
                !std::isfinite(static_cast<float>(value.number)))
                throw std::runtime_error("Input must contain finite f32 numbers.");
            message.inputs[index].push_back(static_cast<float>(value.number));
        }
    }
    for (const auto& input : message.inputs)
        if (input.empty()) throw std::runtime_error("Missing input index.");
    return message;
}

bool read_frame(std::string& line, bool& too_long) {
    line.clear();
    too_long = false;
    char character;
    bool any = false;
    while (std::cin.get(character)) {
        any = true;
        if (character == '\n') return true;
        if (line.size() < frame_limit) line += character;
        else too_long = true;
    }
    return any;
}

void error_response(const std::string& id, const std::string& text, bool fatal = false) {
    std::cout << "{\"schema_version\":2,\"type\":\"response\",\"request_id\":"
              << json_quote(id) << ",\"status\":\"error\",\"fatal\":"
              << (fatal ? "true" : "false") << ",\"error\":"
              << json_quote(text) << "}" << std::endl;
}

void append_tensor_description(std::ostringstream& out, size_t index,
                               const ov::Shape& shape) {
    if (index) out << ',';
    out << "{\"index\":" << index << ",\"dtype\":\"f32\",\"shape\":"
        << shape_json(shape) << '}';
}

int main(int argc, char** argv) {
    try {
        const bool validate_only = argc == 2 && std::string(argv[1]) == "--validate-only";
        if (!validate_only && (argc != 4 || std::string(argv[2]) != "--model"))
            throw std::runtime_error("Usage: ncs2_multi_worker PLUGINS --model XML | --validate-only");
        const std::string case_id = validate_only ? "validation" : fs::path(argv[3]).stem().string();
        std::vector<TensorSpec> inputs;
        std::vector<TensorSpec> outputs;
        ov::Core core;
        ov::CompiledModel compiled;
        ov::InferRequest request;
        if (validate_only) {
            inputs = {{0, {1,4}}, {1, {1,4}}};
            outputs = {{0, {1,4}}, {1, {1,4}}};
        } else {
        core = ov::Core(argv[1]);
        core.set_property("MYRIAD", {{"LOG_LEVEL", "LOG_NONE"}});
        const auto graph = core.read_model(argv[3]);
        if (graph->inputs().empty() || graph->outputs().empty() ||
            graph->inputs().size() > 32 || graph->outputs().size() > 32)
            throw std::runtime_error("IR requires 1..32 inputs and outputs.");
        size_t input_elements = 0, output_elements = 0;
        for (size_t i = 0; i < graph->inputs().size(); ++i) {
            const auto port = graph->input(i);
            if (port.get_element_type() != ov::element::f32 || port.get_partial_shape().is_dynamic())
                throw std::runtime_error("All inputs must be static f32 tensors.");
            const auto shape = port.get_shape();
            input_elements += count(shape);
            inputs.push_back({i, shape});
        }
        for (size_t i = 0; i < graph->outputs().size(); ++i) {
            const auto port = graph->output(i);
            if (port.get_element_type() != ov::element::f32 || port.get_partial_shape().is_dynamic())
                throw std::runtime_error("All outputs must be static f32 tensors.");
            const auto shape = port.get_shape();
            output_elements += count(shape);
            outputs.push_back({i, shape});
        }
        if (input_elements > tensor_limit || output_elements > tensor_limit)
            throw std::runtime_error("Aggregate input/output limit is 16384 elements each.");
        const auto devices = core.get_property("MYRIAD", ov::available_devices);
        if (devices.size() != 1)
            throw std::runtime_error("Expected exactly one accessible MYRIAD device.");
        compiled = core.compile_model(graph, "MYRIAD");
        request = compiled.create_infer_request();
        }
        std::ostringstream ready;
        ready << "{\"schema_version\":2,\"type\":\"ready\",\"case\":"
              << json_quote(case_id)
              << ",\"device\":" << json_quote(validate_only ? "none" : "MYRIAD")
              << ",\"validation_only\":" << (validate_only ? "true" : "false") << ",\"inputs\":[";
        for (const auto& item : inputs) append_tensor_description(ready, item.index, item.shape);
        ready << "],\"outputs\":[";
        for (const auto& item : outputs) append_tensor_description(ready, item.index, item.shape);
        ready << "]}";
        std::cout << ready.str() << std::endl;

        size_t completed = 0;
        std::string line;
        bool too_long = false;
        while (read_frame(line, too_long)) {
            std::string id;
            bool fatal = false;
            try {
                if (too_long) throw std::runtime_error("JSON frame exceeds 1 MiB.");
                const auto root = ncs2json::Parser(line).parse();
                if (root.kind == J::Object && root.object.count("request_id") &&
                    root.at("request_id").kind == J::String &&
                    root.at("request_id").string.size() <= 128)
                    id = root.at("request_id").string;
                const auto message = decode(line, inputs);
                id = message.id;
                if (message.operation == "health" || message.operation == "shutdown" || validate_only) {
                    std::cout << "{\"schema_version\":2,\"type\":\"response\",\"request_id\":"
                              << json_quote(id) << ",\"status\":\"ok\",\"case\":"
                              << json_quote(case_id)
                              << ",\"completed\":" << completed
                              << ",\"hardware_executed\":false}" << std::endl;
                    if (message.operation == "shutdown") break;
                    continue;
                }
                fatal = true;
                for (size_t i = 0; i < inputs.size(); ++i) {
                    auto tensor = request.get_input_tensor(i);
                    std::copy(message.inputs[i].begin(), message.inputs[i].end(),
                              tensor.data<float>());
                }
                request.start_async();
                if (!request.wait_for(std::chrono::seconds(10)))
                    throw std::runtime_error("Inference timeout; parent must reap worker.");
                std::ostringstream response;
                response << std::setprecision(9);
                response << "{\"schema_version\":2,\"type\":\"response\",\"request_id\":"
                          << json_quote(id) << ",\"status\":\"ok\",\"case\":"
                          << json_quote(case_id)
                          << ",\"hardware_executed\":true,\"outputs\":[";
                for (size_t i = 0; i < outputs.size(); ++i) {
                    if (i) response << ',';
                    const auto tensor = request.get_output_tensor(i);
                    if (tensor.get_element_type() != ov::element::f32 || tensor.get_shape() != outputs[i].shape)
                        throw std::runtime_error("Output shape/type mismatch.");
                    const auto* data = tensor.data<const float>();
                    response << "{\"index\":" << i << ",\"dtype\":\"f32\",\"shape\":"
                              << shape_json(outputs[i].shape) << ",\"data\":[";
                    for (size_t j = 0; j < count(outputs[i].shape); ++j) {
                        if (j) response << ',';
                        if (!std::isfinite(data[j]))
                            throw std::runtime_error("Non-finite device output.");
                        response << data[j];
                    }
                    response << "]}";
                }
                response << "]}";
                if (response.str().size() > frame_limit) throw std::runtime_error("Response exceeds 1 MiB.");
                ++completed;
                std::cout << response.str() << std::endl;
            } catch (const std::exception& error) {
                error_response(id, error.what(), fatal);
                if (fatal) return 1;
            }
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "multi-worker: " << error.what() << std::endl;
        return 1;
    }
}
