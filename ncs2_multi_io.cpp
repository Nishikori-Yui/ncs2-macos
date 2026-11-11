// Static multi-port fixtures with independent scalar references.
#include <openvino/openvino.hpp>
#include <openvino/core/graph_util.hpp>
#include <openvino/opsets/opset1.hpp>
#include <algorithm>
#include <cmath>
#include <filesystem>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

struct Fixture {
    std::shared_ptr<ov::Model> graph;
    std::string stem;
    std::vector<ov::Shape> input_shapes;
    std::vector<ov::Shape> output_shapes;
};

Fixture make_fixture(const std::string& id) {
    namespace op = ov::opset1;
    if (id == "add-sub") {
        const ov::Shape shape{1, 4};
        auto a = std::make_shared<op::Parameter>(ov::element::f32, shape);
        auto b = std::make_shared<op::Parameter>(ov::element::f32, shape);
        a->set_friendly_name("a"); b->set_friendly_name("b");
        auto sum = std::make_shared<op::Add>(a, b);
        auto difference = std::make_shared<op::Subtract>(a, b);
        return {std::make_shared<ov::Model>(ov::OutputVector{sum, difference},
                                           ov::ParameterVector{a, b}),
                "multi_add_sub", {shape, shape}, {shape, shape}};
    }
    if (id == "concat-unequal") {
        auto a = std::make_shared<op::Parameter>(ov::element::f32, ov::Shape{1, 3});
        auto b = std::make_shared<op::Parameter>(ov::element::f32, ov::Shape{1, 5});
        a->set_friendly_name("a"); b->set_friendly_name("b");
        auto joined = std::make_shared<op::Concat>(ov::OutputVector{a, b}, 1);
        return {std::make_shared<ov::Model>(ov::OutputVector{joined}, ov::ParameterVector{a, b}),
                "multi_concat_unequal", {{1, 3}, {1, 5}}, {{1, 8}}};
    }
    if (id == "split-halves") {
        auto a = std::make_shared<op::Parameter>(ov::element::f32, ov::Shape{1, 8});
        a->set_friendly_name("a");
        auto axis = op::Constant::create(ov::element::i64, ov::Shape{}, {1});
        auto split = std::make_shared<op::Split>(a, axis, 2);
        return {std::make_shared<ov::Model>(ov::OutputVector{split->output(0), split->output(1)},
                                           ov::ParameterVector{a}),
                "multi_split_halves", {{1, 8}}, {{1, 4}, {1, 4}}};
    }
    throw std::runtime_error("Unknown case: " + id);
}

std::vector<std::vector<float>> input_values(const std::string& id, int iteration) {
    if (id == "add-sub") {
        std::vector<std::vector<float>> inputs(2, std::vector<float>(4));
        for (int i = 0; i < 4; ++i) {
            inputs[0][i] = float(iteration + i) / 4;
            inputs[1][i] = float(i - 2) / 2;
        }
        return inputs;
    }
    std::vector<float> first(id == "concat-unequal" ? 3 : 8);
    for (size_t i = 0; i < first.size(); ++i)
        first[i] = float(iteration + int(i) - 7) / 8;
    if (id == "split-halves") return {first};
    std::vector<float> second(5);
    for (int i = 0; i < 5; ++i) second[i] = float(3 * i - iteration + 1) / 16;
    return {first, second};
}

std::vector<std::vector<float>> reference(const std::string& id,
                                         const std::vector<std::vector<float>>& inputs) {
    if (id == "concat-unequal") {
        auto joined = inputs[0];
        joined.insert(joined.end(), inputs[1].begin(), inputs[1].end());
        return {joined};
    }
    if (id == "split-halves")
        return {std::vector<float>(inputs[0].begin(), inputs[0].begin() + 4),
                std::vector<float>(inputs[0].begin() + 4, inputs[0].end())};
    std::vector<std::vector<float>> result(2, std::vector<float>(4));
    for (size_t i = 0; i < 4; ++i) {
        result[0][i] = inputs[0][i] + inputs[1][i];
        result[1][i] = inputs[0][i] - inputs[1][i];
    }
    return result;
}

int main(int argc, char** argv) {
    try {
        if (argc < 2) throw std::runtime_error("Expected plugins.xml path");
        std::string id = "add-sub";
        std::string output_directory;
        bool case_seen = false, export_seen = false;
        for (int i = 2; i < argc; i += 2) {
            if (i + 1 == argc) throw std::runtime_error("Expected a value after option");
            const std::string option(argv[i]);
            if (option == "--case" && !case_seen) { id = argv[i + 1]; case_seen = true; }
            else if (option == "--export" && !export_seen) {
                output_directory = argv[i + 1]; export_seen = true;
            } else throw std::runtime_error("Unknown or duplicate option: " + option);
        }
        const auto fixture = make_fixture(id);
        if (export_seen) {
            const std::filesystem::path directory(output_directory);
            if (!std::filesystem::create_directory(directory))
                throw std::runtime_error("Export directory must not already exist");
            const auto xml = (directory / (fixture.stem + ".xml")).string();
            const auto bin = (directory / (fixture.stem + ".bin")).string();
            ov::serialize(fixture.graph, xml, bin, ov::pass::Serialize::Version::IR_V10);
            ov::Core reader;
            const auto reloaded = reader.read_model(xml);
            if (reloaded->inputs().size() != fixture.input_shapes.size() ||
                reloaded->outputs().size() != fixture.output_shapes.size())
                throw std::runtime_error("Reloaded port count mismatch");
            for (size_t i = 0; i < fixture.input_shapes.size(); ++i)
                if (reloaded->input(i).get_shape() != fixture.input_shapes[i] ||
                    reloaded->input(i).get_element_type() != ov::element::f32)
                    throw std::runtime_error("Reloaded input metadata mismatch");
            for (size_t i = 0; i < fixture.output_shapes.size(); ++i)
                if (reloaded->output(i).get_shape() != fixture.output_shapes[i] ||
                    reloaded->output(i).get_element_type() != ov::element::f32)
                    throw std::runtime_error("Reloaded output metadata mismatch");
            std::cout << "MULTI_IO_EXPORT=PASS; case=" << id << "; xml=" << xml
                      << "; bin=" << bin << "; reloaded=true\n";
            return 0;
        }
        ov::Core core(argv[1]);
        core.set_property("MYRIAD", {{"LOG_LEVEL", "LOG_NONE"}});
        if (core.get_property("MYRIAD", ov::available_devices).size() != 1)
            throw std::runtime_error("Expected one MYRIAD device");
        auto compiled = core.compile_model(fixture.graph, "MYRIAD");
        auto request = compiled.create_infer_request();
        size_t checked = 0;
        for (int iteration = 0; iteration < 23; ++iteration) {
            const auto inputs = input_values(id, iteration);
            const auto expected = reference(id, inputs);
            for (size_t i = 0; i < inputs.size(); ++i) {
                auto tensor = request.get_input_tensor(i);
                if (tensor.get_shape() != fixture.input_shapes[i] ||
                    tensor.get_element_type() != ov::element::f32)
                    throw std::runtime_error("Unexpected input metadata");
                std::copy(inputs[i].begin(), inputs[i].end(), tensor.data<float>());
            }
            request.infer();
            for (size_t i = 0; i < expected.size(); ++i) {
                const auto tensor = request.get_output_tensor(i);
                if (tensor.get_shape() != fixture.output_shapes[i] ||
                    tensor.get_element_type() != ov::element::f32)
                    throw std::runtime_error("Unexpected output metadata");
                const auto* actual = tensor.data<const float>();
                for (size_t j = 0; j < expected[i].size(); ++j) {
                    if (!std::isfinite(actual[j]) || std::abs(actual[j] - expected[i][j]) > 0.002f)
                        throw std::runtime_error("Output mismatch at iteration " + std::to_string(iteration));
                    ++checked;
                }
            }
        }
        std::cout << "MULTI_IO_RESULT=PASS; device=MYRIAD; case=" << id
                  << "; inputs=" << fixture.input_shapes.size()
                  << "; outputs=" << fixture.output_shapes.size()
                  << "; iterations=23; checked_elements=" << checked << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "MULTI_IO_RESULT=FAIL; " << error.what() << '\n';
        return 1;
    }
}
