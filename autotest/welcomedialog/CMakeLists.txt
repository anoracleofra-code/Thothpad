# SPDX-FileCopyrightText: 2026 ThothPad contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.16)

project(welcomedialogtest VERSION 1.0.0 LANGUAGES CXX)

find_package(Qt6 REQUIRED COMPONENTS Core Gui Test Widgets)

qt_standard_project_setup()

ecm_add_test(
    welcomedialogtest.cpp
    ../../src/welcomedialog.h
    ../../src/welcomedialog.cpp
    ../../icons.qrc
    LINK_LIBRARIES
        PRIVATE
            Qt::Core
            Qt::Gui
            Qt::Test
            Qt::Widgets
    TEST_NAME welcomedialogtest
)

thothpad_configure_test(welcomedialogtest)

target_include_directories(welcomedialogtest PRIVATE ../../src)

enable_testing(true)
