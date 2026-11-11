// Bounded native-arm64 throughput and equivalent host/device preprocessing experiments.
#include "ncs2_models.hpp"
#include <condition_variable>
#include <deque>
#include <mutex>

double elapsed_ms(Clock::time_point a, Clock::time_point b=Clock::now()) {
    return std::chrono::duration<double,std::milli>(b-a).count();
}
void numbers(std::ostream& out, const std::vector<double>& a) {
    out << '['; for(size_t i=0;i<a.size();++i){if(i)out<<',';out<<a[i];} out<<']';
}
void stats(std::ostream& out, const std::vector<double>& values) {
    if(values.empty())throw std::runtime_error("No timing samples.");
    auto a=values; std::sort(a.begin(),a.end());
    out<<"{\"mean_ms\":"<<std::accumulate(a.begin(),a.end(),0.0)/a.size()
       <<",\"p50_ms\":"<<a[size_t(std::ceil(.5*a.size()))-1]
       <<",\"p95_ms\":"<<a[size_t(std::ceil(.95*a.size()))-1]
       <<",\"min_ms\":"<<a.front()<<",\"max_ms\":"<<a.back()<<",\"samples_ms\":";
    numbers(out,values);out<<'}';
}
void validate_output(const Case& c,const std::vector<float>& actual,
                     const std::vector<double>& expected,size_t& checked,double& error_max) {
    if(actual.size()!=expected.size())throw std::runtime_error("Output count mismatch.");
    for(size_t i=0;i<actual.size();++i){
        const double err=std::abs(double(actual[i])-expected[i]);
        if(!std::isfinite(actual[i]) || err>c.atol+c.rtol*std::abs(expected[i]))
            throw std::runtime_error("Output mismatch at element "+std::to_string(i));
        error_max=std::max(error_max,err);++checked;
    }
}
void copy_output(ov::InferRequest& request,const Case& c,std::vector<float>& output) {
    const auto t=request.get_output_tensor();
    if(t.get_element_type()!=ov::element::f32 || t.get_shape()!=c.output)
        throw std::runtime_error("Output shape/type mismatch.");
    std::copy_n(t.data<const float>(),output.size(),output.data());
}
std::shared_ptr<ov::Model> prep_model(const Case& c) {
    auto graph=model(c);
    auto input=graph->get_parameters().front();
    // Capture consumers before constructing the normalization nodes.
    const auto consumers=input->output(0).get_target_inputs();
    auto scaled=std::make_shared<op::Multiply>(input,op::Constant::create(ov::element::f32,{},std::vector<float>{.5f}));
    auto shifted=std::make_shared<op::Add>(scaled,op::Constant::create(ov::element::f32,{},std::vector<float>{.25f}));
    for(auto target:consumers)target.replace_source_output(shifted);
    graph->validate_nodes_and_infer_types();return graph;
}

int main(int argc,char** argv) {
    std::string stage="arguments",mode,id;bool executed=false;
    try {
        if(argc!=10)throw std::runtime_error("Usage: ncs2_perf PLUGINS [async|host-preprocess|device-preprocess] CASE OUT ITERATIONS WARMUP DEPTH MISMATCH(0|1) TIMEOUT_MS");
        mode=argv[2];id=argv[3];
        if(mode!="async"&&mode!="host-preprocess"&&mode!="device-preprocess")throw std::runtime_error("Unknown mode.");
        const size_t n=parse_count(argv[5],false),warmup=parse_count(argv[6],true),depth=parse_count(argv[7],false);
        const std::string timeout_text=argv[9];
        if(timeout_text.empty() || timeout_text.find_first_not_of("0123456789")!=std::string::npos)
            throw std::runtime_error("Timeout must be a decimal integer.");
        const size_t timeout_ms=std::stoul(timeout_text);
        if(timeout_ms<1 || timeout_ms>300000) throw std::runtime_error("Timeout out of range (1..300000 ms).");
        if(n>1000||warmup>100||depth>4||depth>n)throw std::runtime_error("Limits: iterations<=1000; warmup<=100; depth<=min(4,iterations).");
        if(mode!="async"&&depth!=1)throw std::runtime_error("Preprocess comparison uses depth 1.");
        const bool corrupt=std::string(argv[8])=="1";
        if(!corrupt&&std::string(argv[8])!="0")throw std::runtime_error("Invalid mismatch flag.");
        auto it=std::find_if(cases.begin(),cases.end(),[&](const Case& c){return c.id==id;});
        if(it==cases.end()||it->kind!="conv")throw std::runtime_error("Use a convolution case.");
        const Case c=*it;
        stage="prepare";
        std::vector<std::vector<float>> inputs(n+warmup),outputs(n+warmup,std::vector<float>(elements(c.output)));
        std::vector<std::vector<double>> expected(n+warmup);
        for(size_t p=0;p<n+warmup;++p){
            inputs[p]=input_values(c,p);auto x=inputs[p];
            if(mode!="async")for(auto& v:x)v=v*.5f+.25f;
            expected[p]=reference(c,x);
        }
        stage="serialize";auto graph=mode=="device-preprocess"?prep_model(c):model(c);
        fs::create_directories(argv[4]);const auto xml=fs::path(argv[4])/"model.xml",bin=fs::path(argv[4])/"model.bin";
        if(fs::exists(xml)||fs::exists(bin))throw std::runtime_error("Refusing to overwrite IR.");
        ov::pass::Serialize writer(xml.string(),bin.string(),ov::pass::Serialize::Version::IR_V11);writer.run_on_model(graph);
        ov::Core core(argv[1]);graph=core.read_model(xml.string(),bin.string());
        if(graph->input().get_shape()!=c.input||graph->output().get_shape()!=c.output)throw std::runtime_error("IR shape mismatch.");
        stage="enumerate";auto devices=core.get_property("MYRIAD",ov::available_devices);
        if(devices.size()!=1)throw std::runtime_error("Expected exactly one accessible MYRIAD device.");
        stage="compile";const auto compile_begin=Clock::now();auto compiled=core.compile_model(graph,"MYRIAD");
        const double compile_ms=elapsed_ms(compile_begin);
        std::string optimal="null",optimal_error;
        try { optimal=std::to_string(compiled.get_property(ov::optimal_number_of_infer_requests)); }
        catch(const std::exception& e){optimal_error=e.what();}
        size_t checked=0;double max_error=0;
        std::vector<double> latency,prep_times,copy_times,infer_times,output_times;
        double wall_ms=0;size_t completed=0,max_inflight=0;
        if(mode=="async") {
            // Callbacks refer only to shared state, never InferRequest (no ownership cycle).
            struct Slot {size_t pass=0;Clock::time_point start,done;std::exception_ptr error;};
            std::mutex mutex;std::condition_variable cv;std::deque<size_t> ready;
            std::vector<Slot> slots(depth);
            std::vector<ov::InferRequest> requests;requests.reserve(depth);
            for(size_t s=0;s<depth;++s)requests.push_back(compiled.create_infer_request());
            struct Drain {
                std::vector<ov::InferRequest>& requests;bool active=false;
                ~Drain(){
                    if(!active)return;
                    for(auto& r:requests){try{r.cancel();}catch(...){}}
                    // Keep callback state alive while draining; the parent bounds a stuck teardown.
                    for(auto& r:requests){try{r.wait();}catch(...){}}
                }
            } drain{requests};
            stage="warmup";
            for(size_t p=0;p<warmup;++p){auto& r=requests[p%depth];auto t=r.get_input_tensor();
                std::copy(inputs[p].begin(),inputs[p].end(),t.data<float>());r.infer();executed=true;
                copy_output(r,c,outputs[p]);validate_output(c,outputs[p],expected[p],checked,max_error);
            }
            drain.active=true;
            for(size_t s=0;s<depth;++s)requests[s].set_callback([&,s](std::exception_ptr e){
                const auto now=Clock::now();{
                    std::lock_guard<std::mutex> lock(mutex);slots[s].error=e;slots[s].done=now;ready.push_back(s);
                }cv.notify_one();
            });
            size_t submitted=0,inflight=0;latency.resize(n);copy_times.resize(n);output_times.resize(n);
            auto submit=[&](size_t s){
                const size_t p=warmup+submitted++;slots[s].pass=p;slots[s].start=Clock::now();
                auto t=requests[s].get_input_tensor();std::copy(inputs[p].begin(),inputs[p].end(),t.data<float>());
                copy_times[p-warmup]=elapsed_ms(slots[s].start);++inflight;max_inflight=std::max(max_inflight,inflight);
                requests[s].start_async();
            };
            stage="inference";const auto begin=Clock::now();
            for(size_t s=0;s<depth;++s)submit(s);
            while(completed<n){
                size_t s;{
                    std::unique_lock<std::mutex> lock(mutex);
                    if(!cv.wait_for(lock,std::chrono::milliseconds(timeout_ms),[&]{return !ready.empty();}))
                        throw std::runtime_error("Timed out waiting for async callback; parent process enforces teardown deadline.");
                    s=ready.front();ready.pop_front();
                }
                requests[s].wait();if(slots[s].error)std::rethrow_exception(slots[s].error);
                executed=true;const size_t p=slots[s].pass;--inflight;++completed;
                latency[p-warmup]=elapsed_ms(slots[s].start,slots[s].done);
                const auto out_begin=Clock::now();copy_output(requests[s],c,outputs[p]);output_times[p-warmup]=elapsed_ms(out_begin);
                if(submitted<n)submit(s);
            }
            wall_ms=elapsed_ms(begin);
            // All callbacks have returned before captured state is destroyed.
            for(auto& r:requests)r.set_callback([](std::exception_ptr){});
            drain.active=false;
        } else {
            auto request=compiled.create_infer_request();std::vector<float> normalized(elements(c.input));
            Clock::time_point measured_begin;
            for(size_t p=0;p<n+warmup;++p){
                stage=p<warmup?"warmup":"inference";const auto a=Clock::now();
                if(p==warmup)measured_begin=a;
                const float* data=inputs[p].data();
                if(mode=="host-preprocess"){
                    for(size_t i=0;i<normalized.size();++i)normalized[i]=data[i]*.5f+.25f;
                    data=normalized.data();
                }
                const auto b=Clock::now();auto t=request.get_input_tensor();std::copy_n(data,normalized.size(),t.data<float>());
                const auto d=Clock::now();request.infer();executed=true;const auto e=Clock::now();
                copy_output(request,c,outputs[p]);const auto f=Clock::now();
                if(p==n+warmup-1)wall_ms=elapsed_ms(measured_begin,f);
                if(p>=warmup){prep_times.push_back(elapsed_ms(a,b));copy_times.push_back(elapsed_ms(b,d));
                    infer_times.push_back(elapsed_ms(d,e));output_times.push_back(elapsed_ms(e,f));latency.push_back(elapsed_ms(a,f));++completed;
                }
            }
            max_inflight=1;
        }
        stage="validation";
        if(corrupt)outputs[warmup][0]+=1000;
        // Async warmups were already checked; validation never inflates measured throughput.
        for(size_t p=mode=="async"?warmup:0;p<n+warmup;++p)validate_output(c,outputs[p],expected[p],checked,max_error);
        std::cout<<std::setprecision(12)<<"PERF_JSON={\"schema_version\":1,\"status\":\"passed\",\"stage\":\"validation\",\"hardware_executed\":true,\"device\":\"MYRIAD\",\"mode\":"<<json_quote(mode)<<',';
        metadata(std::cout,c);
        std::cout<<",\"iterations\":"<<n<<",\"warmup\":"<<warmup<<",\"depth\":"<<depth<<",\"completed\":"<<completed
                 <<",\"max_inflight\":"<<max_inflight<<",\"checked_elements\":"<<checked<<",\"max_abs_error\":"<<max_error
                 <<",\"compile_and_boot_ms\":"<<compile_ms<<",\"wall_ms\":"<<wall_ms<<",\"requests_per_s\":"<<1000*n/wall_ms
                 <<",\"optimal_requests\":"<<optimal<<",\"optimal_requests_error\":"<<json_quote(optimal_error)<<",\"latency\":";
        stats(std::cout,latency);std::cout<<",\"copy_in\":";stats(std::cout,copy_times);std::cout<<",\"copy_out\":";stats(std::cout,output_times);
        if(mode!="async"){std::cout<<",\"host_preprocess\":";stats(std::cout,prep_times);std::cout<<",\"infer\":";stats(std::cout,infer_times);}
        std::cout<<"}"<<std::endl;return 0;
    } catch(const std::exception& e){
        std::cout<<"PERF_JSON={\"schema_version\":1,\"status\":\"failed\",\"mode\":"<<json_quote(mode)<<",\"case\":"<<json_quote(id)
                 <<",\"stage\":"<<json_quote(stage)<<",\"hardware_executed\":"<<(executed?"true":"false")<<",\"error\":"<<json_quote(e.what())<<"}"<<std::endl;return 1;
    }
}
