#include <array>
#include <cassert>
#include <memory>
#include <cstdlib>
struct MouseEvent {
    virtual ~MouseEvent() = default;
    virtual int GetX() = 0;
    virtual int GetY() = 0;
    virtual int GetButton() = 0;
    virtual int GetAction() = 0;
    virtual int GetClickNum() = 0;
};
struct TestMouse final: MouseEvent {
    std::array<int,5> values;
    explicit TestMouse(std::array<int,5> v):values(v){}
    int GetX() override{return values[0];}
    int GetY() override{return values[1];}
    int GetButton() override{return values[2];}
    int GetAction() override{return values[3];}
    int GetClickNum() override{return values[4];}
};
static int count=0;
static void* expected_self;
static std::array<int,5> expected;
extern "C" void legacy_mouse(void* self,int x,int y,int button,int action,int clicks) {
    assert(self==expected_self);
    assert((std::array<int,5>{x,y,button,action,clicks}==expected));
    ++count;
}
extern "C" void stack_chk_fail(){std::abort();}
extern "C" void arkweb_mouse_compat(void*,const std::shared_ptr<MouseEvent>&);
int main(){
    int identity;expected_self=&identity;
    for(auto v:{std::array<int,5>{327,25,1,1,1},{327,25,1,2,1},{10,100,1,3,1},{-4,-8,2,1,2}}){
        expected=v;
        std::shared_ptr<MouseEvent> e=std::make_shared<TestMouse>(v);
        arkweb_mouse_compat(expected_self,e);
        assert(e.use_count()==1);
    }
    std::shared_ptr<MouseEvent> empty;
    arkweb_mouse_compat(expected_self,empty);
    assert(count==4);
}
