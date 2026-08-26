# SPDX-FileCopyrightText: 2026 ThothPad contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.16)

project(proseawarenesswidgettest VERSION 1.0.0 LANGUAGES CXX)

find_package(Qt6 REQUIRED COMPONENTS Core Gui Test Widgets)

qt_standard_project_setup()

ecm_add_test(
    proseawarenesswidgettest.cpp
    ../../src/prose/proseawarenesswidget.h
    ../../src/prose/proseawarenesswidget.cpp
    ../../src/prose/prosediagnostic.h
    LINK_LIBRARIES
        PRIVATE
            Qt::Core
            Qt::Gui
            Qt::Test
            Qt::Widgets
    TEST_NAME proseawarenesswidgettest
)

thothpad_configure_test(proseawarenesswidgettest)

target_include_directories(proseawarenesswidgettest PRIVATE ../../src)

enable_testing(true)
