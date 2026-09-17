/* 僅存取測試暫存目錄與匿名主機 RAM，不開區塊設備。 */
static int fs_read(const char *path, ulong address, loff_t offset, loff_t count, loff_t *received)
{
    unsigned int i;
    char name[32];
    FILE *input;
    for (i=0; i<ARRAY_SIZE(bpi_rescue_payloads); ++i) {
        const struct bpi_lab_payload *p=&bpi_rescue_payloads[i];
        if (strcmp(path,p->path)) continue;
        assert(address==p->address && offset==0 && count==(loff_t)p->size+1);
        snprintf(name,sizeof(name),"%u.bin",i);
        input=fopen(name,"rb"); assert(input);
        *received=fread((void *)address,1,count,input); fclose(input);
        if (fault==7) --*received;
        if (fault==8) ++*received;
        return fault==9 ? -1 : 0;
    }
    return -1;
}
int main(int argc, char **argv)
{
    unsigned int i;
    char *probe[]={"bpirescue","probe",NULL};
    char *load[]={"bpirescue","load",NULL,NULL};
    char *boot[]={"bpirescue","boot",NULL};
    fault=argc>1 ? atoi(argv[1]) : 0;
    assert(mmap((void *)0x70000000,0x500000,PROT_READ|PROT_WRITE,
                MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0)==(void *)0x70000000);
    for (i=0; i<ARRAY_SIZE(bpi_rescue_payloads); ++i) {
        const struct bpi_lab_payload *p=&bpi_rescue_payloads[i];
        size_t size=(p->size+65536+4095)&~4095UL;
        assert(mmap((void *)p->address,size,PROT_READ|PROT_WRITE,
                    MAP_PRIVATE|MAP_ANONYMOUS|MAP_FIXED_NOREPLACE,-1,0)==(void *)p->address);
    }
    if (fault==1) sd.cid[0]^=1;
    if (fault==2) sd.version=0;
    if (fault==3) --sd.block_dev.lba;
    if (fault==14) audio_fw_state=1;
    if (fault==15) bpi_lab_loaded=1;
    if (fault==16) gd->start_addr_sp=0x2100000;
    if (fault==17) sd.block_dev.dev=1;
    if ((fault>=1 && fault<=6) || fault>=14) {
        assert(do_bpirescue(NULL,0,2,probe)); assert(!called && !audio_called); return 0;
    }
    assert(!do_bpirescue(NULL,0,2,probe));
    assert(bpi_lab_used);
    for (i=0; i<ARRAY_SIZE(bpi_rescue_payloads); ++i) {
        load[2]=(char *)bpi_rescue_payloads[i].role;
        if (fault>=7 && fault<=9) {
            assert(do_bpirescue(NULL,0,3,load)); assert(!called && !audio_called); return 0;
        }
        assert(!do_bpirescue(NULL,0,3,load));
    }
    if (fault==10) *(char *)bpi_rescue_payloads[0].address^=1;
    if (fault==11) sd.cid[0]^=1;
    if (fault>=10 && fault<=13) {
        assert(do_bpirescue(NULL,0,2,boot)); assert(!called && !audio_called); return 0;
    }
    assert(!do_bpirescue(NULL,0,2,boot)); assert(called==1 && audio_called==1);
    assert(do_bpirescue(NULL,0,2,boot)); assert(called==1);
    return 0;
}
