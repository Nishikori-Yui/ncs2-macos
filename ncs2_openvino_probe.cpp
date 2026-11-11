#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
#include <vector>

#include <openvino/openvino.hpp>
#include <openvino/opsets/opset8.hpp>

int main(int argc, char** argv) {
    try {
        const std::string plugins = argc > 1
            ? argv[1]
            : "vendor/openvino-2022.3/bin/arm64/Release/plugins.xml";
        const std::string mode = argc > 2 ? argv[2] : "smoke";
        if (mode != "list" && mode != "smoke" && mode != "bench") {
            throw std::runtime_error("Mode must be list, smoke, or bench.");
        }
        auto count = [](const char* value, bool allow_zero) {
            const std::string text(value);
            if (text.empty() || text.find_first_not_of("0123456789") != std::string::npos)
                throw std::runtime_error("Counts must be decimal integers.");
            const auto n = std::stoul(text);
            if (n > 100000 || (!allow_zero && n == 0))
                throw std::runtime_error("Iteration count out of range (1..100000; warmup permits 0).");
            return static_cast<size_t>(n);
        };
        const size_t iterations = mode == "bench" ? (argc > 3 ? count(argv[3], false) : 100) : 1;
        const size_t warmup = mode == "bench" ? (argc > 4 ? count(argv[4], true) : 10) : 0;
        ov::Core core(plugins);
        if (const auto level = std::getenv("NCS2_LOG_LEVEL")) {
            core.set_property("MYRIAD", {{"LOG_LEVEL", std::string(level)}});
        }
        std::cout << "runtime=" << ov::get_openvino_version() << '\n';
        const auto devices = core.get_property("MYRIAD", ov::available_devices);
        std::cout << "device_count=" << devices.size() << '\n';
        for (const auto& device : devices) {
            std::cout << "MYRIAD=" << device << '\n';
        }

        if (devices.empty()) {
            std::cerr << "No MYRIAD device available; skipping inference.\n";
            return 2;
        }
        if (mode == "list") return 0;

        // A camera-free smoke test: compile and run a tiny elementwise graph.
        const auto input = std::make_shared<ov::opset8::Parameter>(
            ov::element::f32, ov::Shape{1, 4});
        input->set_friendly_name("input");
        const auto relu = std::make_shared<ov::opset8::Relu>(input);
        const auto result = std::make_shared<ov::opset8::Result>(relu);
        const auto model = std::make_shared<ov::Model>(
            ov::ResultVector{result}, ov::ParameterVector{input}, "ncs2_arm64_smoke");

        using Clock = std::chrono::steady_clock;
        const auto compile_start = Clock::now();
        auto compiled = core.compile_model(model, "MYRIAD");
        const auto compile_ms = std::chrono::duration<double, std::milli>(Clock::now() - compile_start).count();
        auto request = compiled.create_infer_request();
        std::vector<float> input_values{-1.0f, 0.5f, -2.0f, 3.0f};
        request.set_input_tensor(ov::Tensor(
            ov::element::f32, ov::Shape{1, 4}, input_values.data()));
        std::vector<double> timings;
        double max_error = 0;
        for (size_t i = 0; i < warmup + iterations; ++i) {
            input_values = i % 2 == 0 ? std::vector<float>{-1, 0.5f, -2, 3}
                                      : std::vector<float>{2, -0.5f, 1, -3};
            request.set_input_tensor(ov::Tensor(ov::element::f32, ov::Shape{1, 4}, input_values.data()));
            const auto begin = Clock::now();
            request.infer();
            const double ms = std::chrono::duration<double, std::milli>(Clock::now() - begin).count();
            const auto output = request.get_output_tensor();
            if (output.get_element_type() != ov::element::f32 || output.get_shape() != ov::Shape{1, 4})
                throw std::runtime_error("Unexpected output dtype or shape.");
            const auto* values = output.data<const float>();
            for (size_t j = 0; j < input_values.size(); ++j) {
                const auto error = std::abs(static_cast<double>(values[j]) - std::max(0.0f, input_values[j]));
                if (!std::isfinite(values[j]) || error > 0.001)
                    throw std::runtime_error("ReLU output validation failed.");
                max_error = std::max(max_error, error);
            }
            if (i == 0) std::cout << "inference_output=" << values[0] << ',' << values[1] << ','
                                << values[2] << ',' << values[3] << '\n';
            if (i >= warmup) timings.push_back(ms);
        }
        std::sort(timings.begin(), timings.end());
        const double mean = std::accumulate(timings.begin(), timings.end(), 0.0) / timings.size();
        auto percentile = [&](double p) { return timings[static_cast<size_t>(std::ceil(p * timings.size())) - 1]; };
        std::cout << std::setprecision(10)
                  << "RESULT_JSON={\"device\":\"MYRIAD\",\"model\":\"relu_1x4\",\"validated\":true,"
                  << "\"iterations\":" << iterations << ",\"warmup\":" << warmup
                  << ",\"compile_and_boot_ms\":" << compile_ms << ",\"mean_ms\":" << mean
                  << ",\"p50_ms\":" << percentile(0.5) << ",\"p95_ms\":" << percentile(0.95)
                  << ",\"min_ms\":" << timings.front() << ",\"max_ms\":" << timings.back()
                  << ",\"max_abs_error\":" << max_error << "}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
