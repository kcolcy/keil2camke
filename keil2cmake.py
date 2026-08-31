import xml.etree.ElementTree as ET
import os
import re
import argparse
import sys

import configparser
from pathlib import Path


def parse_cpu_info(cpu_string, device_name):
    """
    从 uvprojx 的 <Cpu> 和 <Device> 字段自动解析芯片架构信息。

    参数:
        cpu_string: str，例如 "FPU2 CPUTYPE(\"Cortex-M4\") TZ"
        device_name: str，例如 "STM32F405RGTx"

    返回:
        dict，包含 cpu_type, fpu_type, float_abi, linker_cpu, device_family
    """
    info = {
        'cpu_type': 'Cortex-M3',
        'fpu_type': None,
        'float_abi': 'soft',
        'linker_cpu': 'Cortex-M3',
        'device_family': 'STM32F1xx',
    }

    # 从 Cpu 字段解析 CPU 类型
    cpu_match = re.search(r'CPUTYPE\("([^"]+)"\)', cpu_string)
    if cpu_match:
        info['cpu_type'] = cpu_match.group(1)

    # 从 Cpu 字段解析 FPU 类型
    if 'FPU3' in cpu_string or 'DFPU' in cpu_string:
        info['fpu_type'] = 'fpv5-d16'
        info['float_abi'] = 'hard'
    elif 'FPU' in cpu_string:
        info['fpu_type'] = 'fpv4-sp-d16'
        info['float_abi'] = 'hard'

    # 根据 CPU 类型和 FPU 生成链接器 CPU 标志
    cpu = info['cpu_type']
    if cpu == 'Cortex-M7':
        if info['fpu_type'] == 'fpv5-d16':
            info['linker_cpu'] = 'Cortex-M7.fp.dp'
        else:
            info['linker_cpu'] = 'Cortex-M7.fp.sp'
    elif cpu == 'Cortex-M4':
        if info['fpu_type']:
            info['linker_cpu'] = 'Cortex-M4.fp.sp'
        else:
            info['linker_cpu'] = 'Cortex-M4'
    else:
        info['linker_cpu'] = cpu

    # 从 Device 字段推导 STM32 系列
    # 例: STM32F405RGTx -> STM32F4xx, STM32H743VITx -> STM32H7xx, STM32F103ZE -> STM32F1xx
    dev_match = re.match(r'(STM32[A-Z]\d)', device_name)
    if dev_match:
        info['device_family'] = dev_match.group(1) + 'xx'

    return info


def parse_uvprojx(uvprojx_path):
    """解析uvprojx文件，提取所有 Target 的项目配置

    参数:
        uvprojx_path: str，.uvprojx 工程文件路径

    返回:
        list of dict，每个元素对应一个 Target 的完整配置，
        键名与原单 Target 版本一致:
        project_name, source_files, include_paths, defines, linker_script,
        device, c_flags, asm_flags, ld_flags, output_dir, use_armclang,
        opt_level, cpu_info, device_family
    """
    tree = ET.parse(uvprojx_path)
    root = tree.getroot()

    # 收集所有 Target 节点（注意：只有一个 Target 时 findall 也会正常返回列表）
    target_nodes = root.findall('.//Targets/Target')
    if not target_nodes:
        raise ValueError(f"未在 {uvprojx_path} 中找到任何 Target 节点")

    results = []
    for target in target_nodes:
        # 项目名称（TargetName）
        project_name = 'Unknown'
        name_node = target.find('TargetName')
        if name_node is not None and name_node.text:
            project_name = name_node.text.strip()

        # 输出目录
        output_dir = 'build/'
        out_node = target.find('./TargetOption/TargetCommonOption/OutputDirectory')
        if out_node is not None and out_node.text:
            output_dir = out_node.text

        # 源文件收集（只收集当前 Target 下的 Groups，避免多个 Target 互相串扰）
        source_files = []
        for group in target.findall('./Groups/Group'):
            for file in group.findall('./Files/File'):
                path_node = file.find('FilePath')
                if path_node is None or not path_node.text:
                    continue
                file_path = path_node.text.strip()
                if file_path.endswith(('.c', '.C', '.cpp', '.s', '.S', '.asm')):
                    source_files.append(file_path)

        # 包含路径（当前 Target 的 TargetOption）
        include_paths = []
        includes = target.find('./TargetOption/TargetArmAds/Cads/VariousControls/IncludePath')
        if includes is not None and includes.text:
            include_paths.extend([p.strip() for p in includes.text.split(';') if p.strip()])

        # 预定义宏
        defines = []
        defs = target.find('./TargetOption/TargetArmAds/Cads/VariousControls/Define')
        if defs is not None and defs.text:
            defines.extend([d.strip() for d in defs.text.split(',') if d.strip()])

        # 链接器脚本
        linker_script = None
        scatter_file = target.find('./TargetOption/TargetArmAds/LDads/ScatterFile')
        if scatter_file is not None and scatter_file.text:
            linker_script = scatter_file.text

        # 设备信息
        device_name = None
        device_node = target.find('./TargetOption/TargetCommonOption/Device')
        if device_node is not None and device_node.text:
            device_name = device_node.text

        # Cpu 字段（包含 CPU 类型、FPU 信息等）
        cpu_string = ''
        cpu_node = target.find('./TargetOption/TargetCommonOption/Cpu')
        if cpu_node is not None and cpu_node.text:
            cpu_string = cpu_node.text

        # 解析芯片架构信息
        cpu_info = parse_cpu_info(cpu_string, device_name if device_name else 'STM32F103')

        # 编译器版本检测
        use_armclang = False
        armclang_node = target.find('./TargetOption/TargetArmAds/UseArmClang')
        if armclang_node is not None and armclang_node.text == '1':
            use_armclang = True

        # 编译器选项
        def _misc_text(xpath):
            node = target.find(xpath)
            if node is not None and node.text is not None:
                return node.text
            return ''

        c_flags = _misc_text('./TargetOption/TargetArmAds/Cads/VariousControls/MiscControls')
        asm_flags = _misc_text('./TargetOption/TargetArmAds/Aads/VariousControls/MiscControls')
        ld_flags = _misc_text('./TargetOption/TargetArmAds/LDads/VariousControls/MiscControls')

        # 优化级别
        opt_level = '0'
        opt_node = target.find('./TargetOption/TargetArmAds/Cads/Optimization')
        if opt_node is not None and opt_node.text:
            opt_level = opt_node.text

        results.append({
            'project_name': project_name,
            'source_files': source_files,
            'include_paths': include_paths,
            'defines': defines,
            'linker_script': linker_script,
            'device': device_name.strip() if device_name else 'Unknown',
            'c_flags': c_flags,
            'asm_flags': asm_flags,
            'ld_flags': ld_flags,
            'output_dir': output_dir,
            'use_armclang': use_armclang,
            'opt_level': opt_level,
            'cpu_info': cpu_info,
            'device_family': cpu_info['device_family'],
        })

    return results


def select_target(targets, target_index=None):
    """从多个 Target 中选择一个用于生成。

    参数:
        targets: list of dict，parse_uvprojx 的返回值
        target_index: int or None，通过命令行 --target 指定的 1-based 编号

    返回:
        选中的 Target dict；用户取消或参数无效时返回 None
    """
    # 只有一个 Target 时直接使用，无需交互
    if len(targets) == 1:
        return targets[0]

    print(f"\n检测到 {len(targets)} 个 Target：")
    for i, t in enumerate(targets, start=1):
        print(f"  [{i}] {t['project_name']}  (Device: {t['device']}, 源文件数: {len(t['source_files'])})")

    # 提醒：链接脚本文件名需与所选 Target 对应
    print("\n提醒：生成的链接脚本路径为 MDK-ARM/${CMAKE_PROJECT_NAME}/${CMAKE_PROJECT_NAME}.sct，")
    print("      请确保 MDK-ARM 目录下链接文件 (.sct) 的文件名与所选 Target 名称一致；")
    print("      若 Target 名称包含特殊字符（如括号），会被替换为下划线，.sct 文件名需对应替换后的名称。")
    print("      最简单的处理办法为将顶层cmakelist中的 CMAKE_PROJECT_NAME 修改成和.sct 文件名一致。")

    # 优先使用命令行参数 --target
    if target_index is not None:
        if 1 <= target_index <= len(targets):
            return targets[target_index - 1]
        print(f"错误：--target 参数 {target_index} 超出范围 (1-{len(targets)})。")
        return None

    # 交互式选择
    while True:
        try:
            choice = input(f"\n请输入要生成的 Target 编号 (1-{len(targets)})，输入 q 取消：").strip()
        except EOFError:
            print("错误：无法读取输入（标准输入已关闭），请使用 --target 参数指定 Target 编号。")
            return None
        if choice.lower() in ('q', 'quit', 'exit'):
            print("已取消，未生成任何文件。")
            return None
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(targets):
                return targets[idx - 1]
        print(f"输入无效，请输入 1-{len(targets)} 之间的数字。")

def generate_stm32cubemx_cmake(source_files, include_paths, defines, device_family):
    """
    生成 CMake 文本块，自动为源文件和包含路径添加 ${CMAKE_CURRENT_SOURCE_DIR}/../ 前缀。
    对于 startup_stm32xxxx.s 文件，额外添加 ../../MDK-ARM/ 前缀。

    参数:
        source_files: list of str，源文件路径列表（例如 ["../Core/Src/main.c", "startup_stm32f429xx.s"]）
        include_paths: list of str，头文件包含路径列表（例如 ["../Core/Inc", "../Drivers/..."]）
        defines: list of str，预定义宏列表（例如 ["USE_FULL_LL_DRIVER", "HSE_VALUE=8000000", ...]）
        device_family: str，芯片系列（例如 "STM32F4xx"）

    返回:
        str，完整的 CMakeLists.txt 文本
    """
    # 为源文件路径添加前缀，启动文件需要特殊处理
    prefixed_sources = []
    for src in source_files:
        # 提取文件名（不含路径）
        base_name = os.path.basename(src)
        # 判断是否为启动文件：以 startup_stm32 开头，以 .s 结尾
        if base_name.startswith('startup_stm32') and base_name.endswith('.s'):
            # 添加 ../../MDK-ARM/ 前缀
            prefixed_src = f'${{CMAKE_CURRENT_SOURCE_DIR}}/../../MDK-ARM/{src}'
        else:
            prefixed_src = f'${{CMAKE_CURRENT_SOURCE_DIR}}/../{src}'
        prefixed_sources.append(prefixed_src)

    # 为包含路径添加前缀
    prefixed_includes = [f'${{CMAKE_CURRENT_SOURCE_DIR}}/../{inc}' for inc in include_paths]

    # 分类源文件（应用 vs 驱动）
    app_sources = []
    driver_sources = []
    for src in prefixed_sources:
        # 规则：包含 Drivers/{device_family}_HAL_Driver/Src/ 或 stm32XXX_ll_ 或 system_stm32XXX.c 的归为驱动
        # device_family 例如 "STM32F4xx"，对应文件名中的小写形式 "stm32f4xx"
        family_lower = device_family.lower()  # "stm32f4xx"
        if (f'Drivers/{device_family}_HAL_Driver/Src/' in src or
            f'{family_lower}_ll_' in src or
            src.endswith(f'system_{family_lower}.c')):
            driver_sources.append(src)
        else:
            app_sources.append(src)

    # 辅助函数：将列表格式化为 CMake set 语句的多行内容
    def format_list(var_name, items, indent='\t'):
        if not items:
            return f'set({var_name})\n'
        lines = [f'set({var_name}'] + [f'{indent}{item}' for item in items] + [')']
        return '\n'.join(lines) + '\n'

    # 预定义宏，末尾加上生成器表达式 $<$<CONFIG:Debug>:DEBUG>
    defines_with_debug = defines + ['$<$<CONFIG:Debug>:DEBUG>']
    defines_block = format_list('MX_Defines_Syms', defines_with_debug)

    include_block = format_list('MX_Include_Dirs', prefixed_includes)

    app_src_block = format_list('MX_Application_Src', app_sources)

    driver_src_block = format_list('STM32_Drivers_Src', driver_sources)

    # 固定框架
    cmake_text = f"""cmake_minimum_required(VERSION 3.22)
# Enable CMake support for ASM and C languages
enable_language(C ASM)
# STM32CubeMX generated symbols (macros)
{defines_block}
# STM32CubeMX generated include paths
{include_block}
# STM32CubeMX generated application sources
{app_src_block}
# STM32 HAL/LL Drivers
{driver_src_block}
# Drivers Midllewares
#   默认情况下 Midllewares 和 User 的 src 会被放读取到 MX_Application_Src 中，直接编译也可以，
#   为了方便管理，建议将 Midllewares 的 src 放到 Midllewares_Src 中，User 的 src 放到顶层 CMakeLists.txt 中，
#   请自行复制 Midllewares 相关代码到 Midllewares Src 中，
#   自行复制 User 的 src 到顶层 CMakeLists.txt，Midllewares 网口示例如下
#   1. 添加 LwIP_Src
#   set(LwIP_Src
#   网口相关的 Midllewares
#   )
#   2. 添加 LwIP_Src 到下面的 MX_LINK_LIBS 中
#   3. Create LwIP static library
#   add_library(LwIP OBJECT)
#   target_sources(LwIP PRIVATE ${{LwIP_Src}})
#   target_link_libraries(LwIP PUBLIC stm32cubemx)


# Link directories setup
set(MX_LINK_DIRS

)
# Project static libraries
set(MX_LINK_LIBS 
    STM32_Drivers
    ${{TOOLCHAIN_LINK_LIBRARIES}}
    
)
# Interface library for includes and symbols
add_library(stm32cubemx INTERFACE)
target_include_directories(stm32cubemx INTERFACE ${{MX_Include_Dirs}})
target_compile_definitions(stm32cubemx INTERFACE ${{MX_Defines_Syms}})

# Create STM32_Drivers static library
add_library(STM32_Drivers OBJECT)
target_sources(STM32_Drivers PRIVATE ${{STM32_Drivers_Src}})
target_link_libraries(STM32_Drivers PUBLIC stm32cubemx)


# Add STM32CubeMX generated application sources to the project
target_sources(${{CMAKE_PROJECT_NAME}} PRIVATE ${{MX_Application_Src}})

# Link directories setup
target_link_directories(${{CMAKE_PROJECT_NAME}} PRIVATE ${{MX_LINK_DIRS}})

# Add libraries to the project
target_link_libraries(${{CMAKE_PROJECT_NAME}} ${{MX_LINK_LIBS}})

# Add the map file to the list of files to be removed with 'clean' target
set_target_properties(${{CMAKE_PROJECT_NAME}} PROPERTIES ADDITIONAL_CLEAN_FILES ${{CMAKE_PROJECT_NAME}}.map)

# Validate that STM32CubeMX code is compatible with C standard
if((CMAKE_C_STANDARD EQUAL 90) OR (CMAKE_C_STANDARD EQUAL 99))
    message(ERROR "Generated code requires C11 or higher")
endif()"""

    return cmake_text



def generate_armclang_cmake(cpu_info):
    cpu_type = cpu_info['cpu_type']
    fpu_type = cpu_info['fpu_type']
    float_abi = cpu_info['float_abi']
    linker_cpu = cpu_info['linker_cpu']

    # 构造 TARGET_FLAGS
    target_flags = f'--target=arm-arm-none-eabi -mcpu={cpu_type.lower()}'
    if fpu_type:
        target_flags += f' -mfpu={fpu_type}'
    target_flags += f' -mfloat-abi={float_abi}'

    cmake_text = f'''set(CMAKE_SYSTEM_NAME               Generic)
set(CMAKE_SYSTEM_PROCESSOR          arm)

set(CMAKE_C_COMPILER_ID ARMClang)
set(CMAKE_CXX_COMPILER_ID ARMClang)

# ARMCLANG V6 from Keil MDK C/C++ compiler
set(TOOLCHAIN_PATH                "D:\\\\Software\\\\Keil_v5\\\\ARM\\\\ARMCLANG\\\\bin\\\\")

set(CMAKE_C_COMPILER                "${{TOOLCHAIN_PATH}}armclang.exe")
set(CMAKE_ASM_COMPILER              "${{CMAKE_C_COMPILER}}")
set(CMAKE_CXX_COMPILER              "${{TOOLCHAIN_PATH}}armclang.exe")
set(CMAKE_LINKER                    "${{TOOLCHAIN_PATH}}armlink.exe")
set(CMAKE_OBJCOPY                   "${{TOOLCHAIN_PATH}}fromelf.exe")
set(CMAKE_SIZE                      "${{TOOLCHAIN_PATH}}fromelf.exe")

set(CMAKE_EXECUTABLE_SUFFIX_ASM     ".elf")
set(CMAKE_EXECUTABLE_SUFFIX_C       ".elf")
set(CMAKE_EXECUTABLE_SUFFIX_CXX     ".elf")

set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)

# MCU specific flags
set(TARGET_FLAGS "{target_flags} ")

# C compiler flags
set(CMAKE_C_FLAGS "${{CMAKE_C_FLAGS}} ${{TARGET_FLAGS}}")
# ASM compiler flags
set(CMAKE_ASM_FLAGS "${{CMAKE_C_FLAGS}} -masm=auto")
set(CMAKE_C_FLAGS "${{CMAKE_C_FLAGS}} -gdwarf-4 -ffunction-sections")

# The cyclomatic-complexity parameter must be defined for the Cyclomatic complexity feature in STM32CubeIDE to work.
# However, most GCC toolchains do not support this option, which causes a compilation error; for this reason, the feature is disabled by default.
# set(CMAKE_C_FLAGS "${{CMAKE_C_FLAGS}} -fcyclomatic-complexity")

set(CMAKE_C_FLAGS_DEBUG "-O1 -g")
set(CMAKE_C_FLAGS_RELEASE "-Os -g0")
set(CMAKE_CXX_FLAGS_DEBUG "-O1 -g")
set(CMAKE_CXX_FLAGS_RELEASE "-Os -g0")

set(CMAKE_CXX_FLAGS "${{CMAKE_C_FLAGS}} -fno-rtti -fno-exceptions -fno-threadsafe-statics")

set(CMAKE_EXE_LINKER_FLAGS "--cpu={linker_cpu}")
set(CMAKE_EXE_LINKER_FLAGS "${{CMAKE_EXE_LINKER_FLAGS}} --strict")
set(CMAKE_EXE_LINKER_FLAGS "${{CMAKE_EXE_LINKER_FLAGS}} --scatter  \\"${{CMAKE_SOURCE_DIR}}/MDK-ARM/${{CMAKE_PROJECT_NAME}}/${{CMAKE_PROJECT_NAME}}.sct\\"")
set(CMAKE_EXE_LINKER_FLAGS "${{CMAKE_EXE_LINKER_FLAGS}} --summary_stderr --info summarysizes --map")
set(CMAKE_EXE_LINKER_FLAGS "${{CMAKE_EXE_LINKER_FLAGS}} --load_addr_map_info --xref --callgraph --symbols")
set(CMAKE_EXE_LINKER_FLAGS "${{CMAKE_EXE_LINKER_FLAGS}} --info sizes --info totals --info unused --info veneers")

# Ninja generator uses these rule variables.
# armlink syntax: armlink [options] --list <map> -o <output> <objects>
#
if(NOT CMAKE_C_LINK_EXECUTABLE)
    set(CMAKE_C_LINK_EXECUTABLE
        "<CMAKE_LINKER> <CMAKE_C_LINK_FLAGS> <LINK_FLAGS> <FLAGS> --list=<TARGET>.map -o <TARGET> <OBJECTS>"
    )
endif()
if(NOT CMAKE_CXX_LINK_EXECUTABLE)
    set(CMAKE_CXX_LINK_EXECUTABLE
        "<CMAKE_LINKER> <CMAKE_CXX_LINK_FLAGS> <LINK_FLAGS> <FLAGS> --list=<TARGET>.map -o <TARGET> <OBJECTS>"
    )
endif()
if(NOT CMAKE_ASM_LINK_EXECUTABLE)
    set(CMAKE_ASM_LINK_EXECUTABLE
        "<CMAKE_LINKER> <CMAKE_ASM_LINK_FLAGS> <LINK_FLAGS> <FLAGS> --list=<TARGET>.map -o <TARGET> <OBJECTS>"
    )
endif()
'''
    return cmake_text




def generate_top_cmake(prj_name):
    # Target 名称可能包含括号等 CMake 不支持的字符（如 "H7(test)"），
    # 统一替换为下划线，保证生成的 project() 命令合法
    prj_name = re.sub(r'[^A-Za-z0-9_.-]', '_', prj_name)

    cmake_text_header = '''cmake_minimum_required(VERSION 3.22)

#
# This file is generated only once,
# and is not re-generated if converter is called multiple times.
#
# User is free to modify the file as much as necessary
#

# Setup compiler settings
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS ON)


# Define the build type
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Debug")
endif()
'''
    prj_name = f'''
# Set the project name
set(CMAKE_PROJECT_NAME {prj_name})
'''
    
    cmake_text_tail = '''
# Enable compile command to ease indexing with e.g. clangd
set(CMAKE_EXPORT_COMPILE_COMMANDS TRUE)

# Core project settings
project(${CMAKE_PROJECT_NAME})
message("Build type: " ${CMAKE_BUILD_TYPE})

# Enable CMake support for ASM and C languages
enable_language(C ASM)

# Create an executable object type
add_executable(${CMAKE_PROJECT_NAME})

# Add STM32CubeMX generated sources
add_subdirectory(cmake/stm32cubemx)

# Link directories setup
target_link_directories(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user defined library search paths
)

# Add sources to executable
target_sources(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user sources here
)

# Add include paths
target_include_directories(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user defined include paths
)

# Add project symbols (macros)
target_compile_definitions(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user defined symbols
)

# Remove wrong libob.a library dependency when using cpp files
list(REMOVE_ITEM CMAKE_C_IMPLICIT_LINK_LIBRARIES ob)

# Add linked libraries
target_link_libraries(${CMAKE_PROJECT_NAME}
    stm32cubemx

    # Add user defined libraries
)
'''
    return cmake_text_header + prj_name + cmake_text_tail


def generate_CMakePresets():
    CMakePresets = '''{
    "version": 3,
    "configurePresets": [
        {
            "name": "default",
            "hidden": true,
            "generator": "Ninja",
            "binaryDir": "${sourceDir}/build/${presetName}",
            "toolchainFile": "${sourceDir}/cmake/armclang.cmake",
            "cacheVariables": {
            }
        },
        {
            "name": "Debug",
            "inherits": "default",
            "cacheVariables": {
                "CMAKE_BUILD_TYPE": "Debug"
            }
        },
        {
            "name": "Release",
            "inherits": "default",
            "cacheVariables": {
                "CMAKE_BUILD_TYPE": "Release"
            }
        }
    ],
    "buildPresets": [
        {
            "name": "Debug",
            "configurePreset": "Debug"
        },
        {
            "name": "Release",
            "configurePreset": "Release"
        }
    ]
}'''
    return CMakePresets

def find_uvprojx_files(root_dir, max_depth=5):
    """
    在指定目录下递归搜索所有 .uvprojx 文件，深度不超过 max_depth。
    返回文件绝对路径列表。
    """
    result = []
    # 确保 root_dir 是绝对路径
    root_dir = os.path.abspath(root_dir)

    def _search(current_dir, current_depth):
        if current_depth > max_depth:
            return
        try:
            for entry in os.listdir(current_dir):
                full_path = os.path.join(current_dir, entry)
                if os.path.isfile(full_path) and entry.endswith('.uvprojx'):
                    result.append(full_path)
                elif os.path.isdir(full_path):
                    _search(full_path, current_depth + 1)
        except PermissionError:
            # 忽略无法访问的目录
            pass

    _search(root_dir, 0)
    return result

def main():
    # 确保控制台输出使用 UTF-8，避免中文在部分环境下乱码
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except Exception:
            pass

    # 命令行参数
    parser = argparse.ArgumentParser(description='将 Keil MDK (.uvprojx) 工程转换为 CMake 工程')
    parser.add_argument('uvprojx', nargs='?', default=None,
                        help='指定 .uvprojx 工程文件路径；不指定时自动在当前目录及其子目录中搜索')
    parser.add_argument('--target', type=int, default=None, metavar='N',
                        help='指定要生成的 Target 编号（从 1 开始）；工程有多个 Target 时可用该参数免交互选择')
    parser.add_argument('--list-targets', action='store_true',
                        help='仅列出工程中的 Target 列表，不生成文件')
    args = parser.parse_args()

    # 获取当前脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))

    # 确定要处理的 .uvprojx 文件
    if args.uvprojx:
        uvprojx_path = os.path.abspath(args.uvprojx)
        if not os.path.isfile(uvprojx_path):
            print(f"错误：找不到指定的工程文件 {uvprojx_path}")
            return
    else:
        # 搜索所有 .uvprojx 文件（最大深度5级）
        uvprojx_files = find_uvprojx_files(script_dir, max_depth=5)

        if not uvprojx_files:
            print("错误：在当前目录及其子目录（最深5层）中未找到任何 .uvprojx 文件。")
            print("可通过命令行参数直接指定工程文件路径，例如：python keil2cmake.py MDK-ARM/H7.uvprojx")
            return

        # 实际工程中只有一个 .uvprojx；即使找到多个，也直接使用读到的第一个
        uvprojx_path = uvprojx_files[0]

    print(f"正在处理项目文件：{uvprojx_path}")

    # 解析所有 Target
    targets = parse_uvprojx(uvprojx_path)

    # 仅列出 Target 模式
    if args.list_targets:
        print(f"\n工程 {os.path.basename(uvprojx_path)} 包含 {len(targets)} 个 Target：")
        for i, t in enumerate(targets, start=1):
            print(f"  [{i}] {t['project_name']}  (Device: {t['device']}, 源文件数: {len(t['source_files'])})")
        return

    # 选择 Target：只有一个时直接生成；有多个时输出列表让用户选择
    project_data = select_target(targets, target_index=args.target)
    if project_data is None:
        return

    print(f"已选择 Target：{project_data['project_name']}")

    # 定义输出路径（仍然在脚本所在目录下的 cmake 子目录中）
    paths = {
        'stm32cubemx': os.path.join(script_dir, 'cmake', 'stm32cubemx', 'CMakeLists.txt'),
        'armclang': os.path.join(script_dir, 'cmake', 'armclang.cmake'),
        'top': os.path.join(script_dir, 'CMakeLists.txt'),
        'CMakePresets': os.path.join(script_dir, 'CMakePresets.json')   # 添加这一行
    }

    # 写入文件，自动创建目录
    for key, path in paths.items():
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # 以 utf_8 编码写入文件，确保中文字符不会出现乱码
        with open(path, 'w', encoding='utf-8') as f:
            if key == 'stm32cubemx':
                content = generate_stm32cubemx_cmake(
                    project_data['source_files'],
                    project_data['include_paths'],
                    project_data['defines'],
                    project_data['device_family']
                )
            elif key == 'armclang':
                content = generate_armclang_cmake(project_data['cpu_info'])
            elif key == 'CMakePresets':
                content = generate_CMakePresets()
            else:
                content = generate_top_cmake(project_data['project_name'])
            f.write(content)

    print("CMake 文件生成完成。")

if __name__ == "__main__":
    main()
