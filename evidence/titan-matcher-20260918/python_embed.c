/* 以官方封裝的 Python 執行診斷腳本，不啟動 flashserver 入口。 */
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>
int main(int argc, char **argv) {
 if(argc != 4) return 2;
 void *lib=dlopen(argv[1], RTLD_NOW|RTLD_GLOBAL);
 if(!lib){fprintf(stderr,"%s\n",dlerror());return 3;}
 wchar_t path[16384];mbstowcs(path,argv[2],16383);
 void (*setpath)(const wchar_t*)=dlsym(lib,"Py_SetPath");
 void (*init)(void)=dlsym(lib,"Py_Initialize");
 int (*run)(FILE*,const char*,int,void*)=dlsym(lib,"PyRun_SimpleFileExFlags");
 int (*finish)(void)=dlsym(lib,"Py_FinalizeEx");
 if(!setpath||!init||!run||!finish)return 4;
 int *nosite=dlsym(lib,"Py_NoSiteFlag");*nosite=1;
 int *ignore=dlsym(lib,"Py_IgnoreEnvironmentFlag");*ignore=1;
 setpath(path);init();FILE *f=fopen(argv[3],"r");if(!f)return 5;
 int status=run(f,argv[3],1,NULL);finish();return status?1:0;
}
