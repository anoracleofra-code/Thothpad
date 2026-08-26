# SPDX-FileCopyrightText: 2026 ThothPad contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.16)

project(prosinstrumentationtest VERSION 1.0.0 LANGUAGES CXX)

find_package(Qt6 REQUIRED COMPONENTS Concurrent Core Test)

qt_standard_project_setup()

ecm_add_test(
    prosinstrumentationtest.cpp
    ../../src/prose/proseinstrumentation.h
    ../../src/prose/proseinstrumentation.cpp
    LINK_LIBRARIES
        PRIVATE
            Qt::Concurrent
            Qt::Core
            Qt::Test
    TEST_NAME prosinstrumentationtest
)

thothpad_configure_test(prosinstrumentationtest)

target_compile_definitions(prosinstrumentationtest PRIVATE THOTHPAD_INSTRUMENTATION)
target_include_directories(prosinstrumentationtest PRIVATE ../../src)

enable_testing(true)
