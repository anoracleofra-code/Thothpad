# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.16)

project(prosediagnostictest VERSION 1.0.0 LANGUAGES CXX)

find_package(Qt6 REQUIRED COMPONENTS Core Test)

qt_standard_project_setup()

ecm_add_test(
    prosediagnostictest.cpp
    ../../src/prose/prosediagnostic.h
    LINK_LIBRARIES
        PRIVATE
            Qt::Core
            Qt::Test
    TEST_NAME prosediagnostictest
)

thothpad_configure_test(prosediagnostictest)

target_include_directories(prosediagnostictest PRIVATE ../../src)

enable_testing(true)
