// Persistent local JSON-lines worker. The compatibility runtime stays in this process.
#include "ncs2_models.hpp"
#include "ncs2_json.hpp"
#include <set>
using J=ncs2json::Value;
constexpr size_t frame_limit=1024*1024;
struct Message { std::string id,operation; std::vector<float> input; };
Message decode(const std::string& line,const Case& c) {
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
        if(shape.kind!=J::Array||shape.array.size()!=c.input.size())throw std::runtime_error("Input shape mismatch.");
        for(size_t i=0;i<c.input.size();++i)
            if(shape.array[i].kind!=J::Number||shape.array[i].number!=c.input[i])throw std::runtime_error("Input shape mismatch.");
        const auto& data=tensor.at("data");
        if(data.kind!=J::Array||data.array.size()!=elements(c.input))throw std::runtime_error("Input element count mismatch.");
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
        const bool validate_only=argc==3&&std::string(argv[1])=="--validate-only";
        if(!validate_only&&(argc!=4||(std::string(argv[2])!="--case"&&std::string(argv[2])!="--model")))
            throw std::runtime_error("Usage: ncs2_worker PLUGINS --case CASE | PLUGINS --model XML | --validate-only CASE");
        const bool external=!validate_only&&std::string(argv[2])=="--model";
        const std::string case_id=external?fs::path(argv[3]).stem().string():argv[validate_only?2:3];
        Case c;std::shared_ptr<ov::Model> graph;
        if(!external){
            const auto found=std::find_if(cases.begin(),cases.end(),[&](const Case& c){return c.id==case_id;});
            if(found==cases.end()||found->kind!="conv")throw std::runtime_error("Worker requires a supported convolution fixture.");
            c=*found;
        }
        ov::Core core;ov::CompiledModel compiled;ov::InferRequest request;size_t completed=0;
        if(!validate_only){
            core=ov::Core(argv[1]);core.set_property("MYRIAD",{{"LOG_LEVEL","LOG_NONE"}});
            graph=external?core.read_model(argv[3]):model(c);
            if(external){
                if(graph->inputs().size()!=1||graph->outputs().size()!=1 ||
                   graph->input().get_element_type()!=ov::element::f32||graph->output().get_element_type()!=ov::element::f32)
                    throw std::runtime_error("External IR needs exactly one static f32 input and output.");
                c={case_id,"external",graph->input().get_shape(),graph->output().get_shape()};
                for(const auto& shape:{c.input,c.output}){
                    size_t count=1;
                    if(shape.empty()||shape.size()>8)throw std::runtime_error("IR tensor rank must be 1..8.");
                    for(auto dim:shape){
                        if(dim==0||dim>16384||count>16384/dim)throw std::runtime_error("IR tensor limit is 16384 elements.");
                        count*=dim;
                    }
                }
            }
            const auto devices=core.get_property("MYRIAD",ov::available_devices);
            if(devices.size()!=1)throw std::runtime_error("Expected exactly one accessible MYRIAD device.");
            compiled=core.compile_model(graph,"MYRIAD");request=compiled.create_infer_request();
        }
        std::cout<<"{\"schema_version\":1,\"type\":\"ready\",\"case\":"<<json_quote(c.id)
                 <<",\"device\":"<<json_quote(validate_only?"none":"MYRIAD")
                 <<",\"validation_only\":"<<(validate_only?"true":"false")
                 <<",\"input\":{\"dtype\":\"f32\",\"shape\":"<<shape_json(c.input)
                 <<"},\"output\":{\"dtype\":\"f32\",\"shape\":"<<shape_json(c.output)<<"}}"<<std::endl;
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
                const auto m=decode(line,c);id=m.id;
                if(m.operation=="health"||m.operation=="shutdown"||validate_only){
                    std::cout<<"{\"schema_version\":1,\"type\":\"response\",\"request_id\":"<<json_quote(id)
                             <<",\"status\":\"ok\",\"case\":"<<json_quote(c.id)<<",\"completed\":"<<completed
                             <<",\"hardware_executed\":false}"<<std::endl;
                    if(m.operation=="shutdown")break;
                    continue;
                }
                fatal=true;
                auto tensor=request.get_input_tensor();std::copy(m.input.begin(),m.input.end(),tensor.data<float>());
                const auto start=Clock::now();request.start_async();
                if(!request.wait_for(std::chrono::seconds(10)))throw std::runtime_error("Inference timeout; parent must reap worker.");
                const double ms=std::chrono::duration<double,std::milli>(Clock::now()-start).count();
                const auto output=request.get_output_tensor();
                if(output.get_element_type()!=ov::element::f32||output.get_shape()!=c.output)throw std::runtime_error("Output shape/type mismatch.");
                const auto* data=output.data<const float>();
                for(size_t i=0;i<elements(c.output);++i)if(!std::isfinite(data[i]))throw std::runtime_error("Non-finite device output.");
                ++completed;
                std::cout<<std::setprecision(9)<<"{\"schema_version\":1,\"type\":\"response\",\"request_id\":"<<json_quote(id)
                         <<",\"status\":\"ok\",\"case\":"<<json_quote(c.id)<<",\"shape\":"<<shape_json(c.output)
                         <<",\"dtype\":\"f32\",\"infer_ms\":"<<ms<<",\"hardware_executed\":true,\"output\":[";
                for(size_t i=0;i<elements(c.output);++i){if(i)std::cout<<',';std::cout<<data[i];}
                std::cout<<"]}"<<std::endl;
            }catch(const std::exception& e){error_response(id,e.what(),fatal);if(fatal)return 1;}
        }
        return 0;
    }catch(const std::exception& e){std::cerr<<"worker: "<<e.what()<<std::endl;return 1;}
}
