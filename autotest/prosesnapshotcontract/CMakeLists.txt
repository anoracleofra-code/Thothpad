# SPDX-FileCopyrightText: 2026 ThothPad contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.16)

project(prosesnapshotcontracttest VERSION 1.0.0 LANGUAGES CXX)

find_package(Qt6 REQUIRED COMPONENTS Core Gui Test Widgets)

qt_standard_project_setup()

ecm_add_test(
    prosesnapshotcontracttest.cpp
    LINK_LIBRARIES
        PRIVATE
            Qt::Core
            Qt::Gui
            Qt::Test
            Qt::Widgets
    TEST_NAME prosesnapshotcontracttest
)

thothpad_configure_test(prosesnapshotcontracttest)
target_include_directories(prosesnapshotcontracttest PRIVATE ../../src)

enable_testing(true)
