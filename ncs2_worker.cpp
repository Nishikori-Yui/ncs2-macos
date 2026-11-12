// Persistent local JSON-lines worker. The compatibility runtime stays in this process.
#include "ncs2_json.hpp"
#include <openvino/openvino.hpp>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <memory>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>
namespace fs = std::filesystem;
using J=ncs2json::Value;
constexpr size_t frame_limit=1024*1024;

std::string json_quote(const std::string& text) {
    std::ostringstream out;out<<'"';
    for(const unsigned char c:text) {
        if(c=='"'||c=='\\')out<<'\\'<<c;
        else if(c<32)out<<"\\u"<<std::hex<<std::setw(4)<<std::setfill('0')<<int(c)<<std::dec;
        else out<<c;
    }
    return out<<'"',out.str();
}

std::string shape_json(const ov::Shape& shape) {
    std::ostringstream out;out<<'[';
    for(size_t i=0;i<shape.size();++i){if(i)out<<',';out<<shape[i];}
    return out<<']',out.str();
}

size_t count(const ov::Shape& shape) {
    if(shape.empty()||shape.size()>8)throw std::runtime_error("IR tensor rank must be 1..8.");
    size_t result=1;
    for(const auto dimension:shape){
        if(dimension==0||dimension>16384||result>16384/dimension)
            throw std::runtime_error("IR tensor limit is 16384 elements.");
        result*=dimension;
    }
    return result;
}

struct Message { std::string id,operation; std::vector<float> input; };
Message decode(const std::string& line,const ov::Shape& input_shape) {
    const auto root=ncs2json::Parser(line).parse();
    if(root.kind!=J::Object)throw std::runtime_error("Request must be a JSON object.");
    const auto& version=root.at("schema_version");
    if(version.kind!=J::Number||version.number!=1)throw std::runtime_error("Unsupported schema_version.");
    Message m;m.id=root.at("request_id").as_string();
    if(m.id.empty()||m.id.size()>128)throw std::runtime_error("request_id must contain 1..128 UTF-8 bytes.");
    m.operation=root.at("operation").as_string();
    const std::set<std::string> allowed=m.operation=="infer"
        ? std::set<std::string>{"schema_version","request_id","operation","input"}
        : std::set<std::string>{"schema_version","request_id","operation"};
    for(const auto& field:root.object)if(!allowed.count(field.first))throw std::runtime_error("Unknown request field: "+field.first);
    if(m.operation!="infer"&&m.operation!="health"&&m.operation!="shutdown")throw std::runtime_error("Unknown operation.");
    if(m.operation=="infer"){
        const auto& tensor=root.at("input");
        if(tensor.kind!=J::Object||tensor.object.size()!=3||tensor.at("dtype").as_string()!="f32")
            throw std::runtime_error("input must have dtype f32, shape, and data.");
        const auto& shape=tensor.at("shape");
        if(shape.kind!=J::Array||shape.array.size()!=input_shape.size())throw std::runtime_error("Input shape mismatch.");
        for(size_t i=0;i<input_shape.size();++i)
            if(shape.array[i].kind!=J::Number||shape.array[i].number!=input_shape[i])throw std::runtime_error("Input shape mismatch.");
        const auto& data=tensor.at("data");
        if(data.kind!=J::Array||data.array.size()!=count(input_shape))throw std::runtime_error("Input element count mismatch.");
        m.input.reserve(data.array.size());
        for(const auto& v:data.array){
            if(v.kind!=J::Number||!std::isfinite(static_cast<float>(v.number)))throw std::runtime_error("Input must contain finite f32 numbers.");
            m.input.push_back(static_cast<float>(v.number));
        }
    }
    return m;
}
bool read_frame(std::string& line,bool& too_long){
    line.clear();too_long=false;char c;bool any=false;
    while(std::cin.get(c)){
        any=true;if(c=='\n')return true;
        if(line.size()<frame_limit)line+=c;else too_long=true;
    }
    return any;
}
void error_response(const std::string& id,const std::string& text,bool fatal=false){
    std::cout<<"{\"schema_version\":1,\"type\":\"response\",\"request_id\":"<<json_quote(id)
             <<",\"status\":\"error\",\"fatal\":"<<(fatal?"true":"false")<<",\"error\":"<<json_quote(text)<<"}"<<std::endl;
}
int main(int argc,char** argv){
    try{
        const bool validate_only=argc==2&&std::string(argv[1])=="--validate-only";
        if(!validate_only&&(argc!=4||std::string(argv[2])!="--model"))
            throw std::runtime_error("Usage: ncs2_worker PLUGINS --model XML | --validate-only");
        const std::string case_id=validate_only?"validation":fs::path(argv[3]).stem().string();
        ov::Shape input_shape{1,4},output_shape{1,4};
        ov::Core core;ov::CompiledModel compiled;ov::InferRequest request;size_t completed=0;
        if(!validate_only){
            core=ov::Core(argv[1]);core.set_property("MYRIAD",{{"LOG_LEVEL","LOG_NONE"}});
            const auto graph=core.read_model(argv[3]);
            if(graph->inputs().size()!=1||graph->outputs().size()!=1 ||
               graph->input().get_element_type()!=ov::element::f32||graph->output().get_element_type()!=ov::element::f32 ||
               graph->input().get_partial_shape().is_dynamic()||graph->output().get_partial_shape().is_dynamic())
                throw std::runtime_error("External IR needs exactly one static f32 input and output.");
            input_shape=graph->input().get_shape();output_shape=graph->output().get_shape();
            count(input_shape);count(output_shape);
            const auto devices=core.get_property("MYRIAD",ov::available_devices);
            if(devices.size()!=1)throw std::runtime_error("Expected exactly one accessible MYRIAD device.");
            compiled=core.compile_model(graph,"MYRIAD");request=compiled.create_infer_request();
        }
        std::cout<<"{\"schema_version\":1,\"type\":\"ready\",\"case\":"<<json_quote(case_id)
                 <<",\"device\":"<<json_quote(validate_only?"none":"MYRIAD")
                 <<",\"validation_only\":"<<(validate_only?"true":"false")
                 <<",\"input\":{\"dtype\":\"f32\",\"shape\":"<<shape_json(input_shape)
                 <<"},\"output\":{\"dtype\":\"f32\",\"shape\":"<<shape_json(output_shape)<<"}}"<<std::endl;
        std::string line;bool too_long=false;
        while(read_frame(line,too_long)){
            std::string id;bool fatal=false;
            try{
                if(too_long)throw std::runtime_error("JSON frame exceeds 1 MiB.");
                // Extract identity only after a full, strict parse; invalid JSON has empty identity.
                const auto root=ncs2json::Parser(line).parse();
                if(root.kind==J::Object&&root.object.count("request_id")&&root.at("request_id").kind==J::String &&
                   root.at("request_id").string.size()<=128)
                    id=root.at("request_id").string;
                const auto m=decode(line,input_shape);id=m.id;
                if(m.operation=="health"||m.operation=="shutdown"||validate_only){
                    std::cout<<"{\"schema_version\":1,\"type\":\"response\",\"request_id\":"<<json_quote(id)
                             <<",\"status\":\"ok\",\"case\":"<<json_quote(case_id)<<",\"completed\":"<<completed
                             <<",\"hardware_executed\":false}"<<std::endl;
                    if(m.operation=="shutdown")break;
                    continue;
                }
                fatal=true;
                auto tensor=request.get_input_tensor();std::copy(m.input.begin(),m.input.end(),tensor.data<float>());
                const auto start=std::chrono::steady_clock::now();request.start_async();
                if(!request.wait_for(std::chrono::seconds(10)))throw std::runtime_error("Inference timeout; parent must reap worker.");
                const double ms=std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count();
                const auto output=request.get_output_tensor();
                if(output.get_element_type()!=ov::element::f32||output.get_shape()!=output_shape)throw std::runtime_error("Output shape/type mismatch.");
                const auto* data=output.data<const float>();
                for(size_t i=0;i<count(output_shape);++i)if(!std::isfinite(data[i]))throw std::runtime_error("Non-finite device output.");
                ++completed;
                std::cout<<std::setprecision(9)<<"{\"schema_version\":1,\"type\":\"response\",\"request_id\":"<<json_quote(id)
                         <<",\"status\":\"ok\",\"case\":"<<json_quote(case_id)<<",\"shape\":"<<shape_json(output_shape)
                         <<",\"dtype\":\"f32\",\"infer_ms\":"<<ms<<",\"hardware_executed\":true,\"output\":[";
                for(size_t i=0;i<count(output_shape);++i){if(i)std::cout<<',';std::cout<<data[i];}
                std::cout<<"]}"<<std::endl;
            }catch(const std::exception& e){error_response(id,e.what(),fatal);if(fatal)return 1;}
        }
        return 0;
    }catch(const std::exception& e){std::cerr<<"worker: "<<e.what()<<std::endl;return 1;}
}
