# SPDX-FileCopyrightText: 2026 ThothPad contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.16)

project(writerengineclienttest VERSION 1.0.0 LANGUAGES CXX)

find_package(Qt6 REQUIRED COMPONENTS Core Test)

qt_standard_project_setup()

ecm_add_test(
    writerengineclienttest.cpp
    ../../src/prose/writerengineclient.h
    ../../src/prose/writerengineclient.cpp
    ../../src/prose/performancepolicy.h
    ../../src/prose/performancepolicy.cpp
    LINK_LIBRARIES
        PRIVATE
            Qt::Core
            Qt::Test
    TEST_NAME writerengineclienttest
)

thothpad_configure_test(writerengineclienttest)

target_compile_definitions(writerengineclienttest PRIVATE APPVERSION="${RELEASE_SERVICE_VERSION}")
target_include_directories(writerengineclienttest PRIVATE ../../src)

enable_testing(true)
