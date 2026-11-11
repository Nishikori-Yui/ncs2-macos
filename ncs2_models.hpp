#pragma once
// Synthetic IR generation, hardware execution, and independent scalar references.
#include <openvino/openvino.hpp>
#include <openvino/opsets/opset8.hpp>
#include <openvino/pass/serialize.hpp>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <iomanip>
#include <limits>
#include <iostream>
#include <numeric>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace fs = std::filesystem;
namespace op = ov::opset8;
using Clock = std::chrono::steady_clock;

struct Case {
    std::string id, kind;
    ov::Shape input, output;
    size_t channels = 0;
    double atol = 0.002, rtol = 0.002;
};

const std::vector<Case> cases{
    {"relu_4", "relu", {1,4}, {1,4}},
    {"relu_256", "relu", {1,256}, {1,256}},
    {"relu_1024", "relu", {1,1,32,32}, {1,1,32,32}},
    {"relu_16384", "relu", {1,4,64,64}, {1,4,64,64}},
    {"affine_channels", "affine", {1,4,8,8}, {1,4,8,8}},
    {"conv_1x1x8x8", "conv", {1,1,8,8}, {1,1,6,6}, 1, 0.01, 0.01},
    {"conv_1x3x16x16", "conv", {1,3,16,16}, {1,4,14,14}, 4, 0.01, 0.01},
    {"conv_1x8x32x32", "conv", {1,8,32,32}, {1,16,30,30}, 16, 0.02, 0.01},
    {"reshape_relu", "reshape", {1,2,3,4}, {1,4,6}},
    {"transpose_relu", "transpose", {1,2,3,4}, {1,3,4,2}},
    {"fake_quantize_256", "fq", {1,1,8,8}, {1,1,8,8}, 0, 0.002, 0.002},
    {"convert_u8_relu", "u8", {1,1,8,8}, {1,1,8,8}},
    {"avgpool_2x2", "avgpool", {1,1,4,4}, {1,1,2,2}},
    {"maxpool_2x2", "maxpool", {1,1,4,4}, {1,1,2,2}},
    {"concat_channels", "concat", {1,1,2,3}, {1,2,2,3}},
    {"broadcast_channels", "broadcast", {1,1,2,3}, {1,2,2,3}}
};

std::string json_quote(const std::string& text) {
    std::ostringstream out; out << '"';
    for (unsigned char c : text) {
        if (c == 34 || c == 92) out << char(92) << c;
        else if (c < 32) out << char(92) << 'u' << std::hex << std::setw(4) << std::setfill('0') << int(c) << std::dec;
        else out << c;
    }
    out << '"'; return out.str();
}

std::string shape_json(const ov::Shape& shape) {
    std::ostringstream out; out << '[';
    for (size_t i=0; i<shape.size(); ++i) { if (i) out << ','; out << shape[i]; }
    out << ']'; return out.str();
}

size_t elements(const ov::Shape& shape) {
    return std::accumulate(shape.begin(), shape.end(), size_t{1}, std::multiplies<size_t>());
}

size_t parse_count(const char* value, bool zero) {
    const std::string s(value);
    if (s.empty() || s.find_first_not_of("0123456789") != std::string::npos)
        throw std::runtime_error("Count must be a decimal integer.");
    const auto n = std::stoul(s);
    if (n > 10000 || (!zero && n==0)) throw std::runtime_error("Count out of range (1..10000, warmup permits 0).");
    return n;
}

std::vector<float> weights(const Case& c) {
    std::vector<float> w(c.channels*c.input[1]*9);
    for (size_t i=0; i<w.size(); ++i) w[i] = (static_cast<int>((i*7)%9)-4)/32.0f;
    return w;
}

std::shared_ptr<ov::Model> model(const Case& c) {
    const auto input = std::make_shared<op::Parameter>(c.kind == "u8" ? ov::element::u8 : ov::element::f32, c.input);
    input->set_friendly_name("input"); input->output(0).get_tensor().set_names({"input"});
    std::shared_ptr<ov::Node> node = input;
    if (c.kind == "conv") {
        const auto w=op::Constant::create(ov::element::f32, {c.channels,c.input[1],3,3}, weights(c));
        node=std::make_shared<op::Convolution>(input,w,ov::Strides{1,1},ov::CoordinateDiff{0,0},ov::CoordinateDiff{0,0},ov::Strides{1,1});
    } else if (c.kind == "affine") {
        const auto scale=op::Constant::create(ov::element::f32,{1,4,1,1},std::vector<float>{0.5f,1,1.5f,2});
        const auto bias=op::Constant::create(ov::element::f32,{1,4,1,1},std::vector<float>{-0.25f,0.125f,0.25f,-0.5f});
        node=std::make_shared<op::Multiply>(input,scale);
        node=std::make_shared<op::Add>(node,bias);
        node=std::make_shared<op::Relu>(node);
    } else if (c.kind == "reshape") {
        const auto pattern=op::Constant::create(ov::element::i64,{3},std::vector<int64_t>{1,4,6});
        node=std::make_shared<op::Reshape>(input,pattern,false);
        node=std::make_shared<op::Relu>(node);
    } else if (c.kind == "transpose") {
        const auto order=op::Constant::create(ov::element::i64,{4},std::vector<int64_t>{0,2,3,1});
        node=std::make_shared<op::Transpose>(input,order);
        node=std::make_shared<op::Relu>(node);
    } else if (c.kind == "avgpool") {
        node=std::make_shared<ov::op::v1::AvgPool>(input,ov::Strides{2,2},ov::Shape{0,0},ov::Shape{0,0},ov::Shape{2,2},true);
    } else if (c.kind == "maxpool") {
        node=std::make_shared<ov::op::v1::MaxPool>(input,ov::Strides{2,2},ov::Shape{0,0},ov::Shape{0,0},ov::Shape{2,2});
    } else if (c.kind == "concat") {
        const auto negated=std::make_shared<op::Negative>(input);
        node=std::make_shared<op::Concat>(ov::OutputVector{input->output(0),negated->output(0)},1);
    } else if (c.kind == "broadcast") {
        const auto shape=op::Constant::create(ov::element::i64,{4},std::vector<int64_t>{1,2,2,3});
        node=std::make_shared<op::Broadcast>(input,shape);
    } else if (c.kind == "fq") {
        const auto low=op::Constant::create(ov::element::f32,{},std::vector<float>{-1});
        const auto high=op::Constant::create(ov::element::f32,{},std::vector<float>{1});
        node=std::make_shared<op::FakeQuantize>(input,low,high,low,high,256);
    } else {
        if (c.kind == "u8") node=std::make_shared<op::Convert>(input,ov::element::f32);
        node=std::make_shared<op::Relu>(node);
    }
    node->set_friendly_name("output");
    return std::make_shared<ov::Model>(ov::ResultVector{std::make_shared<op::Result>(node)},ov::ParameterVector{input},c.id);
}

std::vector<float> input_values(const Case& c, size_t pass) {
    std::vector<float> x(elements(c.input));
    for (size_t i=0;i<x.size();++i) {
        x[i]=c.kind=="u8" ? float((i*37+pass*13)%256) : (static_cast<int>((i*37+pass*13)%65)-32)/16.0f;
        // Avoid the exact half-level tie at zero; tie semantics are a separate test.
        if (c.kind=="fq" && x[i]==0) x[i]=1.0f/256;
    }
    return x;
}

// No OpenVINO evaluate(), CPU plugin, or tensor library in this reference.
std::vector<double> reference(const Case& c,const std::vector<float>& x) {
    std::vector<double> y(elements(c.output));
    if (c.kind=="avgpool" || c.kind=="maxpool") {
        for(size_t row=0;row<2;++row) for(size_t col=0;col<2;++col) {
            double value=c.kind=="avgpool" ? 0.0 : -std::numeric_limits<double>::infinity();
            for(size_t dy=0;dy<2;++dy) for(size_t dx=0;dx<2;++dx) {
                const double sample=x[(row*2+dy)*4+col*2+dx];
                if(c.kind=="avgpool") value+=sample/4.0;
                else value=std::max(value,sample);
            }
            y[row*2+col]=value;
        }
    } else if (c.kind=="concat" || c.kind=="broadcast") {
        for(size_t i=0;i<x.size();++i) {
            y[i]=x[i];
            y[x.size()+i]=c.kind=="concat" ? -double(x[i]) : x[i];
        }
    } else if (c.kind=="conv") {
        const auto w=weights(c); const size_t ci=c.input[1],h=c.input[2],width=c.input[3],oh=h-2,ow=width-2;
        for (size_t oc=0;oc<c.channels;++oc) for(size_t r=0;r<oh;++r) for(size_t col=0;col<ow;++col) {
            double sum=0;
            for(size_t ic=0;ic<ci;++ic) for(size_t kh=0;kh<3;++kh) for(size_t kw=0;kw<3;++kw)
                sum+=double(x[(ic*h+r+kh)*width+col+kw])*w[((oc*ci+ic)*3+kh)*3+kw];
            y[(oc*oh+r)*ow+col]=sum;
        }
    } else if (c.kind=="transpose") {
        for(size_t ch=0;ch<2;++ch) for(size_t r=0;r<3;++r) for(size_t col=0;col<4;++col)
            y[(r*4+col)*2+ch]=std::max(0.0f,x[(ch*3+r)*4+col]);
    } else {
        const double scale[]={0.5,1,1.5,2}, bias[]={-0.25,0.125,0.25,-0.5};
        for(size_t i=0;i<x.size();++i) {
            if(c.kind=="affine") y[i]=std::max(0.0,double(x[i])*scale[i/64]+bias[i/64]);
            else if(c.kind=="fq") y[i]=x[i]<=-1 ? -1 : x[i]>1 ? 1 : std::floor((double(x[i])+1)*127.5+0.5)/127.5-1;
            else y[i]=std::max(0.0f,x[i]);
        }
    }
    return y;
}

void metadata(std::ostream& out,const Case& c) {
    out << "\"case\":" << json_quote(c.id) << ",\"kind\":" << json_quote(c.kind)
        << ",\"input_shape\":" << shape_json(c.input) << ",\"output_shape\":" << shape_json(c.output)
        << ",\"input_dtype\":" << json_quote(c.kind=="u8" ? "u8" : "f32") << ",\"output_dtype\":\"f32\""
        << ",\"atol\":" << c.atol << ",\"rtol\":" << c.rtol;
}

