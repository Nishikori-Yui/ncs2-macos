// Two inputs and two distinct outputs, verified on MYRIAD.
#include <openvino/openvino.hpp>
#include <openvino/core/graph_util.hpp>
#include <openvino/opsets/opset1.hpp>
#include <cmath>
#include <filesystem>
#include <iostream>
#include <stdexcept>

int main(int argc, char** argv) {
    try {
        if (argc != 2 && !(argc == 4 && std::string(argv[2]) == "--export"))
            throw std::runtime_error("Expected plugins.xml path, optionally followed by --export DIR");
        namespace op = ov::opset1;
        const ov::Shape shape{1, 4};
        auto a = std::make_shared<op::Parameter>(ov::element::f32, shape);
        auto b = std::make_shared<op::Parameter>(ov::element::f32, shape);
        a->set_friendly_name("a"); b->set_friendly_name("b");
        auto sum = std::make_shared<op::Add>(a, b);
        auto difference = std::make_shared<op::Subtract>(a, b);
        auto graph = std::make_shared<ov::Model>(ov::OutputVector{sum, difference},
                                                 ov::ParameterVector{a, b});
        if (argc == 4) {
            const std::filesystem::path directory(argv[3]);
            if (!std::filesystem::create_directory(directory))
                throw std::runtime_error("Export directory must not already exist");
            const auto xml = (directory / "multi_add_sub.xml").string();
            const auto bin = (directory / "multi_add_sub.bin").string();
            ov::serialize(graph, xml, bin, ov::pass::Serialize::Version::IR_V10);
            std::cout << "MULTI_IO_EXPORT=PASS; xml=" << xml << "; bin=" << bin << '\n';
            return 0;
        }
        ov::Core core(argv[1]);
        core.set_property("MYRIAD", {{"LOG_LEVEL", "LOG_NONE"}});
        if (core.get_property("MYRIAD", ov::available_devices).size() != 1)
            throw std::runtime_error("Expected one MYRIAD device");
        auto compiled = core.compile_model(graph, "MYRIAD");
        auto request = compiled.create_infer_request();
        for (int iteration = 0; iteration < 23; ++iteration) {
            auto* av = request.get_input_tensor(0).data<float>();
            auto* bv = request.get_input_tensor(1).data<float>();
            for (int i = 0; i < 4; ++i) {
                av[i] = float(iteration + i) / 4;
                bv[i] = float(i - 2) / 2;
            }
            request.infer();
            const auto out0 = request.get_output_tensor(0);
            const auto out1 = request.get_output_tensor(1);
            if (out0.get_shape() != shape || out1.get_shape() != shape ||
                out0.get_element_type() != ov::element::f32 ||
                out1.get_element_type() != ov::element::f32)
                throw std::runtime_error("Unexpected output metadata");
            for (int i = 0; i < 4; ++i) {
                const float expected_sum = float(iteration + i) / 4 + float(i - 2) / 2;
                const float expected_diff = float(iteration + i) / 4 - float(i - 2) / 2;
                const float actual_sum = out0.data<const float>()[i];
                const float actual_diff = out1.data<const float>()[i];
                if (!std::isfinite(actual_sum) || !std::isfinite(actual_diff) ||
                    std::abs(actual_sum - expected_sum) > 0.002f ||
                    std::abs(actual_diff - expected_diff) > 0.002f)
                    throw std::runtime_error("Output mismatch at iteration " + std::to_string(iteration));
            }
        }
        std::cout << "MULTI_IO_RESULT=PASS; device=MYRIAD; inputs=2; outputs=2; "
                     "iterations=23; checked_elements=184\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "MULTI_IO_RESULT=FAIL; " << error.what() << '\n';
        return 1;
    }
}
