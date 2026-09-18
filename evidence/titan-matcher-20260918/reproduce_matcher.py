"""直接執行官方匹配位元碼；停在產生燒錄操作之前，不連接硬體。"""
import hashlib
import json
import math
import re
import types

from flashboot.source.exceptions import FlashNotMatchError


def child_code(parent, name):
    matches = [item for item in parent.co_consts
               if isinstance(item, types.CodeType) and item.co_name == name]
    if len(matches) != 1:
        raise RuntimeError('找不到唯一的官方函式：' + name)
    return matches[0]


kind, start, length = pyz_toc['flashboot.source.actions']
module_code = marshal.loads(zlib.decompress(pyz_data[start:start + length]))
class_code = child_code(module_code, 'MultiFlash')
method_names = ('_check_partition', 'build_multi_flash',
                'build_multi_flash_use_partition')
method_codes = {name: child_code(class_code, name) for name in method_names}
fixture_dir = os.environ['TITAN_FIXTURE_DIR']
if not os.path.isfile(os.path.join(fixture_dir, 'partition_universal.json')):
    raise RuntimeError('測試輸入缺少 partition_universal.json')


def run_case(name, relate, variables, explicit=None, directory=fixture_dir):
    logs = []
    state = types.SimpleNamespace(
        part_config=explicit, match_partition=[], relate_partition=relate,
        flash_dir=directory)
    globals_map = {'__builtins__': __builtins__, 're': re, 'os': os, 'math': math,
                   'temp': types.SimpleNamespace(**variables),
                   'FlashNotMatchError': FlashNotMatchError}
    for method_name, code in method_codes.items():
        # 函式本文直接來自官方 PYZ；此處只提供狀態與日誌相依。
        function = types.FunctionType(code, globals_map, method_name)
        setattr(state, method_name, types.MethodType(function, state))
    # 不產生、驗證或執行實際燒錄操作；只記錄匹配後的交接點。
    state.convert_2_flash_action = lambda: list(state.match_partition)
    handlers = types.SimpleNamespace(info_cb=logs.append)
    result = {'name': name, 'relate_partition': relate,
              'variables': variables, 'part_config': explicit}
    try:
        result['returned_partitions'] = state.build_multi_flash(handlers, False)
        result['status'] = 'matched'
    except Exception as error:
        result.update(status='error', error_type=type(error).__name__,
                      error_text=str(error))
    result['matched_partitions'] = state.match_partition
    # 此欄保留官方程式的原始日誌，供逐字比對失敗畫面。
    result['official_logs'] = logs
    return result


cases = [
    run_case('fixed_name_default_flow', ['partition_universal.json'],
             {'size0': 'NULL', 'size1': 'universal'}),
    run_case('block_placeholder_default_flow', ['partition_{size1}.json'],
             {'size0': 'NULL', 'size1': 'universal'}),
    run_case('official_two_placeholders',
             ['partition_{size0}.json', 'partition_{size1}.json'],
             {'size0': 'NULL', 'size1': 'universal'}),
    run_case('explicit_part_override', ['partition_universal.json'],
             {'size0': 'NULL', 'size1': 'universal'},
             explicit=['partition_universal.json']),
    run_case('block_placeholder_with_mtd_present', ['partition_{size1}.json'],
             {'size0': '2M', 'size1': 'universal'}),
    run_case('block_placeholder_null_device', ['partition_{size1}.json'],
             {'size0': '2M', 'size1': 'NULL'}),
]

package_dir = os.environ.get('TITAN_PACKAGE_DIR')
if package_dir:
    import yaml
    from flashboot.source import actions as official_actions
    config_path = os.path.join(package_dir, 'fastboot.yaml')
    with open(config_path) as stream:
        config = yaml.safe_load(stream)
    blocks = [item['multi_flash'] for item in config['actions'] if 'multi_flash' in item]
    if len(blocks) != 1:
        raise RuntimeError('實包須只有一個 multi_flash')
    case = run_case('actual_package_default_flow', blocks[0]['relate_partition'],
                    {'size0': '2M', 'size1': 'universal'}, directory=package_dir)
    case['package_directory'] = package_dir
    case['fastboot_yaml_sha256'] = hashlib.sha256(open(config_path, 'rb').read()).hexdigest()
    # 實包階段使用完整官方類別，包含 JSON 讀取、轉換與每個操作的 validate。
    official_actions.temp.size0 = '2M'
    official_actions.temp.size1 = 'universal'
    action = official_actions.MultiFlash(
        id='offline-evidence', timeout=blocks[0]['timeout'],
        retry=blocks[0].get('retry', 1), flash_dir=package_dir,
        relate_partition=blocks[0]['relate_partition'])
    package_logs = []
    converted = action.build_multi_flash(
        types.SimpleNamespace(info_cb=package_logs.append), False)
    case['official_conversion_logs'] = package_logs
    case['official_flash_actions'] = []
    for item in converted:
        absolute = os.path.join(package_dir, item.file)
        entry = {'partition': item.partition, 'file': item.file,
                 'gzip_level': item.gzip_level,
                 'file_exists': os.path.isfile(absolute),
                 'file_bytes': os.path.getsize(absolute)}
        if entry['file_bytes'] < 8 * 1024 * 1024:
            entry['sha256'] = hashlib.sha256(open(absolute, 'rb').read()).hexdigest()
        case['official_flash_actions'].append(entry)
    assert [entry['partition'] for entry in case['official_flash_actions']] == [
        'gpt', 'bootinfo', 'fsbl', 'env', 'opensbi', 'uboot', 'bootfs', 'rootfs']
    assert all(entry['file_exists'] for entry in case['official_flash_actions'])
    case['control_files'] = {
        filename: hashlib.sha256(open(os.path.join(package_dir, filename), 'rb').read()).hexdigest()
        for filename in ('fastboot.yaml', 'partition_universal.json', 'manifest.json')
        if os.path.isfile(os.path.join(package_dir, filename))
    }
    manifest_path = os.path.join(os.path.dirname(package_dir), 'manifest.json')
    if os.path.isfile(manifest_path):
        import zipfile
        with open(manifest_path) as stream:
            manifest = json.load(stream)
        archive_path = os.path.join(os.path.dirname(package_dir), manifest['artifact']['name'])
        digest = hashlib.sha256()
        with open(archive_path, 'rb') as stream:
            for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                digest.update(chunk)
        assert digest.hexdigest() == manifest['artifact']['sha256']
        small_files = dict(case['control_files'])
        small_files.update({item['file']: item['sha256']
                            for item in case['official_flash_actions'] if 'sha256' in item})
        extra_header = 'factory/bootinfo_emmc.bin'
        extra_path = os.path.join(package_dir, extra_header)
        if os.path.isfile(extra_path):
            small_files[extra_header] = hashlib.sha256(open(extra_path, 'rb').read()).hexdigest()
        with zipfile.ZipFile(archive_path) as archive:
            for filename, expected in small_files.items():
                assert hashlib.sha256(archive.read(filename)).hexdigest() == expected
            for item in case['official_flash_actions']:
                assert archive.getinfo(item['file']).file_size == item['file_bytes']
        case['artifact'] = {
            'name': manifest['artifact']['name'], 'sha256': digest.hexdigest(),
            'bytes': os.path.getsize(archive_path),
            'checked_zip_files_sha256': small_files,
            'manifest_sha256': hashlib.sha256(open(manifest_path, 'rb').read()).hexdigest(),
            'scope': '重算完整 ZIP 雜湊；ZIP 控制檔與小載荷逐位元核對實際重播目錄，各操作檔案大小核對 ZIP 成員',
        }
    case['scope'] = '完整官方類別建立操作與驗證檔案；未呼叫 execute、未啟動 FastbootDev'
    cases.append(case)

with open(os.path.join(bundle_root, 'extraction.json')) as stream:
    extraction = json.load(stream)
result = {
    'scope': '直接執行官方匹配函式與官方例外類型；未執行 GUI、USB、燒錄動作或核心啟動',
    'backend_sha256': extraction['source_sha256'],
    'python_version': sys.version.split()[0],
    'official_functions': {
        name: {'source_filename': code.co_filename, 'source_line': code.co_firstlineno,
               'marshaled_code_sha256': hashlib.sha256(marshal.dumps(code)).hexdigest()}
        for name, code in method_codes.items()
    },
    'cases': cases,
}
assert cases[0]['error_type'] == 'FlashNotMatchError', cases[0]
assert '20014' in cases[0]['error_text']
assert cases[0]['matched_partitions'] == []
for case in cases[1:5]:
    assert case['status'] == 'matched', case
    assert case['matched_partitions'] == ['partition_universal.json'], case
assert cases[5]['error_type'] == 'FlashNotMatchError'
assert '20014' in cases[5]['error_text']
print(json.dumps(result, ensure_ascii=False, indent=2))
