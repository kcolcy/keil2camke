import xml.etree.ElementTree as ET
import os
import argparse

import configparser
from pathlib import Path


def parse_uvprojx(uvprojx_path):
    """解析uvprojx文件，提取项目配置"""
    tree = ET.parse(uvprojx_path)
    root = tree.getroot()
    
    # 项目基本信息
    # print("Getting target info...")
    project_name = root.find('.//Targets/Target/TargetName').text
    # print(f"Project Name: {project_name}")
    output_dir = root.find('.//Targets/Target/TargetOption/TargetCommonOption/OutputDirectory').text or 'build/'
    
    # 源文件收集
    # print("\n\nCollecting source files...")
    source_files = []
    for group in root.findall('.//Groups/Group'):
        for file in group.findall('Files/File'):
            file_path = file.find('FilePath').text
            if file_path.endswith(('.c', '.C', '.cpp', '.s', '.S', '.asm')):
                source_files.append(file_path)
                # print(f"source file: {file_path}")
    
    # 包含路径
    # print("\n\nSetting include paths...")
    include_paths = []
    includes = root.find('.//TargetOption/TargetArmAds/Cads/VariousControls/IncludePath')
    if includes is not None and includes.text:
        include_paths.extend(includes.text.split(';'))
        # print(f"Include Paths: {include_paths}")
    
    # 预定义宏
    # print("\n\nLoading preset defines...")
    defines = []
    defs = root.find('.//TargetOption/TargetArmAds/Cads/VariousControls/Define')
    if defs is not None and defs.text:
        defines.extend([d.strip() for d in defs.text.split(',')])
        # print(f"Defines: {defines}")
    
    # 链接器脚本
    # print("\n\nLooking for scatter file...")
    linker_script = None
    scatter_file = root.find('.//TargetOption/TargetArmAds/LDads/ScatterFile')
    if scatter_file is not None and scatter_file.text:
        linker_script = scatter_file.text
    # print(f"Linker Script: {linker_script}")

    # 设备信息
    # print("\n\nGetting device info...")
    device_name = None
    device_node = root.find('.//Targets/Target/TargetOption/TargetCommonOption/Device')
    if device_node is not None and device_node.text:
        device_name = device_node.text
    # print(f"Device: {device_name}")

    # 编译器版本检测
    # print("\n\nValidating compilor version...")
    use_armclang = False
    armclang_node = root.find('.//TargetOption/TargetArmAds/UseArmClang')
    if armclang_node is not None and armclang_node.text == '1':
        use_armclang = True
    
    # 编译器选项
    # print("\n\nSetting options for compilors and linkers...")
    c_flags = root.find('.//TargetOption/TargetArmAds/Cads/VariousControls/MiscControls') 
    asm_flags = root.find('.//TargetOption/TargetArmAds/Aads/VariousControls/MiscControls') 
    ld_flags = root.find('.//TargetOption/TargetArmAds/LDads/VariousControls/MiscControls') 
    if c_flags is not None and c_flags.text is not None:
        c_flags = c_flags.text
    else:
        c_flags = ''
    if asm_flags is not None and asm_flags.text is not None:
        asm_flags = asm_flags.text
    else:
        asm_flags = ''
    if ld_flags is not None:
        ld_flags = ld_flags.text
    else:
        ld_flags = ''
    # print(f"C Flags: {c_flags}")
    # print(f"ASM Flags: {asm_flags}")
    # print(f"LD Flags: {ld_flags}")
    
    # 优化级别
    # print("\n\nDefining optimize level...")
    opt_level = "0"
    opt_node = root.find('.//TargetOption/TargetArmAds/Cads/Optimization')
    if opt_node is not None and opt_node.text:
        opt_level = opt_node.text
    
    return {
        'project_name': project_name,
        'source_files': source_files,
        'include_paths': include_paths,
        'defines': defines,
        'linker_script': linker_script,
        'device': device_name.strip() if device_name else "Unknown",
        'c_flags': c_flags,
        'asm_flags': asm_flags,
        'ld_flags': ld_flags,
        'output_dir': output_dir,
        'use_armclang': use_armclang,
        'opt_level': opt_level
    }

def generate_stm32cubemx_cmake(source_files, include_paths, defines):
    """
    生成 CMake 文本块，自动为源文件和包含路径添加 ${CMAKE_CURRENT_SOURCE_DIR}/../ 前缀。
    对于 startup_stm32xxxx.s 文件，额外添加 ../../MDK-ARM/ 前缀。

    参数:
        source_files: list of str，源文件路径列表（例如 ["../Core/Src/main.c", "startup_stm32f429xx.s"]）
        include_paths: list of str，头文件包含路径列表（例如 ["../Core/Inc", "../Drivers/..."]）
        defines: list of str，预定义宏列表（例如 ["USE_FULL_LL_DRIVER", "HSE_VALUE=8000000", ...]）

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
        # 规则：包含 Drivers/STM32F4xx_HAL_Driver/Src/ 或 stm32f4xx_ll_ 或 system_stm32f4xx.c 的归为驱动
        if ('Drivers/STM32F4xx_HAL_Driver/Src/' in src or
            'stm32f4xx_ll_' in src or
            src.endswith('system_stm32f4xx.c')):
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



def generate_armclang_cmake():
    cmake_text = '''set(CMAKE_SYSTEM_NAME               Generic)
set(CMAKE_SYSTEM_PROCESSOR          arm)

set(CMAKE_C_COMPILER_ID ARMClang)
set(CMAKE_CXX_COMPILER_ID ARMClang)

# ARMCLANG V6 from Keil MDK C/C++ compiler 用户需要自行调整实际路径
set(TOOLCHAIN_PATH                "D:\\\\Software\\\\Keil_v5\\\\ARM\\\\ARMCLANG\\\\bin\\\\")

set(CMAKE_C_COMPILER                "${TOOLCHAIN_PATH}armclang.exe")
set(CMAKE_ASM_COMPILER              "${CMAKE_C_COMPILER}")
set(CMAKE_CXX_COMPILER              "${TOOLCHAIN_PATH}armclang.exe")
set(CMAKE_LINKER                    "${TOOLCHAIN_PATH}armlink.exe")
set(CMAKE_OBJCOPY                   "${TOOLCHAIN_PATH}fromelf.exe")
set(CMAKE_SIZE                      "${TOOLCHAIN_PATH}fromelf.exe")

set(CMAKE_EXECUTABLE_SUFFIX_ASM     ".elf")
set(CMAKE_EXECUTABLE_SUFFIX_C       ".elf")
set(CMAKE_EXECUTABLE_SUFFIX_CXX     ".elf")

set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)

# MCU specific flags
set(TARGET_FLAGS "--target=arm-arm-none-eabi -mcpu=cortex-m4 -mfpu=fpv4-sp-d16 -mfloat-abi=hard ")

# C compiler flags
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} ${TARGET_FLAGS}")
# ASM compiler flags
set(CMAKE_ASM_FLAGS "${CMAKE_C_FLAGS} -masm=auto")
set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -gdwarf-4 -ffunction-sections")

# The cyclomatic-complexity parameter must be defined for the Cyclomatic complexity feature in STM32CubeIDE to work.
# However, most GCC toolchains do not support this option, which causes a compilation error; for this reason, the feature is disabled by default.
# set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -fcyclomatic-complexity")

set(CMAKE_C_FLAGS_DEBUG "-O1 -g")
set(CMAKE_C_FLAGS_RELEASE "-Os -g0")
set(CMAKE_CXX_FLAGS_DEBUG "-O1 -g")
set(CMAKE_CXX_FLAGS_RELEASE "-Os -g0")

set(CMAKE_CXX_FLAGS "${CMAKE_C_FLAGS} -fno-rtti -fno-exceptions -fno-threadsafe-statics")

set(CMAKE_EXE_LINKER_FLAGS "--cpu=Cortex-M4.fp.sp")
set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} --strict")
set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} --scatter  \\"${CMAKE_SOURCE_DIR}/MDK-ARM/${CMAKE_PROJECT_NAME}/${CMAKE_PROJECT_NAME}.sct\\"")
set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} --summary_stderr --info summarysizes --map")
set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} --load_addr_map_info --xref --callgraph --symbols")
set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} --info sizes --info totals --info unused --info veneers")

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
    # 获取当前脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 搜索所有 .uvprojx 文件（最大深度5级）
    uvprojx_files = find_uvprojx_files(script_dir, max_depth=5)
    
    if not uvprojx_files:
        print("错误：在当前目录及其子目录（最深5层）中未找到任何 .uvprojx 文件。")
        return
    
    # 如果找到多个，提示并默认使用第一个
    if len(uvprojx_files) > 1:
        print("警告：找到多个 .uvprojx 文件，将使用第一个进行处理：")
        for f in uvprojx_files:
            print(f"  {f}")
    
    uvprojx_path = uvprojx_files[0]
    print(f"正在处理项目文件：{uvprojx_path}")
    
    # 解析项目文件（假设 parse_uvprojx 等函数已定义）
    project_data = parse_uvprojx(uvprojx_path)
    
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
        with open(path, 'w') as f:
            if key == 'stm32cubemx':
                content = generate_stm32cubemx_cmake(
                    project_data['source_files'],
                    project_data['include_paths'],
                    project_data['defines']
                )
            elif key == 'armclang':
                content = generate_armclang_cmake()
            elif key == 'CMakePresets':
                content = generate_CMakePresets()
            else:
                content = generate_top_cmake(project_data['project_name'])
            f.write(content)
    
    print("CMake 文件生成完成。")

if __name__ == "__main__":
    main()
