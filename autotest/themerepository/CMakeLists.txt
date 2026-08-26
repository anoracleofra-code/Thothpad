# SPDX-FileCopyrightText: 2026 ThothPad contributors
#
# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.16)

project(themerepositorytest VERSION 1.0.0 LANGUAGES CXX)

find_package(Qt6 REQUIRED COMPONENTS Core Gui Test)

qt_standard_project_setup()

ecm_add_test(
    themerepositorytest.cpp
    ../../src/theme/chromecolors.h
    ../../src/theme/chromecolors.cpp
    ../../src/theme/theme.h
    ../../src/theme/theme.cpp
    ../../src/theme/themerepository.h
    ../../src/theme/themerepository.cpp
    LINK_LIBRARIES
        PRIVATE
            Qt::Core
            Qt::Gui
            Qt::Test
    TEST_NAME themerepositorytest
)

thothpad_configure_test(themerepositorytest)

target_include_directories(themerepositorytest PRIVATE ../../src)

enable_testing(true)
