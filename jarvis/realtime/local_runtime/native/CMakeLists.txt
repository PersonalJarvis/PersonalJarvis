cmake_minimum_required(VERSION 3.22)
project(jarvis_native_audio LANGUAGES C CXX)

if(WIN32)
    add_compile_definitions(_WIN32_WINNT=0x0A00 WINVER=0x0A00)
endif()

if(NOT DEFINED JARVIS_LFM_SOURCE)
    message(FATAL_ERROR "JARVIS_LFM_SOURCE must name the pinned LFM runner source tree")
endif()
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TOOLS ON CACHE BOOL "" FORCE)
set(LLAMA_BUILD_COMMON ON CACHE BOOL "" FORCE)
set(LLAMA_CURL OFF CACHE BOOL "" FORCE)
set(GGML_NATIVE OFF CACHE BOOL "" FORCE)
add_subdirectory("${JARVIS_LFM_SOURCE}" lfm EXCLUDE_FROM_ALL)
foreach(dependency common llama)
    if(MSVC)
        target_compile_options(${dependency} PRIVATE
            "$<$<COMPILE_LANGUAGE:CXX>:/FI${CMAKE_CURRENT_SOURCE_DIR}/standard_headers.hpp>")
    else()
        target_compile_options(${dependency} PRIVATE "$<$<COMPILE_LANGUAGE:CXX>:-include>"
            "$<$<COMPILE_LANGUAGE:CXX>:${CMAKE_CURRENT_SOURCE_DIR}/standard_headers.hpp>")
    endif()
endforeach()

add_executable(jarvis-native-audio worker.cpp)
target_compile_features(jarvis-native-audio PRIVATE cxx_std_17)
target_link_libraries(jarvis-native-audio PRIVATE liquid-audio)
target_compile_definitions(jarvis-native-audio PRIVATE
    JARVIS_LFM_REVISION="ec9d1fdd9cc18643c5e161b65b8053f6f6f34a4b")

if(WIN32)
    target_compile_definitions(jarvis-native-audio PRIVATE _WIN32_WINNT=0x0A00 WINVER=0x0A00)
endif()
