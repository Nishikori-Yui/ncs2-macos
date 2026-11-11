#pragma once
// Bounded JSON parser for the local worker protocol; no external host dependency.
#include <cmath>
#include <cstdlib>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

namespace ncs2json {
struct Value {
    enum Kind { Null, Boolean, Number, String, Array, Object } kind = Null;
    double number = 0;
    bool boolean = false;
    std::string string;
    std::vector<Value> array;
    std::map<std::string,Value> object;
    const Value& at(const std::string& key) const {
        if(kind!=Object || object.find(key)==object.end()) throw std::runtime_error("Missing field: "+key);
        return object.at(key);
    }
    const std::string& as_string() const {
        if(kind!=String)throw std::runtime_error("Expected JSON string.");
        return string;
    }
};
class Parser {
    const std::string& s; size_t p=0;
    [[noreturn]] void fail() const {throw std::runtime_error("Invalid JSON at byte "+std::to_string(p));}
    void whitespace(){while(p<s.size()&&(s[p]==' '||s[p]=='\r'||s[p]=='\n'||s[p]=='\t'))++p;}
    bool take(char c){whitespace();if(p<s.size()&&s[p]==c){++p;return true;}return false;}
    void expect(char c){if(!take(c))fail();}
    unsigned hex4(){
        unsigned v=0;
        for(int n=0;n<4;++n){
            if(p==s.size())fail();
            const char c=s[p++];v*=16;
            if(c>='0'&&c<='9')v+=c-'0';
            else if(c>='a'&&c<='f')v+=c-'a'+10;
            else if(c>='A'&&c<='F')v+=c-'A'+10;
            else fail();
        }return v;
    }
    static void utf8(std::string& out,unsigned c){
        if(c<0x80)out+=char(c);
        else if(c<0x800){out+=char(0xc0|(c>>6));out+=char(0x80|(c&63));}
        else if(c<0x10000){out+=char(0xe0|(c>>12));out+=char(0x80|((c>>6)&63));out+=char(0x80|(c&63));}
        else{out+=char(0xf0|(c>>18));out+=char(0x80|((c>>12)&63));out+=char(0x80|((c>>6)&63));out+=char(0x80|(c&63));}
    }
    std::string string(){
        expect('"');std::string out;
        while(p<s.size()){
            const unsigned char c=s[p++];
            if(c=='"')return out;
            if(c<32)fail();
            if(c=='\\'){
                if(p==s.size())fail();
                switch(s[p++]){
                    case '"':out+='"';break; case '\\':out+='\\';break; case '/':out+='/';break;
                    case 'b':out+='\b';break; case 'f':out+='\f';break; case 'n':out+='\n';break;
                    case 'r':out+='\r';break; case 't':out+='\t';break;
                    case 'u':{
                        unsigned code=hex4();
                        if(code>=0xd800 && code<=0xdbff){
                            if(p+2>s.size()||s[p]!='\\'||s[p+1]!='u')fail();
                            p+=2;const unsigned low=hex4();
                            if(low<0xdc00||low>0xdfff)fail();
                            code=0x10000+((code-0xd800)<<10)+(low-0xdc00);
                        }else if(code>=0xdc00 && code<=0xdfff)fail();
                        utf8(out,code);break;
                    }
                    default:fail();
                }
            } else if(c<0x80)out+=char(c);
            else {
                const unsigned count=c>=0xc2&&c<=0xdf?1:c>=0xe0&&c<=0xef?2:c>=0xf0&&c<=0xf4?3:0;
                if(count==0)fail();
                unsigned code=c & (count==1?31:count==2?15:7);
                for(unsigned i=0;i<count;++i){if(p==s.size())fail();unsigned b=static_cast<unsigned char>(s[p++]);if((b&0xc0)!=0x80)fail();code=(code<<6)|(b&63);}
                if(code<(count==1?0x80:count==2?0x800:0x10000)||code>0x10ffff||(code>=0xd800&&code<=0xdfff))fail();
                utf8(out,code);
            }
        }fail();
    }
    Value value(unsigned depth){
        if(depth>8)throw std::runtime_error("JSON nesting limit exceeded.");
        whitespace();if(p==s.size())fail();Value v;
        if(s[p]=='{'){
            ++p;v.kind=Value::Object;if(take('}'))return v;
            do{
                auto key=string();expect(':');auto child=value(depth+1);
                if(v.object.size()>=32||!v.object.emplace(key,std::move(child)).second)throw std::runtime_error("Too many or duplicate JSON object keys.");
                if(take('}'))return v;
            }while(take(','));fail();
        }
        if(s[p]=='['){
            ++p;v.kind=Value::Array;if(take(']'))return v;
            do{
                if(v.array.size()>=16384)throw std::runtime_error("JSON array limit exceeded.");
                v.array.push_back(value(depth+1));
                if(take(']'))return v;
            }while(take(','));fail();
        }
        if(s[p]=='"'){v.kind=Value::String;v.string=string();return v;}
        for(const auto& word:std::vector<std::string>{"true","false","null"}){
            if(s.compare(p,word.size(),word)==0){
                p+=word.size();v.kind=word=="null"?Value::Null:Value::Boolean;v.boolean=word=="true";return v;
            }
        }
        const size_t start=p;
        if(s[p]=='-')++p;
        if(p==s.size())fail();
        if(s[p]=='0')++p;
        else if(s[p]>='1'&&s[p]<='9'){do{++p;}while(p<s.size()&&s[p]>='0'&&s[p]<='9');}
        else fail();
        if(p<s.size()&&s[p]=='.'){++p;const size_t a=p;while(p<s.size()&&s[p]>='0'&&s[p]<='9')++p;if(a==p)fail();}
        if(p<s.size()&&(s[p]=='e'||s[p]=='E')){
            ++p;if(p<s.size()&&(s[p]=='+'||s[p]=='-'))++p;
            const size_t a=p;while(p<s.size()&&s[p]>='0'&&s[p]<='9')++p;if(a==p)fail();
        }
        v.kind=Value::Number;v.number=std::strtod(s.substr(start,p-start).c_str(),nullptr);
        if(!std::isfinite(v.number))throw std::runtime_error("JSON number exceeds finite range.");
        return v;
    }
public:
    explicit Parser(const std::string& text):s(text){}
    Value parse(){
        if(s.size()>1024*1024)throw std::runtime_error("JSON frame exceeds 1 MiB.");
        auto v=value(0);whitespace();if(p!=s.size())fail();return v;
    }
};
}
