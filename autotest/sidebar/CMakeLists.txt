# SPDX-FileCopyrightText: 2026 ThothPad contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.16)
project(sidebartest VERSION 1.0.0 LANGUAGES CXX)

find_package(Qt6 REQUIRED COMPONENTS Core Gui Test Widgets)

qt_standard_project_setup()

ecm_add_test(
    sidebartest.cpp
    ../../src/sidebar.h
    ../../src/sidebar.cpp
    LINK_LIBRARIES
        PRIVATE
            Qt::Core
            Qt::Gui
            Qt::Test
            Qt::Widgets
    TEST_NAME sidebartest
)

thothpad_configure_test(sidebartest)

enable_testing(true)
