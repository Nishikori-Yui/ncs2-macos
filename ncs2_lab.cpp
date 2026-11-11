// IR/operator laboratory; shared fixtures live in the header.
#include "ncs2_models.hpp"

int main(int argc,char** argv) {
    std::string stage="arguments",id;
    bool hardware_executed = false;
    try {
        if(argc==2 && std::string(argv[1])=="catalog") {
            std::cout << "CATALOG_JSON=[";
            for(size_t i=0;i<cases.size();++i) { if(i)std::cout<<',';std::cout<<'{';metadata(std::cout,cases[i]);std::cout<<'}'; }
            std::cout<<"]\n"; return 0;
        }
        if(argc!=8) throw std::runtime_error("Usage: ncs2_lab PLUGINS [run|trace|export|reference] CASE DIRECTORY ITERATIONS WARMUP MISMATCH_TEST(0|1)");
        const std::string plugins=argv[1],mode=argv[2];id=argv[3];
        if(mode!="run" && mode!="trace" && mode!="export" && mode!="reference") throw std::runtime_error("Unknown mode.");
        const auto trace_start=Clock::now();
        auto event=[&](const std::string& name) {
            if(mode=="trace") std::cout<<"EVENT_JSON={\"event\":"<<json_quote(name)<<",\"elapsed_ms\":"
                <<std::chrono::duration<double,std::milli>(Clock::now()-trace_start).count()<<"}"<<std::endl;
        };
        const size_t iterations=parse_count(argv[5],false), warmup=parse_count(argv[6],true);
        const bool corrupt=std::string(argv[7])=="1";
        if(std::string(argv[7])!="0" && !corrupt) throw std::runtime_error("Invalid mismatch test flag.");
        const auto found=std::find_if(cases.begin(),cases.end(),[&](const Case& c){return c.id==id;});
        if(found==cases.end()) throw std::runtime_error("Unknown case.");
        const auto& c=*found;
        if(mode=="reference") {
            for(size_t pass=0;pass<2;++pass) {
                const auto x=input_values(c,pass);const auto y=reference(c,x);
                std::cout<<"REFERENCE_JSON={\"case\":"<<json_quote(c.id)<<",\"pass\":"<<pass<<",\"input\":[";
                for(size_t j=0;j<x.size();++j){if(j)std::cout<<',';std::cout<<std::setprecision(17)<<x[j];}
                std::cout<<"],\"expected\":[";
                for(size_t j=0;j<y.size();++j){if(j)std::cout<<',';std::cout<<std::setprecision(17)<<y[j];}
                std::cout<<"]}\n";
            }
            return 0;
        }
        event("graph_build");stage="graph_build";auto graph=model(c);
        if(graph->output().get_shape()!=c.output) throw std::runtime_error("Graph shape does not match reference shape.");
        stage="serialize";fs::create_directories(argv[4]);const auto xml=fs::path(argv[4])/(id+".xml"),bin=fs::path(argv[4])/(id+".bin");
        if(fs::exists(xml)||fs::exists(bin)) throw std::runtime_error("Refusing to overwrite existing IR artifacts.");
        ov::pass::Serialize serializer(xml.string(),bin.string(),ov::pass::Serialize::Version::IR_V11);serializer.run_on_model(graph);
        stage="read_ir";ov::Core core(plugins);graph=core.read_model(xml.string(),bin.string());
        if(graph->input().get_shape()!=c.input || graph->output().get_shape()!=c.output) throw std::runtime_error("IR round-trip shape mismatch.");
        if(mode=="export") {
            std::cout<<"CASE_JSON={\"schema_version\":1,";metadata(std::cout,c);std::cout<<",\"status\":\"exported\",\"stage\":\"read_ir\",\"hardware_executed\":false}\n";return 0;
        }
        stage="device_enumeration";const auto devices=core.get_property("MYRIAD",ov::available_devices);
        if(devices.empty()) throw std::runtime_error("No MYRIAD devices; check direct USB access before diagnosing hardware.");
        if(devices.size()!=1) throw std::runtime_error("Expected exactly one MYRIAD for an unambiguous baseline.");
        event("compile_begin");stage="compile";const auto compile_start=Clock::now();auto compiled=core.compile_model(graph,"MYRIAD");
        const double compile_ms=std::chrono::duration<double,std::milli>(Clock::now()-compile_start).count();
        event("compile_end");
        if(mode=="trace") {
            event("compiled_hold_begin");std::this_thread::sleep_for(std::chrono::seconds(1));event("compiled_hold_end");
        }
        stage="request_create";auto request=compiled.create_infer_request();
        double max_abs=0,max_rel=0;size_t checked=0;std::vector<double> durations;
        for(size_t pass=0;pass<warmup+iterations;++pass) {
            stage="input";auto x=input_values(c,pass);const auto expected=reference(c,x);
            auto tensor=request.get_input_tensor();
            if(c.kind=="u8") {auto* data=tensor.data<uint8_t>();for(size_t i=0;i<x.size();++i)data[i]=static_cast<uint8_t>(x[i]);}
            else std::copy(x.begin(),x.end(),tensor.data<float>());
            stage="inference";event("infer_begin");const auto start=Clock::now();request.infer();
            const double ms=std::chrono::duration<double,std::milli>(Clock::now()-start).count();
            hardware_executed=true;event("infer_end");
            stage="validation";const auto output=request.get_output_tensor();
            if(output.get_element_type()!=ov::element::f32 || output.get_shape()!=c.output) throw std::runtime_error("Output shape/dtype mismatch.");
            const auto* data=output.data<const float>();
            for(size_t i=0;i<expected.size();++i) {
                const double actual=data[i]+(corrupt && pass==0 && i==0 ? 1000.0 : 0.0),error=std::abs(actual-expected[i]);
                if(!std::isfinite(actual)||error>c.atol+c.rtol*std::abs(expected[i])) {
                    std::ostringstream message;message<<"Mismatch pass="<<pass<<" element="<<i<<" expected="<<expected[i]<<" actual="<<actual<<" abs_error="<<error;
                    throw std::runtime_error(message.str());
                }
                max_abs=std::max(max_abs,error);max_rel=std::max(max_rel,error/std::max(1e-12,std::abs(expected[i])));++checked;
            }
            if(pass>=warmup)durations.push_back(ms);
        }
        std::sort(durations.begin(),durations.end());
        const double mean=std::accumulate(durations.begin(),durations.end(),0.0)/durations.size();
        auto pct=[&](double p){return durations[static_cast<size_t>(std::ceil(p*durations.size()))-1];};
        std::cout<<std::setprecision(12)<<"CASE_JSON={\"schema_version\":1,";metadata(std::cout,c);
        std::cout<<",\"status\":\"passed\",\"stage\":\"validation\",\"hardware_executed\":true,\"device\":\"MYRIAD\",\"device_id\":"<<json_quote(devices.front())
                 <<",\"iterations\":"<<iterations<<",\"warmup\":"<<warmup<<",\"checked_elements\":"<<checked
                 <<",\"compile_and_boot_ms\":"<<compile_ms<<",\"mean_ms\":"<<mean<<",\"p50_ms\":"<<pct(.5)<<",\"p95_ms\":"<<pct(.95)
                 <<",\"min_ms\":"<<durations.front()<<",\"max_ms\":"<<durations.back()<<",\"max_abs_error\":"<<max_abs<<",\"max_rel_error\":"<<max_rel<<"}\n";
        event("teardown_begin");
        return 0;
    } catch(const std::exception& error) {
        std::cout<<"CASE_JSON={\"schema_version\":1,\"case\":"<<json_quote(id)<<",\"status\":\"failed\",\"stage\":"<<json_quote(stage)
                 <<",\"hardware_executed\":"<<(hardware_executed?"true":"false")<<",\"error\":"<<json_quote(error.what())<<"}\n";
        return 1;
    }
}
