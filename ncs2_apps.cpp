// Camera-free synthetic demos. No trained classifier or production anomaly model.
#include <openvino/openvino.hpp>
#include <openvino/opsets/opset8.hpp>
#include <openvino/pass/serialize.hpp>
#include <CommonCrypto/CommonDigest.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#if !defined(__APPLE__) || !defined(__aarch64__)
#error "ncs2_apps requires native macOS arm64."
#endif

namespace fs = std::filesystem;
namespace op = ov::opset8;
using Clock = std::chrono::steady_clock;

std::string quote(const std::string& s) {
    std::ostringstream o;
    o << '"';
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') o << '\\' << c;
        else if (c < 32) o << "\\u" << std::hex << std::setw(4) << std::setfill('0') << int(c) << std::dec;
        else o << c;
    }
    return o.str() + '"';
}

template <class T> void array(std::ostream& o, const std::vector<T>& values) {
    o << '[';
    for (size_t i = 0; i < values.size(); ++i) {
        if (i) o << ',';
        // Raw tensor files retain the exact NaN/Inf bit patterns on failure.
        if (std::isfinite(double(values[i]))) o << values[i]; else o << "null";
    }
    o << ']';
}

void write_bytes(const fs::path& p, const void* data, size_t size) {
    std::ofstream out(p, std::ios::binary);
    if (!out) throw std::runtime_error("Cannot create artifact: " + p.string());
    out.write(static_cast<const char*>(data), std::streamsize(size));
    out.close();
    if (!out) throw std::runtime_error("Cannot finish artifact: " + p.string());
}

void write_floats(const fs::path& p, const std::vector<float>& values) {
    write_bytes(p, values.data(), values.size() * sizeof(float));
}

std::string sha256(const fs::path& p) {
    std::ifstream input(p, std::ios::binary);
    if (!input) throw std::runtime_error("Cannot hash artifact: " + p.string());
    CC_SHA256_CTX context;
    CC_SHA256_Init(&context);
    char block[16384];
    while (input.read(block, sizeof(block)) || input.gcount())
        CC_SHA256_Update(&context, block, CC_LONG(input.gcount()));
    if (!input.eof()) throw std::runtime_error("Artifact hash read failed.");
    unsigned char digest[CC_SHA256_DIGEST_LENGTH];
    CC_SHA256_Final(digest, &context);
    std::ostringstream out;
    for (auto byte : digest) out << std::hex << std::setw(2) << std::setfill('0') << unsigned(byte);
    return out.str();
}

double number(const std::string& text) {
    size_t used = 0;
    double value = std::stod(text, &used);
    if (used != text.size() || !std::isfinite(value)) throw std::runtime_error("Invalid finite number: " + text);
    return value;
}

std::vector<float> read_values(const fs::path& p, size_t count) {
    if (fs::file_size(p) > 1024 * 1024) throw std::runtime_error("Input file exceeds 1 MiB limit.");
    std::ifstream input(p);
    if (!input) throw std::runtime_error("Cannot open input values.");
    std::vector<float> values;
    std::string token;
    while (input >> token) {
        const double value = number(token);
        if (std::abs(value) > 65504) throw std::runtime_error("Input outside finite FP16 range.");
        values.push_back(float(value));
        if (values.size() > count) throw std::runtime_error("Input element count mismatch (too many).");
    }
    if (!input.eof()) throw std::runtime_error("Input read failed.");
    if (values.size() != count) throw std::runtime_error("Input element count mismatch.");
    return values;
}

ov::Shape input_shape(const std::string& mode) {
    return mode != "timeseries" ? ov::Shape{1, 1, 32, 32} : ov::Shape{1, 1, 1, 64};
}
ov::Shape output_shape(const std::string& mode) {
    if (mode == "audio-legacy") return ov::Shape{1, 4, 30, 30};
    return mode == "audio" ? ov::Shape{1, 4, 15, 15} : ov::Shape{1, 1, 1, 60};
}

std::vector<float> weights(const std::string& mode) {
    if (mode == "timeseries") return {-0.25f, -0.25f, -0.25f, -0.25f, 1.0f};
    std::vector<float> values(36);
    for (size_t i = 0; i < values.size(); ++i) values[i] = (int(i * 11 % 13) - 6) / 64.0f;
    return values;
}

std::shared_ptr<ov::Model> make_model(const std::string& mode) {
    const bool audio = mode != "timeseries";
    auto input = std::make_shared<op::Parameter>(ov::element::f32, input_shape(mode));
    input->set_friendly_name("input");
    input->output(0).get_tensor().set_names({"input"});
    auto w = op::Constant::create(ov::element::f32,
        audio ? ov::Shape{4, 1, 3, 3} : ov::Shape{1, 1, 1, 5}, weights(mode));
    std::shared_ptr<ov::Node> output = std::make_shared<op::Convolution>(
        input, w, ov::Strides{1, 1}, ov::CoordinateDiff{0, 0},
        ov::CoordinateDiff{0, 0}, ov::Strides{1, 1});
    if (mode == "audio") {
        output = std::make_shared<op::Relu>(output);
        output = std::make_shared<op::AvgPool>(output, ov::Strides{2, 2},
            ov::Shape{0, 0}, ov::Shape{0, 0}, ov::Shape{2, 2}, true);
    } else if (!audio) {
        // |x[t] - mean(x[t-4:t])|, t=4..63. Not a trained neural detector.
        output = std::make_shared<op::Abs>(output);
    }
    output->set_friendly_name(audio ? "untrained_features" : "absolute_prediction_error");
    output->output(0).get_tensor().set_names({"output"});
    return std::make_shared<ov::Model>(ov::ResultVector{std::make_shared<op::Result>(output)},
        ov::ParameterVector{input}, "synthetic_" + mode);
}

// Scalar CPU oracle; no OpenVINO evaluation/CPU plugin or MYRIAD calls.
std::vector<float> reference(const std::string& mode, const std::vector<float>& x) {
    std::vector<float> out;
    if (mode == "audio-legacy") {
        // Reproduce the draft's bare convolution and float-product/double-sum
        // oracle exactly, solely to measure the previously discarded mismatch.
        const auto w = weights(mode);
        for (size_t c = 0; c < 4; ++c) for (size_t row = 0; row < 30; ++row)
            for (size_t col = 0; col < 30; ++col) {
                double sum = 0;
                for (size_t dy = 0; dy < 3; ++dy) for (size_t dx = 0; dx < 3; ++dx)
                    sum += float(x[(row + dy) * 32 + col + dx] * w[c * 9 + dy * 3 + dx]);
                out.push_back(float(sum));
            }
    } else if (mode == "timeseries") {
        for (size_t t = 4; t < x.size(); ++t) {
            double prediction = 0;
            for (size_t lag = 1; lag <= 4; ++lag) prediction += double(x[t - lag]) / 4;
            out.push_back(float(std::abs(double(x[t]) - prediction)));
        }
    } else {
        const auto w = weights(mode);
        for (size_t channel = 0; channel < 4; ++channel)
            for (size_t row = 0; row < 15; ++row)
                for (size_t col = 0; col < 15; ++col) {
                    double pooled = 0;
                    for (size_t dy = 0; dy < 2; ++dy)
                        for (size_t dx = 0; dx < 2; ++dx) {
                            double conv = 0;
                            for (size_t ky = 0; ky < 3; ++ky)
                                for (size_t kx = 0; kx < 3; ++kx)
                                    conv += double(x[(2 * row + dy + ky) * 32 + 2 * col + dx + kx]) *
                                        double(w[channel * 9 + ky * 3 + kx]);
                            pooled += std::max(0.0f, float(conv)) / 4.0;
                        }
                    out.push_back(float(pooled));
                }
    }
    return out;
}

struct Comparison {
    size_t checked = 0, mismatches = 0, nonfinite = 0;
    double max_abs_error = 0;
};

Comparison compare(const std::vector<float>& actual, const std::vector<float>& expected,
                   double atol, double rtol) {
    if (actual.size() != expected.size()) throw std::runtime_error("Comparison shape/count mismatch.");
    Comparison result;
    for (size_t i = 0; i < actual.size(); ++i) {
        ++result.checked;
        if (!std::isfinite(actual[i]) || !std::isfinite(expected[i])) {
            ++result.nonfinite; ++result.mismatches; continue;
        }
        const double error = std::abs(double(actual[i]) - expected[i]);
        result.max_abs_error = std::max(result.max_abs_error, error);
        if (error > atol + rtol * std::abs(double(expected[i]))) ++result.mismatches;
    }
    return result;
}

struct Report {
    std::string mode, stage = "arguments", status = "failed", error, device_id;
    bool cpu = false, hardware_executed = false, ir_roundtrip = false;
    int repeats = 3;
    double atol = 0.0005, rtol = 0.01, repeat_atol = 0.00001, repeat_rtol = 0.0001;
    fs::path path, artifacts;
    std::vector<float> input, expected;
    std::vector<std::vector<float>> outputs;
    std::vector<double> inference_ms;
    std::vector<Comparison> comparisons, repeat_comparisons;
    std::vector<ov::Shape> actual_shapes;

    void write(std::ostream& out) const {
        out << std::setprecision(17) << "{\"schema_version\":2,\"status\":" << quote(status)
            << ",\"stage\":" << quote(stage) << ",\"error\":" << quote(error)
            << ",\"mode\":" << quote(mode) << ",\"device\":" << quote(cpu ? "scalar_CPU_reference" : "MYRIAD")
            << ",\"device_id\":" << quote(device_id)
            << ",\"execution\":" << quote(cpu ? "reference_only" : "hardware")
            << ",\"hardware_executed\":" << (hardware_executed ? "true" : "false")
            << ",\"ir_roundtrip\":" << (ir_roundtrip ? "true" : "false")
            << ",\"trained\":false,\"input_shape\":";
        array(out, input_shape(mode)); out << ",\"output_shape\":"; array(out, output_shape(mode));
        out << ",\"repeats_requested\":" << repeats << ",\"repeats_completed\":" << outputs.size()
            << ",\"tolerance\":{\"atol\":" << atol << ",\"rtol\":" << rtol
            << ",\"repeat_atol\":" << repeat_atol << ",\"repeat_rtol\":" << repeat_rtol << '}'
            << ",\"input\":"; array(out, input);
        out << ",\"reference\":"; array(out, expected);
        out << ",\"outputs\":[";
        for (size_t i = 0; i < outputs.size(); ++i) { if (i) out << ','; array(out, outputs[i]); }
        out << "],\"actual_shapes\":[";
        for (size_t i = 0; i < actual_shapes.size(); ++i) { if (i) out << ','; array(out, actual_shapes[i]); }
        out << "],\"inference_ms\":"; array(out, inference_ms);
        const auto checks = [&](const char* name, const std::vector<Comparison>& values) {
            out << ',' << quote(name) << ":[";
            for (size_t i = 0; i < values.size(); ++i) {
                if (i) out << ',';
                const auto& c = values[i];
                out << "{\"checked\":" << c.checked << ",\"mismatches\":" << c.mismatches
                    << ",\"nonfinite\":" << c.nonfinite << ",\"max_abs_error\":" << c.max_abs_error << '}';
            }
            out << ']';
        };
        checks("comparisons", comparisons); checks("repeat_comparisons", repeat_comparisons);
        out << ",\"artifacts\":[";
        bool first = true;
        if (!artifacts.empty() && fs::exists(artifacts)) {
            std::vector<fs::path> files;
            for (const auto& entry : fs::directory_iterator(artifacts)) if (entry.is_regular_file()) files.push_back(entry.path());
            std::sort(files.begin(), files.end());
            for (const auto& file : files) {
                if (!first) out << ','; first = false;
                out << "{\"path\":" << quote(artifacts.filename().string() + "/" + file.filename().string())
                    << ",\"bytes\":" << fs::file_size(file) << ",\"sha256\":" << quote(sha256(file)) << '}';
            }
        }
        out << "]}\n";
    }

    void checkpoint() const {
        std::ostringstream json; write(json);
        if (!path.empty()) {
            const fs::path temp = path.string() + ".partial";
            const auto text = json.str(); write_bytes(temp, text.data(), text.size());
            fs::rename(temp, path);
        }
    }
};

void tensor_check(const ov::Tensor& tensor, const ov::Shape& expected) {
    if (tensor.get_element_type() != ov::element::f32 || tensor.get_shape() != expected ||
        tensor.get_size() != ov::shape_size(expected)) throw std::runtime_error("Tensor shape/dtype/count mismatch.");
}

int main(int argc, char** argv) {
    Report report;
    try {
        if (argc < 5) throw std::runtime_error(
            "Usage: ncs2_apps PLUGINS audio|timeseries|audio-legacy INPUT_VALUES REPORT "
            "[--cpu-reference] [--repeats N] [--atol A] [--rtol R] [--repeat-atol A] [--repeat-rtol R]");
        // Reserve a new artifact namespace before writing; existing reports are never replaced.
        const fs::path path(argv[4]), artifacts(path.string() + ".artifacts");
        if (fs::exists(path) || fs::exists(path.string() + ".partial") || fs::exists(artifacts))
            throw std::runtime_error("Refusing to overwrite existing report/artifacts.");
        if (!path.parent_path().empty()) fs::create_directories(path.parent_path());
        if (!fs::create_directory(artifacts)) throw std::runtime_error("Cannot reserve artifact directory.");
        report.path = path; report.artifacts = artifacts;
        report.mode = argv[2];
        if (report.mode != "audio" && report.mode != "timeseries" && report.mode != "audio-legacy")
            throw std::runtime_error("Unknown app mode.");
        for (int i = 5; i < argc; ++i) {
            const std::string key = argv[i];
            if (key == "--cpu-reference") { report.cpu = true; continue; }
            if (i + 1 >= argc) throw std::runtime_error("Missing option value.");
            const double value = number(argv[++i]);
            if (key == "--repeats") {
                if (value < 2 || value > 20 || std::floor(value) != value) throw std::runtime_error("Repeats must be 2..20.");
                report.repeats = int(value);
            } else if (value < 0 || value > 1) throw std::runtime_error("Tolerances must be finite and in [0,1].");
            else if (key == "--atol") report.atol = value;
            else if (key == "--rtol") report.rtol = value;
            else if (key == "--repeat-atol") report.repeat_atol = value;
            else if (key == "--repeat-rtol") report.repeat_rtol = value;
            else throw std::runtime_error("Unknown option: " + key);
        }
        report.stage = "input"; report.checkpoint();
        fs::copy_file(argv[3], artifacts / "source.txt");
        report.input = read_values(argv[3], ov::shape_size(input_shape(report.mode)));
        write_floats(artifacts / "input.f32", report.input);
        report.expected = reference(report.mode, report.input);
        const auto sanity = compare(report.expected, report.expected, 0, 0);
        if (sanity.nonfinite) throw std::runtime_error("Nonfinite CPU reference.");
        write_floats(artifacts / "reference.f32", report.expected);
        report.stage = "serialize"; report.checkpoint();
        auto graph = make_model(report.mode);
        if (graph->input().get_shape() != input_shape(report.mode) || graph->output().get_shape() != output_shape(report.mode))
            throw std::runtime_error("Graph shape mismatch.");
        const auto xml = artifacts / "model.xml", bin = artifacts / "model.bin";
        ov::pass::Serialize serializer(xml.string(), bin.string(), ov::pass::Serialize::Version::IR_V11);
        serializer.run_on_model(graph);
        report.stage = "read_ir"; report.checkpoint();
        // Creating a Core and reading IR do not enumerate or open a USB device.
        ov::Core core(report.cpu ? "" : argv[1]);
        graph = core.read_model(xml.string(), bin.string());
        if (graph->input().get_shape() != input_shape(report.mode) || graph->output().get_shape() != output_shape(report.mode))
            throw std::runtime_error("IR round-trip shape mismatch.");
        report.ir_roundtrip = true;
        ov::CompiledModel compiled;
        ov::InferRequest request;
        if (!report.cpu) {
            report.stage = "device_enumeration"; report.checkpoint();
            const auto devices = core.get_property("MYRIAD", ov::available_devices);
            if (devices.size() != 1) throw std::runtime_error("Expected exactly one MYRIAD device.");
            report.device_id = devices.front();
            report.stage = "compile"; report.checkpoint();
            compiled = core.compile_model(graph, "MYRIAD");
            report.stage = "request"; report.checkpoint();
            request = compiled.create_infer_request();
        }
        bool passed = true;
        for (int pass = 0; pass < report.repeats; ++pass) {
            report.stage = "inference"; report.checkpoint();
            if (report.cpu) {
                report.outputs.push_back(reference(report.mode, report.input));
                report.actual_shapes.push_back(output_shape(report.mode));
            } else {
                auto tensor = request.get_input_tensor(); tensor_check(tensor, input_shape(report.mode));
                std::copy(report.input.begin(), report.input.end(), tensor.data<float>());
                const auto start = Clock::now(); request.infer(); report.hardware_executed = true;
                report.inference_ms.push_back(std::chrono::duration<double, std::milli>(Clock::now() - start).count());
                const auto output = request.get_output_tensor();
                // Preserve actual bytes BEFORE any shape, finite, or accuracy rejection.
                write_bytes(artifacts / ("output-" + std::to_string(pass) + ".raw"), output.data(), output.get_byte_size());
                report.actual_shapes.push_back(output.get_shape());
                tensor_check(output, output_shape(report.mode));
                const float* data = output.data<const float>();
                report.outputs.emplace_back(data, data + output.get_size());
            }
            write_floats(artifacts / ("output-" + std::to_string(pass) + ".f32"), report.outputs.back());
            report.comparisons.push_back(compare(report.outputs.back(), report.expected, report.atol, report.rtol));
            passed = passed && report.comparisons.back().mismatches == 0;
            if (pass) {
                report.repeat_comparisons.push_back(compare(report.outputs.back(), report.outputs.front(), report.repeat_atol, report.repeat_rtol));
                passed = passed && report.repeat_comparisons.back().mismatches == 0;
            }
            report.stage = "validation"; report.checkpoint();
        }
        if (!passed) throw std::runtime_error("Reference or repeated-output tolerance exceeded; all available values retained.");
        report.status = "passed"; report.stage = "complete"; report.checkpoint();
        std::cout << "APPS_NATIVE_RESULT=PASS; report=" << report.path << '\n';
        return 0;
    } catch (const std::exception& error) {
        report.status = "failed";
        report.error = error.what();
        try { report.checkpoint(); }
        catch (const std::exception& failure) { std::cerr << "Report write failed: " << failure.what() << '\n'; }
        std::cerr << "APPS_NATIVE_RESULT=FAIL; stage=" << report.stage << "; error=" << report.error << '\n';
        return 1;
    }
}
